"""Local bridge between the voice page and the Visa Research Agent.

Serves the page, and runs research jobs against the agent's streaming route so the voice agent can
start one, hear progress while it runs, and get back a brief built only from the finished plan.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent
POLL_WAIT_SECONDS = 15

# The visa agent's own on-screen wording for each step, said aloud instead.
STAGE_LABELS = {
    "search": "Searching the destination's official government sites",
    "corpus": "Gathering the official pages already known",
    "crawl": "Gathering the official pages already known",
    "select": "Choosing which pages to read",
    "fetch": "Reading the chosen pages",
    "adjudicate": "Checking which page answers each question",
    "retrieve": "Collecting the pages the plan will cite",
    "write": "Writing the plan from those pages, usually the longest step",
}


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


ENV = load_env()
VISA_AGENT_URL = ENV.get("VISA_AGENT_URL", "http://127.0.0.1:8098").rstrip("/")


class ResearchRequest(BaseModel):
    passport_country: str
    residence_country: str
    destination: str
    purpose: str = "tourism"


@dataclass
class Job:
    request: ResearchRequest
    stages: list[str] = field(default_factory=list)
    status: str = "running"  # running | done | refused | error
    plan: dict[str, Any] | None = None
    problem: str | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def update(self) -> None:
        self.changed.set()
        self.changed = asyncio.Event()


JOBS: dict[str, Job] = {}
app = FastAPI(title="Visa Voice Agent")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/config")
async def config() -> dict[str, str]:
    agent_id = ENV.get("ELEVENLABS_AGENT_ID", "")
    if not agent_id:
        raise HTTPException(500, "ELEVENLABS_AGENT_ID is not set; run setup_agent.py first.")
    return {"agent_id": agent_id}


@app.post("/api/research")
async def start_research(request: ResearchRequest) -> dict[str, str]:
    job_id = uuid.uuid4().hex[:12]
    job = Job(request)
    JOBS[job_id] = job
    asyncio.create_task(run_job(job))
    return {"job_id": job_id}


@app.get("/api/research/{job_id}")
async def research_status(job_id: str, seen: int = 0) -> dict[str, Any]:
    """Long-poll: answer as soon as there is a stage past `seen` or an outcome."""

    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "No such research job.")
    if job.status == "running" and len(job.stages) <= seen:
        try:
            await asyncio.wait_for(job.changed.wait(), POLL_WAIT_SECONDS)
        except TimeoutError:
            pass
    answer: dict[str, Any] = {
        "status": job.status,
        "stages_seen": len(job.stages),
        "new_stages": [STAGE_LABELS.get(s, s) for s in job.stages[seen:]],
    }
    if job.status == "done" and job.plan is not None:
        answer["brief"] = voice_brief(job.plan)
        answer["plan"] = job.plan
    if job.problem:
        answer["problem"] = job.problem
    return answer


async def run_job(job: Job) -> None:
    body = {
        "destination": job.request.destination,
        "traveller": {
            "passport_nationality": job.request.passport_country,
            "country_of_residence": job.request.residence_country,
            "travel_purpose": job.request.purpose,
        },
    }
    try:
        timeout = httpx.Timeout(10, read=600)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{VISA_AGENT_URL}/visa-plans/stream", json=body
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    job.status, job.problem = "refused", describe_failure(response)
                    return
                async for line in response.aiter_lines():
                    if line.strip():
                        record(job, json.loads(line))
        if job.status == "running":
            job.status, job.problem = "error", "The research ended without a plan."
    except httpx.ConnectError:
        job.status = "error"
        job.problem = f"The Visa Research Agent is not running at {VISA_AGENT_URL}."
    except Exception as exc:  # said aloud, so keep it plain
        job.status, job.problem = "error", f"The research failed: {type(exc).__name__}."
    finally:
        job.update()


def record(job: Job, event: dict[str, Any]) -> None:
    kind = event.get("event")
    if kind == "stage":
        stage = event.get("stage", "")
        # corpus and crawl read as one step; don't announce it twice.
        if STAGE_LABELS.get(stage) != (STAGE_LABELS.get(job.stages[-1]) if job.stages else None):
            job.stages.append(stage)
            job.update()
    elif kind == "plan":
        job.status, job.plan = "done", event["plan"]
    elif kind == "refusal":
        job.status, job.problem = "refused", detail_message(event.get("detail"))
    elif kind == "error":
        job.status, job.problem = "error", event.get("message", "The plan could not be generated.")


def describe_failure(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    message = detail_message(detail)
    if response.status_code == 401:
        return "The Visa Research Agent requires sign-in. Run it with sign-in turned off."
    if response.status_code == 429:
        return "The Visa Research Agent's free plan allowance for this address is used up."
    return message


def detail_message(detail: Any) -> str:
    if isinstance(detail, dict):
        parts = [str(detail.get("message", "The request was refused."))]
        parts += [str(r) for r in detail.get("reasons", []) or []]
        return " ".join(parts)
    if isinstance(detail, list):  # a validation error, e.g. an unknown country
        return "; ".join(str(item.get("msg", item)) for item in detail)
    return str(detail or "The request was refused.")


def voice_brief(plan: dict[str, Any]) -> dict[str, Any]:
    """Everything the voice agent may say, and nothing else.

    Each fact keeps the authority that published it, so the agent can say who said so.
    """

    sources = {s["source_id"]: s for s in plan.get("sources", [])}

    def said_by(ids: list[str]) -> list[str]:
        return sorted({sources[i]["authority"] for i in ids if i in sources})

    decision = {True: "visa required", False: "no visa required", None: "unknown"}[
        plan.get("visa_required")
    ]
    brief: dict[str, Any] = {
        "destination": plan.get("destination"),
        "decision": decision,
        "decision_condition": plan.get("decision_condition"),
        "decided_for": plan.get("decided_for"),
        "visa_type": plan.get("visa_type"),
        "explanation": plan.get("explanation"),
        "decision_said_by": said_by(plan.get("decision_source_ids", [])),
    }
    if where := plan.get("where_to_apply"):
        brief["where_to_apply"] = {
            "authority": where.get("authority"),
            "method": where.get("application_method"),
            "location": where.get("location"),
            "in_person": where.get("in_person"),
        }
    if auth := plan.get("travel_authorisation"):
        brief["travel_authorisation"] = {"name": auth.get("name"), "condition": auth.get("condition")}
    brief["steps"] = [
        {"title": s["title"], "action": s["action"], "timing": s["timing"]}
        for s in plan.get("application_steps", [])
    ]
    brief["requirements"] = [
        {"name": r["name"], "description": r["description"], "why": r["reason_it_applies"]}
        for r in plan.get("requirements", [])
    ]
    brief["exceptions"] = [
        {"if": e["condition"], "then": e["consequence"]} for e in plan.get("exceptions", [])
    ]
    brief["disagreements"] = [
        {"topic": d["topic"], "summary": d["summary"]} for d in plan.get("disagreements", [])
    ]
    brief["could_not_verify"] = [
        {"missing": g["missing"], "next_step": g["next_step"]} for g in plan.get("gaps", [])
    ]
    brief["sources"] = [
        {"authority": s["authority"], "title": s["title"], "stale": s.get("is_stale", False)}
        for s in sources.values()
    ]
    return brief


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
