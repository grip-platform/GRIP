#!/usr/bin/env python3
"""Version-aware configuration file migration system.

Tracks config versions, chains migrations automatically, creates
timestamped backups before applying changes. Supports dry-run,
rollback, validation, and JSON output.

CLI:
    python3 lib/config_migrator.py             -- check and apply migrations
    python3 lib/config_migrator.py --dry-run   -- preview without applying
    python3 lib/config_migrator.py --rollback settings.json -- restore backup
    python3 lib/config_migrator.py --status    -- show current versions
    python3 lib/config_migrator.py --json      -- JSON output
"""

from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(v: str) -> tuple[int, int, int]:
    """Parse a semver string into a (major, minor, patch) tuple."""
    m = _SEMVER_RE.match(v)
    if not m:
        raise ValueError(f"Invalid semver: {v!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def version_key(v: str) -> tuple[int, int, int]:
    """Sort key for semver strings."""
    return parse_version(v)


# ---------------------------------------------------------------------------
# Config I/O  (YAML-free: gates.yaml stored as JSON fallback)
# ---------------------------------------------------------------------------

def _read_yaml_as_json(path: str) -> dict:
    """Minimal YAML-subset reader for simple key: value files.

    Handles:
      - key: value (string, number, boolean, null)
      - nested dicts (indentation-based, 2-space)
      - lists (- item)
      - comments (#)

    Falls back to JSON if the file starts with '{'.
    """
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return json.loads(text)
    return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> dict:
    """Minimal YAML parser for flat/shallow config files."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict]] = [(-1, root)]

    for raw_line in text.splitlines():
        # Strip comments (not inside quotes — good enough for config files)
        line = raw_line.split("#")[0].rstrip()
        if not line or not line.strip():
            continue

        indent = len(line) - len(line.lstrip())
        content = line.strip()

        # Pop stack to correct depth
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        current = stack[-1][1]

        if content.startswith("- "):
            # List item — the parent key should already be a list
            val = _yaml_value(content[2:].strip())
            # Find which key in current dict is the list
            for k in reversed(list(current.keys())):
                if isinstance(current[k], list):
                    current[k].append(val)
                    break
            continue

        if ":" in content:
            key, _, rest = content.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest:
                current[key] = _yaml_value(rest)
            else:
                # Could be a nested dict or a list — peek ahead later;
                # for now create a dict (will be replaced if list detected)
                child: dict[str, Any] = {}
                current[key] = child
                stack.append((indent, child))

    return root


def _yaml_value(s: str) -> Any:
    """Convert a YAML scalar string to a Python value."""
    if s in ("true", "True", "yes", "on"):
        return True
    if s in ("false", "False", "no", "off"):
        return False
    if s in ("null", "~", ""):
        return None
    # Remove surrounding quotes
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    # Try number
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def read_config(path: str) -> dict:
    """Read a config file (JSON or YAML-subset)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    if p.suffix in (".yaml", ".yml"):
        return _read_yaml_as_json(path)
    return json.loads(p.read_text(encoding="utf-8"))


def write_config(path: str, data: dict) -> None:
    """Write a config file (JSON or YAML-as-JSON)."""
    p = Path(path)
    # Always write as JSON — YAML files get a .json sidecar with version info
    if p.suffix in (".yaml", ".yml"):
        # Write the version-tracked JSON sidecar
        sidecar = str(p) + ".migrator.json"
        Path(sidecar).write_text(
            json.dumps(data, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    else:
        p.write_text(
            json.dumps(data, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Config type detection
# ---------------------------------------------------------------------------

CONFIG_TYPES = {
    "settings.json": "settings",
    "genome.json": "genome",
    "gates.yaml": "gates",
}


def detect_config_type(path: str) -> Optional[str]:
    """Detect the config type from a file path."""
    basename = os.path.basename(path)
    if basename in CONFIG_TYPES:
        return CONFIG_TYPES[basename]
    # cache/*.json → "cache"
    parts = Path(path).parts
    if "cache" in parts and basename.endswith(".json"):
        return "cache"
    return None


def get_version(data: dict) -> str:
    """Extract the _config_version from data, defaulting to '0.0.0'."""
    return data.get("_config_version", "0.0.0")


def set_version(data: dict, version: str) -> dict:
    """Set _config_version on data (returns mutated data)."""
    data["_config_version"] = version
    return data


# ---------------------------------------------------------------------------
# Backup & Rollback
# ---------------------------------------------------------------------------

def _timestamp_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def create_backup(path: str, timestamp: Optional[str] = None) -> str:
    """Create a timestamped backup. Returns the backup path."""
    ts = timestamp or _timestamp_str()
    backup_path = f"{path}.bak.{ts}"
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Cannot backup non-existent file: {path}")
    Path(backup_path).write_bytes(p.read_bytes())
    return backup_path


def find_latest_backup(path: str) -> Optional[str]:
    """Find the most recent backup for a config file."""
    pattern = f"{path}.bak.*"
    backups = sorted(glob.glob(pattern))
    return backups[-1] if backups else None


def rollback(path: str) -> str:
    """Restore the most recent backup. Returns the backup path used."""
    backup = find_latest_backup(path)
    if not backup:
        raise FileNotFoundError(f"No backup found for {path}")
    import shutil
    shutil.copy2(backup, path)
    return backup


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# Schema definitions: config_type → dict of field → expected_type
SCHEMAS: dict[str, dict[str, dict[str, type | tuple]]] = {
    "settings": {
        "required": {},  # No hard requirements — flexible
        "optional": {
            "_config_version": str,
            "alwaysThinkingEnabled": bool,
        },
    },
    "genome": {
        "required": {},
        "optional": {
            "_config_version": str,
            "fitness_score": (int, float),
        },
    },
    "gates": {
        "required": {},
        "optional": {
            "_config_version": str,
        },
    },
    "cache": {
        "required": {},
        "optional": {
            "_config_version": str,
        },
    },
}


def validate(data: dict, config_type: str) -> list[str]:
    """Validate config data against its schema. Returns list of errors."""
    errors: list[str] = []
    schema = SCHEMAS.get(config_type)
    if not schema:
        return errors  # Unknown type, no validation

    for field, expected in schema.get("required", {}).items():
        if field not in data:
            errors.append(f"Missing required field: {field}")
        elif not isinstance(data[field], expected):
            errors.append(
                f"Field {field!r}: expected {expected.__name__}, "
                f"got {type(data[field]).__name__}"
            )

    for field, expected in schema.get("optional", {}).items():
        if field in data:
            if isinstance(expected, tuple):
                if not isinstance(data[field], expected):
                    names = "/".join(t.__name__ for t in expected)
                    errors.append(
                        f"Field {field!r}: expected {names}, "
                        f"got {type(data[field]).__name__}"
                    )
            elif not isinstance(data[field], expected):
                errors.append(
                    f"Field {field!r}: expected {expected.__name__}, "
                    f"got {type(data[field]).__name__}"
                )

    return errors


# ---------------------------------------------------------------------------
# Migration Registry
# ---------------------------------------------------------------------------

# Key: (config_type, from_version, to_version)
# Value: migration function (dict) -> dict
MigrationFunc = Callable[[dict], dict]
_REGISTRY: dict[tuple[str, str, str], MigrationFunc] = {}


def register_migration(
    config_type: str, from_ver: str, to_ver: str
) -> Callable[[MigrationFunc], MigrationFunc]:
    """Decorator to register a migration function."""
    def decorator(fn: MigrationFunc) -> MigrationFunc:
        key = (config_type, from_ver, to_ver)
        if key in _REGISTRY:
            raise ValueError(f"Duplicate migration: {key}")
        _REGISTRY[key] = fn
        return fn
    return decorator


def get_registry() -> dict[tuple[str, str, str], MigrationFunc]:
    """Return the migration registry (for testing)."""
    return _REGISTRY


def available_versions(config_type: str) -> list[str]:
    """Return sorted list of all versions mentioned in migrations for a type."""
    versions: set[str] = set()
    for (ct, fv, tv) in _REGISTRY:
        if ct == config_type:
            versions.add(fv)
            versions.add(tv)
    return sorted(versions, key=version_key)


def latest_version(config_type: str) -> str:
    """Return the latest version for a config type, or '0.0.0' if none."""
    versions = available_versions(config_type)
    return versions[-1] if versions else "0.0.0"


def resolve_chain(
    config_type: str, from_ver: str, to_ver: str
) -> list[tuple[str, str, MigrationFunc]]:
    """Resolve a migration chain from from_ver to to_ver.

    Uses BFS to find the shortest path through registered migrations.
    Returns list of (from, to, func) tuples in order.
    Raises ValueError if no path exists.
    """
    if from_ver == to_ver:
        return []

    # Build adjacency from registry
    graph: dict[str, list[tuple[str, MigrationFunc]]] = {}
    for (ct, fv, tv), fn in _REGISTRY.items():
        if ct == config_type:
            graph.setdefault(fv, []).append((tv, fn))

    # BFS
    from collections import deque
    queue: deque[list[tuple[str, str, MigrationFunc]]] = deque()
    visited: set[str] = {from_ver}

    for tv, fn in graph.get(from_ver, []):
        queue.append([(from_ver, tv, fn)])
        visited.add(tv)

    while queue:
        path = queue.popleft()
        current = path[-1][1]
        if current == to_ver:
            return path
        for tv, fn in graph.get(current, []):
            if tv not in visited:
                visited.add(tv)
                queue.append(path + [(current, tv, fn)])

    raise ValueError(
        f"No migration path from {from_ver} to {to_ver} "
        f"for config type {config_type!r}"
    )


# ---------------------------------------------------------------------------
# Built-in Migrations
# ---------------------------------------------------------------------------

@register_migration("settings", "0.0.0", "1.0.0")
def _settings_v0_to_v1(data: dict) -> dict:
    """Initial version stamp for settings."""
    set_version(data, "1.0.0")
    return data


@register_migration("settings", "1.0.0", "2.0.0")
def _settings_v1_to_v2(data: dict) -> dict:
    """Add alwaysThinkingEnabled field if missing."""
    if "alwaysThinkingEnabled" not in data:
        data["alwaysThinkingEnabled"] = True
    set_version(data, "2.0.0")
    return data


@register_migration("genome", "0.0.0", "1.0.0")
def _genome_v0_to_v1(data: dict) -> dict:
    """Initial version stamp for genome."""
    set_version(data, "1.0.0")
    return data


@register_migration("genome", "1.0.0", "2.0.0")
def _genome_v1_to_v2(data: dict) -> dict:
    """Rename 'fitness' to 'fitness_score' if present."""
    if "fitness" in data:
        data["fitness_score"] = data.pop("fitness")
    set_version(data, "2.0.0")
    return data


@register_migration("gates", "0.0.0", "1.0.0")
def _gates_v0_to_v1(data: dict) -> dict:
    """Initial version stamp for gates."""
    set_version(data, "1.0.0")
    return data


@register_migration("cache", "0.0.0", "1.0.0")
def _cache_v0_to_v1(data: dict) -> dict:
    """Initial version stamp for cache configs."""
    set_version(data, "1.0.0")
    return data


# ---------------------------------------------------------------------------
# Migration Engine
# ---------------------------------------------------------------------------

class MigrationResult:
    """Result of a migration operation on a single config."""

    def __init__(
        self,
        path: str,
        config_type: str,
        from_version: str,
        to_version: str,
        *,
        applied: bool = False,
        dry_run: bool = False,
        backup_path: Optional[str] = None,
        chain: Optional[list[tuple[str, str]]] = None,
        errors: Optional[list[str]] = None,
        skipped: bool = False,
        skip_reason: Optional[str] = None,
    ):
        self.path = path
        self.config_type = config_type
        self.from_version = from_version
        self.to_version = to_version
        self.applied = applied
        self.dry_run = dry_run
        self.backup_path = backup_path
        self.chain = chain or []
        self.errors = errors or []
        self.skipped = skipped
        self.skip_reason = skip_reason

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "path": self.path,
            "config_type": self.config_type,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "applied": self.applied,
            "dry_run": self.dry_run,
            "chain": [{"from": f, "to": t} for f, t in self.chain],
        }
        if self.backup_path:
            d["backup_path"] = self.backup_path
        if self.errors:
            d["errors"] = self.errors
        if self.skipped:
            d["skipped"] = True
            d["skip_reason"] = self.skip_reason
        return d


def migrate_config(
    path: str,
    *,
    dry_run: bool = False,
    target_version: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> MigrationResult:
    """Migrate a single config file.

    Args:
        path: Path to the config file.
        dry_run: If True, report what would happen without changing files.
        target_version: Version to migrate to (default: latest).
        timestamp: Override timestamp for backups (testing).

    Returns:
        MigrationResult with details of what happened.
    """
    config_type = detect_config_type(path)
    if config_type is None:
        return MigrationResult(
            path=path,
            config_type="unknown",
            from_version="0.0.0",
            to_version="0.0.0",
            skipped=True,
            skip_reason="Unknown config type",
        )

    try:
        data = read_config(path)
    except FileNotFoundError:
        return MigrationResult(
            path=path,
            config_type=config_type,
            from_version="0.0.0",
            to_version="0.0.0",
            skipped=True,
            skip_reason=f"File not found: {path}",
        )
    except (json.JSONDecodeError, Exception) as exc:
        return MigrationResult(
            path=path,
            config_type=config_type,
            from_version="0.0.0",
            to_version="0.0.0",
            skipped=True,
            skip_reason=f"Parse error: {exc}",
        )

    current = get_version(data)
    target = target_version or latest_version(config_type)

    if current == target:
        return MigrationResult(
            path=path,
            config_type=config_type,
            from_version=current,
            to_version=target,
            skipped=True,
            skip_reason="Already at target version",
        )

    try:
        chain = resolve_chain(config_type, current, target)
    except ValueError as exc:
        return MigrationResult(
            path=path,
            config_type=config_type,
            from_version=current,
            to_version=target,
            skipped=True,
            skip_reason=str(exc),
        )

    chain_summary = [(fv, tv) for fv, tv, _ in chain]

    if dry_run:
        return MigrationResult(
            path=path,
            config_type=config_type,
            from_version=current,
            to_version=target,
            dry_run=True,
            chain=chain_summary,
        )

    # Create backup before mutation
    backup_path = create_backup(path, timestamp=timestamp)

    # Apply chain
    migrated = copy.deepcopy(data)
    for _fv, _tv, fn in chain:
        migrated = fn(migrated)

    # Validate
    errors = validate(migrated, config_type)
    if errors:
        # Restore from backup on validation failure
        rollback(path)
        return MigrationResult(
            path=path,
            config_type=config_type,
            from_version=current,
            to_version=target,
            applied=False,
            backup_path=backup_path,
            chain=chain_summary,
            errors=errors,
        )

    # Write migrated config
    write_config(path, migrated)

    return MigrationResult(
        path=path,
        config_type=config_type,
        from_version=current,
        to_version=target,
        applied=True,
        backup_path=backup_path,
        chain=chain_summary,
    )


def discover_configs(base_dir: str) -> list[str]:
    """Discover known config files under base_dir."""
    found: list[str] = []
    base = Path(base_dir)

    for name in CONFIG_TYPES:
        p = base / name
        if p.exists():
            found.append(str(p))

    # Also check config/ subdirectory
    config_dir = base / "config"
    if config_dir.is_dir():
        for name in CONFIG_TYPES:
            p = config_dir / name
            if p.exists():
                found.append(str(p))

    # cache/*.json
    cache_dir = base / "cache"
    if cache_dir.is_dir():
        for p in sorted(cache_dir.glob("*.json")):
            found.append(str(p))

    return found


def status_report(base_dir: str) -> list[dict]:
    """Generate a status report for all discovered configs."""
    configs = discover_configs(base_dir)
    report = []
    for path in configs:
        config_type = detect_config_type(path)
        try:
            data = read_config(path)
            current = get_version(data)
        except Exception:
            current = "error"
        target = latest_version(config_type) if config_type else "unknown"
        needs_migration = current != target and current != "error"
        report.append({
            "path": path,
            "config_type": config_type or "unknown",
            "current_version": current,
            "latest_version": target,
            "needs_migration": needs_migration,
        })
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _handle_rollback(args) -> int:
    """Handle --rollback mode."""
    try:
        backup_used = rollback(args.rollback)
        if args.json_output:
            print(json.dumps({"restored": args.rollback, "from_backup": backup_used}))
        else:
            print(f"Restored {args.rollback} from {backup_used}")
        return 0
    except FileNotFoundError as exc:
        if args.json_output:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


def _handle_status(args) -> int:
    """Handle --status mode."""
    report = status_report(args.base_dir)
    if args.json_output:
        print(json.dumps(report, indent=2))
    else:
        if not report:
            print("No config files found.")
            return 0
        for entry in report:
            flag = " [MIGRATE]" if entry["needs_migration"] else ""
            print(
                f"  {entry['path']}: "
                f"v{entry['current_version']} -> v{entry['latest_version']}"
                f"{flag}"
            )
    return 0


def _format_chain(result) -> str:
    """Format a migration chain as a version arrow string."""
    return " -> ".join(f"v{f}" for f, _ in result.chain) + f" -> v{result.chain[-1][1]}"


def _print_migrate_results(results, dry_run: bool):
    """Print human-readable migration results."""
    applied = [r for r in results if r.applied]
    skipped = [r for r in results if r.skipped]
    dry = [r for r in results if r.dry_run and r.chain]
    errored = [r for r in results if r.errors]

    if dry_run:
        if dry:
            print("Migrations that would be applied:")
            for r in dry:
                print(f"  {r.path} ({r.config_type}): {_format_chain(r)}")
        else:
            print("All configs are up to date.")
        return

    if applied:
        print("Applied migrations:")
        for r in applied:
            print(f"  {r.path}: {_format_chain(r)}")
            print(f"    Backup: {r.backup_path}")

    if errored:
        print("\nFailed migrations:")
        for r in errored:
            print(f"  {r.path}: {', '.join(r.errors)}")

    if skipped:
        up_to_date = [r for r in skipped if r.skip_reason == "Already at target version"]
        other = [r for r in skipped if r.skip_reason != "Already at target version"]
        if up_to_date:
            print(f"\n{len(up_to_date)} config(s) already up to date.")
        for r in other:
            print(f"  Skipped {r.path}: {r.skip_reason}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="GRIP Config Migrator — version-aware configuration migration"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview migrations without applying")
    parser.add_argument("--rollback", metavar="CONFIG_FILE", help="Rollback a config file to its most recent backup")
    parser.add_argument("--status", action="store_true", help="Show current config versions")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    parser.add_argument(
        "--base-dir", default=os.environ.get("GRIP_DIR", os.path.expanduser("~/.claude")),
        help="Base directory to scan for configs (default: $GRIP_DIR or ~/.claude)",
    )
    args = parser.parse_args(argv)

    if args.rollback:
        return _handle_rollback(args)
    if args.status:
        return _handle_status(args)

    # Migrate mode (default)
    configs = discover_configs(args.base_dir)
    if not configs:
        if args.json_output:
            print(json.dumps({"results": [], "message": "No configs found"}))
        else:
            print("No config files found.")
        return 0

    results = [migrate_config(path, dry_run=args.dry_run) for path in configs]

    if args.json_output:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        _print_migrate_results(results, args.dry_run)

    return 1 if any(r.errors for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
