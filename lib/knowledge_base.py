#!/usr/bin/env python3
"""GRIP Knowledge Base -- unified search across all GRIP data sources.

Indexes and searches across converge metrics, genome, skills, modules,
commands, memory, and tests with ranked results and source attribution.
"""

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class Source(Enum):
    """Indexable GRIP data sources."""

    CONVERGE_METRICS = "converge_metrics"
    GENOME = "genome"
    SKILLS = "skills"
    MODULES = "modules"
    COMMANDS = "commands"
    MEMORY = "memory"
    TESTS = "tests"


@dataclass
class SearchResult:
    """A single search result with source attribution."""

    query: str
    source: Source
    title: str
    snippet: str
    relevance_score: float
    file_path: str


@dataclass
class IndexEntry:
    """A single entry in the knowledge index."""

    source: Source
    title: str
    content: str
    path: str
    keywords: List[str] = field(default_factory=list)


def _tokenize(text: str) -> set:
    """Split text into lowercase word tokens.

    Handles snake_case, CamelCase, hyphens, and general whitespace.
    Strips punctuation. Returns a set of unique lowercase tokens.
    """
    # Replace common separators with spaces
    normalized = re.sub(r"[_\-/]", " ", text)
    # Split CamelCase
    normalized = re.sub(r"([a-z])([A-Z])", r"\1 \2", normalized)
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", normalized)
    # Extract word-like tokens
    words = re.findall(r"[a-zA-Z0-9]+", normalized)
    return {w.lower() for w in words if len(w) > 1}


def _compute_relevance(query_tokens: set, entry: IndexEntry) -> float:
    """Compute relevance score for an entry against query tokens.

    Algorithm:
    - Base: Jaccard similarity between query tokens and content tokens
    - Boost: +0.3 if query words appear in title
    - Boost: +0.1 for each keyword match
    """
    if not query_tokens:
        return 0.0

    content_tokens = _tokenize(entry.content)
    title_tokens = _tokenize(entry.title)
    keyword_tokens = set()
    for kw in entry.keywords:
        keyword_tokens.update(_tokenize(kw))

    # Jaccard similarity on content
    all_entry_tokens = content_tokens | title_tokens | keyword_tokens
    intersection = query_tokens & all_entry_tokens
    union = query_tokens | all_entry_tokens

    if not union:
        return 0.0

    jaccard = len(intersection) / len(union)

    # Title boost
    title_overlap = query_tokens & title_tokens
    title_boost = 0.3 if title_overlap else 0.0

    # Keyword boost
    keyword_overlap = query_tokens & keyword_tokens
    keyword_boost = len(keyword_overlap) * 0.1

    return jaccard + title_boost + keyword_boost


