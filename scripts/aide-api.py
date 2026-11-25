#!/usr/bin/env python3
"""
Copilot API Server - FastAPI backend for the GRIP Copilot Dashboard.

Serves copilot data (briefings, KPIs, decisions, rituals, preferences)
from local JSON/JSONL files. Frontend: React app at apps/grip-web on port 3000.

Usage:
    python3 scripts/aide-api.py [--port 8080] [--host 0.0.0.0]
    ./scripts/start-aide.sh  # Start both API + frontend
"""

import asyncio
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Paths
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
DATA_DIR = CLAUDE_DIR / "data" / "aide"
RITUALS_DIR = DATA_DIR / "rituals"
WORKFLOWS_DIR = DATA_DIR / "workflows"

@asynccontextmanager
async def lifespan(a: FastAPI):
    """Startup: ensure data directories exist."""
    for d in [DATA_DIR, RITUALS_DIR, WORKFLOWS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="GRIP Copilot API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# The dashboard's own pages: CORS lets them read responses, and the access
# guard accepts writes from no other browser origin.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:4173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:4173",
    "http://127.0.0.1:3000",
    "https://grip-copilot.vercel.app",
    "https://grip-copilot-*.vercel.app",
    # Tailscale network access (local devices)
    "http://100.64.0.10:5173",
    "http://100.64.0.10:4173",
    "http://100.64.0.10:3000",
    "http://your-mac:5173",
    "http://your-mac:4173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Only this machine and the tailnet may call the API, as in the launch agent's
# app: both install lib/aide_access_guard.py, which every install ships.
sys.path.append(str(Path(__file__).resolve().parent.parent / "lib"))
from aide_access_guard import install as install_access_guard  # noqa: E402

install_access_guard(app, ALLOWED_ORIGINS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_path_component(component: str) -> str:
    """Validate a path component (ritual_id, workflow_id) against traversal attacks.

    Allowlist approach: only alphanumeric, hyphen, and underscore are permitted.
    This rejects ``..``, ``/``, ``\\``, null bytes, and any other special
    characters that could be used for path injection.
    """
    if not component or not _SAFE_ID_RE.match(component):
        raise HTTPException(status_code=400, detail=f"Invalid path component: {component}")
    return component


def _safe_resolve(base_dir: Path, filename: str) -> Path:
    """Resolve *filename* under *base_dir* and verify it stays within bounds.

    Returns the resolved ``Path``.  Raises ``HTTPException(400)`` if the
    resolved path escapes *base_dir* (e.g. via ``..`` or symlink).
    """
    resolved = (base_dir / filename).resolve()  # codeql[py/path-injection] Containment check on next line prevents traversal
    base_resolved = base_dir.resolve()
    if not str(resolved).startswith(str(base_resolved) + os.sep) and resolved != base_resolved:
        raise HTTPException(status_code=400, detail="Path traversal detected")
    return resolved


def _read_json(path: Path) -> Any:
    """Read a JSON file, return empty dict on missing/invalid."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _read_jsonl(path: Path, limit: int = 0, offset: int = 0) -> List[Dict]:
    """Read a JSONL file, return list of dicts. Newest first."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if offset > 0:
        entries = entries[offset:]
    if limit > 0:
        entries = entries[:limit]
    return entries


def _read_yaml(path: Path) -> Any:
    """Read a YAML file, return empty dict on missing/invalid."""
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/copilot/health")
def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(DATA_DIR),
        "data_dir_exists": DATA_DIR.exists(),
    }


# ---------------------------------------------------------------------------
# Briefing
# ---------------------------------------------------------------------------

@app.get("/api/copilot/briefing")
def get_briefing(type: str = Query("morning", enum=["morning", "eod"])):
    """Get the latest morning or EOD briefing from ritual history."""
    ritual_id = "morning-brief" if type == "morning" else "eod-digest"
    history = _read_jsonl(RITUALS_DIR / "history.jsonl")

    for entry in history:
        if entry.get("ritual_id") == ritual_id:
            return {
                "status": "ok",
                "type": type,
                "briefing": entry,
            }

    return {
        "status": "ok",
        "type": type,
        "briefing": None,
        "message": f"No {type} briefing found in history",
    }


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

@app.get("/api/copilot/kpis")
def get_kpis():
    """Get KPIs from company.json."""
    company = _read_json(DATA_DIR / "company.json")
    kpis = company.get("kpis", [])
    goals = company.get("goals", {})

    on_track = sum(
        1
        for k in kpis
        if k.get("current_value", 0) >= k.get("target_value", 1) * 0.8
    )

    return {
        "status": "ok",
        "kpis": kpis,
        "goals": goals,
        "summary": {
            "total": len(kpis),
            "on_track": on_track,
            "at_risk": len(kpis) - on_track,
        },
    }


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

@app.get("/api/copilot/decisions")
def get_decisions(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get decisions from decision-log.jsonl (newest first)."""
    entries = _read_jsonl(DATA_DIR / "decision-log.jsonl", limit=limit, offset=offset)

    # Strip heavy context to keep response size manageable
    slim = []
    for e in entries:
        slim.append({
            "id": e.get("id"),
            "timestamp": e.get("timestamp"),
            "ritual_id": e.get("ritual_id", ""),
            "decision": e.get("decision", {}),
            "outcome": e.get("outcome"),
            "sequence": e.get("sequence"),
        })

    return {
        "status": "ok",
        "decisions": slim,
        "count": len(slim),
    }


@app.get("/api/copilot/decisions/{decision_id}")
def get_decision(decision_id: str):
    """Get a single decision by ID (full detail including context)."""
    _validate_path_component(decision_id)
    entries = _read_jsonl(DATA_DIR / "decision-log.jsonl")

    for entry in entries:
        if entry.get("id") == decision_id:
            return {"status": "ok", "decision": entry}

    raise HTTPException(status_code=404, detail=f"Decision {decision_id} not found")


# ---------------------------------------------------------------------------
# Rituals
# ---------------------------------------------------------------------------

@app.get("/api/copilot/rituals")
def get_rituals():
    """Get ritual states from active.json, enriched with workflow metadata."""
    active = _read_json(RITUALS_DIR / "active.json")
    rituals_data = active.get("rituals", {})

    enriched = {}
    for ritual_id, state in rituals_data.items():
        # ritual_id comes from data (active.json) -- validate before path use
        if not _SAFE_ID_RE.match(ritual_id):
            continue  # skip malformed entries silently
        workflow_path = _safe_resolve(WORKFLOWS_DIR, f"{ritual_id}.yaml")
        workflow = _read_yaml(workflow_path)

        enriched[ritual_id] = {
            **state,
            "name": workflow.get("name", ritual_id),
            "version": workflow.get("version", ""),
            "schedule": workflow.get("schedule", {}),
            "step_count": len(
                workflow.get("workflow", {}).get("steps", [])
            ),
        }

    enabled_count = sum(1 for r in rituals_data.values() if r.get("enabled"))

    return {
        "status": "ok",
        "rituals": enriched,
        "summary": {
            "total": len(rituals_data),
            "enabled": enabled_count,
            "disabled": len(rituals_data) - enabled_count,
        },
        "updated_at": active.get("updated_at"),
    }


@app.get("/api/copilot/rituals/history")
def get_ritual_history(
    ritual_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """Get ritual execution history."""
    entries = _read_jsonl(RITUALS_DIR / "history.jsonl", limit=limit)

    if ritual_id:
        entries = [e for e in entries if e.get("ritual_id") == ritual_id]

    return {
        "status": "ok",
        "history": entries,
        "count": len(entries),
    }


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

def _format_steps(steps: list) -> list:
    """Format workflow steps for API response."""
    return [
        {
            "name": s.get("name", f"step-{i}"),
            "type": s.get("type", "unknown"),
            "description": s.get("description", ""),
        }
        for i, s in enumerate(steps)
    ]


def _format_workflow(path) -> dict:
    """Read a workflow YAML file and return its API representation."""
    wf = _read_yaml(path)
    workflow_id = path.stem
    steps = wf.get("workflow", {}).get("steps", [])
    return {
        "id": workflow_id,
        "name": wf.get("name", workflow_id),
        "version": wf.get("version", ""),
        "description": wf.get("description", ""),
        "schedule": wf.get("schedule", {}),
        "steps": _format_steps(steps),
        "step_count": len(steps),
    }


@app.get("/api/copilot/workflows")
def get_workflows():
    """Get all workflow definitions from YAML files with metadata."""
    workflows = []
    if WORKFLOWS_DIR.exists():
        for path in sorted(WORKFLOWS_DIR.glob("*.yaml")):
            workflows.append(_format_workflow(path))

    return {
        "status": "ok",
        "workflows": workflows,
        "count": len(workflows),
    }


@app.get("/api/copilot/workflows/{workflow_id}")
def get_workflow(workflow_id: str):
    """Get a single workflow definition with full detail."""
    _validate_path_component(workflow_id)
    workflow_path = _safe_resolve(WORKFLOWS_DIR, f"{workflow_id}.yaml")
    if not workflow_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Workflow {workflow_id} not found"
        )

    wf = _read_yaml(workflow_path)
    return {
        "status": "ok",
        "workflow": {
            "id": workflow_id,
            **wf,
        },
    }


# ---------------------------------------------------------------------------
# Ritual Trigger
# ---------------------------------------------------------------------------

@app.post("/api/copilot/ritual/{ritual_id}/run")
async def trigger_ritual(ritual_id: str):
    """Trigger a ritual run via aide-cli.py."""
    _validate_path_component(ritual_id)
    cli_path = CLAUDE_DIR / "scripts" / "aide-cli.py"
    if not cli_path.exists():
        raise HTTPException(status_code=500, detail="aide-cli.py not found")

    workflow_path = _safe_resolve(WORKFLOWS_DIR, f"{ritual_id}.yaml")
    if not workflow_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Workflow {ritual_id} not found"
        )

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(cli_path), "run", ritual_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(CLAUDE_DIR),
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=30.0
        )
        success = process.returncode == 0
        return {
            "status": "ok" if success else "error",
            "ritual_id": ritual_id,
            "returncode": process.returncode,
            "stdout": stdout.decode()[-2000:] if stdout else "",
            "stderr": stderr.decode()[-500:] if not success and stderr else "",
        }
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return {
            "status": "error",
            "ritual_id": ritual_id,
            "error": "Ritual execution timed out (30s)",
        }


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

@app.get("/api/copilot/preferences")
def get_preferences():
    """Get learned preferences and company preferences."""
    learned = _read_json(DATA_DIR / "preferences.json")
    company = _read_json(DATA_DIR / "company.json")

    return {
        "status": "ok",
        "learned": learned.get("preferences", []),
        "corrections": learned.get("corrections", []),
        "company_preferences": company.get("preferences", {}),
        "company": company.get("company", {}),
        "team": company.get("team", []),
    }


@app.put("/api/copilot/preferences")
def update_preferences(body: Dict[str, Any]):
    """Update company preferences. Merges into existing company.json."""
    company_path = DATA_DIR / "company.json"
    company = _read_json(company_path)

    # Update company info if provided
    if "company" in body:
        company.setdefault("company", {}).update(body["company"])

    # Update preferences if provided
    if "preferences" in body:
        company.setdefault("preferences", {}).update(body["preferences"])

    company["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Atomic write
    import tempfile
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=str(DATA_DIR), suffix=".tmp", delete=False
    )
    try:
        json.dump(company, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, str(company_path))
    except Exception:
        os.unlink(tmp.name)
        raise

    return {"status": "ok", "message": "Preferences updated"}


@app.put("/api/copilot/rituals/{ritual_id}")
def update_ritual(ritual_id: str, body: Dict[str, Any]):
    """Toggle a ritual enabled/disabled."""
    _validate_path_component(ritual_id)
    active_path = RITUALS_DIR / "active.json"
    active = _read_json(active_path)
    rituals = active.get("rituals", {})

    if ritual_id not in rituals:
        raise HTTPException(status_code=404, detail=f"Ritual {ritual_id} not found")

    if "enabled" in body:
        rituals[ritual_id]["enabled"] = bool(body["enabled"])

    active["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Atomic write
    import tempfile
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=str(RITUALS_DIR), suffix=".tmp", delete=False
    )
    try:
        json.dump(active, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, str(active_path))
    except Exception:
        os.unlink(tmp.name)
        raise

    return {"status": "ok", "ritual_id": ritual_id, "enabled": rituals[ritual_id]["enabled"]}


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

@app.get("/api/copilot/company")
def get_company():
    """Get company info (team, goals, integrations)."""
    company = _read_json(DATA_DIR / "company.json")
    return {"status": "ok", **company}


# ---------------------------------------------------------------------------
# KONO Stats (bonus - useful for dashboard)
# ---------------------------------------------------------------------------

@app.get("/api/copilot/kono/stats")
async def get_kono_stats():
    """Get KONO memory substrate stats."""
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(CLAUDE_DIR / "lib" / "kono_substrate.py"), "stats",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(CLAUDE_DIR / "lib"),
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=10.0
        )
        if process.returncode == 0:
            return json.loads(stdout.decode())
        return {"status": "error", "error": stderr.decode()[-500:]}
    except (asyncio.TimeoutError, json.JSONDecodeError, FileNotFoundError) as e:
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Aide API Server")
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    uvicorn.run(
        "aide-api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
