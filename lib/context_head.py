"""Context memory-chain HEAD pointer — the thin chain-position shim (P3).

``state/precog/context-HEAD.txt`` holds the id of the latest context node.
Reads/writes are atomic (write-temp + ``os.replace``) so a concurrent reader
never observes a half-written pointer. ``read_latest()`` is the single shim
every reader routes through (``kono_preload`` / ``holistic_synthesiser`` /
``/recall`` / ``/save`` — design §7).

This module is the READ side of the chain: it performs NO signing/crypto and
imports NO HAL/LLM module. The P3 import-isolation lint
(``tests/test_context_chain.py::test_verify_path_imports_no_hal_or_llm``)
makes that a compile-time fact, not a convention.

Design: GRIP ``drafts/happi-context-event-memory-chain-design.md`` §4, §7.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Mirrors lib/precog/idr.py::_DEFAULT_IDR_PATH resolution (CLAUDE_DIR override).
_DEFAULT_HEAD_PATH = (
    Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
    / "state" / "precog" / "context-HEAD.txt"
)


def _session_path(base: Path) -> Path:
    """B2 per-session namespacing — insert the current session's 4-char hex
    prefix into ``base``'s filename stem so two concurrent ``~/.claude`` sessions
    keep INDEPENDENT, non-interleaving chains/HEADs (``context-HEAD.txt`` →
    ``context-HEAD-a7b2.txt``). Derives the prefix from the live ``task_namespace``
    session resolver (DRY — same sanitisation that namespaces tasks), so a raw
    ``CLAUDE_SESSION_ID`` containing path separators can never reach a filename.

    BACK-COMPAT (the safety contract): when no session id is set —
    ``CLAUDE_SESSION_ID`` empty AND no breadcrumb — ``task_namespace`` would fall
    back to a per-PID prefix; we suppress that here and return ``base`` UNCHANGED,
    so today's single-session behaviour (and the monkeypatched-base e2e test) is
    byte-identical. The prefix is applied ONLY when an actual session id exists."""
    from task_namespace import _resolve_session_id, session_prefix  # noqa: E402

    sid = os.environ.get("CLAUDE_SESSION_ID", "").strip()
    if not sid:
        # Only an explicit session id namespaces; a PID/breadcrumb fallback would
        # break legacy single-session byte-identity, so resolve-but-only-if-real.
        resolved = _resolve_session_id()
        if resolved.startswith("pid-"):
            return base  # no real session id → legacy path, unchanged
    return base.with_name(f"{base.stem}-{session_prefix()}{base.suffix}")


def head_path(path: Path | None = None) -> Path:
    return path or _session_path(_DEFAULT_HEAD_PATH)


def read_head(path: Path | None = None) -> str | None:
    """Return the latest context node id, or ``None`` if the chain has no head."""
    p = head_path(path)
    if not p.exists():
        return None
    head = p.read_text(encoding="utf-8").strip()
    return head or None


def write_head(node_id: str, path: Path | None = None) -> None:
    """Atomically point HEAD at ``node_id``: write a temp sibling then
    ``os.replace`` (atomic rename on POSIX) so readers see all-or-nothing."""
    p = head_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(node_id.strip() + "\n", encoding="utf-8")
    os.replace(tmp, p)


def read_latest(chain_path: Path | None = None, head_pointer: Path | None = None) -> Any:
    """Canonical reader shim — resolve HEAD and return the latest context node
    (``PrecogIDR``) or ``None``. Local import of ``context_chain`` keeps this
    module load import-clean (no chain/forest deps at import time)."""
    from lib.context_chain import read_context_chain  # noqa: E402 (deferred; avoids cycle)

    head = read_head(head_pointer)
    if head is None:
        return None
    chain = read_context_chain(head=head, path=chain_path)
    return chain[-1] if chain else None
