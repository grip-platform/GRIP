#!/usr/bin/env python3
"""
Skill Indexer - Populates embeddings.db with skill/mode/command concepts

Indexes GRIP skills, modes, and commands for semantic matching.
Uses EmbeddingEngine from embeddings.py.

Usage:
    python3 lib/skill_indexer.py index     # Index all skills/modes/commands
    python3 lib/skill_indexer.py stats     # Show index stats
    python3 lib/skill_indexer.py search "query"  # Semantic search
    python3 lib/skill_indexer.py search "query" --type concept_mode  # Filter by type
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any

# Add lib to path for embeddings import
CLAUDE_DIR = Path.home() / ".claude"
if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from embeddings import get_engine  # W5 follow-up: dispatch via get_engine()
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False


class SkillIndexer:
    """Index skills, modes, and commands into embeddings database."""

    def __init__(self, claude_dir: Optional[Path] = None):
        self.claude_dir = claude_dir or CLAUDE_DIR
        self.skills_dir = self.claude_dir / "skills"
        self.modes_dir = self.claude_dir / "modes" / "definitions"
        self.commands_dir = self.claude_dir / "commands"
        self.engine = None

    def _get_engine(self):
        """Lazy-load embedding engine via the get_engine() dispatcher (W5 follow-up)."""
        if self.engine is None:
            if not HAS_EMBEDDINGS:
                raise RuntimeError("embeddings.py not available. Run embeddings-init.sh first.")
            self.engine = get_engine()
            self.engine.init_schema()
        return self.engine

    def extract_frontmatter(self, content: str) -> Dict[str, Any]:
        """
        Extract YAML frontmatter from SKILL.md content.

        Args:
            content: Markdown content with optional YAML frontmatter

        Returns:
            Dict with parsed frontmatter fields
        """
        frontmatter = {}
        match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if match:
            yaml_content = match.group(1)
            for line in yaml_content.split('\n'):
                if ':' in line:
                    key, _, value = line.partition(':')
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    frontmatter[key] = value
        return frontmatter

    def extract_description(self, content: str) -> str:
        """
        Extract description from skill content.
        Falls back to first paragraph after title.
        """
        # Try frontmatter description first
        fm = self.extract_frontmatter(content)
        if fm.get('description'):
            return fm['description']

        # Find first meaningful paragraph after title
        lines = content.split('\n')
        in_frontmatter = False
        found_title = False

        for line in lines:
            stripped = line.strip()
            if stripped == '---':
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter:
                continue
            if stripped.startswith('#'):
                found_title = True
                continue
            if found_title and stripped and not stripped.startswith('|') and not stripped.startswith('-'):
                # First non-empty, non-table, non-list line after title
                return stripped[:200]

        return ""

    def extract_triggers(self, content: str) -> List[str]:
        """Extract trigger patterns from skill content."""
        triggers = []

        # From frontmatter
        fm = self.extract_frontmatter(content)
        if fm.get('triggers'):
            # Could be comma-separated or newline-separated
            raw = fm['triggers']
            triggers.extend([t.strip() for t in re.split(r'[,\n]', raw) if t.strip()])

        # From content - look for /command patterns
        triggers.extend(re.findall(r'`(/[a-z-]+)`', content))

        return list(set(triggers))

    def scan_skills(self) -> List[Dict[str, Any]]:
        """
        Scan all skills in skills directory.

        Returns:
            List of skill metadata dicts
        """
        skills = []

        if not self.skills_dir.exists():
            return skills

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue

            content = skill_file.read_text()
            name = skill_dir.name
            description = self.extract_description(content)
            triggers = self.extract_triggers(content)

            # Create indexable text
            index_text = f"{name}: {description}"
            if triggers:
                index_text += f" Triggers: {', '.join(triggers)}"

            skills.append({
                "name": name,
                "description": description,
                "triggers": triggers,
                "index_text": index_text,
                "path": str(skill_file)
            })

        return skills

    def scan_modes(self) -> List[Dict[str, Any]]:
        """
        Scan all mode definitions.

        Returns:
            List of mode metadata dicts
        """
        modes = []

        if not self.modes_dir.exists():
            return modes

        for yaml_file in self.modes_dir.glob("*-mode.yaml"):
            content = yaml_file.read_text()
            name = yaml_file.stem.replace('-mode', '')

            # Extract description (simple YAML parsing)
            description = ""
            for line in content.split('\n'):
                if line.startswith('description:'):
                    description = line.split(':', 1)[1].strip().strip('"').strip("'")
                    break

            # Extract skills list
            skills_list = []
            in_skills = False
            for line in content.split('\n'):
                if 'skills:' in line:
                    in_skills = True
                    continue
                if in_skills:
                    if line.strip().startswith('- '):
                        skill = line.strip()[2:].strip()
                        skills_list.append(skill)
                    elif line.strip() and not line.startswith(' '):
                        in_skills = False

            index_text = f"mode {name}: {description}"
            if skills_list:
                index_text += f" Skills: {', '.join(skills_list[:5])}"

            modes.append({
                "name": name,
                "description": description,
                "skills": skills_list,
                "index_text": index_text,
                "path": str(yaml_file)
            })

        return modes

    def scan_commands(self) -> List[Dict[str, Any]]:
        """
        Scan command documentation.

        Returns:
            List of command metadata dicts
        """
        commands = []

        # Check docs/COMMANDS.md for command list
        commands_file = self.claude_dir / "docs" / "COMMANDS.md"
        if commands_file.exists():
            content = commands_file.read_text()
            # Parse markdown table rows: | `/name` | description |
            for match in re.finditer(r'^\|\s*`?/([a-z0-9-]+)`?\s*\|\s*([^|]+?)\s*\|', content, re.M):
                name = match.group(1)
                description = match.group(2).strip()
                commands.append({
                    "name": name,
                    "description": description,
                    "index_text": f"/{name}: {description}",
                    "path": str(commands_file)
                })

        return commands

    def _index_skills(self, engine) -> int:
        count = 0
        for skill in self.scan_skills():
            engine.store_embedding(
                text=skill["index_text"],
                embed_type="concept_skill",
                metadata={
                    "name": skill["name"],
                    "description": skill["description"],
                    "triggers": skill["triggers"],
                    "path": skill["path"],
                },
            )
            count += 1
        return count

    def _index_modes(self, engine) -> int:
        count = 0
        for mode in self.scan_modes():
            engine.store_embedding(
                text=mode["index_text"],
                embed_type="concept_mode",
                metadata={
                    "name": mode["name"],
                    "description": mode["description"],
                    "skills": mode.get("skills", []),
                    "path": mode["path"],
                },
            )
            count += 1
        return count

    def _index_commands(self, engine) -> int:
        count = 0
        for cmd in self.scan_commands():
            engine.store_embedding(
                text=cmd["index_text"],
                embed_type="concept_command",
                metadata={
                    "name": cmd["name"],
                    "description": cmd["description"],
                    "path": cmd.get("path", ""),
                },
            )
            count += 1
        return count

    def index_all(self) -> Dict[str, int]:
        """Index all skills, modes, and commands. Returns count dict."""
        engine = self._get_engine()
        return {
            "skills": self._index_skills(engine),
            "modes": self._index_modes(engine),
            "commands": self._index_commands(engine),
        }

    def search(
        self,
        query: str,
        concept_type: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search indexed concepts.

        Args:
            query: Search query
            concept_type: Filter by type (concept_skill, concept_mode, concept_command)
            limit: Max results

        Returns:
            List of matching concepts with scores
        """
        engine = self._get_engine()
        return engine.search_hybrid(
            query_text=query,
            embed_type=concept_type,
            limit=limit
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get indexing statistics."""
        engine = self._get_engine()
        stats = engine.get_stats()

        # Extract concept counts
        by_type = stats.get("by_type", {})
        return {
            "skills_indexed": by_type.get("concept_skill", 0),
            "modes_indexed": by_type.get("concept_mode", 0),
            "commands_indexed": by_type.get("concept_command", 0),
            "total_embeddings": stats.get("total_embeddings", 0)
        }

    def close(self):
        """Close database connection."""
        if self.engine:
            self.engine.close()


def _build_arg_parser():
    import argparse
    parser = argparse.ArgumentParser(description="GRIP Skill Indexer")
    sub = parser.add_subparsers(dest="command", help="Commands")
    sub.add_parser("index", help="Index all skills, modes, and commands")
    sub.add_parser("stats", help="Show index statistics")
    p_search = sub.add_parser("search", help="Search indexed concepts")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument(
        "--type",
        choices=["concept_skill", "concept_mode", "concept_command"],
        help="Filter by concept type",
    )
    p_search.add_argument("--limit", type=int, default=5, help="Max results")
    return parser


def _cmd_index(indexer, args):  # noqa: ARG001
    print("Indexing skills, modes, and commands...")
    counts = indexer.index_all()
    print(json.dumps({
        "status": "ok",
        "indexed": counts,
        "total": sum(counts.values()),
    }, indent=2))


def _cmd_stats(indexer, args):  # noqa: ARG001
    print(json.dumps({"status": "ok", **indexer.get_stats()}, indent=2))


def _cmd_search(indexer, args):
    print(json.dumps({
        "status": "ok",
        "query": args.query,
        "type_filter": args.type,
        "results": indexer.search(query=args.query, concept_type=args.type, limit=args.limit),
    }, indent=2))


COMMANDS = {"index": _cmd_index, "stats": _cmd_stats, "search": _cmd_search}


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    indexer = SkillIndexer()
    try:
        COMMANDS[args.command](indexer, args)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        sys.exit(1)
    finally:
        indexer.close()


if __name__ == "__main__":
    main()
