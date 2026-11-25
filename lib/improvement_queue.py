"""Improvement queue for auto-fix pipeline.

Stores classified improvement items (fix_now / fix_later / investigate)
and provides queue draining for automatic execution.

Part of the GRIP automatic improvement MVP (Gen 276).
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from state_paths import RSI_IMPROVEMENT_QUEUE, state_file  # noqa: E402

# Under state/rsi/ since April audit task D5; state_file reads a queue still
# in the old dotfile at the install root once, by copying it over.
QUEUE_FILE = RSI_IMPROVEMENT_QUEUE


class ImprovementQueue:
    """Manages a persistent queue of improvement items."""

    def __init__(self, queue_path: Path = None):
        self.path = queue_path or state_file(QUEUE_FILE)

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"items": [], "version": 1}

    def _save(self, data: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, default=str))

    def add(
        self,
        category: str,
        description: str,
        classification: str,
        file_path: str = None,
        fix_command: str = None,
    ) -> dict:
        """Add an improvement item to the queue.

        Args:
            category: Type of improvement (count_drift, dead_code_ref, etc.)
            description: Human-readable description
            classification: fix_now | fix_later | investigate
            file_path: Optional file to modify
            fix_command: Optional bash command to execute
        Returns:
            The created item dict
        """
        data = self._load()
        item = {
            "id": str(uuid.uuid4())[:8],
            "category": category,
            "description": description,
            "classification": classification,
            "file_path": file_path,
            "fix_command": fix_command,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }
        data["items"].append(item)
        self._save(data)
        return item

    def drain_fix_now(self) -> list:
        """Return all pending fix_now items and mark them in_progress."""
        data = self._load()
        fix_now = [
            i
            for i in data["items"]
            if i["classification"] == "fix_now" and i["status"] == "pending"
        ]
        for item in fix_now:
            item["status"] = "in_progress"
        self._save(data)
        return fix_now

    def mark_done(self, item_id: str):
        """Mark an item as completed."""
        data = self._load()
        for item in data["items"]:
            if item["id"] == item_id:
                item["status"] = "completed"
                item["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._save(data)

    def mark_failed(self, item_id: str, reason: str = ""):
        """Mark an item as failed."""
        data = self._load()
        for item in data["items"]:
            if item["id"] == item_id:
                item["status"] = "failed"
                item["failed_at"] = datetime.now(timezone.utc).isoformat()
                item["failure_reason"] = reason
        self._save(data)

    def list_pending(self) -> list:
        """Return all pending items regardless of classification."""
        data = self._load()
        return [i for i in data["items"] if i["status"] == "pending"]

    def list_by_classification(self, classification: str) -> list:
        """Return pending items of a specific classification."""
        return [
            i for i in self.list_pending() if i["classification"] == classification
        ]

    def stats(self) -> dict:
        """Return queue statistics."""
        data = self._load()
        items = data["items"]
        return {
            "total": len(items),
            "pending": len([i for i in items if i["status"] == "pending"]),
            "in_progress": len([i for i in items if i["status"] == "in_progress"]),
            "completed": len([i for i in items if i["status"] == "completed"]),
            "failed": len([i for i in items if i["status"] == "failed"]),
            "fix_now": len(
                [
                    i
                    for i in items
                    if i["classification"] == "fix_now" and i["status"] == "pending"
                ]
            ),
            "fix_later": len(
                [
                    i
                    for i in items
                    if i["classification"] == "fix_later" and i["status"] == "pending"
                ]
            ),
        }

    def clear_completed(self):
        """Remove completed items from the queue."""
        data = self._load()
        data["items"] = [i for i in data["items"] if i["status"] != "completed"]
        self._save(data)


# CLI interface for bash integration
if __name__ == "__main__":
    import sys

    q = ImprovementQueue()

    if len(sys.argv) < 2:
        print("Usage: improvement_queue.py [stats|list|drain|clear]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "stats":
        s = q.stats()
        print(f"Total: {s['total']} | Pending: {s['pending']} | "
              f"Fix Now: {s['fix_now']} | Fix Later: {s['fix_later']} | "
              f"Completed: {s['completed']} | Failed: {s['failed']}")
    elif cmd == "list":
        for item in q.list_pending():
            print(f"  [{item['classification']}] {item['id']}: {item['description']}")
    elif cmd == "drain":
        items = q.drain_fix_now()
        for item in items:
            print(json.dumps(item))
    elif cmd == "clear":
        q.clear_completed()
        print("Cleared completed items")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
