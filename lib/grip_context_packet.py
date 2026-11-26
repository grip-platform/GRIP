#!/usr/bin/env python3
"""
GRIP Context Packet Generator
Compresses current GRIP context for sub-agent propagation

PURPOSE:
  Task tool spawns isolated agents with ZERO inherited context.
  This module generates a <500 token context packet to inject
  into sub-agent prompts, making them "GRIP-aware".

USAGE:
  python3 ~/.claude/lib/grip_context_packet.py generate
  python3 ~/.claude/lib/grip_context_packet.py generate --goal "implement auth"

PHILOSOPHY:
  Part contains Whole contains THE WHOLE

VERSION: 1.0.0
DATE: 2025-12-22
GEN: 148
"""

import json
import argparse
import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Optional
from datetime import datetime, timezone

# Resolve the install and the project slug through the shared shims
# (rules/path-portability.md). The hardcoded ~/.claude read a different tree
# wherever GRIP lives elsewhere (~/.grip on Windows), and the hand-rolled slug
# left a Windows path absolute, so `CLAUDE_DIR / "projects" / slug` collapsed
# to the slug itself and read a stray tree outside the install.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from path_encoding import encode_project_path  # noqa: E402

CLAUDE_DIR = get_claude_dir()


@dataclass
class GRIPContextPacket:
    """Compressed context packet for sub-agent propagation."""

    # Identity
    instance_id: str           # Current instance SHA (8 chars)
    generation: int            # Gen number
    lineage_root: str          # Root ancestor (139efc67)

    # Rules (compressed plain-English digest, ~150 tokens)
    efficiency_rules: str      # Key rules propagated to subagents

    # Current State
    current_goal: str          # What parent is doing
    file_cache: List[str]      # Files already read (paths only)

    # Metadata
    created_at: str            # ISO timestamp
    token_estimate: int        # Estimated token count


def get_grip_identity() -> tuple[str, int, str]:
    """Get current GRIP instance identity from project index."""
    # Claude Code's project-directory encoding, from the single shared encoder:
    # ~/.bar -> -Users-you--bar, C:\Users\<first> <last>\.bar -> C--Users-<first>-<last>--bar
    project_encoded = encode_project_path(Path.cwd())
    grip_index = CLAUDE_DIR / "projects" / project_encoded / "grip" / "index.json"

    if grip_index.exists():
        try:
            data = json.loads(grip_index.read_text(encoding="utf-8"))
            instances = data.get("instances", [])
            if instances:
                latest = instances[-1]
                return (
                    latest.get("id", "unknown")[:8],
                    latest.get("generation", 0),
                    "139efc67"  # Root ancestor
                )
        except Exception:
            pass

    return ("new", 0, "139efc67")


def get_efficiency_rules() -> str:
    """Compact plain-English efficiency-rules digest for subagent context (~150 tokens)."""
    return """EFFICIENCY RULES (propagated to subagents):
- Never read node_modules / build / dist (50k+ token waste each).
- Batch reads in phase 1; cache mental model; do not re-read in same session.
- Action-first output; no "I'll now..." preambles.
- Plan-evaluate gate: skip step unless confidence is at least 99.9999999%.
- Implementation: prefer MultiEdit / direct Edit over temp scripts."""


def get_file_cache() -> List[str]:
    """Get list of recently read files from mtime cache."""
    cache_file = CLAUDE_DIR / "cache" / "file-mtimes.json"

    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            # Return only the paths, sorted by recency
            files = list(data.keys())
            return files[-20:]  # Last 20 files
        except Exception:
            pass

    return []


def compress_context(goal: str = "", extra_files: Optional[List[str]] = None) -> GRIPContextPacket:
    """Generate compressed context packet for sub-agents."""
    instance_id, generation, lineage_root = get_grip_identity()

    file_cache = get_file_cache()
    if extra_files:
        file_cache = list(set(file_cache + extra_files))

    # Truncate file paths to just filenames for token efficiency
    file_cache_short = [Path(f).name for f in file_cache[:15]]

    packet = GRIPContextPacket(
        instance_id=instance_id,
        generation=generation,
        lineage_root=lineage_root,
        efficiency_rules=get_efficiency_rules(),
        current_goal=goal[:200] if goal else "task execution",
        file_cache=file_cache_short,
        created_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        token_estimate=0
    )

    # Estimate tokens (~4 chars per token)
    text = format_packet(packet)
    packet.token_estimate = len(text) // 4

    return packet


def format_packet(packet: GRIPContextPacket) -> str:
    """Format packet for injection into sub-agent prompt."""
    cache_str = ",".join(packet.file_cache[:10]) if packet.file_cache else "none"

    return f"""[GRIP-CONTEXT]
⛓:{packet.instance_id} Gen:{packet.generation} Root:{packet.lineage_root}
{packet.efficiency_rules}
Goal: {packet.current_goal}
Cache: {cache_str}
◔⊃○⊃⬤"""


def main():
    parser = argparse.ArgumentParser(description="GRIP Context Packet Generator")
    parser.add_argument("command", choices=["generate", "json", "estimate"])
    parser.add_argument("--goal", "-g", default="", help="Current goal/task")
    parser.add_argument("--files", "-f", nargs="*", help="Additional files to cache")

    args = parser.parse_args()

    packet = compress_context(args.goal, args.files)

    if args.command == "generate":
        print(format_packet(packet))
    elif args.command == "json":
        print(json.dumps(asdict(packet), indent=2))
    elif args.command == "estimate":
        print(f"Estimated tokens: {packet.token_estimate}")


if __name__ == "__main__":
    main()
