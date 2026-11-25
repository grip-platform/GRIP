"""
GRIP Command Index — searchable catalog of all commands, skills, and tools.

Scans the GRIP directory to build an index of skills (SKILL.md), CLI tools
(lib/*.py with argparse or __main__), and shell scripts (lib/*.sh with
usage/help patterns). Supports keyword search across name, description,
and usage examples.

Usage:
    python3 lib/command_index.py build                   # Build and cache index
    python3 lib/command_index.py search "converge"       # Search for matching entries
    python3 lib/command_index.py list --type cli          # List all CLI tools
    python3 lib/command_index.py list --type skill        # List all skills
    python3 lib/command_index.py stats                    # Show counts by type
    python3 lib/command_index.py build --json             # JSON output
    python3 lib/command_index.py search "mode" --json     # JSON search results
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class Entry:
    """A single command, skill, or tool in the index."""
    name: str
    entry_type: str  # "skill", "cli", "shell"
    path: str
    description: str
    usage_example: Optional[str] = None


@dataclass
class CommandIndex:
    """The full searchable index."""
    entries: List[Entry] = field(default_factory=list)
    built_at: str = ""


@dataclass
class Match:
    """A search result with relevance scoring."""
    entry: Entry
    relevance: float  # 0.0 to 1.0
    matched_field: str  # "name", "description", "usage"


def _first_sentence(text: str) -> str:
    """Extract the first sentence from a block of text."""
    text = text.strip()
    if not text:
        return ""
    # Split on sentence-ending punctuation followed by whitespace or end
    match = re.match(r'(.+?[.!?])(?:\s|$)', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # No sentence-ending punctuation: take first line
    return text.split('\n')[0].strip()


def _extract_skill(skill_dir: Path) -> Optional[Entry]:
    """Extract an Entry from a skill's SKILL.md file."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None

    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    name = skill_dir.name
    description = ""
    usage_example = None

    # Try YAML frontmatter first
    fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        # Extract name from frontmatter
        name_match = re.search(r'^name:\s*(.+)$', fm, re.MULTILINE)
        if name_match:
            name = name_match.group(1).strip().strip('"\'')
        # Extract description from frontmatter
        desc_match = re.search(r'^description:\s*(.+)$', fm, re.MULTILINE)
        if desc_match:
            description = _first_sentence(desc_match.group(1).strip().strip('"\''))

    # If no frontmatter description, try first heading + paragraph
    if not description:
        # Find first heading
        heading_match = re.search(r'^#+\s+(.+)$', content, re.MULTILINE)
        if heading_match:
            name = name or heading_match.group(1).strip()
            # First non-empty paragraph after heading
            after_heading = content[heading_match.end():]
            para_match = re.search(r'\n\n(.+?)(?:\n\n|\n```|\n#|\Z)', after_heading, re.DOTALL)
            if para_match:
                description = _first_sentence(para_match.group(1))

    # Extract first code block as usage example
    code_match = re.search(r'```(?:\w*)\n(.*?)\n```', content, re.DOTALL)
    if code_match:
        example = code_match.group(1).strip()
        if example:
            usage_example = example

    return Entry(
        name=name,
        entry_type="skill",
        path=str(skill_md),
        description=description,
        usage_example=usage_example,
    )


def _extract_cli(py_file: Path) -> Optional[Entry]:
    """Extract an Entry from a Python CLI file."""
    try:
        content = py_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Must have argparse or __main__ pattern to be a CLI tool
    has_argparse = "argparse" in content or "ArgumentParser" in content
    has_main = 'if __name__' in content
    if not has_argparse and not has_main:
        return None

    name = py_file.stem
    description = ""
    usage_example = None

    # Extract module docstring
    doc_match = re.match(r'^(?:#.*\n)*\s*"""(.*?)"""', content, re.DOTALL)
    if not doc_match:
        doc_match = re.match(r"^(?:#.*\n)*\s*'''(.*?)'''", content, re.DOTALL)
    if doc_match:
        description = _first_sentence(doc_match.group(1))

    # Extract argparse description if available
    if not description:
        ap_match = re.search(
            r'ArgumentParser\s*\(\s*description\s*=\s*["\'](.+?)["\']',
            content,
        )
        if ap_match:
            description = _first_sentence(ap_match.group(1))

    # Extract usage example from docstring Usage: block
    if doc_match:
        docstring = doc_match.group(1)
        usage_match = re.search(
            r'Usage:\s*\n((?:\s+.+\n?)+)', docstring
        )
        if usage_match:
            usage_example = usage_match.group(1).strip()

    return Entry(
        name=name,
        entry_type="cli",
        path=str(py_file),
        description=description,
        usage_example=usage_example,
    )


