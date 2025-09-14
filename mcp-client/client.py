import asyncio
import sys
from typing import Optional
from contextlib import AsyncExitStack
import os

from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from dotenv import load_dotenv

# For CV text extraction
import fitz  # PyMuPDF
import docx

# Load environment variables
load_dotenv()

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# -------- Helpers for extracting text --------
def extract_text_from_pdf(file_path: str) -> str:
    text = ""
    with fitz.open(file_path) as pdf:
        for page in pdf:
            text += page.get_text()
    return text


def extract_text_from_docx(file_path: str) -> str:
    d = docx.Document(file_path)
    return "\n".join([para.text for para in d.paragraphs])


def get_cv_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext in [".txt", ".md"]:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    else:
        return ""


# -------- MCP Web Client --------
class MCPWebClient:
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()

    async def connect_to_server(self, server_script_path: str):
        is_python = server_script_path.endswith(".py")
        is_js = server_script_path.endswith(".js")
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None,
        )

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))
        await self.session.initialize()

        response = await self.session.list_tools()
        print("✅ Connected to server with tools:", [t.name for t in response.tools])

    async def process_cv(self, file_path: str) -> dict:
        """Extract text from CV, send to MCP server/Claude, return JSON profile + full text."""
        cv_text = get_cv_text(file_path)

        # print FULL extracted text (no truncation)
        print("\n===== Extracted CV Text (FULL) =====\n")
        print(cv_text)
        print("\n====================================\n")

        if not cv_text.strip():
            return {"error": "Unable to extract text from CV. Please upload a valid PDF, DOCX, or TXT."}

        # Call server tool: extract_profile
        profile_result = await self.session.call_tool("extract_profile", {"cv_text": cv_text})
        if not profile_result or not profile_result.content:
            return {"error": "Failed to extract profile from CV"}

        # Tool result may be a text chunk; parse as JSON
        first_chunk = profile_result.content[0]
        profile_text = first_chunk.text if hasattr(first_chunk, "text") else str(first_chunk)

        import json
        try:
            profile = json.loads(profile_text)
        except Exception:
            return {"error": "Claude did not return valid JSON", "raw": profile_text}

        # return FULL text + structured profile
        return {
            "extracted_text": cv_text,
            "profile": profile
        }

    async def cleanup(self):
        await self.exit_stack.aclose()


mcp_client = MCPWebClient()


# -------- Flask Routes --------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(file_path)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(mcp_client.process_cv(file_path))

    return jsonify(result)


async def main():
    if len(sys.argv) < 2:
        print("Usage: python client.py <path_to_server_script>")
        sys.exit(1)

    await mcp_client.connect_to_server(sys.argv[1])
    app.run(host="0.0.0.0", port=5000, debug=True)


if __name__ == "__main__":
    asyncio.run(main())
