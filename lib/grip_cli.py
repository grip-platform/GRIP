#!/usr/bin/env python3
"""GRIP CLI -- Unified command router for all GRIP tools.

Dispatches to GRIP lib/ modules via subcommands with short aliases.
Levenshtein-based did-you-mean on typos, categorised help display.

Usage:
    python3 lib/grip_cli.py <command> [args]
    python3 lib/grip_cli.py --help
    python3 lib/grip_cli.py --version
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VERSION = "6.0.0"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CLICommand:
    """A registered CLI command."""

    name: str
    aliases: List[str] = field(default_factory=list)
    module_path: str = ""
    description: str = ""
    category: str = "General"

    @property
    def all_names(self) -> List[str]:
        """Return name + all aliases."""
        return [self.name] + self.aliases


class CLIRegistry:
    """Registry of CLI commands with lookup by name or alias."""

    def __init__(self) -> None:
        self.commands: Dict[str, CLICommand] = {}
        self._alias_map: Dict[str, str] = {}

    def register(self, cmd: CLICommand) -> None:
        """Register a command. Raises ValueError on duplicate name/alias."""
        if cmd.name in self.commands or cmd.name in self._alias_map:
            raise ValueError(f"Duplicate command name: {cmd.name}")
        for alias in cmd.aliases:
            if alias in self._alias_map or alias in self.commands:
                raise ValueError(f"Duplicate alias: {alias}")
        self.commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._alias_map[alias] = cmd.name

    def lookup(self, name: str) -> Optional[CLICommand]:
        """Find command by name or alias. Returns None if not found."""
        if name in self.commands:
            return self.commands[name]
        canonical = self._alias_map.get(name)
        if canonical:
            return self.commands[canonical]
        return None

    @property
    def categories(self) -> Dict[str, List[CLICommand]]:
        """Group commands by category, sorted alphabetically within each."""
        cats: Dict[str, List[CLICommand]] = {}
        for cmd in self.commands.values():
            cats.setdefault(cmd.category, []).append(cmd)
        for cmds in cats.values():
            cmds.sort(key=lambda c: c.name)
        return cats

    @property
    def all_names(self) -> List[str]:
        """Every valid name and alias."""
        names = list(self.commands.keys())
        names.extend(self._alias_map.keys())
        return sorted(names)


# ---------------------------------------------------------------------------
# Levenshtein edit distance (no external deps)
# ---------------------------------------------------------------------------

def levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Cost: 0 if chars match, 1 otherwise
            cost = 0 if c1 == c2 else 1
            curr_row.append(
                min(
                    curr_row[j] + 1,       # insert
                    prev_row[j + 1] + 1,   # delete
                    prev_row[j] + cost,     # replace
                )
            )
        prev_row = curr_row
    return prev_row[-1]


def suggest_command(typo: str, registry: CLIRegistry, max_results: int = 3) -> List[str]:
    """Return up to *max_results* closest command names for a typo."""
    scored: List[Tuple[int, str]] = []
    for name in registry.all_names:
        dist = levenshtein(typo, name)
        scored.append((dist, name))
    scored.sort(key=lambda t: (t[0], t[1]))
    # Only suggest if distance is reasonable (at most half the typo length + 2)
    threshold = max(len(typo) // 2 + 2, 3)
    return [name for dist, name in scored[:max_results] if dist <= threshold]


# ---------------------------------------------------------------------------
# Help & version
# ---------------------------------------------------------------------------

CATEGORY_ORDER = [
    "Diagnostics",
    "Analytics",
    "Session",
    "Convergence",
    "Infrastructure",
]


def format_help(registry: CLIRegistry) -> str:
    """Render categorised help text."""
    lines = [
        "GRIP CLI -- Unified Command Router",
        "===================================",
        "",
    ]
    cats = registry.categories
    # Emit in preferred order, then any remaining
    emitted: set = set()
    for cat_name in CATEGORY_ORDER:
        if cat_name in cats:
            _emit_category(lines, cat_name, cats[cat_name])
            emitted.add(cat_name)
    for cat_name in sorted(cats.keys()):
        if cat_name not in emitted:
            _emit_category(lines, cat_name, cats[cat_name])

    lines.append("")
    lines.append("Usage: python3 lib/grip_cli.py <command> [args]")
    lines.append("       python3 lib/grip_cli.py <command> --help")
    return "\n".join(lines)


def _emit_category(lines: List[str], name: str, cmds: List[CLICommand]) -> None:
    lines.append(f"{name}:")
    for cmd in cmds:
        alias_str = ", ".join(cmd.aliases) if cmd.aliases else ""
        label = f"  {cmd.name}"
        if alias_str:
            label += f" ({alias_str})"
        padding = max(1, 28 - len(label))
        lines.append(f"{label}{' ' * padding}{cmd.description}")
    lines.append("")


def format_version() -> str:
    """Return GRIP version string."""
    return f"GRIP CLI v{VERSION}"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def resolve_module_path(module_path: str) -> str:
    """Resolve module_path relative to the GRIP project root."""
    grip_dir = os.environ.get("GRIP_DIR", os.path.join(os.path.expanduser("~"), ".claude"))
    return os.path.join(grip_dir, module_path)


def dispatch(command: str, args: List[str], registry: CLIRegistry) -> int:
    """Look up *command* and exec its module. Returns exit code."""
    cmd = registry.lookup(command)
    if cmd is None:
        suggestions = suggest_command(command, registry)
        msg = f"grip: unknown command '{command}'"
        if suggestions:
            msg += f"\n\nDid you mean?\n"
            for s in suggestions:
                msg += f"  {s}\n"
        print(msg, file=sys.stderr)
        return 127

    module = resolve_module_path(cmd.module_path)
    if not os.path.isfile(module):
        print(f"grip: module not found: {cmd.module_path}", file=sys.stderr)
        return 126

    result = subprocess.run(
        [sys.executable, module] + args,
        cwd=os.getcwd(),
    )
    return result.returncode


# ---------------------------------------------------------------------------
# Built-in command registry
# ---------------------------------------------------------------------------

def build_default_registry() -> CLIRegistry:
    """Construct the registry with all known GRIP commands."""
    reg = CLIRegistry()
    commands = [
        # Diagnostics
        CLICommand("doctor", ["doc"], "lib/grip_doctor.py",
                   "System health diagnostic", "Diagnostics"),
        CLICommand("health", ["h"], "lib/session_health.py",
                   "Session health monitor", "Diagnostics"),
        CLICommand("autopsy", ["au"], "lib/session_autopsy.py",
                   "Session post-mortem", "Diagnostics"),

        # Analytics
        CLICommand("metrics", ["m"], "lib/converge_metrics.py",
                   "Convergence metrics", "Analytics"),
        CLICommand("forecast", ["fc"], "lib/converge_forecast.py",
                   "Pre-run forecasting", "Analytics"),
        CLICommand("scorecard", ["sc"], "lib/sprint_scorecard.py",
                   "Sprint scorecard", "Analytics"),
        CLICommand("landscape", ["ls"], "lib/fitness_landscape.py",
                   "Gene fitness landscape", "Analytics"),

        # Session
        CLICommand("context", ["ctx"], "lib/context_optimizer.py",
                   "Context budget analysis", "Session"),
        CLICommand("planner", ["pl"], "lib/session_planner.py",
                   "Session planning", "Session"),
        CLICommand("workflow", ["wf"], "lib/workflow_automator.py",
                   "Workflow automation", "Session"),
        CLICommand("tests", ["t"], "lib/test_discovery.py",
                   "Test discovery and runner", "Session"),

        # Convergence
        CLICommand("storyteller", ["st"], "lib/converge_storyteller.py",
                   "Run narratives", "Convergence"),
        CLICommand("cookbook", ["cb"], "lib/converge_cookbook.py",
                   "Convergence recipes", "Convergence"),
        CLICommand("replay", ["rp"], "lib/converge_replay.py",
                   "Convergence replay", "Convergence"),
        CLICommand("diff", ["d"], "lib/converge_diff.py",
                   "Convergence diff viewer", "Convergence"),
        CLICommand("ab-test", ["ab"], "lib/converge_ab_test.py",
                   "A/B testing", "Convergence"),
        CLICommand("meta", ["ma"], "lib/converge_meta.py",
                   "Meta-analysis", "Convergence"),

        # Infrastructure
        CLICommand("export", ["ex"], "lib/export_formatter.py",
                   "Data export", "Infrastructure"),
        CLICommand("registry", ["reg"], "lib/module_registry.py",
                   "Module registry", "Infrastructure"),
        CLICommand("knowledge", ["kb"], "lib/knowledge_base.py",
                   "Knowledge base search", "Infrastructure"),
        CLICommand("changelog", ["cl"], "lib/changelog_generator.py",
                   "Changelog generator", "Infrastructure"),
    ]
    for cmd in commands:
        reg.register(cmd)
    return reg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns exit code."""
    if argv is None:
        argv = sys.argv[1:]

    registry = build_default_registry()

    if not argv or argv[0] in ("--help", "-h"):
        print(format_help(registry))
        return 0

    if argv[0] == "--version":
        print(format_version())
        return 0

    command = argv[0]
    args = argv[1:]
    return dispatch(command, args, registry)


if __name__ == "__main__":
    sys.exit(main())
