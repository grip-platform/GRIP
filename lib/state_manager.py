"""GRIP State Manager - Centralized state file management (GRASP Pure Fabrication).

Consolidates all state file access into a single module, eliminating scattered
dotfiles across the CLAUDE_DIR root.

GRASP Principles Applied:
- Pure Fabrication: Technical service class not from domain model
- Information Expert: Knows all state file locations and formats
- High Cohesion: Single responsibility - state persistence
- Low Coupling: Other modules depend on this, not scattered paths

Usage:
    from lib.state_manager import StateManager

    state = StateManager()

    # Read state
    data = state.load("focus-cards")

    # Write state
    state.save("focus-cards", {"cards": [...], "updated_at": "..."})

    # Get path (for legacy compatibility during migration)
    path = state.path("audit-chain")

    # Lock management
    with state.lock("kono-session-counters"):
        data = state.load("kono-session-counters")
        data["count"] += 1
        state.save("kono-session-counters", data)
"""
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported/run as lib.X, so lib/ is not itself on sys.path
    from lib._compat import fcntl

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def _claude_dir() -> Path:
    """Resolve GRIP root: CLAUDE_DIR_OVERRIDE > CLAUDE_DIR > ~/.claude."""
    override = os.environ.get("CLAUDE_DIR_OVERRIDE")
    if override:
        return Path(override)
    env_dir = os.environ.get("CLAUDE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".claude"


CLAUDE_DIR = _claude_dir()
STATE_DIR = CLAUDE_DIR / "state"

# Schema version for state files — bump when format changes
SCHEMA_VERSION = 1

# Registry of all known state files with their categories
# This is the single source of truth for state file locations
STATE_REGISTRY: Dict[str, Dict[str, Any]] = {
    # Session state
    "model-state": {"category": "session", "description": "Current model selection"},
    "orchestrator-state": {"category": "session", "description": "Orchestrator context"},
    "autonomous-state": {"category": "session", "description": "Autonomous mode flags"},
    "plan-iteration-state": {"category": "session", "description": "Planning iteration tracking"},

    # RSI (Recursive Self-Improvement) state
    "rsi-sync-state": {"category": "rsi", "description": "RSI sync timestamps"},
    "rsi-convergence-state": {"category": "rsi", "description": "RSI convergence metrics"},
    "rsi-last-collect": {"category": "rsi", "description": "Last RSI collection time"},
    "auto-converge-state": {"category": "rsi", "description": "Auto-convergence state"},
    "rsi-delegate-state": {"category": "rsi", "description": "RSI delegate tracking"},

    # Audit & tracking state
    "audit-chain-state": {"category": "audit", "description": "Audit chain data"},
    "drift-state": {"category": "audit", "description": "Configuration drift tracking"},
    "insight-verifier-state": {"category": "audit", "description": "Insight verification"},
    "insight-stack": {"category": "audit", "description": "Insight stack data"},

    # Feature state
    "focus-cards-state": {"category": "features", "description": "Focus cards UI state"},
    "context-gate-state": {"category": "features", "description": "Context gating state"},
    "meta-inference-state": {"category": "features", "description": "Meta-inference daemon"},
    "quality-debounce": {"category": "features", "description": "Quality check debouncing"},
    "read-cache": {"category": "features", "description": "File read cache"},

    # Integrations state
    "kono-session-counters": {"category": "integrations", "description": "Kono session counts"},
    "kono-auto-capture-cache": {"category": "integrations", "description": "Kono auto-capture"},
    "slack-notify-last": {"category": "integrations", "description": "Last Slack notification"},
    "grip-ui-state": {"category": "integrations", "description": "GRIP UI state"},

    # Memory state
    "auto-memory-state": {"category": "memory", "description": "Auto memory saver"},

    # Approval state
    "destructive-approval": {"category": "approvals", "description": "Destructive action approvals"},

    # Config (not migrated - special file)
    "mcp": {"category": "config", "description": "MCP configuration", "keep_root": True},

    # Decisions
    "decisions": {"category": "decisions", "description": "Decision tracking"},
}

CACHE_DIR = CLAUDE_DIR / "cache"

CACHE_REGISTRY: Dict[str, Dict[str, Any]] = {
    # Genome / evolutionary
    "genome": {"description": "Feature scorecard"},
    "genome-shared": {"description": "Shared feature scorecard export"},
    "genome-history": {"description": "Feature scorecard history", "ext": "jsonl", "ttl_days": 30, "max_size_mb": 5},
    "genome-outcomes": {"description": "Feature scorecard outcomes", "ext": "jsonl", "ttl_days": 30, "max_size_mb": 5},
    "genome-kono-bridge": {"description": "Feature scorecard knowledge base bridge"},
    "fitness-bridge": {"description": "Fitness bridge data"},

    # Session / metrics
    "embodiment-tracker": {"description": "Playbook maturity tracker", "ext": "jsonl", "ttl_days": 30, "max_size_mb": 5},
    "steering-audit": {"description": "Steering audit log", "ext": "jsonl", "ttl_days": 30, "max_size_mb": 5},
    "context-deltas": {"description": "Context usage deltas", "ext": "jsonl", "ttl_days": 14, "max_size_mb": 2},
    "violation-memory-unknown": {"description": "Quality violation memory", "ext": "jsonl", "ttl_days": 30, "max_size_mb": 2},
    "review-verdicts": {"description": "Review verdict history", "ext": "jsonl", "ttl_days": 30, "max_size_mb": 5},
    "warm-memory": {"description": "Warm memory log", "ext": "jsonl", "ttl_days": 14, "max_size_mb": 2},

    # Plans / caching
    "last-plan": {"description": "Last plan cache", "ttl_days": 7},
    "convergence-strategies": {"description": "Convergence strategy cache", "ttl_days": 30},
    "mode-selection-cache": {"description": "Preset selection cache", "ttl_days": 7},
    "command-index": {"description": "Command index cache"},
    "cube-state": {"description": "Quality metrics cube"},
    "plan-claims": {"description": "Plan claims registry", "ttl_days": 30},
    "brain-merges": {"description": "Brain merge history", "ttl_days": 30},
    "pair-session-state": {"description": "Pair session state", "ttl_days": 7},
    "test-pass-rate": {"description": "Test pass rate cache", "ttl_days": 14},
}


# ---------------------------------------------------------------------------
# State Manager
# ---------------------------------------------------------------------------

@dataclass
class StateManager:
    """Centralized state file manager.

    Provides unified access to all GRIP state files, consolidating them
    into a structured state/ directory.
    """
    state_dir: Path = field(default_factory=lambda: STATE_DIR)
    _legacy_mode: bool = field(default=True)  # Support both old and new locations

    cache_dir: Path = field(default_factory=lambda: CACHE_DIR)

    def __post_init__(self) -> None:
        """Ensure state and cache directories exist."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "archive").mkdir(exist_ok=True)
        # Create category subdirectories
        for info in STATE_REGISTRY.values():
            if not info.get("keep_root"):
                category_dir = self.state_dir / info["category"]
                category_dir.mkdir(exist_ok=True)

    def path(self, name: str, *, legacy: bool = False) -> Path:
        """Get path for a state file.

        Args:
            name: State file identifier (without extension)
            legacy: If True, return legacy dotfile path

        Returns:
            Path to the state file
        """
        info = STATE_REGISTRY.get(name, {"category": "misc"})

        # Special files that stay in root
        if info.get("keep_root"):
            return CLAUDE_DIR / f".{name}.json"

        # Legacy path (dotfile in root)
        if legacy:
            return CLAUDE_DIR / f".{name}.json"

        # New consolidated path
        category = info.get("category", "misc")
        return self.state_dir / category / f"{name}.json"

    def lock_path(self, name: str) -> Path:
        """Get lock file path for a state file."""
        state_path = self.path(name)
        return state_path.with_suffix(".lock")

    def _resolve_path(self, name: str) -> Path:
        """Resolve actual path, checking both new and legacy locations."""
        new_path = self.path(name)
        legacy_path = self.path(name, legacy=True)

        # Prefer new location if it exists
        if new_path.exists():
            return new_path

        # Fall back to legacy location if in legacy mode
        if self._legacy_mode and legacy_path.exists():
            return legacy_path

        # Return new path for creation
        return new_path

    def load(self, name: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Load state from file.

        Args:
            name: State file identifier
            default: Default value if file doesn't exist

        Returns:
            State dictionary
        """
        path = self._resolve_path(name)

        if not path.exists():
            return default if default is not None else {}

        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, IOError):
            return default if default is not None else {}

    def save(self, name: str, data: Dict[str, Any], *, migrate: bool = True) -> Path:
        """Save state to file.

        Args:
            name: State file identifier
            data: State dictionary to save
            migrate: If True, save to new location (migrate from legacy)

        Returns:
            Path where state was saved
        """
        if migrate:
            path = self.path(name)
            # Ensure parent directory exists
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path = self._resolve_path(name)

        # Stamp schema version for future migration support
        if "_schema_version" not in data:
            data["_schema_version"] = SCHEMA_VERSION
        path.write_text(json.dumps(data, indent=2, default=str))
        return path

    @contextmanager
    def lock(self, name: str, timeout: float = 10.0) -> Iterator[None]:
        """Context manager for exclusive access to a state file.

        Args:
            name: State file identifier
            timeout: Lock acquisition timeout in seconds

        Yields:
            None when lock is acquired
        """
        lock_path = self.lock_path(name)
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with open(lock_path, "w") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    # ------------------------------------------------------------------
    # Cache methods
    # ------------------------------------------------------------------

    def cache_path(self, name: str) -> Path:
        """Get path for a cache file.

        Args:
            name: Cache file identifier (must exist in CACHE_REGISTRY or
                  falls back to .json extension)

        Returns:
            Path inside cache_dir
        """
        info = CACHE_REGISTRY.get(name, {})
        ext = info.get("ext", "json")
        return self.cache_dir / f"{name}.{ext}"

    def load_cache(self, name: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Load a cache file.

        Args:
            name: Cache file identifier
            default: Default value if file doesn't exist or is invalid

        Returns:
            Parsed JSON dictionary
        """
        path = self.cache_path(name)
        if not path.exists():
            return default if default is not None else {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, IOError):
            return default if default is not None else {}

    def save_cache(self, name: str, data: Dict[str, Any]) -> Path:
        """Save data to a cache file.

        Args:
            name: Cache file identifier
            data: Data to serialise

        Returns:
            Path where cache was saved
        """
        path = self.cache_path(name)
        path.write_text(json.dumps(data, indent=2, default=str))
        return path

    def cleanup_expired_cache(self, *, keep_last_n: int = 1000) -> Dict[str, str]:
        """Archive expired cache files and truncate oversized JSONL files.

        For each CACHE_REGISTRY entry:
        - ttl_days set and file older than ttl_days  -> archive to cache/archive/
        - max_size_mb set and file exceeds limit     -> truncate JSONL to last N lines

        Args:
            keep_last_n: Lines to keep when truncating oversized JSONL files

        Returns:
            Report dict mapping cache name to action taken
        """
        import shutil
        import time

        now = time.time()
        archive_dir = self.cache_dir / "archive"
        report: Dict[str, str] = {}

        for name, info in CACHE_REGISTRY.items():
            path = self.cache_path(name)
            if not path.exists():
                report[name] = "missing"
                continue

            ttl_days: Optional[int] = info.get("ttl_days")
            max_mb: Optional[float] = info.get("max_size_mb")
            age_days = (now - path.stat().st_mtime) / 86400

            if ttl_days is not None and age_days > ttl_days:
                dest = archive_dir / path.name
                shutil.move(str(path), str(dest))
                report[name] = f"archived (age {age_days:.1f}d > ttl {ttl_days}d)"
                continue

            if max_mb is not None and path.stat().st_size > max_mb * 1_048_576:
                lines = path.read_text().splitlines()
                kept = lines[-keep_last_n:]
                path.write_text("\n".join(kept) + "\n")
                report[name] = f"truncated ({len(lines)} -> {len(kept)} lines)"
                continue

            report[name] = "ok"

        return report

    def migrate_all(self, *, dry_run: bool = False) -> Dict[str, str]:
        """Migrate all legacy dotfiles to new state directory.

        Args:
            dry_run: If True, don't actually move files

        Returns:
            Dictionary of {name: status} for each migrated file
        """
        results: Dict[str, str] = {}

        for name, info in STATE_REGISTRY.items():
            if info.get("keep_root"):
                results[name] = "skipped (keep_root)"
                continue

            legacy_path = self.path(name, legacy=True)
            new_path = self.path(name)

            if not legacy_path.exists():
                results[name] = "skipped (no legacy file)"
                continue

            if new_path.exists():
                results[name] = "skipped (already migrated)"
                continue

            if dry_run:
                results[name] = f"would migrate {legacy_path} -> {new_path}"
            else:
                # Read from legacy, write to new
                try:
                    data = json.loads(legacy_path.read_text())
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    new_path.write_text(json.dumps(data, indent=2, default=str))
                    results[name] = f"migrated to {new_path}"
                except Exception as e:
                    results[name] = f"error: {e}"

        return results

    def cleanup_legacy(self, *, dry_run: bool = False) -> Dict[str, str]:
        """Remove legacy dotfiles that have been migrated.

        Args:
            dry_run: If True, don't actually delete files

        Returns:
            Dictionary of {name: status} for each file
        """
        results: Dict[str, str] = {}

        for name, info in STATE_REGISTRY.items():
            if info.get("keep_root"):
                continue

            legacy_path = self.path(name, legacy=True)
            new_path = self.path(name)

            if not legacy_path.exists():
                results[name] = "skipped (no legacy file)"
                continue

            if not new_path.exists():
                results[name] = "skipped (not yet migrated)"
                continue

            # Both exist - can clean up legacy
            if dry_run:
                results[name] = f"would delete {legacy_path}"
            else:
                try:
                    legacy_path.unlink()
                    # Also clean up lock file if present
                    legacy_lock = legacy_path.with_suffix(".lock")
                    if legacy_lock.exists():
                        legacy_lock.unlink()
                    results[name] = "deleted"
                except Exception as e:
                    results[name] = f"error: {e}"

        return results

    def status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all state and cache files.

        Returns:
            Dictionary with status of each registered state file and cache file
        """
        result: Dict[str, Dict[str, Any]] = {}

        for name, info in STATE_REGISTRY.items():
            legacy_path = self.path(name, legacy=True)
            new_path = self.path(name)
            result[name] = {
                "kind": "state",
                "category": info.get("category"),
                "description": info.get("description"),
                "legacy_exists": legacy_path.exists(),
                "new_exists": new_path.exists(),
                "migrated": new_path.exists() and not info.get("keep_root"),
                "legacy_path": str(legacy_path),
                "new_path": str(new_path),
            }

        for name, info in CACHE_REGISTRY.items():
            path = self.cache_path(name)
            size_mb = path.stat().st_size / 1_048_576 if path.exists() else 0.0
            result[f"cache:{name}"] = {
                "kind": "cache",
                "description": info.get("description"),
                "exists": path.exists(),
                "size_mb": round(size_mb, 3),
                "ttl_days": info.get("ttl_days"),
                "max_size_mb": info.get("max_size_mb"),
                "path": str(path),
            }

        return result


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

_manager: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    """Get singleton StateManager instance."""
    global _manager
    if _manager is None:
        _manager = StateManager()
    return _manager


def load_state(name: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Load state (convenience function)."""
    return get_state_manager().load(name, default)


def save_state(name: str, data: Dict[str, Any]) -> Path:
    """Save state (convenience function)."""
    return get_state_manager().save(name, data)


def state_path(name: str) -> Path:
    """Get state path (convenience function)."""
    return get_state_manager().path(name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    manager = StateManager()

    if len(sys.argv) < 2:
        print("Usage: python state_manager.py <command>")
        print("Commands: status, migrate, migrate-dry, cleanup, cleanup-dry, cache-status, cache-cleanup")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        all_status = manager.status()
        state_items = {k: v for k, v in all_status.items() if v["kind"] == "state"}
        print(f"\n{'State File':<30} {'Category':<15} {'Legacy':<8} {'New':<8} {'Migrated':<10}")
        print("-" * 80)
        for name, info in sorted(state_items.items()):
            legacy = "Y" if info["legacy_exists"] else "-"
            new = "Y" if info["new_exists"] else "-"
            migrated = "Y" if info["migrated"] else "-"
            print(f"{name:<30} {info['category']:<15} {legacy:<8} {new:<8} {migrated:<10}")

    elif cmd == "cache-status":
        all_status = manager.status()
        cache_items = {k: v for k, v in all_status.items() if v["kind"] == "cache"}
        print(f"\n{'Cache File':<35} {'Exists':<8} {'Size MB':<10} {'TTL days':<10} {'Max MB':<8}")
        print("-" * 80)
        for key, info in sorted(cache_items.items()):
            name = key[len("cache:"):]
            exists = "Y" if info["exists"] else "-"
            size = f"{info['size_mb']:.3f}" if info["exists"] else "-"
            ttl = str(info["ttl_days"]) if info["ttl_days"] is not None else "-"
            max_mb = str(info["max_size_mb"]) if info["max_size_mb"] is not None else "-"
            print(f"{name:<35} {exists:<8} {size:<10} {ttl:<10} {max_mb:<8}")

    elif cmd == "cache-cleanup":
        report = manager.cleanup_expired_cache()
        for name, action in sorted(report.items()):
            print(f"{name}: {action}")

    elif cmd == "migrate":
        results = manager.migrate_all()
        for name, status in results.items():
            print(f"{name}: {status}")

    elif cmd == "migrate-dry":
        results = manager.migrate_all(dry_run=True)
        for name, status in results.items():
            print(f"{name}: {status}")

    elif cmd == "cleanup":
        results = manager.cleanup_legacy()
        for name, status in results.items():
            print(f"{name}: {status}")

    elif cmd == "cleanup-dry":
        results = manager.cleanup_legacy(dry_run=True)
        for name, status in results.items():
            print(f"{name}: {status}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
