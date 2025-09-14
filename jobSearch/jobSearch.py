import os
import json
from typing import Any

import httpx
from dotenv import load_dotenv
from anthropic import Anthropic
from mcp.server.fastmcp import FastMCP

# Load environment variables
load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SCRAPINGDOG_API_KEY = os.getenv("SCRAPINGDOG_API_KEY")

anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)

# Initialize FastMCP server
mcp = FastMCP("job-search")


# ---------------- Helper Functions ----------------
async def call_claude(prompt: str, max_tokens: int = 800) -> str:
    """Send prompt to Claude and return raw text."""
    response = anthropic.messages.create(
        model="claude-3-7-sonnet-20250219",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ---------------- MCP Tools ----------------
@mcp.tool()
async def extract_profile(cv_text: str) -> dict:
    print("somebody called me ")
    """Extract skills, experience, location, and job title from CV text.

    Args:
        cv_text: The raw text of the CV
    """
    prompt = f"""
    Extract the following from this CV:
    - skills (list of strings)
    - job experience (summary sentence)
    - location
    - generate a 1-line jobTitle (based on skills + experience)

    Respond ONLY in valid JSON with this structure:
    {{
        "skills": [...],
        "experience": "...",
        "location": "...",
        "jobTitle": "..."
    }}

    CV Content:
    {cv_text}
    """
    
    print("The prompt is : ", prompt)

    raw = await call_claude(prompt)
    try:
        profile = json.loads(raw.strip())
        print("sending profile ", profile)
        # Debug print in terminal
        print("\n===== Extracted Profile =====\n")
        print(json.dumps(profile, indent=2))
        print("\n=============================\n")
        return profile
    except Exception:
        print("\n❌ Claude did not return valid JSON")
        print("Raw output:", raw)
        return {"error": "Claude did not return valid JSON", "raw": raw}

# ---------------- Main ----------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
