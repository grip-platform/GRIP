#!/usr/bin/env python3
"""
memory-brief.py — Compact memory digest for session-start output.

Reads KONO recalled memories + instance next_up and emits a trust-building
session-start block. Called by hooks/startup/output.sh.

Usage:
    python3 memory-brief.py --kono <json_string> --achievement <text> --project <path>

Output (stdout):
    [MEMORY] N recalled | Last: <achievement>
      -> (tag) content[:90]
      -> ...

Token budget: max 5 lines, 100 chars each = ~500 tokens
"""

import argparse
import json
import os

from path_encoding import project_dir


MAX_MEMORIES = 3
MAX_CONTENT_LEN = 90
MAX_NEXT_UP_LEN = 120


def load_next_up(project_path: str) -> str:
    """Read next_up from latest GRIP instance for this project."""
    try:
        index = project_dir(project_path) / 'grip' / 'index.json'
        if not index.exists():
            return ''
        data = json.loads(index.read_text())
        instances = data.get('instances', [])
        if not instances:
            return ''
        next_up = instances[-1].get('next_up', '')
        return str(next_up)[:MAX_NEXT_UP_LEN] if next_up else ''
    except Exception:
        return ''


def load_memory_brief_fallback(project_path: str) -> tuple:
    """Fallback: extract latest gen + achievement from MEMORY.md GRIP-BRIEF section."""
    try:
        memory_md = project_dir(project_path) / 'memory' / 'MEMORY.md'
        if not memory_md.exists():
            return ('', '')
        text = memory_md.read_text()
        # Find GRIP-BRIEF section
        start = text.find('<!-- GRIP-BRIEF-START -->')
        end = text.find('<!-- GRIP-BRIEF-END -->')
        if start < 0 or end < 0:
            return ('', '')
        brief = text[start:end]
        # Parse table rows: | 355 | RSI Fibonacci wave... |
        import re
        rows = re.findall(r'\|\s*(\d+)\s*\|\s*(.+?)\s*\|', brief)
        if not rows:
            return ('', '')
        # First data row is latest (table is ordered newest-first)
        gen, achievement = rows[0]
        return (gen.strip(), achievement.strip())
    except Exception:
        return ('', '')


def deduplicate(results: list) -> list:
    """Deduplicate memories by content fingerprint (first 60 chars)."""
    seen = set()
    unique = []
    for r in results:
        fp = str(r.get('content', ''))[:60]
        if fp not in seen:
            seen.add(fp)
            unique.append(r)
    return unique


def format_tag(tags: list) -> str:
    """Extract most informative tag label."""
    if not tags:
        return 'memory'
    priority = ['permanent', 'learned', 'session', 'checkpoint', 'global']
    for p in priority:
        if p in tags:
            return p
    return tags[0] if tags else 'memory'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kono', default='', help='KONO_RECALLED_MEMORIES JSON string')
    parser.add_argument('--achievement', default='', help='Last session achievement')
    parser.add_argument('--project', default=os.getcwd(), help='Project path')
    args = parser.parse_args()

    # Parse KONO JSON
    memories = []
    try:
        if args.kono and args.kono.strip():
            data = json.loads(args.kono)
            raw = data.get('results', [])
            memories = deduplicate(raw)[:MAX_MEMORIES]
    except Exception:
        pass

    # Load next_up
    next_up = load_next_up(args.project)

    # Fallback to MEMORY.md when all inputs empty
    if not memories and not next_up and not args.achievement:
        gen, ach = load_memory_brief_fallback(args.project)
        if gen or ach:
            brief = f'[MEMORY] Gen {gen}' if gen else '[MEMORY]'
            if ach:
                brief += f' | Last: {ach[:80]}'
            print(brief)
        return

    # Header
    count = len(memories)
    header = f'[MEMORY] {count} recalled'
    if args.achievement:
        header += f' | Last: {args.achievement[:80]}'
    print(header)

    # Memory entries
    for m in memories:
        content = str(m.get('content', ''))[:MAX_CONTENT_LEN]
        tag = format_tag(m.get('tags', []))
        # Filter out skill-registry noise (long skill descriptions aren't session context)
        if len(content) == MAX_CONTENT_LEN and ':' in content[:30]:
            continue  # Likely a skill description, skip
        print(f'  -> ({tag}) {content}')

    # next_up as last entry
    if next_up:
        print(f'  -> (next) {next_up}')


if __name__ == '__main__':
    main()
