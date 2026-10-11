"""Create or update the ElevenLabs agent and its client tools from the files in agent/.

Run again after editing the prompt or settings; it updates the same agent in place.
"""

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
API = "https://api.elevenlabs.io"
AGENT_NAME = "Visa Voice Agent"
LLM = "claude-haiku-4-5"  # fast enough for turn-taking; the research itself happens elsewhere
FIRST_MESSAGE = (
    "Hi! I can look up visa rules from a country's own government pages. "
    "Where are you headed, and what passport will you travel on?"
)

TOOLS = [
    {
        "type": "client",
        "name": "start_visa_research",
        "description": (
            "Start researching visa rules for a confirmed trip. Call only once all four "
            "details are confirmed. Returns at once; then call check_visa_research."
        ),
        "expects_response": True,
        "response_timeout_secs": 20,
        "parameters": {
            "type": "object",
            "properties": {
                "passport_country": {
                    "type": "string",
                    "description": "Country of the traveller's passport, in English, e.g. India.",
                },
                "residence_country": {
                    "type": "string",
                    "description": "Country the traveller lives in and applies from, e.g. United Kingdom.",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination country in English, e.g. Japan.",
                },
                "purpose": {
                    "type": "string",
                    "description": "Purpose of the trip.",
                    "enum": ["tourism", "business", "study", "transit"],
                },
            },
            "required": ["passport_country", "residence_country", "destination", "purpose"],
        },
    },
    {
        "type": "client",
        "name": "check_visa_research",
        "description": (
            "Check on the research in progress. Waits up to about 15 seconds for news, then "
            "returns its status, any new steps, and, when done, the brief to answer from."
        ),
        "expects_response": True,
        "response_timeout_secs": 30,
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


def read_env() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def write_env_value(key: str, value: str) -> None:
    path = ROOT / ".env"
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")


def check(response: httpx.Response) -> dict:
    if response.is_error:
        sys.exit(f"{response.request.method} {response.request.url.path} -> "
                 f"{response.status_code}: {response.text[:800]}")
    return response.json() if response.content else {}


def sync_tools(client: httpx.Client) -> list[str]:
    """Create each tool, or update the one already there with its name."""

    existing = {
        t["tool_config"]["name"]: t["id"]
        for t in check(client.get("/v1/convai/tools")).get("tools", [])
    }
    ids = []
    for tool in TOOLS:
        body = {"tool_config": tool}
        if tool["name"] in existing:
            tool_id = existing[tool["name"]]
            check(client.patch(f"/v1/convai/tools/{tool_id}", json=body))
        else:
            tool_id = check(client.post("/v1/convai/tools", json=body))["id"]
        ids.append(tool_id)
    return ids


def main() -> None:
    env = read_env()
    if not env.get("ELEVENLABS_API_KEY"):
        sys.exit("Set ELEVENLABS_API_KEY in .env first.")
    client = httpx.Client(
        base_url=API, headers={"xi-api-key": env["ELEVENLABS_API_KEY"]}, timeout=30
    )
    tool_ids = sync_tools(client)
    body = {
        "name": AGENT_NAME,
        "conversation_config": {
            "agent": {
                "first_message": FIRST_MESSAGE,
                "language": "en",
                "prompt": {
                    "prompt": (ROOT / "agent" / "prompt.md").read_text(),
                    "llm": LLM,
                    "temperature": 0.2,
                    "tool_ids": tool_ids,
                },
            },
        },
        "platform_settings": {
            # Public for local development: the page connects with the agent id alone.
            "auth": {"enable_auth": False},
        },
    }
    if env.get("VOICE_ID"):
        body["conversation_config"]["tts"] = {"voice_id": env["VOICE_ID"]}

    agent_id = env.get("ELEVENLABS_AGENT_ID")
    if agent_id:
        check(client.patch(f"/v1/convai/agents/{agent_id}", json=body))
        print(f"Updated agent {agent_id}")
    else:
        agent_id = check(client.post("/v1/convai/agents/create", json=body))["agent_id"]
        write_env_value("ELEVENLABS_AGENT_ID", agent_id)
        print(f"Created agent {agent_id} and saved its id to .env")
    print(json.dumps({"tools": tool_ids}, indent=2))


if __name__ == "__main__":
    main()
