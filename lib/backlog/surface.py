"""W4 — /grip-anywhere MCP-App live-Kanban surface (READ-ONLY adapter).

Plan unified-backlog-issue-system.md W4/L5 (FAST). grip-apps is the MCP
*server* (GRIP Emergent UI components) — there is no importable lib API
and no documented payload schema in-tree, so (per the /ultraread
no-fabricated-coverage lesson + KISS) W4 does NOT invent a grip-apps
wire format. It provides ONE clean composable READ-ONLY adapter that any
MCP-App / /grip-anywhere consumer renders: it returns the canonical
reduced board + the W3-rendered HTML + plain stats in a stable generic
dict.

Rule 21: composes the W1 reducer (`reduce`) and the W3 renderer
(`render_dashboard`) UNCHANGED — zero new model/render logic. READ-ONLY
by construction (returns a dict; writes nothing; the Pages/Projects
publish stays gated behind the operator-approval marker, HITL Q3, until
W5). Deterministic: identical substrate ⇒ identical payload.
"""
from __future__ import annotations

from pathlib import Path

from lib.backlog.dashboard import render_dashboard
from lib.backlog.model import STATUSES
from lib.backlog.reducer import reduce

SURFACE_VERSION = "backlog-surface/1"


def backlog_surface(root: Path, *, title: str = "GRIP Backlog",
                     codec: bool = False) -> dict:
    """Build the READ-ONLY MCP-App board payload from canonical substrate.

    Returns a stable generic dict an MCP-App / /grip-anywhere consumer
    renders directly. Pure: identical substrate ⇒ identical payload;
    zero filesystem/network writes. Empty substrate ⇒ a valid empty
    payload (degrade-to-silence — no fake items, never an error).
    """
    model = reduce(Path(root))  # W1 — pure, deterministic, sorted
    columns = {s: [] for s in STATUSES}
    for it in model:
        columns.setdefault(it.status, []).append({
            "id": it.id,
            "title": it.title,
            "priority": it.priority,
            "source": it.source,
            "repo": it.repo,
        })
    return {
        "version": SURFACE_VERSION,
        "title": title,
        "read_only": True,                       # binding-DA: no write legs
        "publish": "operator-gated (HITL Q3)",   # surfaced, NOT performed
        "count": len(model),
        "columns": columns,                      # deterministic per-status
        "stats": {s: len(columns.get(s, [])) for s in STATUSES},
        "html": render_dashboard(model, title=title, codec=codec),  # W3
    }
