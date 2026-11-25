#!/usr/bin/env python3
"""
Plan Registry - Session-linked plan index for GRIP

Links plans to atomic GRIP sessions via lineage tree.
Enables semantic search across plans via embeddings.

Part of Cortex Integration Phase 3.

Usage:
    python3 plan-registry.py register --plan my-plan --path /path/to.md
    python3 plan-registry.py link --plan my-plan --instance abc123 --gen 200
    python3 plan-registry.py search "cortex integration"
    python3 plan-registry.py backfill
"""

import json
import os
import sys
from file_lock import FileLock
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any
import argparse
import tempfile
import shutil

# Ensure lib/ is on sys.path for sibling imports
_lib_dir = str(Path(__file__).resolve().parent)
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from json_utils import safe_load_json

CLAUDE_DIR = Path.home() / ".claude"
PLANS_DIR = CLAUDE_DIR / "plans"
INDEX_FILE = PLANS_DIR / "plan-index.json"
LOCK_FILE = PLANS_DIR / ".index.lock"
BACKUP_DIR = PLANS_DIR / "backups"
MAX_BACKUPS = 5


class PlanRegistry:
    """Thread-safe plan registry with atomic writes."""

    def __init__(self, index_path: Optional[Path] = None):
        self.index_path = index_path or INDEX_FILE
        self.lock_path = LOCK_FILE
        self._lock_fd = None  # Kept for test compat (now unused)
        self._file_lock = None

    def _acquire_lock(self) -> None:
        """Acquire exclusive lock for concurrent session safety."""
        self._file_lock = FileLock(self.lock_path, timeout=10)
        self._file_lock.__enter__()

    def _release_lock(self) -> None:
        """Release exclusive lock."""
        if self._file_lock:
            self._file_lock.__exit__(None, None, None)
            self._file_lock = None

    def _load_index(self) -> Dict[str, Any]:
        """Load index with default fallback."""
        default = {
            "version": "1.0.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "plans": {},
            "orphaned": [],
            "stats": {"total": 0, "linked": 0, "orphaned": 0, "embedded": 0}
        }
        data = safe_load_json(self.index_path)
        return data if data is not None else default

    def _save_index(self, index: Dict[str, Any]) -> None:
        """Atomic write via temp file + rename."""
        index["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._update_stats(index)

        # Write to temp file first
        fd, tmp_path = tempfile.mkstemp(
            dir=self.index_path.parent,
            prefix=".plan-index-",
            suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(index, f, indent=2)
            # Atomic replace (cross-platform)
            os.replace(tmp_path, self.index_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _update_stats(self, index: Dict[str, Any]) -> None:
        """Recalculate stats from plans."""
        plans = index.get("plans", {})
        orphaned = index.get("orphaned", [])

        total = len(plans) + len(orphaned)
        linked = sum(1 for p in plans.values() if p.get("session_links"))
        embedded = sum(1 for p in plans.values() if p.get("embedded", False))

        index["stats"] = {
            "total": total,
            "linked": linked,
            "orphaned": len(orphaned),
            "embedded": embedded
        }

    def _extract_title(self, content: str) -> str:
        """Extract title from markdown # Plan: header."""
        # Match: # Plan: Title
        match = re.search(r'^#\s*Plan:\s*(.+)$', content, re.MULTILINE)
        if match:
            return match.group(1).strip()

        # Fallback: first # header
        match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        if match:
            return match.group(1).strip()

        return "Untitled Plan"

    def _extract_summary(self, content: str, max_length: int = 2000) -> str:
        """Extract summary for embedding (objective + context)."""
        lines = content.split("\n")
        summary_parts = []
        in_objective = False

        for line in lines:
            # Capture Objective section
            if re.match(r'^##\s*Objective', line, re.IGNORECASE):
                in_objective = True
                continue
            elif re.match(r'^##\s', line) and in_objective:
                in_objective = False
            elif in_objective:
                summary_parts.append(line)

        # Also capture Context if present
        for line in lines[:30]:  # First 30 lines for context
            if re.match(r'^\*\*Context\*\*:', line):
                summary_parts.insert(0, line)

        summary = "\n".join(summary_parts).strip()
        return summary[:max_length] if summary else content[:max_length]

    def _backup_index(self) -> Optional[Path]:
        """Create backup, rotate to keep MAX_BACKUPS."""
        if not self.index_path.exists():
            return None

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # Create timestamped backup
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = BACKUP_DIR / f"plan-index-{timestamp}.json"
        shutil.copy2(self.index_path, backup_path)

        # Rotate old backups
        backups = sorted(BACKUP_DIR.glob("plan-index-*.json"), reverse=True)
        for old_backup in backups[MAX_BACKUPS:]:
            old_backup.unlink()

        return backup_path

    def _build_or_update_entry(self, index, plan_id, plan_path, title, now):
        """Return entry dict — updated existing or freshly built."""
        existing = index["plans"].get(plan_id)
        if existing:
            existing["path"] = str(plan_path)
            existing["title"] = title
            existing["updated_at"] = now
            return existing
        return {
            "plan_id": plan_id,
            "path": str(plan_path),
            "title": title,
            "created_at": now,
            "updated_at": now,
            "session_links": [],
            "status": "active",
            "embedded": False,
        }

    @staticmethod
    def _append_session_link(entry, instance_id, gen, project, now):
        """Append session link if not already present (dedup by instance_id)."""
        if not instance_id:
            return
        link = {
            "instance_id": instance_id,
            "gen": gen or 0,
            "project": project or "",
            "link_type": "created",
            "linked_at": now,
        }
        existing_links = entry.get("session_links", [])
        if not any(l.get("instance_id") == instance_id for l in existing_links):
            existing_links.append(link)
        entry["session_links"] = existing_links

    def register_plan(
        self,
        plan_id: str,
        plan_path: Path,
        instance_id: Optional[str] = None,
        gen: Optional[int] = None,
        project: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a plan in the index. Coordinator over build + link helpers."""
        self._acquire_lock()
        try:
            index = self._load_index()
            content = plan_path.read_text() if plan_path.exists() else ""
            title = self._extract_title(content)
            now = datetime.now(timezone.utc).isoformat()
            entry = self._build_or_update_entry(index, plan_id, plan_path, title, now)
            self._append_session_link(entry, instance_id, gen, project, now)
            if plan_id in index.get("orphaned", []):
                index["orphaned"].remove(plan_id)
            index["plans"][plan_id] = entry
            self._save_index(index)
            return entry
        finally:
            self._release_lock()

    def link_session(
        self,
        plan_id: str,
        instance_id: str,
        gen: int,
        project: str,
        link_type: str = "resumed"
    ) -> bool:
        """
        Link an existing plan to a new session.

        Args:
            plan_id: Plan to link
            instance_id: GRIP instance ID
            gen: Generation number
            project: Encoded project path
            link_type: Type of link (created, resumed, referenced)

        Returns:
            True if linked, False if plan not found
        """
        self._acquire_lock()
        try:
            index = self._load_index()

            if plan_id not in index["plans"]:
                return False

            now = datetime.now(timezone.utc).isoformat()
            link = {
                "instance_id": instance_id,
                "gen": gen,
                "project": project,
                "link_type": link_type,
                "linked_at": now
            }

            # Avoid duplicates
            existing_links = index["plans"][plan_id].get("session_links", [])
            if not any(l.get("instance_id") == instance_id for l in existing_links):
                existing_links.append(link)
                index["plans"][plan_id]["session_links"] = existing_links
                index["plans"][plan_id]["updated_at"] = now
                self._save_index(index)

            return True
        finally:
            self._release_lock()

    @staticmethod
    def _search_via_embeddings(query, index, limit):
        """Semantic search via the get_engine() dispatcher (W5 follow-up).

        Returns ranked plan list or [] on any failure (caller falls back to keyword).
        """
        try:
            from embeddings import get_engine  # W5 follow-up
            engine = get_engine()
            engine.init_schema()
            emb_results = engine.search_hybrid(query_text=query, embed_type="plan", limit=limit)
            results = []
            for r in emb_results:
                plan_id = r.get("metadata", {}).get("plan_id")
                if plan_id and plan_id in index["plans"]:
                    plan = index["plans"][plan_id].copy()
                    plan["search_score"] = r.get("rrf_score", r.get("similarity", 0))
                    plan["search_method"] = "embedding"
                    results.append(plan)
            engine.close()
            return results[:limit]
        except (ImportError, Exception):
            return []

    @staticmethod
    def _search_via_keyword(query, index, limit):
        """Keyword fallback search — no embedding dependency."""
        query_lower = query.lower()
        results = []
        for plan_id, plan in index["plans"].items():
            title = plan.get("title", "").lower()
            path = plan.get("path", "").lower()
            if query_lower in title or query_lower in path or query_lower in plan_id:
                plan_copy = plan.copy()
                plan_copy["search_score"] = 1.0 if query_lower in title else 0.5
                plan_copy["search_method"] = "keyword"
                results.append(plan_copy)
        results.sort(key=lambda x: x.get("search_score", 0), reverse=True)
        return results[:limit]

    def search_plans(
        self,
        query: str,
        limit: int = 5,
        use_embeddings: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search plans — embedding-first with keyword fallback."""
        index = self._load_index()
        if use_embeddings:
            results = self._search_via_embeddings(query, index, limit)
            if results:
                return results
        return self._search_via_keyword(query, index, limit)

    def _embed_one_plan(self, engine, plan_id, plan) -> bool:
        """Embed a single plan if not yet embedded; return True on success."""
        if plan.get("embedded"):
            return False
        plan_path = Path(plan["path"])
        if not plan_path.exists():
            return False
        summary = self._extract_summary(plan_path.read_text())
        engine.store_embedding(
            text=summary,
            embed_type="plan",
            metadata={
                "plan_id": plan_id,
                "title": plan.get("title", ""),
                "path": str(plan_path),
            },
        )
        plan["embedded"] = True
        return True

    def embed_all_plans(self) -> int:
        """Embed all plans not yet embedded. Routes via get_engine() (W5 follow-up)."""
        try:
            from embeddings import get_engine  # W5 follow-up
        except ImportError:
            print("Embeddings module not available", file=sys.stderr)
            return 0
        engine = get_engine()
        engine.init_schema()
        self._acquire_lock()
        try:
            index = self._load_index()
            embedded_count = sum(
                self._embed_one_plan(engine, pid, plan)
                for pid, plan in index["plans"].items()
            )
            self._save_index(index)
            engine.close()
            return embedded_count
        finally:
            self._release_lock()

    @staticmethod
    def _new_entry(plan_id, plan_path, title, now, session_links, status):
        return {
            "plan_id": plan_id,
            "path": str(plan_path),
            "title": title,
            "created_at": now,
            "updated_at": now,
            "session_links": session_links,
            "status": status,
            "embedded": False,
        }

    def _backfill_one(self, plan_path, index, session_dir, now):
        """Process a single plan file. Returns "linked" | "skip_already_linked" | "orphaned"."""
        plan_id = plan_path.stem
        if plan_id in index["plans"] and index["plans"][plan_id].get("session_links"):
            return "skip_already_linked"
        title = self._extract_title(plan_path.read_text())
        session_link = self._find_session_reference(plan_id, session_dir)
        if session_link:
            index["plans"][plan_id] = self._new_entry(
                plan_id, plan_path, title, now, [session_link], "active",
            )
            return "linked"
        if plan_id not in index["plans"]:
            index["plans"][plan_id] = self._new_entry(
                plan_id, plan_path, title, now, [], "orphaned",
            )
        return "orphaned"

    def backfill(self, session_dir: Optional[Path] = None) -> Dict[str, int]:
        """Retroactively link existing plans by scanning session history.

        Coordinator over _backfill_one; helper-result strings drive the counters.
        """
        if session_dir is None:
            session_dir = CLAUDE_DIR / "projects"
        self._backup_index()
        self._acquire_lock()
        try:
            index = self._load_index()
            plan_files = list(PLANS_DIR.glob("*.md"))
            now = datetime.now(timezone.utc).isoformat()
            linked = 0
            orphaned_list: list = []
            for plan_path in plan_files:
                outcome = self._backfill_one(plan_path, index, session_dir, now)
                if outcome in ("linked", "skip_already_linked"):
                    linked += 1
                elif outcome == "orphaned":
                    orphaned_list.append(plan_path.stem)
            index["orphaned"] = orphaned_list
            self._save_index(index)
            return {
                "total": len(plan_files),
                "linked": linked,
                "orphaned": len(orphaned_list),
            }
        finally:
            self._release_lock()

    @staticmethod
    def _load_grip_instances(project_dir: Path) -> dict:
        """Load instance map from project's grip/index.json (uuid -> instance dict)."""
        grip_data = safe_load_json(project_dir / "grip" / "index.json")
        if grip_data is None:
            return {}
        return {inst.get("session_uuid", ""): inst for inst in grip_data.get("instances", [])}

    @staticmethod
    def _build_session_link(entry, instances, project_dir, jsonl_file):
        """Compose a session-link dict from a matched JSONL entry."""
        session_uuid = entry.get("source", {}).get("session_uuid", "")
        instance_info = instances.get(session_uuid, {})
        return {
            "instance_id": instance_info.get("instance_id", session_uuid[:8]),
            "gen": instance_info.get("lineage", {}).get("generation", 0),
            "project": project_dir.name,
            "link_type": "backfilled",
            "linked_at": datetime.now(timezone.utc).isoformat(),
            "source_file": jsonl_file.name,
        }

    def _scan_jsonl_for_pattern(self, jsonl_file: Path, search_pattern: str,
                                 instances: dict, project_dir: Path) -> Optional[dict]:
        """Scan one JSONL file for the search pattern; return link dict or None."""
        try:
            with open(jsonl_file) as f:
                for line in f:
                    if search_pattern not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    return self._build_session_link(entry, instances, project_dir, jsonl_file)
        except Exception:
            return None
        return None

    def _scan_project_dir(self, project_dir: Path, search_pattern: str) -> Optional[dict]:
        """Scan all JSONL files in one project dir; return first match or None."""
        instances = self._load_grip_instances(project_dir)
        for jsonl_file in project_dir.glob("*.jsonl"):
            link = self._scan_jsonl_for_pattern(jsonl_file, search_pattern, instances, project_dir)
            if link is not None:
                return link
        return None

    def _find_session_reference(
        self,
        plan_id: str,
        session_dir: Path,
    ) -> Optional[Dict[str, Any]]:
        """Search session JSONL files for plan references — flat coordinator."""
        search_pattern = f"{plan_id}.md"
        for project_dir in session_dir.iterdir():
            if not project_dir.is_dir():
                continue
            link = self._scan_project_dir(project_dir, search_pattern)
            if link is not None:
                return link
        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics."""
        index = self._load_index()
        return index.get("stats", {})

    def list_plans(
        self,
        status: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        List plans with optional status filter.

        Args:
            status: Filter by status (active, orphaned, executed)
            limit: Max results

        Returns:
            List of plan summaries
        """
        index = self._load_index()
        plans = []

        for plan_id, plan in index["plans"].items():
            if status and plan.get("status") != status:
                continue

            plans.append({
                "plan_id": plan_id,
                "title": plan.get("title", "Untitled"),
                "status": plan.get("status", "active"),
                "links": len(plan.get("session_links", [])),
                "embedded": plan.get("embedded", False),
                "created_at": plan.get("created_at", "")
            })

        # Sort by creation date descending
        plans.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return plans[:limit]


def _build_arg_parser():
    parser = argparse.ArgumentParser(description="Plan Registry - Session-linked plan index")
    sub = parser.add_subparsers(dest="command", help="Commands")
    p_reg = sub.add_parser("register", help="Register a plan")
    p_reg.add_argument("--plan", required=True, help="Plan ID")
    p_reg.add_argument("--path", required=True, help="Path to plan file")
    p_reg.add_argument("--instance", help="GRIP instance ID")
    p_reg.add_argument("--gen", type=int, help="Generation number")
    p_reg.add_argument("--project", help="Encoded project path")
    p_link = sub.add_parser("link", help="Link plan to session")
    p_link.add_argument("--plan", required=True, help="Plan ID")
    p_link.add_argument("--instance", required=True, help="GRIP instance ID")
    p_link.add_argument("--gen", type=int, required=True, help="Generation number")
    p_link.add_argument("--project", required=True, help="Encoded project path")
    p_link.add_argument("--type", default="resumed", help="Link type")
    p_search = sub.add_parser("search", help="Search plans")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--limit", type=int, default=5, help="Max results")
    p_search.add_argument("--no-embeddings", action="store_true", help="Skip embedding search")
    p_backfill = sub.add_parser("backfill", help="Backfill existing plans")
    p_backfill.add_argument("--session-dir", help="Session directory to scan")
    sub.add_parser("embed", help="Embed all plans")
    sub.add_parser("stats", help="Show index statistics")
    p_list = sub.add_parser("list", help="List plans")
    p_list.add_argument("--status", help="Filter by status")
    p_list.add_argument("--limit", type=int, default=20, help="Max results")
    sub.add_parser("backup", help="Backup index")
    return parser


def _cmd_register(registry, args):
    entry = registry.register_plan(
        plan_id=args.plan, plan_path=Path(args.path),
        instance_id=args.instance, gen=args.gen, project=args.project,
    )
    print(json.dumps({"status": "ok", "plan": entry}, indent=2))


def _cmd_link(registry, args):
    success = registry.link_session(
        plan_id=args.plan, instance_id=args.instance,
        gen=args.gen, project=args.project, link_type=args.type,
    )
    if success:
        print(json.dumps({"status": "ok", "linked": True}))
    else:
        print(json.dumps({"status": "error", "message": "Plan not found"}))
        sys.exit(1)


def _cmd_search(registry, args):
    results = registry.search_plans(
        query=args.query, limit=args.limit, use_embeddings=not args.no_embeddings,
    )
    print(json.dumps({"status": "ok", "results": results}, indent=2))


def _cmd_backfill(registry, args):
    session_dir = Path(args.session_dir) if args.session_dir else None
    print(json.dumps({"status": "ok", **registry.backfill(session_dir)}))


def _cmd_embed(registry, args):  # noqa: ARG001
    print(json.dumps({"status": "ok", "embedded": registry.embed_all_plans()}))


def _cmd_stats(registry, args):  # noqa: ARG001
    print(json.dumps({"status": "ok", **registry.get_stats()}, indent=2))


def _cmd_list(registry, args):
    plans = registry.list_plans(status=args.status, limit=args.limit)
    print(json.dumps({"status": "ok", "plans": plans}, indent=2))


def _cmd_backup(registry, args):  # noqa: ARG001
    backup_path = registry._backup_index()
    if backup_path:
        print(json.dumps({"status": "ok", "backup": str(backup_path)}))
    else:
        print(json.dumps({"status": "ok", "message": "No index to backup"}))


COMMANDS = {
    "register": _cmd_register, "link": _cmd_link, "search": _cmd_search,
    "backfill": _cmd_backfill, "embed": _cmd_embed, "stats": _cmd_stats,
    "list": _cmd_list, "backup": _cmd_backup,
}


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    registry = PlanRegistry()
    COMMANDS[args.command](registry, args)


if __name__ == "__main__":
    main()
