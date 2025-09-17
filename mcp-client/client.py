import asyncio
import sys
from typing import Optional
from contextlib import AsyncExitStack
import json
import os
import requests

from flask import Flask, request, jsonify, render_template

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import anthropic
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

SCRAPINGDOG_API_KEY = os.getenv("SCRAPINGDOG_API_KEY")

app = Flask(__name__)

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
        server_params = StdioServerParameters(command=command, args=[server_script_path], env=None)

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))
        await self.session.initialize()

        response = await self.session.list_tools()
        print("✅ Connected to server with tools:", [t.name for t in response.tools])

    async def cleanup(self):
        await self.exit_stack.aclose()


mcp_client = MCPWebClient()

# Initialize Anthropic client
client = anthropic.Anthropic()


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

    # Upload file to Claude API
    uploaded_file = client.beta.files.upload(
        file=(file.filename, file.stream, file.mimetype or "application/octet-stream")
    )

    file_id = uploaded_file.id

    # Extraction prompt
    extraction_prompt = """
    Extract ONLY the following fields from this CV:

    {
      "skills": [...],      // list of strings
      "location": "...",    // string
      "experience": "...",  // string (summary of job experience)
      "jobRole": "..."      // probable job title this candidate is best suited for , based on his experience and the skill.
    }

    Rules:
    - If any field is missing, set its value to "N/A".
    - You will decide the Job role based on the candidate experience and the skills and you will return the best single job suited for the candidate.
    - Return ONLY valid JSON, nothing else.
    """

    response = client.beta.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": extraction_prompt},
                    {"type": "document", "source": {"type": "file", "file_id": file_id}},
                ],
            }
        ],
        betas=["files-api-2025-04-14"],
    )

    # Extract Claude raw JSON string
    extracted_text = response.content[0].text if response.content else ""

    # Remove ```json ... ``` wrappers
    cleaned_text = (
        extracted_text.replace("```json", "")
        .replace("```", "")
        .strip()
    )

    try:
        parsed = json.loads(cleaned_text)
    except Exception:
        parsed = {"skills": ["N/A"], "location": "N/A", "experience": "N/A", "jobRole": "N/A"}

    # Extract fields safely
    print("output jobs : ", parsed)
    skills = parsed.get("skills", ["N/A"])
    if not isinstance(skills, list):
        skills = [skills] if skills else ["N/A"]

    location = parsed.get("location", "N/A")
    experience = parsed.get("experience", "N/A")
    jobRole = parsed.get("jobRole", "N/A")

    # ✅ Job Search API request
    params = {
        "api_key": SCRAPINGDOG_API_KEY,
        "query": jobRole if jobRole != "N/A" else "Software Engineer",
        "country": location if location != "N/A" else "us",
    }
    
    print("passing params : ", params)

    jobs_data = []
    try:
        r = requests.get("https://api.scrapingdog.com/google_jobs", params=params)
        if r.status_code == 200:
            job_json = r.json()
            # Extract first few jobs only
            for job in job_json.get("jobs_results", [])[:5]:
                jobs_data.append({
                    "title": job.get("title", "N/A"),
                    "company": job.get("company_name", "N/A"),
                    "location": job.get("location", "N/A"),
                    "link": job.get("share_link", "#"),
                    "description": job.get("description", "N/A")[:300] + "..."
                })
        else:
            print(f"Job API request failed: {r.status_code}")
    except Exception as e:
        print("Job API error:", str(e))
        
    print("Jobs I got : ", jobs_data)

    # ✅ Return extracted + job results
    return jsonify({
        "skills": skills,
        "location": location,
        "experience": experience,
        "jobRole": jobRole,
        "jobs": jobs_data
    })


async def main():
    if len(sys.argv) < 2:
        print("Usage: python client.py <path_to_server_script>")
        sys.exit(1)

    await mcp_client.connect_to_server(sys.argv[1])
    app.run(host="0.0.0.0", port=5000, debug=True)


if __name__ == "__main__":
    asyncio.run(main())
