"""W3 — Pages delight++ dashboard: pure, deterministic, READ-ONLY renderer.

Plan unified-backlog-issue-system.md W3/L5 (FAST). `render_dashboard` is a
pure function of the reduced model: identical model ⇒ byte-identical HTML.
It WRITES NOTHING (no files, no network, no Projects/Pages API) — that is
the binding-DA READ-ONLY floor; the actual Pages/Projects *publish* is a
write-leg gated behind the operator-approval marker (HITL Q3), deferred to
W5. This module only renders a string.

Aesthetic: Swiss-minimal dark (reuses the existing backlog-dashboard.html
visual language — palette tokens, kanban grid, mobile breakpoint — Rule 21
without coupling to that generated blob). Italian-brainrot /140-85 codec is
an OPT-IN easter-egg LAYER only; absent ⇒ renders nothing (degrade-to-
silence — the plan's taste rule: every delight element renders nothing when
its data is absent, never noise).
"""
from __future__ import annotations

from html import escape

from lib.backlog.model import STATUSES, BacklogItem

# Status → column order + accent (reuses backlog-dashboard.html palette).
_STATUS_ACCENT = {
    "open": "#3b82f6", "in_progress": "#eab308", "blocked": "#ef4444",
    "done": "#22c55e", "unknown": "#6b7280",
}
_CSS = """
:root{--bg:#0a0a0a;--card:#141414;--edge:#202020;--ink:#e5e5e5;--mut:#6b7280;--acc:#f97316}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font-family:ui-monospace,SFMono-Regular,Menlo,monospace;padding:24px}
h1{font-size:15px;font-weight:600;letter-spacing:.04em;margin:0 0 4px}
.sub{color:var(--mut);font-size:12px;margin:0 0 20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:20px}
.stat{background:var(--card);border:1px solid var(--edge);padding:10px 12px}
.stat b{display:block;font-size:18px}.stat span{color:var(--mut);font-size:11px}
.kanban{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}
.col{background:var(--card);border:1px solid var(--edge);padding:10px;min-height:60px}
.col h2{font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin:0 0 8px;padding-bottom:6px;border-bottom:2px solid}
.card{background:#1a1a1a;border:1px solid var(--edge);border-left:2px solid var(--acc);padding:8px;margin-bottom:8px}
.card .t{font-size:12px;line-height:1.35}.card .m{color:var(--mut);font-size:10px;margin-top:4px}
.empty{color:var(--mut);font-size:12px;padding:24px;text-align:center}
@media(max-width:768px){.kanban{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}}
""".strip()


def _card(item: BacklogItem) -> str:
    return (
        '<div class="card">'
        f'<div class="t">{escape(item.title)}</div>'
        f'<div class="m">{escape(item.priority)} · {escape(item.source)}'
        f'{" · " + escape(item.repo) if item.repo else ""}</div>'
        "</div>"
    )


def render_dashboard(
    model: list[BacklogItem],
    *,
    title: str = "GRIP Backlog",
    codec: bool = False,
) -> str:
    """Pure deterministic HTML. Identical model ⇒ byte-identical output.

    Empty model ⇒ a clean empty state (degrade-to-silence, never fake
    cards). `codec=True` adds the hidden /140-85 easter-egg marker;
    default False ⇒ it renders nothing at all.
    """
    by_status: dict[str, list[BacklogItem]] = {s: [] for s in STATUSES}
    for it in model:  # model is pre-sorted by reducer (deterministic order)
        by_status.setdefault(it.status, []).append(it)

    stats = "".join(
        f'<div class="stat"><b>{len(by_status.get(s, []))}</b>'
        f'<span>{escape(s)}</span></div>'
        for s in STATUSES
    )
    if not model:
        board = '<div class="empty">No backlog items — nothing to surface.</div>'
    else:
        cols = []
        for s in STATUSES:
            acc = _STATUS_ACCENT.get(s, "#6b7280")
            cards = "".join(_card(i) for i in by_status.get(s, [])) or (
                '<div class="empty">—</div>'
            )
            cols.append(
                f'<div class="col"><h2 style="border-color:{acc};color:{acc}">'
                f'{escape(s)}</h2>{cards}</div>'
            )
        board = '<div class="kanban">' + "".join(cols) + "</div>"

    # /140-85 codec easter-egg: opt-in, degrade-to-silence (nothing if False).
    codec_layer = (
        "<!-- codec:140.85 — Mei Ling on freq; deeds not words -->"
        if codec else ""
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body>"
        f"<h1>{escape(title)}</h1>"
        f'<p class="sub">{len(model)} item(s) · read-only view · '
        "publish gated (operator-approval, HITL Q3)</p>"
        f'<div class="stats">{stats}</div>{board}{codec_layer}'
        "</body></html>"
    )
