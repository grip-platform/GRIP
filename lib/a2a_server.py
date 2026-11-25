"""A2A HTTP REST Server — exposes GRIP converge() kernel via HTTP.

Binds the A2AAdapter to FastAPI endpoints, enabling external AI agents
(Gemini, Codex, Copilot) to submit convergence tasks over HTTP.

Endpoints:
    POST   /a2a/tasks              Create a new task
    GET    /a2a/tasks/{task_id}     Get task status
    POST   /a2a/tasks/{task_id}/execute   Execute with a named template
    POST   /a2a/tasks/{task_id}/cancel    Cancel a running task
    GET    /a2a/tasks/{task_id}/events    Stream task events (SSE)
    GET    /.well-known/agent.json        GRIP agent card

Usage:
    python3 lib/a2a_server.py [--port 8420] [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# Ensure lib/ is importable
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.a2a_adapter import A2AAdapter, TaskState
from lib.a2a_provider_detect import detect_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Task Templates — named convergence patterns (safe callable registry)
# ---------------------------------------------------------------------------

def _template_echo(state: Any) -> Any:
    """Identity — returns state unchanged. For connectivity testing."""
    return state


def _template_increment(state: Any) -> Any:
    """Increment a numeric state. For integration testing."""
    if isinstance(state, (int, float)):
        return state + 1
    if isinstance(state, dict) and "value" in state:
        state["value"] = state["value"] + 1
        return state
    return state


def _criterion_threshold(state: Any) -> bool:
    """Check if state meets a threshold criterion."""
    if isinstance(state, (int, float)):
        return state >= 10
    if isinstance(state, dict):
        return state.get("value", 0) >= state.get("threshold", 10)
    return False


def _criterion_always_false(_: Any) -> bool:
    """Never satisfied — runs to max_depth. For testing depth limits."""
    return False


def _criterion_always_true(_: Any) -> bool:
    """Always satisfied — completes immediately."""
    return True


# Registry: template_name -> (improve_fn, criterion_fn)
TASK_TEMPLATES: dict[str, tuple[Callable, Callable]] = {
    "echo": (_template_echo, _criterion_always_true),
    "increment": (_template_increment, _criterion_threshold),
    "test_depth": (_template_echo, _criterion_always_false),
}


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class CreateTaskRequest(BaseModel):
    """Request body for POST /a2a/tasks."""
    input_data: Any = None
    metadata: dict = Field(default_factory=dict)


class ExecuteTaskRequest(BaseModel):
    """Request body for POST /a2a/tasks/{task_id}/execute."""
    template: str = "echo"
    max_depth: int = Field(default=5, ge=1, le=50)
    label: Optional[str] = None


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

def create_app(adapter: Optional[A2AAdapter] = None) -> FastAPI:
    """Create the FastAPI application with A2A endpoints."""
    _adapter = adapter or A2AAdapter()

    app = FastAPI(
        title="GRIP A2A Server",
        version="0.1.0",
        description="Agent-to-Agent HTTP endpoint for GRIP converge() kernel",
        docs_url="/a2a/docs",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # Store adapter on app state for testability
    app.state.adapter = _adapter

    # --- Agent Card ---
    @app.get("/.well-known/agent.json")
    async def agent_card():
        card_path = Path(_ROOT) / ".well-known" / "agent.json"
        if card_path.exists():
            return JSONResponse(json.loads(card_path.read_text()))
        return JSONResponse({
            "schemaVersion": "1.0",
            "name": "GRIP",
            "description": "Guarded Response Interception Protocol",
            "capabilities": {"a2aVersion": "0.2.1", "streaming": True},
        })

    # --- List Templates ---
    @app.get("/a2a/templates")
    async def list_templates():
        return {
            "templates": {
                name: {
                    "improve": fns[0].__doc__ or "",
                    "criterion": fns[1].__doc__ or "",
                }
                for name, fns in TASK_TEMPLATES.items()
            }
        }

    # --- Create Task ---
    @app.post("/a2a/tasks", status_code=201)
    async def create_task(body: CreateTaskRequest, request: Request):
        provider = detect_provider(
            user_agent=request.headers.get("user-agent", ""),
            metadata=body.metadata,
        )
        meta = {**body.metadata, "detected_provider": provider.name}
        if provider.model:
            meta["detected_model"] = provider.model

        task = _adapter.create_task(input_data=body.input_data, metadata=meta)
        return _adapter.to_a2a_response(task)

    # --- Get Task ---
    @app.get("/a2a/tasks/{task_id}")
    async def get_task(task_id: str):
        task = _adapter.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        return _adapter.to_a2a_response(task)

    # --- Execute Task ---
    @app.post("/a2a/tasks/{task_id}/execute")
    async def execute_task(task_id: str, body: ExecuteTaskRequest):
        task = _adapter.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        if body.template not in TASK_TEMPLATES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown template '{body.template}'. Available: {list(TASK_TEMPLATES.keys())}",
            )

        improve_fn, criterion_fn = TASK_TEMPLATES[body.template]

        try:
            result = _adapter.execute_task(
                task_id=task_id,
                criterion=criterion_fn,
                improve=improve_fn,
                max_depth=body.max_depth,
                label=body.label,
            )
            return _adapter.to_a2a_response(result)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # --- Cancel Task ---
    @app.post("/a2a/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        try:
            task = _adapter.cancel_task(task_id)
            return _adapter.to_a2a_response(task)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # --- Stream Events (SSE) ---
    @app.get("/a2a/tasks/{task_id}/events")
    async def stream_events(task_id: str):
        task = _adapter.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        def event_generator():
            for event in _adapter.stream_events(task_id):
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="GRIP A2A HTTP Server")
    parser.add_argument("--port", type=int, default=8420, help="Port (default: 8420)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host (default: 127.0.0.1)")
    args = parser.parse_args()

    import uvicorn
    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
