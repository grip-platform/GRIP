"""Shared absorber primitives — record shape + writer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import jsonl_io, paths
from ..relevance import RelevanceVerdict, is_work_relevant


@dataclass
class AbsorbRecord:
    """One absorbed unit. Source-agnostic shape (gog/slack/whatsapp)."""
    record_id: str
    source: str               # "gog-gmail" | "gog-calendar" | "gog-drive" | "slack" | "whatsapp"
    captured_utc: str
    occurred_utc: str         # when the underlying event happened
    text: str
    actors: List[str] = field(default_factory=list)
    refs: List[str] = field(default_factory=list)   # URLs, IDs, channel/thread, file paths
    relevance: str = "kept"
    relevance_reason: str = ""
    is_test_fixture: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def write_record(rec: AbsorbRecord) -> bool:
    """Persist a record after running the relevance gate. Return True if kept."""
    verdict: RelevanceVerdict = is_work_relevant(rec.text, hint=rec.source.split("-", 1)[0])
    rec.relevance = "kept" if verdict.relevant else "dropped"
    rec.relevance_reason = verdict.reason
    paths.ensure_state_dir()
    jsonl_io.append(paths.KNOWLEDGE_LOG, rec.to_dict())
    return verdict.relevant


def make_id(source: str, key: str) -> str:
    """Stable per-source record ID: ``<source>:<external-key>``."""
    return f"{source}:{key}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_from_epoch(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def coalesce_actors(*items: Optional[str]) -> List[str]:
    """Filter empties, preserve order, dedupe."""
    seen, out = set(), []
    for it in items:
        if not it:
            continue
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out