def _extract_shell(sh_file: Path) -> Optional[Entry]:
    """Extract an Entry from a shell script."""
    try:
        content = sh_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    name = sh_file.stem
    description = ""
    usage_example = None

    # Extract header comment block (lines starting with # after shebang)
    lines = content.split('\n')
    comment_lines = []
    started = False
    for line in lines:
        stripped = line.strip()
        if not started:
            if stripped.startswith('#!'):
                continue
            if stripped.startswith('#'):
                started = True
                # Strip leading "# " or "#"
                text = re.sub(r'^#\s?', '', stripped)
                if text:
                    comment_lines.append(text)
            elif stripped == '':
                continue
            else:
                break
        else:
            if stripped.startswith('#'):
                text = re.sub(r'^#\s?', '', stripped)
                comment_lines.append(text)
            elif stripped == '':
                break
            else:
                break

    if comment_lines:
        # First non-empty comment line that looks like a description
        # Skip lines that look like file paths or separators
        for cline in comment_lines:
            if cline and not cline.startswith('-') and len(cline) > 5:
                description = _first_sentence(cline)
                break

    # Look for usage pattern in comments or usage() function
    usage_match = re.search(
        r'(?:Usage|USAGE):\s*\n((?:\s*#?\s+.+\n?)+)', content
    )
    if usage_match:
        raw = usage_match.group(1)
        # Clean comment markers
        cleaned = re.sub(r'^\s*#\s?', '', raw, flags=re.MULTILINE).strip()
        if cleaned:
            usage_example = cleaned

    # Also check for usage() function body
    if not usage_example:
        func_match = re.search(
            r'(?:usage|show_help)\s*\(\)\s*\{[^}]*?echo\s+["\'](.+?)["\']',
            content,
            re.DOTALL,
        )
        if func_match:
            usage_example = func_match.group(1)

    return Entry(
        name=name,
        entry_type="shell",
        path=str(sh_file),
        description=description,
        usage_example=usage_example,
    )


def build_index(claude_dir: str = None) -> CommandIndex:
    """Scan all sources and build the command index."""
    if claude_dir is None:
        claude_dir = os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude"))
    base = Path(claude_dir)

    entries: List[Entry] = []

    # 1. Skills: scan skills/*/SKILL.md
    skills_dir = base / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if child.is_dir() and not child.name.startswith(('_', '.')):
                entry = _extract_skill(child)
                if entry is not None:
                    entries.append(entry)

    # 2. CLI: scan lib/*.py
    lib_dir = base / "lib"
    if lib_dir.is_dir():
        for py_file in sorted(lib_dir.glob("*.py")):
            if py_file.name.startswith(('_', '.')):
                continue
            entry = _extract_cli(py_file)
            if entry is not None:
                entries.append(entry)

    # 3. Shell: scan lib/*.sh
    if lib_dir.is_dir():
        for sh_file in sorted(lib_dir.glob("*.sh")):
            if sh_file.name.startswith(('_', '.')):
                continue
            entry = _extract_shell(sh_file)
            if entry is not None:
                entries.append(entry)

    return CommandIndex(
        entries=entries,
        built_at=datetime.now(timezone.utc).isoformat(),
    )


def _tokenize(text: str) -> set:
    """Split text into lowercase word tokens for search matching."""
    if not text:
        return set()
    return set(re.findall(r'[a-z0-9]+', text.lower()))