class KnowledgeIndex:
    """Unified search index across all GRIP data sources."""

    def __init__(self, grip_dir: Optional[str] = None):
        """Initialize the knowledge index.

        Args:
            grip_dir: Root GRIP directory. Defaults to ~/.claude.
        """
        if grip_dir is None:
            grip_dir = os.path.join(os.environ.get("HOME", ""), ".claude")
        self.grip_dir = str(Path(grip_dir).resolve())
        self._entries: List[IndexEntry] = []

    @property
    def entries(self) -> List[IndexEntry]:
        """Return all index entries."""
        return list(self._entries)

    def clear(self) -> None:
        """Clear all index entries."""
        self._entries = []

    def _add_entry(self, entry: IndexEntry) -> None:
        """Add an entry to the index."""
        self._entries.append(entry)

    def index_converge_metrics(self, jsonl_path: Optional[str] = None) -> int:
        """Index converge metrics from JSONL files.

        Indexes run tasks and outcomes from converge metric logs.

        Args:
            jsonl_path: Path to JSONL file or directory. Defaults to
                        data/converge-metrics/ under grip_dir.

        Returns:
            Number of entries indexed.
        """
        if jsonl_path is None:
            jsonl_path = os.path.join(self.grip_dir, "data", "converge-metrics")

        count = 0
        paths_to_index: List[str] = []

        path_obj = Path(jsonl_path)
        if path_obj.is_file() and path_obj.suffix == ".jsonl":
            paths_to_index.append(str(path_obj))
        elif path_obj.is_dir():
            for entry in sorted(path_obj.iterdir()):
                if entry.is_file() and entry.suffix == ".jsonl":
                    paths_to_index.append(str(entry))

        for fpath in paths_to_index:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        task = record.get("task", record.get("category", ""))
                        outcome = record.get("outcome", record.get("detail", ""))
                        title = str(task)[:100] if task else Path(fpath).stem
                        content_parts = []
                        for key in ("task", "outcome", "category", "detail",
                                    "criterion", "type", "severity"):
                            val = record.get(key)
                            if val:
                                content_parts.append(f"{key}: {val}")
                        content = " | ".join(content_parts) if content_parts else str(record)

                        keywords = []
                        if record.get("category"):
                            keywords.append(str(record["category"]))
                        if record.get("type"):
                            keywords.append(str(record["type"]))

                        self._add_entry(IndexEntry(
                            source=Source.CONVERGE_METRICS,
                            title=title,
                            content=content,
                            path=fpath,
                            keywords=keywords,
                        ))
                        count += 1
            except (OSError, UnicodeDecodeError):
                continue

        return count

    def index_skills(self, skills_dir: Optional[str] = None) -> int:
        """Index SKILL.md files from the skills directory.

        Parses YAML frontmatter for name/description and indexes
        the full skill content.

        Args:
            skills_dir: Path to skills directory. Defaults to skills/ under grip_dir.

        Returns:
            Number of entries indexed.
        """
        if skills_dir is None:
            skills_dir = os.path.join(self.grip_dir, "skills")

        skills_path = Path(skills_dir)
        if not skills_path.is_dir():
            return 0

        count = 0
        for skill_dir in sorted(skills_path.iterdir()):
            if not skill_dir.is_dir():
                continue
            # Skip archived skills
            if skill_dir.name.startswith("_"):
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue

            try:
                content = skill_md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Parse YAML frontmatter
            name = skill_dir.name
            description = ""
            keywords = [name]

            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1]
                    for line in frontmatter.split("\n"):
                        line = line.strip()
                        if line.startswith("name:"):
                            name = line[5:].strip()
                        elif line.startswith("description:"):
                            description = line[12:].strip()
                        elif line.startswith("invocable_name:"):
                            keywords.append(line[15:].strip())

            title = name
            # Use description + first 500 chars of content for indexing
            snippet = description + " " + content[:500] if description else content[:500]

            self._add_entry(IndexEntry(
                source=Source.SKILLS,
                title=title,
                content=snippet,
                path=str(skill_md),
                keywords=keywords,
            ))
            count += 1

        return count

    def index_modules(self, lib_dir: Optional[str] = None) -> int:
        """Index Python modules from the lib directory using AST.

        Extracts module docstrings, function names, class names.
        Does not import any modules.

        Args:
            lib_dir: Path to lib directory. Defaults to lib/ under grip_dir.

        Returns:
            Number of entries indexed.
        """
        if lib_dir is None:
            lib_dir = os.path.join(self.grip_dir, "lib")

        lib_path = Path(lib_dir)
        if not lib_path.is_dir():
            return 0

        count = 0
        for entry in sorted(lib_path.iterdir()):
            if not entry.is_file() or entry.suffix != ".py":
                continue
            if entry.name.startswith("_"):
                continue

            try:
                source_code = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            try:
                tree = ast.parse(source_code, filename=str(entry))
            except SyntaxError:
                continue

            module_name = entry.stem
            docstring = ast.get_docstring(tree) or ""

            # Collect function and class names as keywords
            keywords = [module_name]
            content_parts = [f"module: {module_name}"]
            if docstring:
                content_parts.append(docstring)

            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        keywords.append(node.name)
                        func_doc = ast.get_docstring(node)
                        if func_doc:
                            content_parts.append(
                                f"{node.name}: {func_doc.split(chr(10))[0]}"
                            )
                        else:
                            content_parts.append(node.name)
                elif isinstance(node, ast.ClassDef):
                    keywords.append(node.name)
                    cls_doc = ast.get_docstring(node)
                    if cls_doc:
                        content_parts.append(
                            f"{node.name}: {cls_doc.split(chr(10))[0]}"
                        )
                    else:
                        content_parts.append(node.name)

            self._add_entry(IndexEntry(
                source=Source.MODULES,
                title=module_name,
                content=" | ".join(content_parts),
                path=str(entry),
                keywords=keywords,
            ))
            count += 1

        return count

    def index_memory(self, memory_path: Optional[str] = None) -> int:
        """Index MEMORY.md sections.

        Splits memory into sections by ## headers and indexes each
        as a separate entry.

        Args:
            memory_path: Path to MEMORY.md. Defaults to
                         projects/*/memory/MEMORY.md under grip_dir.

        Returns:
            Number of entries indexed.
        """
        paths_to_index: List[str] = []

        if memory_path is not None:
            paths_to_index.append(memory_path)
        else:
            # Search for MEMORY.md files in projects directories
            projects_dir = Path(self.grip_dir) / "projects"
            if projects_dir.is_dir():
                for project_dir in projects_dir.iterdir():
                    if not project_dir.is_dir():
                        continue
                    mem_file = project_dir / "memory" / "MEMORY.md"
                    if mem_file.is_file():
                        paths_to_index.append(str(mem_file))

            # Also check root-level MEMORY.md
            root_mem = Path(self.grip_dir) / "MEMORY.md"
            if root_mem.is_file():
                paths_to_index.append(str(root_mem))

        count = 0
        for fpath in paths_to_index:
            try:
                content = Path(fpath).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Split by ## headers
            sections = re.split(r"(?m)^## ", content)
            for section in sections:
                section = section.strip()
                if not section:
                    continue

                lines = section.split("\n", 1)
                title = lines[0].strip().lstrip("#").strip()
                body = lines[1].strip() if len(lines) > 1 else ""

                if not title:
                    continue

                # Extract keywords from section content
                keywords = []
                # Look for key terms (capitalized words, code references)
                for word in re.findall(r"\b[A-Z][a-zA-Z_]+\b", title + " " + body[:200]):
                    if len(word) > 2:
                        keywords.append(word)

                self._add_entry(IndexEntry(
                    source=Source.MEMORY,
                    title=title,
                    content=body[:1000] if body else title,
                    path=fpath,
                    keywords=keywords[:20],
                ))
                count += 1

        return count

    def index_tests(self, tests_dir: Optional[str] = None) -> int:
        """Index test files by test function/class names.

        Uses AST to extract test names without importing.

        Args:
            tests_dir: Path to tests directory. Defaults to tests/ under grip_dir.

        Returns:
            Number of entries indexed.
        """
        if tests_dir is None:
            tests_dir = os.path.join(self.grip_dir, "tests")

        tests_path = Path(tests_dir)
        if not tests_path.is_dir():
            return 0

        count = 0
        for entry in sorted(tests_path.iterdir()):
            if not entry.is_file() or entry.suffix != ".py":
                continue
            if not entry.name.startswith("test_"):
                continue

            try:
                source_code = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            try:
                tree = ast.parse(source_code, filename=str(entry))
            except SyntaxError:
                continue

            test_name = entry.stem
            docstring = ast.get_docstring(tree) or ""
            keywords = [test_name]
            test_names = []

            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("test_"):
                        test_names.append(node.name)
                        keywords.append(node.name)
                elif isinstance(node, ast.ClassDef):
                    if node.name.startswith("Test"):
                        keywords.append(node.name)
                        for method in ast.iter_child_nodes(node):
                            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                if method.name.startswith("test_"):
                                    test_names.append(f"{node.name}.{method.name}")
                                    keywords.append(method.name)

            content_parts = [f"test file: {test_name}"]
            if docstring:
                content_parts.append(docstring)
            if test_names:
                content_parts.append("tests: " + ", ".join(test_names))

            self._add_entry(IndexEntry(
                source=Source.TESTS,
                title=test_name,
                content=" | ".join(content_parts),
                path=str(entry),
                keywords=keywords,
            ))
            count += 1

        return count

    def index_genome(self, genome_dir: Optional[str] = None) -> int:
        """Index genome DNA files.

        Parses genome YAML/JSON files for principle definitions.

        Args:
            genome_dir: Path to genome directory. Defaults to genome/ under grip_dir.

        Returns:
            Number of entries indexed.
        """
        if genome_dir is None:
            genome_dir = os.path.join(self.grip_dir, "genome")

        genome_path = Path(genome_dir)
        if not genome_path.is_dir():
            return 0

        count = 0
        for entry in sorted(genome_path.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix not in (".json", ".yaml", ".yml", ".md"):
                continue

            try:
                content = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            title = entry.stem
            keywords = [title]

            if entry.suffix == ".json":
                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        for key in data:
                            keywords.append(str(key))
                        content = json.dumps(data, indent=0)[:1000]
                except json.JSONDecodeError:
                    pass

            self._add_entry(IndexEntry(
                source=Source.GENOME,
                title=title,
                content=content[:1000],
                path=str(entry),
                keywords=keywords,
            ))
            count += 1

        return count

    def index_commands(self, commands_dir: Optional[str] = None) -> int:
        """Index GRIP commands from commands directory or CLAUDE.md references.

        Args:
            commands_dir: Path to commands directory. Defaults to commands/ under grip_dir.

        Returns:
            Number of entries indexed.
        """
        if commands_dir is None:
            commands_dir = os.path.join(self.grip_dir, "commands")

        commands_path = Path(commands_dir)
        if not commands_path.is_dir():
            return 0

        count = 0
        for entry in sorted(commands_path.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix not in (".md", ".sh", ".py"):
                continue

            try:
                content = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            title = entry.stem
            keywords = [title]

            # Extract description from first line or frontmatter
            description = ""
            lines = content.strip().split("\n")
            for line in lines[:10]:
                line = line.strip()
                if line.startswith("description:"):
                    description = line[12:].strip()
                    break
                elif line.startswith("#") and not description:
                    description = line.lstrip("#").strip()

            self._add_entry(IndexEntry(
                source=Source.COMMANDS,
                title=title,
                content=description + " " + content[:500] if description else content[:500],
                path=str(entry),
                keywords=keywords,
            ))
            count += 1

        return count

    def build(self) -> Dict[str, int]:
        """Run all indexers and return counts per source.

        Returns:
            Dictionary mapping source name to entry count.
        """
        self.clear()
        counts = {}
        counts["converge_metrics"] = self.index_converge_metrics()
        counts["genome"] = self.index_genome()
        counts["skills"] = self.index_skills()
        counts["modules"] = self.index_modules()
        counts["commands"] = self.index_commands()
        counts["memory"] = self.index_memory()
        counts["tests"] = self.index_tests()
        return counts

    def search(self, query: str, top_k: int = 10) -> List[SearchResult]:
        """Search across all indexed sources.

        Tokenizes the query, computes relevance against all entries,
        and returns the top_k results sorted by score descending.

        Args:
            query: Search query string.
            top_k: Maximum number of results to return.

        Returns:
            List of SearchResult objects sorted by relevance.
        """
        if not query or not query.strip():
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scored: List[tuple] = []
        for entry in self._entries:
            score = _compute_relevance(query_tokens, entry)
            if score > 0:
                snippet = entry.content[:200].replace("\n", " ").strip()
                scored.append((score, SearchResult(
                    query=query,
                    source=entry.source,
                    title=entry.title,
                    snippet=snippet,
                    relevance_score=round(score, 4),
                    file_path=entry.path,
                )))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [result for _, result in scored[:top_k]]

    def search_source(
        self, query: str, source: Source, top_k: int = 5
    ) -> List[SearchResult]:
        """Search within a single source type.

        Args:
            query: Search query string.
            source: Source type to search within.
            top_k: Maximum number of results to return.

        Returns:
            List of SearchResult objects from the specified source.
        """
        if not query or not query.strip():
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scored: List[tuple] = []
        for entry in self._entries:
            if entry.source != source:
                continue
            score = _compute_relevance(query_tokens, entry)
            if score > 0:
                snippet = entry.content[:200].replace("\n", " ").strip()
                scored.append((score, SearchResult(
                    query=query,
                    source=entry.source,
                    title=entry.title,
                    snippet=snippet,
                    relevance_score=round(score, 4),
                    file_path=entry.path,
                )))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [result for _, result in scored[:top_k]]

    def stats(self) -> Dict[str, int]:
        """Return index statistics: entries per source.

        Returns:
            Dictionary mapping source name to entry count.
        """
        counts: Dict[str, int] = {}
        for source in Source:
            counts[source.value] = 0
        for entry in self._entries:
            counts[entry.source.value] += 1
        counts["total"] = len(self._entries)
        return counts


def _build_cli_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="knowledge_base",
        description="GRIP Knowledge Base -- unified search across all data sources",
    )
    parser.add_argument(
        "--grip-dir",
        default=None,
        help="Path to GRIP root directory (default: ~/.claude)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # search
    search_cmd = subparsers.add_parser("search", help="Search across all sources")
    search_cmd.add_argument("query", nargs="+", help="Search query")
    search_cmd.add_argument(
        "-k", "--top-k", type=int, default=10, help="Max results (default: 10)"
    )
    search_cmd.add_argument(
        "-s", "--source", default=None,
        choices=[s.value for s in Source],
        help="Restrict search to a single source",
    )

    # sources
    subparsers.add_parser("sources", help="List available data sources")

    # stats
    subparsers.add_parser("stats", help="Show index statistics")

    # refresh
    subparsers.add_parser("refresh", help="Rebuild the index")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    grip_dir = args.grip_dir
    index = KnowledgeIndex(grip_dir=grip_dir)

    if args.command == "sources":
        print("Available GRIP data sources:")
        for source in Source:
            print(f"  {source.value}")
        return 0

    # All other commands need the index built
    counts = index.build()

    if args.command == "stats":
        stats = index.stats()
        print("GRIP Knowledge Base Statistics")
        print(f"  Total entries: {stats['total']}")
        for source in Source:
            print(f"  {source.value}: {stats[source.value]}")
        return 0

    if args.command == "refresh":
        print("Index rebuilt.")
        total = sum(counts.values())
        print(f"  {total} entries indexed across {len(Source)} sources.")
        for name, count in counts.items():
            if count > 0:
                print(f"  {name}: {count}")
        return 0

    if args.command == "search":
        query = " ".join(args.query)
        if args.source:
            source = Source(args.source)
            results = index.search_source(query, source, top_k=args.top_k)
        else:
            results = index.search(query, top_k=args.top_k)

        if not results:
            print(f"No results for '{query}'.")
            return 0

        for i, result in enumerate(results, 1):
            print(f"{i}. [{result.source.value}] {result.title} "
                  f"(score: {result.relevance_score})")
            print(f"   {result.snippet[:120]}")
            print(f"   {result.file_path}")
            print()

        print(f"{len(results)} result(s) for '{query}'.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
