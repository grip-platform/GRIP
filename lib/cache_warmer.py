#!/usr/bin/env python3
"""Cache Warmer — pre-populate GRIP caches for faster session startup.

Scans skills, rules, git state, genome, and modes. Each cache includes
freshness tracking via SHA256 source hashing so unchanged sources are
skipped on re-run.

Usage:
    python3 lib/cache_warmer.py                  # warm all caches
    python3 lib/cache_warmer.py --only skill,git  # selective
    python3 lib/cache_warmer.py --force            # ignore freshness
    python3 lib/cache_warmer.py --dry-run          # show what would be warmed
    python3 lib/cache_warmer.py --json             # JSON output
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
CACHE_DIR = CLAUDE_DIR / "cache"
SKILLS_DIR = CLAUDE_DIR / "skills"
RULES_DIR = CLAUDE_DIR / "rules"
MODES_DIR = CLAUDE_DIR / "modes" / "definitions"
GENOME_PATH = CACHE_DIR / "genome.json"

CACHE_TYPES = ("skill", "rule", "git", "genome", "mode")

# Maps cache type -> output filename
CACHE_FILES = {
    "skill": "skill-index.json",
    "rule": "rule-index.json",
    "git": "git-state.json",
    "genome": "genome-snapshot.json",
    "mode": "mode-index.json",
}


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def _hash_bytes(data: bytes) -> str:
    """SHA256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    """SHA256 hex digest of a single file."""
    return _hash_bytes(path.read_bytes())


def _hash_directory_contents(directory: Path, glob_pattern: str) -> str:
    """Deterministic hash of all files matching *glob_pattern* under *directory*.

    Sorted by relative path so the hash is stable across platforms.
    """
    if not directory.is_dir():
        return _hash_bytes(b"")
    parts: list[bytes] = []
    for p in sorted(directory.rglob(glob_pattern)):
        if p.is_file():
            parts.append(str(p.relative_to(directory)).encode())
            parts.append(p.read_bytes())
    return _hash_bytes(b"".join(parts))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Freshness check
# ---------------------------------------------------------------------------

def _is_fresh(cache_path: Path, source_hash: str) -> bool:
    """Return True if *cache_path* exists and its stored _source_hash matches."""
    if not cache_path.is_file():
        return False
    try:
        data = json.loads(cache_path.read_text())
        return data.get("_source_hash") == source_hash
    except (json.JSONDecodeError, OSError):
        return False


# ---------------------------------------------------------------------------
# Individual warmers
# ---------------------------------------------------------------------------

def _parse_skill_frontmatter(text: str) -> dict[str, Any]:
    """Extract YAML-like frontmatter between --- fences."""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    meta: dict[str, Any] = {}
    current_key = ""
    current_list: list[str] | None = None
    for line in m.group(1).splitlines():
        list_match = re.match(r"^\s+-\s+(.*)", line)
        if list_match and current_key:
            if current_list is None:
                current_list = []
                meta[current_key] = current_list
            current_list.append(list_match.group(1).strip().strip("\"'"))
            continue
        kv = re.match(r"^(\w[\w-]*):\s*(.*)", line)
        if kv:
            current_key = kv.group(1)
            value = kv.group(2).strip().strip("\"'")
            current_list = None
            if value:
                meta[current_key] = value
            continue
    return meta


def warm_skill(claude_dir: Path | None = None) -> dict[str, Any]:
    """Scan skills/*/SKILL.md, extract metadata."""
    skills_dir = (claude_dir or CLAUDE_DIR) / "skills"
    entries: list[dict[str, Any]] = []
    if not skills_dir.is_dir():
        return {"skills": entries}
    for skill_file in sorted(skills_dir.rglob("SKILL.md")):
        rel = str(skill_file.relative_to(skills_dir))
        try:
            text = skill_file.read_text(errors="replace")
        except OSError:
            continue
        meta = _parse_skill_frontmatter(text)
        entries.append({
            "path": rel,
            "name": meta.get("name", skill_file.parent.name),
            "description": meta.get("description", ""),
            "triggers": meta.get("triggers", []),
            "status": meta.get("status", ""),
            "version": meta.get("version", ""),
        })
    return {"skills": entries}


