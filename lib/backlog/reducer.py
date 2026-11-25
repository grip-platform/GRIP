"""W1 — pure, idempotent substrate → list[BacklogItem] reducer.

Contract (H-BL-2): `reduce(root)` is a pure function of on-disk substrate.
Identical substrate ⇒ byte-identical list (the output is `sorted` by
BacklogItem.sort_key, a total deterministic order — no filesystem
iteration order or dict/set ordering leaks into the result).

READ-ONLY: this module only reads. No write legs (deferred behind the W0
anti-blast TRIAD per the plan's binding-DA invariant).

Rule 21 (compose, don't rebuild) + KISS/YAGNI honesty: only substrate
sources with a clean, importable contract are wired:
  - plans/*.md + drafts/*.md frontmatter   (pure stdlib)
  - pending hypotheses                      (hypothesis_engine._load_hypotheses)
The plan also names the /whoa-update-pls aggregator and the TaskList store.
Neither exposes a clean importable lib API today, so they are HONEST
documented extension points below — NOT fabricated stubs (the
/ultraread fake-council lesson: never invent coverage that isn't real).
"""
from __future__ import annotations

import re
from pathlib import Path

from lib.backlog.model import BacklogItem

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_STATUS_RE = re.compile(r"^\s*(?:status|Status)\s*[:=]\s*(.+?)\s*$", re.MULTILINE)
_PRIO_RE = re.compile(r"\b(P[0-3])\b")
_STATUS_MAP = {
    "ready": "open", "open": "open", "todo": "open",
    "in progress": "in_progress", "in_progress": "in_progress", "wip": "in_progress",
    "blocked": "blocked",
    "done": "done", "complete": "done", "completed": "done", "merged": "done",
}


def _classify_status(raw: str) -> str:
    low = raw.strip().lower()
    for key, val in _STATUS_MAP.items():
        if low.startswith(key):
            return val
    return "unknown"


def _doc_items(root: Path, subdir: str, source: str) -> list[BacklogItem]:
    """Reduce plans/*.md or drafts/*.md into BacklogItems (pure, sorted input)."""
    d = root / subdir
    if not d.is_dir():
        return []  # degrade-to-silence: absent dir ⇒ no items, not an error
    items: list[BacklogItem] = []
    # sorted(glob) → deterministic file order independent of filesystem
    for md in sorted(d.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # unreadable file ⇒ skip silently, never crash the reduce
        tm = _TITLE_RE.search(text)
        title = tm.group(1).strip() if tm else md.stem
        sm = _STATUS_RE.search(text)
        status = _classify_status(sm.group(1)) if sm else "unknown"
        pm = _PRIO_RE.search(sm.group(1) if sm else text[:400])
        priority = pm.group(1) if pm else "unset"
        body = " ".join(text.split())[:280]
        items.append(BacklogItem(
            id=f"{source}:{md.stem}",
            source=source,
            title=title,
            body=body,
            repo="",                       # plans/drafts are repo-agnostic docs
            priority=priority,
            status=status,
            provenance={"path": str(md.relative_to(root))},
        ))
    return items


def _hypothesis_items(root: Path) -> list[BacklogItem]:
    """Reduce non-resolved hypotheses. Degrade-to-silence if engine absent."""
    try:
        from lib.hypothesis_engine import _load_hypotheses  # type: ignore
    except Exception:
        return []  # no clean engine ⇒ contribute nothing (honest, not faked)
    try:
        raw = _load_hypotheses()
    except Exception:
        return []
    items: list[BacklogItem] = []
    for h in raw:
        if not isinstance(h, dict):
            continue
        state = str(h.get("status", "")).lower()
        if state in ("confirmed", "refuted", "resolved"):
            continue  # only OPEN hypotheses are backlog
        hid = str(h.get("id") or h.get("hypothesis_id") or "")
        if not hid:
            continue
        items.append(BacklogItem(
            id=f"hypothesis:{hid}",
            source="hypothesis",
            title=str(h.get("claim", ""))[:120] or hid,
            body=str(h.get("prediction", ""))[:280],
            repo=str(h.get("repo", "")),
            priority="unset",
            status="open",
            provenance={"hypothesis_id": hid, "pr": str(h.get("pr", ""))},
        ))
    return items


# Honest documented extension points (NOT wired — no clean lib API today).
# A future wave adds these only when a real importable contract exists; a
# fabricated stub here would violate the no-faked-coverage rule.
def _whoa_items(root: Path) -> list[BacklogItem]:      # noqa: ARG001
    return []  # TODO(W-future): wire when /whoa-update-pls exposes a lib API


def _tasklist_items(root: Path) -> list[BacklogItem]:  # noqa: ARG001
    return []  # TODO(W-future): wire when the TaskList store exposes a lib API


def reduce(root: Path) -> list[BacklogItem]:
    """Pure substrate → normalised, deterministically-ordered backlog.

    Idempotent: reduce(x) == reduce(x) byte-for-byte (sorted by the total
    BacklogItem.sort_key). Backwards-safe: empty/absent substrate ⇒ [].
    """
    root = Path(root)
    items: list[BacklogItem] = []
    items += _doc_items(root, "plans", "plan")
    items += _doc_items(root, "drafts", "draft")
    items += _hypothesis_items(root)
    items += _whoa_items(root)
    items += _tasklist_items(root)
    return sorted(items, key=lambda i: i.sort_key)
