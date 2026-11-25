#!/usr/bin/env python3
"""Session Diff Viewer — compare GRIP session states across generations/snapshots.

Compares two points in time (git refs, dates, or gen numbers) and shows
what changed: files, skills, genome fitness, and MEMORY.md sections.

Usage:
    python3 lib/session_diff_viewer.py <ref1> <ref2> [options]
    python3 lib/session_diff_viewer.py HEAD~5 HEAD
    python3 lib/session_diff_viewer.py 2026-02-20 2026-02-28
    python3 lib/session_diff_viewer.py gen:380 gen:385

Options:
    --json          Output as JSON
    --no-color      Disable ANSI colours
    --files-only    Show only file changes
    --skills-only   Show only skill changes
    --genome-only   Show only genome changes
    --memory-only   Show only MEMORY.md changes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional
from path_encoding import encode_project_path
from claude_dir import get_claude_dir


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILLS_DIR = "skills"
GENOME_PATHS = [
    "data/genome.json",
    "genome.json",
    "config/genome.json",
    "data/converge_dna.json",
]
MEMORY_PATH = f"projects/{encode_project_path(get_claude_dir())}/memory/MEMORY.md"
MEMORY_PATHS = [
    MEMORY_PATH,
    "MEMORY.md",
    "memory/MEMORY.md",
]


class ChangeKind(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FileChange:
    path: str
    kind: ChangeKind
    insertions: int = 0
    deletions: int = 0
    old_path: Optional[str] = None  # for renames


@dataclass
class SkillChange:
    name: str
    kind: ChangeKind
    files_changed: int = 0
    detail: str = ""


@dataclass
class GeneChange:
    gene: str
    kind: ChangeKind
    old_fitness: Optional[float] = None
    new_fitness: Optional[float] = None
    delta: Optional[float] = None
    dormant: bool = False


@dataclass
class MemoryChange:
    section: str
    kind: ChangeKind
    added_lines: int = 0
    removed_lines: int = 0


@dataclass
class DiffSummary:
    files_added: int = 0
    files_modified: int = 0
    files_deleted: int = 0
    total_insertions: int = 0
    total_deletions: int = 0
    skills_added: int = 0
    skills_modified: int = 0
    skills_removed: int = 0
    genes_added: int = 0
    genes_modified: int = 0
    genes_removed: int = 0
    genes_dormant: int = 0
    memory_sections_added: int = 0
    memory_sections_removed: int = 0
    memory_sections_modified: int = 0


@dataclass
class SessionDiff:
    ref_from: str
    ref_to: str
    resolved_from: str = ""
    resolved_to: str = ""
    files: list[FileChange] = field(default_factory=list)
    skills: list[SkillChange] = field(default_factory=list)
    genome: list[GeneChange] = field(default_factory=list)
    memory: list[MemoryChange] = field(default_factory=list)
    summary: DiffSummary = field(default_factory=DiffSummary)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_COLORS = {
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def _c(text: str, color: str, *, use_color: bool = True) -> str:
    if not use_color:
        return text
    prefix = _COLORS.get(color, "")
    return f"{prefix}{text}{_COLORS['reset']}" if prefix else text


def _kind_color(kind: ChangeKind) -> str:
    return {
        ChangeKind.ADDED: "green",
        ChangeKind.MODIFIED: "yellow",
        ChangeKind.DELETED: "red",
        ChangeKind.RENAMED: "cyan",
    }.get(kind, "reset")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], *, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    cmd = ["git"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or _find_repo_root(),
        timeout=30,
    )


_repo_root_cache: Optional[str] = None


def _find_repo_root() -> str:
    global _repo_root_cache
    if _repo_root_cache:
        return _repo_root_cache
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError("Not inside a git repository")
    _repo_root_cache = result.stdout.strip()
    return _repo_root_cache


def resolve_ref(ref: str, *, cwd: Optional[str] = None) -> str:
    """Resolve a ref string to a commit SHA.

    Supports:
      - Standard git refs (HEAD, branch names, tags, SHAs)
      - Date strings (YYYY-MM-DD) -> closest commit on or before that date
      - Gen numbers (gen:380) -> find commit with 'Gen 380' in message
    """
    if ref.startswith("gen:"):
        return _resolve_gen_ref(ref, cwd=cwd)
    if _looks_like_date(ref):
        return _resolve_date_ref(ref, cwd=cwd)
    return _resolve_git_ref(ref, cwd=cwd)


def _looks_like_date(ref: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", ref))


def _resolve_git_ref(ref: str, *, cwd: Optional[str] = None) -> str:
    result = _git(["rev-parse", "--verify", ref], cwd=cwd)
    if result.returncode != 0:
        raise ValueError(f"Cannot resolve git ref: {ref!r} — {result.stderr.strip()}")
    return result.stdout.strip()


def _resolve_date_ref(date_str: str, *, cwd: Optional[str] = None) -> str:
    result = _git(
        ["log", "-1", "--format=%H", f"--before={date_str}T23:59:59"],
        cwd=cwd,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(f"No commits found on or before {date_str}")
    return result.stdout.strip()


def _resolve_gen_ref(ref: str, *, cwd: Optional[str] = None) -> str:
    gen_num = ref.split(":", 1)[1]
    if not gen_num.isdigit():
        raise ValueError(f"Invalid gen number: {ref!r}")
    result = _git(
        ["log", "--all", "--format=%H", f"--grep=Gen {gen_num}", "-1"],
        cwd=cwd,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(f"No commit found matching Gen {gen_num}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Diff parsers
# ---------------------------------------------------------------------------


def parse_file_changes(ref_from: str, ref_to: str, *, cwd: Optional[str] = None) -> list[FileChange]:
    """Parse git diff --numstat between two refs."""
    result = _git(
        ["diff", "--numstat", "--diff-filter=AMDRC", "-M", ref_from, ref_to],
        cwd=cwd,
    )
    if result.returncode != 0:
        return []

    changes: list[FileChange] = []
    # Also get the name-status for kind detection
    status_result = _git(
        ["diff", "--name-status", "--diff-filter=AMDRC", "-M", ref_from, ref_to],
        cwd=cwd,
    )
    status_map: dict[str, tuple[ChangeKind, Optional[str]]] = {}
    if status_result.returncode == 0:
        for line in status_result.stdout.strip().splitlines():
            parts = line.split("\t")
            if not parts:
                continue
            code = parts[0]
            if code.startswith("R"):
                old_path = parts[1] if len(parts) > 1 else ""
                new_path = parts[2] if len(parts) > 2 else ""
                status_map[new_path] = (ChangeKind.RENAMED, old_path)
            elif code == "A":
                path = parts[1] if len(parts) > 1 else ""
                status_map[path] = (ChangeKind.ADDED, None)
            elif code == "D":
                path = parts[1] if len(parts) > 1 else ""
                status_map[path] = (ChangeKind.DELETED, None)
            elif code == "M":
                path = parts[1] if len(parts) > 1 else ""
                status_map[path] = (ChangeKind.MODIFIED, None)
            elif code == "C":
                new_path = parts[2] if len(parts) > 2 else ""
                status_map[new_path] = (ChangeKind.ADDED, None)

    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        ins_str, del_str, path = parts[0], parts[1], parts[2]
        # Handle renames: numstat shows {old => new}
        if "=>" in path:
            # e.g. "skills/{old-name => new-name}/SKILL.md"
            path = _resolve_rename_path(path)

        insertions = int(ins_str) if ins_str != "-" else 0
        deletions = int(del_str) if del_str != "-" else 0

        kind_info = status_map.get(path, (ChangeKind.MODIFIED, None))
        kind, old_path = kind_info

        changes.append(FileChange(
            path=path,
            kind=kind,
            insertions=insertions,
            deletions=deletions,
            old_path=old_path,
        ))

    return changes


def _resolve_rename_path(path: str) -> str:
    """Resolve git rename notation like 'a/{b => c}/d' to 'a/c/d'."""
    match = re.search(r"\{(.+?) => (.+?)\}", path)
    if match:
        return path[:match.start()] + match.group(2) + path[match.end():]
    # Simple "old => new" form
    if " => " in path:
        return path.split(" => ", 1)[1]
    return path


def parse_skill_changes(
    file_changes: list[FileChange],
    ref_from: str,
    ref_to: str,
    *,
    cwd: Optional[str] = None,
) -> list[SkillChange]:
    """Detect skill additions, modifications, and removals from file changes."""
    skill_files: dict[str, list[FileChange]] = {}
    for fc in file_changes:
        if fc.path.startswith(f"{SKILLS_DIR}/"):
            parts = fc.path.split("/")
            if len(parts) >= 2:
                skill_name = parts[1]
                skill_files.setdefault(skill_name, []).append(fc)

    changes: list[SkillChange] = []
    for skill_name, files in sorted(skill_files.items()):
        kinds = {f.kind for f in files}
        files_changed = len(files)

        if all(k == ChangeKind.ADDED for k in kinds):
            kind = ChangeKind.ADDED
            detail = f"{files_changed} file(s) added"
        elif all(k == ChangeKind.DELETED for k in kinds):
            kind = ChangeKind.DELETED
            detail = f"{files_changed} file(s) removed"
        else:
            kind = ChangeKind.MODIFIED
            added = sum(1 for f in files if f.kind == ChangeKind.ADDED)
            modified = sum(1 for f in files if f.kind == ChangeKind.MODIFIED)
            deleted = sum(1 for f in files if f.kind == ChangeKind.DELETED)
            parts = []
            if added:
                parts.append(f"+{added}")
            if modified:
                parts.append(f"~{modified}")
            if deleted:
                parts.append(f"-{deleted}")
            detail = ", ".join(parts)

        changes.append(SkillChange(
            name=skill_name,
            kind=kind,
            files_changed=files_changed,
            detail=detail,
        ))

    return changes


def parse_genome_changes(
    ref_from: str, ref_to: str, *, cwd: Optional[str] = None
) -> list[GeneChange]:
    """Compare genome JSON between two refs to find gene fitness changes."""
    old_genome = _load_genome_at_ref(ref_from, cwd=cwd)
    new_genome = _load_genome_at_ref(ref_to, cwd=cwd)

    if old_genome is None and new_genome is None:
        return []

    old_genes = _extract_genes(old_genome) if old_genome else {}
    new_genes = _extract_genes(new_genome) if new_genome else {}

    all_gene_names = sorted(set(old_genes.keys()) | set(new_genes.keys()))
    changes: list[GeneChange] = []

    for name in all_gene_names:
        old_info = old_genes.get(name)
        new_info = new_genes.get(name)

        if old_info is None and new_info is not None:
            changes.append(GeneChange(
                gene=name,
                kind=ChangeKind.ADDED,
                new_fitness=new_info.get("fitness"),
                dormant=new_info.get("dormant", False),
            ))
        elif old_info is not None and new_info is None:
            changes.append(GeneChange(
                gene=name,
                kind=ChangeKind.DELETED,
                old_fitness=old_info.get("fitness"),
            ))
        elif old_info is not None and new_info is not None:
            old_fit = old_info.get("fitness")
            new_fit = new_info.get("fitness")
            old_dormant = old_info.get("dormant", False)
            new_dormant = new_info.get("dormant", False)

            if old_fit != new_fit or old_dormant != new_dormant:
                delta = None
                if old_fit is not None and new_fit is not None:
                    try:
                        delta = round(float(new_fit) - float(old_fit), 4)
                    except (TypeError, ValueError):
                        delta = None
                changes.append(GeneChange(
                    gene=name,
                    kind=ChangeKind.MODIFIED,
                    old_fitness=old_fit,
                    new_fitness=new_fit,
                    delta=delta,
                    dormant=new_dormant,
                ))

    return changes


def _load_genome_at_ref(ref: str, *, cwd: Optional[str] = None) -> Optional[dict]:
    """Try to load genome JSON from a specific git ref."""
    for path in GENOME_PATHS:
        result = _git(["show", f"{ref}:{path}"], cwd=cwd)
        if result.returncode == 0 and result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                continue
    return None


def _extract_genes(genome: dict) -> dict[str, dict]:
    """Extract gene name -> info mapping from genome JSON.

    Supports multiple genome formats:
      - {"genes": {"name": {...}}}
      - {"genes": [{"name": "x", "fitness": 0.8}]}
      - {"principles": {"name": {...}}}
      - Direct dict of gene objects
    """
    genes: dict[str, dict] = {}

    # Try "genes" key
    raw = genome.get("genes", genome.get("principles", genome))

    if isinstance(raw, dict):
        for name, info in raw.items():
            if isinstance(info, dict):
                genes[name] = info
            elif isinstance(info, (int, float)):
                genes[name] = {"fitness": info}
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = item.get("name", item.get("id", ""))
                if name:
                    genes[name] = item

    return genes


def parse_memory_changes(
    ref_from: str, ref_to: str, *, cwd: Optional[str] = None
) -> list[MemoryChange]:
    """Compare MEMORY.md between two refs and identify section-level changes."""
    old_content = _load_memory_at_ref(ref_from, cwd=cwd)
    new_content = _load_memory_at_ref(ref_to, cwd=cwd)

    if old_content is None and new_content is None:
        return []

    old_sections = _parse_memory_sections(old_content or "")
    new_sections = _parse_memory_sections(new_content or "")

    all_section_names = sorted(set(old_sections.keys()) | set(new_sections.keys()))
    changes: list[MemoryChange] = []

    for name in all_section_names:
        old_lines = old_sections.get(name)
        new_lines = new_sections.get(name)

        if old_lines is None and new_lines is not None:
            changes.append(MemoryChange(
                section=name,
                kind=ChangeKind.ADDED,
                added_lines=len(new_lines),
            ))
        elif old_lines is not None and new_lines is None:
            changes.append(MemoryChange(
                section=name,
                kind=ChangeKind.DELETED,
                removed_lines=len(old_lines),
            ))
        elif old_lines is not None and new_lines is not None:
            if old_lines != new_lines:
                # Count added/removed lines (simple set diff)
                old_set = set(old_lines)
                new_set = set(new_lines)
                added = len(new_set - old_set)
                removed = len(old_set - new_set)
                changes.append(MemoryChange(
                    section=name,
                    kind=ChangeKind.MODIFIED,
                    added_lines=added,
                    removed_lines=removed,
                ))

    return changes


def _load_memory_at_ref(ref: str, *, cwd: Optional[str] = None) -> Optional[str]:
    """Try to load MEMORY.md from a specific git ref."""
    for path in MEMORY_PATHS:
        result = _git(["show", f"{ref}:{path}"], cwd=cwd)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    return None


def _parse_memory_sections(content: str) -> dict[str, list[str]]:
    """Parse MEMORY.md into sections keyed by heading."""
    sections: dict[str, list[str]] = {}
    current_section = "(preamble)"
    current_lines: list[str] = []

    for line in content.splitlines():
        if line.startswith("## ") or line.startswith("# "):
            # Save previous section
            if current_lines:
                sections[current_section] = current_lines
            current_section = line.lstrip("#").strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Save final section
    if current_lines:
        sections[current_section] = current_lines

    return sections


# ---------------------------------------------------------------------------
# Build the complete diff
# ---------------------------------------------------------------------------


def build_session_diff(
    ref_from: str,
    ref_to: str,
    *,
    cwd: Optional[str] = None,
    files_only: bool = False,
    skills_only: bool = False,
    genome_only: bool = False,
    memory_only: bool = False,
) -> SessionDiff:
    """Build a full SessionDiff comparing two refs."""
    diff = SessionDiff(ref_from=ref_from, ref_to=ref_to)

    # Resolve refs
    try:
        diff.resolved_from = resolve_ref(ref_from, cwd=cwd)
        diff.resolved_to = resolve_ref(ref_to, cwd=cwd)
    except (ValueError, RuntimeError) as exc:
        diff.error = str(exc)
        return diff

    # If same commit, no changes
    if diff.resolved_from == diff.resolved_to:
        return diff

    # Determine which sections to compute
    compute_all = not any([files_only, skills_only, genome_only, memory_only])

    # File changes (needed for skills too)
    if compute_all or files_only or skills_only:
        diff.files = parse_file_changes(diff.resolved_from, diff.resolved_to, cwd=cwd)

    # Skill changes
    if compute_all or skills_only:
        diff.skills = parse_skill_changes(
            diff.files, diff.resolved_from, diff.resolved_to, cwd=cwd
        )

    # Genome changes
    if compute_all or genome_only:
        diff.genome = parse_genome_changes(diff.resolved_from, diff.resolved_to, cwd=cwd)

    # Memory changes
    if compute_all or memory_only:
        diff.memory = parse_memory_changes(diff.resolved_from, diff.resolved_to, cwd=cwd)

    # If only skills/genome/memory requested, clear files unless files_only too
    if skills_only and not files_only and not compute_all:
        diff.files = []
    if genome_only and not files_only and not compute_all:
        diff.files = []
    if memory_only and not files_only and not compute_all:
        diff.files = []

    # Build summary
    diff.summary = _build_summary(diff)

    return diff


def _build_summary(diff: SessionDiff) -> DiffSummary:
    s = DiffSummary()
    for fc in diff.files:
        if fc.kind == ChangeKind.ADDED:
            s.files_added += 1
        elif fc.kind == ChangeKind.MODIFIED:
            s.files_modified += 1
        elif fc.kind == ChangeKind.DELETED:
            s.files_deleted += 1
        s.total_insertions += fc.insertions
        s.total_deletions += fc.deletions

    for sc in diff.skills:
        if sc.kind == ChangeKind.ADDED:
            s.skills_added += 1
        elif sc.kind == ChangeKind.MODIFIED:
            s.skills_modified += 1
        elif sc.kind == ChangeKind.DELETED:
            s.skills_removed += 1

    for gc in diff.genome:
        if gc.kind == ChangeKind.ADDED:
            s.genes_added += 1
        elif gc.kind == ChangeKind.MODIFIED:
            s.genes_modified += 1
        elif gc.kind == ChangeKind.DELETED:
            s.genes_removed += 1
        if gc.dormant:
            s.genes_dormant += 1

    for mc in diff.memory:
        if mc.kind == ChangeKind.ADDED:
            s.memory_sections_added += 1
        elif mc.kind == ChangeKind.MODIFIED:
            s.memory_sections_modified += 1
        elif mc.kind == ChangeKind.DELETED:
            s.memory_sections_removed += 1

    return s


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def session_diff_to_dict(diff: SessionDiff) -> dict:
    """Convert SessionDiff to a JSON-serializable dict."""
    d = asdict(diff)
    # Convert ChangeKind enums to strings
    _convert_enums(d)
    return d


def _convert_enums(obj):
    """Recursively convert Enum values to their .value strings."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, Enum):
                obj[k] = v.value
            elif isinstance(v, (dict, list)):
                _convert_enums(v)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, Enum):
                obj[i] = item.value
            elif isinstance(item, (dict, list)):
                _convert_enums(item)