def warm_rule(claude_dir: Path | None = None) -> dict[str, Any]:
    """Scan rules/*.md, extract first heading + first paragraph."""
    rules_dir = (claude_dir or CLAUDE_DIR) / "rules"
    entries: list[dict[str, Any]] = []
    if not rules_dir.is_dir():
        return {"rules": entries}
    for rule_file in sorted(rules_dir.glob("*.md")):
        try:
            text = rule_file.read_text(errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        heading = ""
        paragraph = ""
        for line in lines:
            if not heading and line.startswith("#"):
                heading = line.lstrip("#").strip()
                continue
            if heading and not paragraph:
                stripped = line.strip()
                if stripped:
                    paragraph = stripped
                    break
        entries.append({
            "file": rule_file.name,
            "heading": heading,
            "summary": paragraph,
        })
    return {"rules": entries}


def warm_git(claude_dir: Path | None = None) -> dict[str, Any]:
    """Cache current branch, last commit, uncommitted count, recent commits."""
    cwd = str(claude_dir or CLAUDE_DIR)

    def _git(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    last_hash = _git("rev-parse", "--short", "HEAD")
    status_lines = _git("status", "--porcelain")
    uncommitted = len([l for l in status_lines.splitlines() if l.strip()]) if status_lines else 0
    log_raw = _git("log", "--oneline", "-10", "--no-decorate")
    recent: list[dict[str, str]] = []
    for line in log_raw.splitlines():
        if line.strip():
            parts = line.split(" ", 1)
            recent.append({
                "hash": parts[0],
                "message": parts[1] if len(parts) > 1 else "",
            })
    return {
        "branch": branch,
        "last_commit": last_hash,
        "uncommitted_count": uncommitted,
        "recent_commits": recent,
    }


def warm_genome(claude_dir: Path | None = None) -> dict[str, Any]:
    """Extract generation, gene count, top genes from genome.json."""
    genome_path = (claude_dir or CLAUDE_DIR) / "cache" / "genome.json"
    if not genome_path.is_file():
        return {"exists": False}
    try:
        data = json.loads(genome_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"exists": False, "error": "parse_failed"}

    generation = data.get("generation", 0)
    genes = data.get("genes", {})
    if isinstance(genes, dict):
        gene_count = len(genes)
        # Top genes by fitness
        sorted_genes = sorted(
            genes.items(),
            key=lambda kv: kv[1].get("fitness", 0) if isinstance(kv[1], dict) else 0,
            reverse=True,
        )
        top = []
        for name, info in sorted_genes[:10]:
            if isinstance(info, dict):
                top.append({
                    "name": name,
                    "fitness": info.get("fitness", 0),
                    "invocations": info.get("invocation_count", 0),
                })
            else:
                top.append({"name": name, "fitness": 0, "invocations": 0})
    elif isinstance(genes, list):
        gene_count = len(genes)
        top = genes[:10]
    else:
        gene_count = 0
        top = []

    return {
        "exists": True,
        "generation": generation,
        "gene_count": gene_count,
        "top_genes": top,
    }


def warm_mode(claude_dir: Path | None = None) -> dict[str, Any]:
    """Scan modes/definitions/*.yaml, extract name and description."""
    modes_dir = (claude_dir or CLAUDE_DIR) / "modes" / "definitions"
    entries: list[dict[str, Any]] = []
    if not modes_dir.is_dir():
        return {"modes": entries}
    for mode_file in sorted(modes_dir.glob("*.yaml")):
        try:
            text = mode_file.read_text(errors="replace")
        except OSError:
            continue
        # Simple YAML key extraction (no dependency on PyYAML)
        name = ""
        description = ""
        for line in text.splitlines():
            if not name:
                m = re.match(r"^name:\s*(.*)", line)
                if m:
                    name = m.group(1).strip().strip("\"'")
            if not description:
                m = re.match(r"^description:\s*(.*)", line)
                if m:
                    description = m.group(1).strip().strip("\"'")
            if name and description:
                break
        entries.append({
            "file": mode_file.name,
            "name": name or mode_file.stem.replace("-mode", ""),
            "description": description,
        })
    return {"modes": entries}


# ---------------------------------------------------------------------------
# Source hash computation per type
# ---------------------------------------------------------------------------

def _source_hash_for(cache_type: str, claude_dir: Path | None = None) -> str:
    base = claude_dir or CLAUDE_DIR
    if cache_type == "skill":
        return _hash_directory_contents(base / "skills", "SKILL.md")
    elif cache_type == "rule":
        return _hash_directory_contents(base / "rules", "*.md")
    elif cache_type == "git":
        # Git state is inherently volatile; hash HEAD ref + index mtime
        head_file = base / ".git" / "HEAD"
        index_file = base / ".git" / "index"
        parts: list[bytes] = []
        if head_file.is_file():
            parts.append(head_file.read_bytes())
        if index_file.is_file():
            parts.append(str(index_file.stat().st_mtime).encode())
        return _hash_bytes(b"".join(parts)) if parts else _hash_bytes(b"")
    elif cache_type == "genome":
        gp = base / "cache" / "genome.json"
        return _hash_file(gp) if gp.is_file() else _hash_bytes(b"")
    elif cache_type == "mode":
        return _hash_directory_contents(base / "modes" / "definitions", "*.yaml")
    return _hash_bytes(b"")


# ---------------------------------------------------------------------------
# Warmer dispatch
# ---------------------------------------------------------------------------

WARMER_FNS = {
    "skill": warm_skill,
    "rule": warm_rule,
    "git": warm_git,
    "genome": warm_genome,
    "mode": warm_mode,
}


def warm_cache(
    types: tuple[str, ...] = CACHE_TYPES,
    force: bool = False,
    dry_run: bool = False,
    claude_dir: Path | None = None,
) -> dict[str, Any]:
    """Run cache warming. Returns a results dict."""
    base = claude_dir or CLAUDE_DIR
    cache_dir = base / "cache"
    results: dict[str, Any] = {"warmed": [], "skipped": [], "errors": [], "timings": {}}
    total_start = time.monotonic()

    for ctype in types:
        if ctype not in WARMER_FNS:
            results["errors"].append({"type": ctype, "error": f"unknown cache type: {ctype}"})
            continue

        cache_path = cache_dir / CACHE_FILES[ctype]
        source_hash = _source_hash_for(ctype, base)

        if not force and _is_fresh(cache_path, source_hash):
            results["skipped"].append(ctype)
            continue

        if dry_run:
            results["warmed"].append(ctype)
            continue

        t0 = time.monotonic()
        try:
            data = WARMER_FNS[ctype](base)
            data["_cached_at"] = _now_iso()
            data["_source_hash"] = source_hash
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(data, indent=2) + "\n")
            elapsed = time.monotonic() - t0
            results["warmed"].append(ctype)
            results["timings"][ctype] = round(elapsed, 4)
        except Exception as exc:
            results["errors"].append({"type": ctype, "error": str(exc)})

    results["total_time"] = round(time.monotonic() - total_start, 4)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_text(results: dict[str, Any], dry_run: bool = False) -> str:
    lines = []
    prefix = "[DRY RUN] " if dry_run else ""
    if results["warmed"]:
        lines.append(f"{prefix}Warmed: {', '.join(results['warmed'])}")
    if results["skipped"]:
        lines.append(f"{prefix}Skipped (fresh): {', '.join(results['skipped'])}")
    if results["errors"]:
        for err in results["errors"]:
            lines.append(f"Error ({err['type']}): {err['error']}")
    if results.get("timings"):
        timing_parts = [f"{k}={v}s" for k, v in results["timings"].items()]
        lines.append(f"Timings: {', '.join(timing_parts)}")
    lines.append(f"Total: {results.get('total_time', 0)}s")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GRIP Cache Warmer")
    parser.add_argument("--only", type=str, default="", help="Comma-separated cache types to warm")
    parser.add_argument("--force", action="store_true", help="Ignore freshness, rebuild all")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be warmed")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")
    parser.add_argument("--claude-dir", type=str, default="", help="Override CLAUDE_DIR")
    args = parser.parse_args(argv)

    types = CACHE_TYPES
    if args.only:
        requested = tuple(t.strip() for t in args.only.split(",") if t.strip())
        invalid = [t for t in requested if t not in CACHE_TYPES]
        if invalid:
            print(f"Unknown cache types: {', '.join(invalid)}", file=sys.stderr)
            print(f"Valid types: {', '.join(CACHE_TYPES)}", file=sys.stderr)
            return 1
        types = requested

    claude_dir = Path(args.claude_dir) if args.claude_dir else None
    results = warm_cache(types=types, force=args.force, dry_run=args.dry_run, claude_dir=claude_dir)

    if args.json_out:
        print(json.dumps(results, indent=2))
    else:
        print(_format_text(results, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
