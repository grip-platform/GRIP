#!/usr/bin/env python3
"""Context-map boundary enforcement for PreToolUse hooks.

When a file is being edited, checks if a context-map rule exists for that
file's feature area. If so, returns the rule content as additionalContext
so the model has architectural boundaries in context.

RC-05 council-approved. This is ADVISORY (additionalContext injection),
not BLOCKING (no deny). The model sees the rules; enforcement is through
awareness, not mechanical gates.

Usage from hooks:
    from context_map_enforcer import get_context_for_file

    context = get_context_for_file("/path/to/file.py", rules_dir)
    if context:
        # Inject as additionalContext in hook response
"""
import os
import re
from pathlib import Path


def _parse_paths_from_frontmatter(content: str) -> list[str]:
    """Extract paths: list from YAML frontmatter."""
    in_frontmatter = False
    in_paths = False
    paths = []
    for line in content.split("\n"):
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            break
        if not in_frontmatter:
            continue
        if line.strip().startswith("paths:"):
            in_paths = True
            continue
        if in_paths:
            m = re.match(r'\s+-\s+"?([^"]+)"?', line)
            if m:
                paths.append(m.group(1))
            elif not line.strip().startswith("-"):
                in_paths = False
    return paths


def _glob_matches(pattern: str, filepath: str) -> bool:
    """Simple glob matching: ** = any path, * = any segment."""
    parts = pattern.replace("**", "DOUBLESTAR").split("*")
    regex = ".*".join(re.escape(p).replace("DOUBLESTAR", ".*") for p in parts)
    return bool(re.match(regex, filepath))


def get_context_for_file(filepath: str, rules_dir: str = None) -> str:
    """Find and return context-map content for a file path.

    Returns empty string if no matching context-map rule exists.
    """
    if rules_dir is None:
        rules_dir = _find_rules_dir(filepath)
    if not rules_dir or not os.path.isdir(rules_dir):
        return ""

    for rule_file in Path(rules_dir).glob("*.md"):
        content = rule_file.read_text()
        paths = _parse_paths_from_frontmatter(content)
        if any(_glob_matches(p, filepath) for p in paths):
            return _compact_context(content, rule_file.stem)
    return ""


def _find_rules_dir(filepath: str) -> str:
    """Walk up from filepath to find .claude/rules/features/."""
    current = Path(filepath).resolve().parent
    for _ in range(10):
        candidate = current / ".claude" / "rules" / "features"
        if candidate.is_dir():
            return str(candidate)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return ""


def _compact_context(content: str, feature_name: str) -> str:
    """Extract key enforcement points from a context-map rule."""
    lines = []
    lines.append(f"[CONTEXT-MAP: {feature_name}]")

    in_section = ""
    for line in content.split("\n"):
        if line.startswith("## State Mutations"):
            in_section = "mutations"
        elif line.startswith("## Edge Cases"):
            in_section = "edges"
        elif line.startswith("## "):
            in_section = ""
        elif in_section and line.strip().startswith("-"):
            lines.append(line.strip())

    if len(lines) == 1:
        return ""
    return " ".join(lines)