# ---------------------------------------------------------------------------
# Terminal output formatters
# ---------------------------------------------------------------------------


def format_session_diff(diff: SessionDiff, *, use_color: bool = True) -> str:
    """Format a SessionDiff for terminal display."""
    lines: list[str] = []
    c = use_color

    # Header
    lines.append(_c("Session Diff", "bold", use_color=c))
    lines.append(
        f"  {_c(diff.ref_from, 'cyan', use_color=c)} -> "
        f"{_c(diff.ref_to, 'cyan', use_color=c)}"
    )
    if diff.resolved_from:
        lines.append(f"  {_c(diff.resolved_from[:12], 'dim', use_color=c)} -> "
                      f"{_c(diff.resolved_to[:12], 'dim', use_color=c)}")
    lines.append("")

    if diff.error:
        lines.append(_c(f"Error: {diff.error}", "red", use_color=c))
        return "\n".join(lines)

    # Files
    if diff.files:
        lines.append(_c("Files", "bold", use_color=c))
        lines.append(_c("─" * 60, "dim", use_color=c))
        for fc in diff.files:
            symbol = _kind_symbol(fc.kind)
            color = _kind_color(fc.kind)
            stat = f"+{fc.insertions}/-{fc.deletions}"
            path_display = fc.path
            if fc.old_path:
                path_display = f"{fc.old_path} -> {fc.path}"
            lines.append(
                f"  {_c(symbol, color, use_color=c)} {path_display} "
                f"{_c(stat, 'dim', use_color=c)}"
            )
        lines.append("")

    # Skills
    if diff.skills:
        lines.append(_c("Skills", "bold", use_color=c))
        lines.append(_c("─" * 60, "dim", use_color=c))
        for sc in diff.skills:
            symbol = _kind_symbol(sc.kind)
            color = _kind_color(sc.kind)
            detail = f" ({sc.detail})" if sc.detail else ""
            lines.append(
                f"  {_c(symbol, color, use_color=c)} {sc.name}{detail}"
            )
        lines.append("")

    # Genome
    if diff.genome:
        lines.append(_c("Genome", "bold", use_color=c))
        lines.append(_c("─" * 60, "dim", use_color=c))
        for gc in diff.genome:
            symbol = _kind_symbol(gc.kind)
            color = _kind_color(gc.kind)
            fitness_str = _format_fitness(gc, use_color=c)
            dormant_str = _c(" [DORMANT]", "dim", use_color=c) if gc.dormant else ""
            lines.append(
                f"  {_c(symbol, color, use_color=c)} {gc.gene} "
                f"{fitness_str}{dormant_str}"
            )
        lines.append("")

    # Memory
    if diff.memory:
        lines.append(_c("MEMORY.md", "bold", use_color=c))
        lines.append(_c("─" * 60, "dim", use_color=c))
        for mc in diff.memory:
            symbol = _kind_symbol(mc.kind)
            color = _kind_color(mc.kind)
            stat = ""
            if mc.added_lines or mc.removed_lines:
                stat = f" (+{mc.added_lines}/-{mc.removed_lines} lines)"
            lines.append(
                f"  {_c(symbol, color, use_color=c)} {mc.section}{stat}"
            )
        lines.append("")

    # Summary
    s = diff.summary
    lines.append(_c("Summary", "bold", use_color=c))
    lines.append(_c("─" * 60, "dim", use_color=c))

    if any([s.files_added, s.files_modified, s.files_deleted]):
        lines.append(
            f"  Files: "
            f"{_c(f'+{s.files_added}', 'green', use_color=c)} added, "
            f"{_c(f'~{s.files_modified}', 'yellow', use_color=c)} modified, "
            f"{_c(f'-{s.files_deleted}', 'red', use_color=c)} deleted "
            f"({s.total_insertions} ins, {s.total_deletions} del)"
        )
    if any([s.skills_added, s.skills_modified, s.skills_removed]):
        lines.append(
            f"  Skills: "
            f"{_c(f'+{s.skills_added}', 'green', use_color=c)} new, "
            f"{_c(f'~{s.skills_modified}', 'yellow', use_color=c)} modified, "
            f"{_c(f'-{s.skills_removed}', 'red', use_color=c)} removed"
        )
    if any([s.genes_added, s.genes_modified, s.genes_removed]):
        lines.append(
            f"  Genome: "
            f"{_c(f'+{s.genes_added}', 'green', use_color=c)} new, "
            f"{_c(f'~{s.genes_modified}', 'yellow', use_color=c)} changed, "
            f"{_c(f'-{s.genes_removed}', 'red', use_color=c)} removed"
            + (f", {s.genes_dormant} dormant" if s.genes_dormant else "")
        )
    if any([s.memory_sections_added, s.memory_sections_modified, s.memory_sections_removed]):
        lines.append(
            f"  Memory: "
            f"{_c(f'+{s.memory_sections_added}', 'green', use_color=c)} sections added, "
            f"{_c(f'~{s.memory_sections_modified}', 'yellow', use_color=c)} modified, "
            f"{_c(f'-{s.memory_sections_removed}', 'red', use_color=c)} removed"
        )

    if not any([diff.files, diff.skills, diff.genome, diff.memory]):
        lines.append(_c("  No changes detected.", "dim", use_color=c))

    return "\n".join(lines)