def search(index: CommandIndex, query: str, top_n: int = 10) -> List[Match]:
    """Keyword search across name, description, and usage fields.

    Uses word overlap scoring: relevance = matched_words / query_words.
    Name matches get a 1.5x boost. Results sorted by descending relevance.
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    results: List[Match] = []

    for entry in index.entries:
        best_score = 0.0
        best_field = "name"

        # Score each field, track the best
        for field_name, text, boost in [
            ("name", entry.name, 1.5),
            ("description", entry.description, 1.0),
            ("usage", entry.usage_example or "", 0.8),
        ]:
            field_tokens = _tokenize(text)
            if not field_tokens:
                continue
            overlap = query_tokens & field_tokens
            if overlap:
                raw = len(overlap) / len(query_tokens)
                score = min(raw * boost, 1.0)
                if score > best_score:
                    best_score = score
                    best_field = field_name

        if best_score > 0:
            results.append(Match(
                entry=entry,
                relevance=round(best_score, 4),
                matched_field=best_field,
            ))

    results.sort(key=lambda m: m.relevance, reverse=True)
    return results[:top_n]


def filter_by_type(index: CommandIndex, entry_type: str) -> List[Entry]:
    """Return entries matching the given type."""
    return [e for e in index.entries if e.entry_type == entry_type]


def save_index(index: CommandIndex, path: str) -> None:
    """Save the index to a JSON file."""
    data = {
        "entries": [asdict(e) for e in index.entries],
        "built_at": index.built_at,
    }
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_index(path: str) -> CommandIndex:
    """Load a cached index from a JSON file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = [Entry(**e) for e in raw.get("entries", [])]
    return CommandIndex(entries=entries, built_at=raw.get("built_at", ""))


def _match_to_dict(m: Match) -> dict:
    """Convert a Match to a serializable dict."""
    return {
        "entry": asdict(m.entry),
        "relevance": m.relevance,
        "matched_field": m.matched_field,
    }


def main():
    parser = argparse.ArgumentParser(
        description="GRIP Command Index — searchable catalog of commands, skills, and tools"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--claude-dir", default=None,
        help="GRIP directory (default: $CLAUDE_DIR or ~/.claude)"
    )

    sub = parser.add_subparsers(dest="command")

    # build
    sub.add_parser("build", help="Build and cache the index")

    # search
    sp_search = sub.add_parser("search", help="Search for commands")
    sp_search.add_argument("query", help="Search query")
    sp_search.add_argument("--top", type=int, default=10, help="Max results")

    # list
    sp_list = sub.add_parser("list", help="List entries")
    sp_list.add_argument("--type", dest="entry_type", choices=["skill", "cli", "shell"],
                         help="Filter by type")

    # stats
    sub.add_parser("stats", help="Show counts by type")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    claude_dir = args.claude_dir
    cache_dir = Path(claude_dir or os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
    cache_path = cache_dir / "cache" / "command-index.json"

    if args.command == "build":
        idx = build_index(claude_dir)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        save_index(idx, str(cache_path))
        if args.json:
            data = {"entries": [asdict(e) for e in idx.entries], "built_at": idx.built_at}
            print(json.dumps(data, indent=2))
        else:
            print(f"Built index: {len(idx.entries)} entries")
            for t in ("skill", "cli", "shell"):
                count = len(filter_by_type(idx, t))
                print(f"  {t}: {count}")
            print(f"Cached to {cache_path}")

    elif args.command == "search":
        # Load cached or build fresh
        if cache_path.is_file():
            idx = load_index(str(cache_path))
        else:
            idx = build_index(claude_dir)

        matches = search(idx, args.query, top_n=args.top)
        if args.json:
            print(json.dumps([_match_to_dict(m) for m in matches], indent=2))
        else:
            if not matches:
                print(f"No matches for '{args.query}'")
            else:
                print(f"Results for '{args.query}' ({len(matches)} matches):\n")
                for m in matches:
                    e = m.entry
                    print(f"  [{e.entry_type:5s}] {e.name}")
                    print(f"         {e.description[:80] if e.description else '(no description)'}")
                    print(f"         relevance: {m.relevance:.2f}  matched: {m.matched_field}")
                    print()

    elif args.command == "list":
        if cache_path.is_file():
            idx = load_index(str(cache_path))
        else:
            idx = build_index(claude_dir)

        entries = idx.entries
        if args.entry_type:
            entries = filter_by_type(idx, args.entry_type)

        if args.json:
            print(json.dumps([asdict(e) for e in entries], indent=2))
        else:
            for e in entries:
                desc = e.description[:60] if e.description else "(no description)"
                print(f"  [{e.entry_type:5s}] {e.name:30s} {desc}")

    elif args.command == "stats":
        if cache_path.is_file():
            idx = load_index(str(cache_path))
        else:
            idx = build_index(claude_dir)

        stats = {}
        for e in idx.entries:
            stats[e.entry_type] = stats.get(e.entry_type, 0) + 1

        if args.json:
            print(json.dumps({"total": len(idx.entries), "by_type": stats}, indent=2))
        else:
            print(f"Total entries: {len(idx.entries)}")
            for t in sorted(stats):
                print(f"  {t}: {stats[t]}")


if __name__ == "__main__":
    main()
