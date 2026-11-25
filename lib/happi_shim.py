"""
HAPPI/1.1 envelope shim — composes SSE/NDJSON streams per happi.md spec.

Each public function yields one complete SSE frame (event + data lines).
The shim is pure stdlib; callers iterate and write to their HTTP response.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any


def _frame(event: str, payload: dict[str, Any]) -> str:
    """One SSE frame: event line + data line + blank line."""
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def emit_started(endpoint: str, meta: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"endpoint": endpoint, "ts": int(time.time())}
    if meta:
        payload.update(meta)
    return _frame("started", payload)


def emit_delta(payload: dict[str, Any]) -> str:
    return _frame("delta", payload)


def emit_completed(summary: dict[str, Any]) -> str:
    return _frame("completed", summary)


def emit_error(msg: str, code: str = "ERR") -> str:
    return _frame("error", {"code": code, "message": msg, "ts": int(time.time())})


# ---------------------------------------------------------------------------
# IDR chain helpers (lightweight — no idr.py dep on VPS)
# ---------------------------------------------------------------------------

def idr_id(predecessor_idr: str | None, payload: dict[str, Any]) -> str:
    """Deterministic IDR id: SHA-256 of predecessor + canonical payload."""
    raw = json.dumps({"predecessor": predecessor_idr, "payload": payload}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def chain_completed(summary: dict[str, Any], chain_head: str | None) -> str:
    """emit_completed with idr_chain_head appended."""
    return emit_completed({**summary, "idr_chain_head": chain_head or ""})


def _next_generation(title: str, depth_limit: int) -> tuple[int, str]:
    match = re.search(r"generation\s+(\d+)", title, re.IGNORECASE)
    if not match:
        raise ValueError("parent title lacks 'generation N' counter")
    current = int(match.group(1))
    if current >= depth_limit:
        raise ValueError(f"generation {current} >= depth_limit {depth_limit}")
    next_gen = current + 1
    new_title = re.sub(
        r"(generation\s+)(\d+)",
        lambda found: found.group(1) + str(next_gen),
        title,
        count=1,
        flags=re.IGNORECASE,
    )
    return next_gen, new_title


def quine_spawn_dry_run(
    *,
    request_id: str,
    repo: str,
    parent_issue: int,
    parent_title: str,
    parent_body: str,
    depth_limit: int = 16,
    predecessor_idr: str | None = None,
) -> list[str]:
    """Compose HAPPI-style SSE frames for a safe quine.spawn dry run.

    The caller supplies the parent issue data, so this helper never shells out
    to GitHub and never creates a live issue.
    """
    if "quine.spawn" not in parent_body:
        raise ValueError("parent issue is not a HAPPI seed (no quine.spawn envelope)")
    next_gen, child_title = _next_generation(parent_title, depth_limit)
    payload = {
        "cmd": "quine.spawn",
        "repo": repo,
        "parent_issue": parent_issue,
        "child_title": child_title,
        "next_generation": next_gen,
        "depth_limit": depth_limit,
        "dry_run": True,
    }
    chain_head = idr_id(predecessor_idr, payload)
    return [
        emit_started("quine.spawn", {"id": request_id, "repo": repo, "parent_issue": parent_issue}),
        emit_delta({"id": request_id, "text": f"DRY-RUN: would spawn from #{parent_issue}"}),
        emit_delta({"id": request_id, "text": f"proposed_title: {child_title}"}),
        emit_delta({"id": request_id, "text": f"generation: {next_gen}"}),
        chain_completed(payload, chain_head),
    ]