def _kind_symbol(kind: ChangeKind) -> str:
    return {
        ChangeKind.ADDED: "+",
        ChangeKind.MODIFIED: "~",
        ChangeKind.DELETED: "-",
        ChangeKind.RENAMED: ">",
    }.get(kind, "?")


def _format_fitness(gc: GeneChange, *, use_color: bool = True) -> str:
    """Format fitness change for a gene."""
    if gc.kind == ChangeKind.ADDED:
        if gc.new_fitness is not None:
            return _c(f"({gc.new_fitness})", "green", use_color=use_color)
        return ""
    if gc.kind == ChangeKind.DELETED:
        if gc.old_fitness is not None:
            return _c(f"(was {gc.old_fitness})", "red", use_color=use_color)
        return ""
    # Modified
    parts = []
    if gc.old_fitness is not None and gc.new_fitness is not None:
        parts.append(f"{gc.old_fitness} -> {gc.new_fitness}")
        if gc.delta is not None:
            sign = "+" if gc.delta >= 0 else ""
            delta_color = "green" if gc.delta >= 0 else "red"
            parts.append(_c(f"({sign}{gc.delta})", delta_color, use_color=use_color))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare GRIP session states across generations/snapshots.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s HEAD~5 HEAD
  %(prog)s 2026-02-20 2026-02-28
  %(prog)s gen:380 gen:385 --genome-only
  %(prog)s abc1234 def5678 --json
        """,
    )
    parser.add_argument("ref_from", help="Starting reference (commit, date, gen:N)")
    parser.add_argument("ref_to", help="Ending reference (commit, date, gen:N)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colours")
    parser.add_argument("--files-only", action="store_true", help="Show only file changes")
    parser.add_argument("--skills-only", action="store_true", help="Show only skill changes")
    parser.add_argument("--genome-only", action="store_true", help="Show only genome changes")
    parser.add_argument("--memory-only", action="store_true", help="Show only MEMORY.md changes")
    parser.add_argument("--repo", default=None, help="Path to git repo (default: auto-detect)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Reset repo root cache if --repo specified
    global _repo_root_cache
    if args.repo:
        _repo_root_cache = args.repo

    use_color = not args.no_color and not args.json and sys.stdout.isatty()

    try:
        diff = build_session_diff(
            args.ref_from,
            args.ref_to,
            cwd=args.repo,
            files_only=args.files_only,
            skills_only=args.skills_only,
            genome_only=args.genome_only,
            memory_only=args.memory_only,
        )
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(_c(f"Error: {exc}", "red", use_color=use_color), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(session_diff_to_dict(diff), indent=2))
    else:
        print(format_session_diff(diff, use_color=use_color))

    if diff.error:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
