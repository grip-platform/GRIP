"""Backlog item schema, validation, and stage transitions.

Part of the GRIP /backlog system — personal knowledge staging with
autonomous AI processing and critical reasoning gates.
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


BACKLOG_DIR = Path(os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude"))) / "backlog"

VALID_STAGES = {"inbox", "processing", "processed", "reviewed", "rejected", "archive"}
VALID_CONTENT_TYPES = {"note", "url", "document", "image", "code", "chat_export", "idea"}
VALID_CATEGORIES = {"task", "reference", "idea", "decision", "learning", "communication", "project", "archive"}
VALID_ACTION_TYPES = {"task", "research", "decision", "communication", "archive", "skill_candidate"}
VALID_CONNECTION_TYPES = {"contradicts", "extends", "blocks", "enables", "duplicates", "supersedes"}
VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}

STAGE_DIRS = {
    "inbox": BACKLOG_DIR / "inbox",
    "processing": BACKLOG_DIR / "processing",
    "processed": BACKLOG_DIR / "processed",
    "reviewed": BACKLOG_DIR / "reviewed",
    "rejected": BACKLOG_DIR / "rejected",
    "archive": BACKLOG_DIR / "archive",
}


def generate_id() -> str:
    """Generate a short hash ID for a backlog item."""
    raw = f"{time.time()}-{os.getpid()}-{os.urandom(4).hex()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def now_iso() -> str:
    """Current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def today_str() -> str:
    """Current date as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class Action:
    """A proposed action for a backlog item."""
    action: str
    type: str = "task"
    priority: str = "P2"
    confidence: float = 0.5
    reasoning: str = ""
    requires_review: bool = True

    def validate(self):
        if self.type not in VALID_ACTION_TYPES:
            raise ValueError(f"Invalid action type: {self.type}")
        if self.priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority: {self.priority}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {self.confidence}")


@dataclass
class Connection:
    """A connection between a backlog item and existing knowledge."""
    target_id: str
    connection_type: str
    description: str
    similarity_score: float = 0.0

    def validate(self):
        if self.connection_type not in VALID_CONNECTION_TYPES:
            raise ValueError(f"Invalid connection type: {self.connection_type}")


@dataclass
class BacklogItem:
    """A single backlog item with full lifecycle tracking."""
    id: str = ""
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    stage: str = "inbox"

    # Source
    source_method: str = "cli"
    source_raw_input: str = ""
    source_file_path: Optional[str] = None
    source_url: Optional[str] = None

    # Content
    content_type: str = "note"
    content_title: Optional[str] = None
    content_body: str = ""
    content_attachments: list = field(default_factory=list)

    # Processing
    category: Optional[str] = None
    tags: list = field(default_factory=list)
    summary: Optional[str] = None
    actions: list = field(default_factory=list)
    connections: list = field(default_factory=list)
    confidence: Optional[float] = None
    reasoning_trace: Optional[str] = None

    # Review
    review_status: Optional[str] = None
    reviewed_at: Optional[str] = None
    user_notes: Optional[str] = None
    accepted_actions: list = field(default_factory=list)
    graduated_to: Optional[str] = None

    # Metadata
    embedding_id: Optional[str] = None
    processor_version: str = "1.0.0"
    processing_time_ms: Optional[int] = None
    token_cost: Optional[float] = None
    error: Optional[str] = None
    retry_count: int = 0

    def __post_init__(self):
        if not self.id:
            self.id = generate_id()
        if not self.created_at:
            self.created_at = now_iso()
        if not self.updated_at:
            self.updated_at = now_iso()

    def validate(self):
        """Validate item against schema constraints."""
        if self.stage not in VALID_STAGES:
            raise ValueError(f"Invalid stage: {self.stage}")
        if self.content_type not in VALID_CONTENT_TYPES:
            raise ValueError(f"Invalid content type: {self.content_type}")
        if self.category is not None and self.category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category: {self.category}")
        if not self.content_body and not self.source_url:
            raise ValueError("Item must have content_body or source_url")
        for action_dict in self.actions:
            if isinstance(action_dict, dict):
                Action(**action_dict).validate()
            elif isinstance(action_dict, Action):
                action_dict.validate()
        for conn_dict in self.connections:
            if isinstance(conn_dict, dict):
                Connection(**conn_dict).validate()
            elif isinstance(conn_dict, Connection):
                conn_dict.validate()

    def to_dict(self) -> dict:
        """Convert to the canonical JSON schema format."""
        return {
            "id": self.id,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stage": self.stage,
            "source": {
                "method": self.source_method,
                "raw_input": self.source_raw_input,
                "file_path": self.source_file_path,
                "url": self.source_url,
            },
            "content": {
                "type": self.content_type,
                "title": self.content_title,
                "body": self.content_body,
                "attachments": self.content_attachments,
            },
            "processing": {
                "category": self.category,
                "tags": self.tags,
                "summary": self.summary,
                "actions": [
                    asdict(a) if isinstance(a, Action) else a
                    for a in self.actions
                ],
                "connections": [
                    asdict(c) if isinstance(c, Connection) else c
                    for c in self.connections
                ],
                "confidence": self.confidence,
                "reasoning_trace": self.reasoning_trace,
            },
            "review": {
                "status": self.review_status,
                "reviewed_at": self.reviewed_at,
                "user_notes": self.user_notes,
                "accepted_actions": self.accepted_actions,
                "graduated_to": self.graduated_to,
            },
            "embedding_id": self.embedding_id,
            "metadata": {
                "processor_version": self.processor_version,
                "processing_time_ms": self.processing_time_ms,
                "token_cost": self.token_cost,
                "error": self.error,
                "retry_count": self.retry_count,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BacklogItem":
        """Reconstruct from canonical JSON schema."""
        source = data.get("source", {})
        content = data.get("content", {})
        processing = data.get("processing", {})
        review = data.get("review", {})
        metadata = data.get("metadata", {})

        return cls(
            id=data.get("id", ""),
            version=data.get("version", 1),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            stage=data.get("stage", "inbox"),
            source_method=source.get("method", "cli"),
            source_raw_input=source.get("raw_input", ""),
            source_file_path=source.get("file_path"),
            source_url=source.get("url"),
            content_type=content.get("type", "note"),
            content_title=content.get("title"),
            content_body=content.get("body", ""),
            content_attachments=content.get("attachments", []),
            category=processing.get("category"),
            tags=processing.get("tags", []),
            summary=processing.get("summary"),
            actions=processing.get("actions", []),
            connections=processing.get("connections", []),
            confidence=processing.get("confidence"),
            reasoning_trace=processing.get("reasoning_trace"),
            review_status=review.get("status"),
            reviewed_at=review.get("reviewed_at"),
            user_notes=review.get("user_notes"),
            accepted_actions=review.get("accepted_actions", []),
            graduated_to=review.get("graduated_to"),
            embedding_id=data.get("embedding_id"),
            processor_version=metadata.get("processor_version", "1.0.0"),
            processing_time_ms=metadata.get("processing_time_ms"),
            token_cost=metadata.get("token_cost"),
            error=metadata.get("error"),
            retry_count=metadata.get("retry_count", 0),
        )

    def filename(self) -> str:
        """Generate filename: {YYYY-MM-DD}-{type}-{short_hash}.json"""
        date_str = self.created_at[:10] if self.created_at else today_str()
        return f"{date_str}-{self.content_type}-{self.id}.json"


def save_item(item: BacklogItem, stage_dir: Path) -> Path:
    """Save item to a stage directory. Atomic via write-then-rename."""
    item.updated_at = now_iso()
    item.validate()
    target = stage_dir / item.filename()
    tmp = target.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(item.to_dict(), f, indent=2)
    os.replace(str(tmp), str(target))
    return target


def load_item(path: Path) -> BacklogItem:
    """Load item from a JSON file."""
    with open(path) as f:
        data = json.load(f)
    return BacklogItem.from_dict(data)


def move_item(item: BacklogItem, from_dir: Path, to_stage: str) -> Path:
    """Atomically move an item between stage directories."""
    old_path = from_dir / item.filename()
    item.stage = to_stage
    new_dir = STAGE_DIRS[to_stage]
    new_path = save_item(item, new_dir)
    if old_path.exists() and old_path != new_path:
        old_path.unlink()
    return new_path


def list_items(stage: str, category: Optional[str] = None,
               tag: Optional[str] = None) -> list[BacklogItem]:
    """List items in a stage, optionally filtered."""
    stage_dir = STAGE_DIRS.get(stage)
    if not stage_dir or not stage_dir.exists():
        return []
    items = []
    for path in sorted(stage_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
        item = load_item(path)
        if category and item.category != category:
            continue
        if tag and tag not in item.tags:
            continue
        items.append(item)
    return items


def count_by_stage() -> dict:
    """Count items in each stage directory."""
    counts = {}
    for stage, stage_dir in STAGE_DIRS.items():
        if stage_dir.exists():
            counts[stage] = len(list(stage_dir.glob("*.json")))
        else:
            counts[stage] = 0
    return counts
