#!/usr/bin/env python3
"""
grip_transfer.py — Session context transfer for cross-session/cross-device handoff.

Assembles a self-contained markdown payload from existing GRIP subsystems
and delivers it via clipboard (pbcopy), iCloud Drive, or stdout.

Usage:
    python3 lib/grip_transfer.py                          # clipboard (default)
    python3 lib/grip_transfer.py --topic "billing"        # filtered transfer
    python3 lib/grip_transfer.py --icloud                 # iCloud Drive (no timeout)
    python3 lib/grip_transfer.py --stdout                 # pipe to stdout

Called by: /transfer command, /handoff command
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
LIB_DIR = CLAUDE_DIR / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


# ---------------------------------------------------------------------------
# Safe imports (graceful degradation per subsystem)
# ---------------------------------------------------------------------------

def _import_module(name):
    """Import a module, returning None on failure."""
    try:
        return __import__(name)
    except ImportError:
        return None


def _safe(fn, *args, **kwargs):
    """Call fn, return None on any error."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Section builders (each returns Optional[str] — a markdown section or None)
# ---------------------------------------------------------------------------

def _build_achievements(umb, instance, topic: Optional[str]) -> Optional[str]:
    """Build achievements section from instance lineage or digest fallback."""
    items = []
    if umb and instance:
        raw = _safe(umb.extract_achievement_timeline, instance, 8) or []
        items = [
            f"- Gen {a['gen']}: {a['achievement']}" for a in raw
            if not topic or _matches_topic(a.get("achievement", ""), topic)
        ]
    if not items:
        digest = _import_module("digest_data")
        if digest:
            items = [f"- {a}" for a in (_safe(digest.parse_achievements) or [])]
    return f"## Recent Achievements\n\n" + "\n".join(items) if items else None


def _build_active_work(next_up: dict, topic: Optional[str]) -> Optional[str]:
    """Build active work section from instance next_up field."""
    if not next_up:
        return None
    labels = {"completed": "### Completed", "remaining": "### Remaining", "blockers": "### Blockers"}
    parts = []
    for key, heading in labels.items():
        raw = next_up.get(key, [])
        items = [f"- {x}" for x in raw if not topic or _matches_topic(x, topic)]
        if items:
            parts.append(f"{heading}\n\n" + "\n".join(items))
    return f"## Active Work\n\n" + "\n\n".join(parts) if parts else None


def _build_decisions(instance: dict, topic: Optional[str]) -> Optional[str]:
    """Build decisions section from instance decisions list."""
    decisions = instance.get("decisions", [])
    if not decisions:
        return None
    items = []
    for d in decisions[:5]:
        text = d if isinstance(d, str) else d.get("decision", str(d))
        if topic and not _matches_topic(text, topic):
            continue
        if len(text) > 200:
            text = text[:197] + "..."
        items.append(f"- {text}")
    return f"## Key Decisions\n\n" + "\n".join(items) if items else None


def _build_memories(umb, project: str, topic: Optional[str]) -> Optional[str]:
    """Build memories section from KONO permanent memories."""
    if not umb:
        return None
    memories = _safe(umb.recall_permanent_memories, project, 6) or []
    items = []
    for m in memories:
        content = m.get("content", "")
        if topic and not _matches_topic(content, topic):
            continue
        perm = "[permanent] " if m.get("is_permanent") else ""
        items.append(f"- {perm}{content}")
    return f"## Key Memories\n\n" + "\n".join(items) if items else None


def _build_strategies(umb, project: str, topic: Optional[str]) -> Optional[str]:
    """Build strategy transfers section from KONO cross-project memories."""
    if not umb:
        return None
    transfers = _safe(umb.recall_strategy_transfers, project, 3) or []
    items = [f"- {t.get('content', '')}" for t in transfers
             if not topic or _matches_topic(t.get("content", ""), topic)]
    return f"## Strategy Notes\n\n" + "\n".join(items) if items else None


def _build_session_context(next_up: dict) -> Optional[str]:
    """Build session facts and remaining work from session-context.md."""
    ctx_path = CLAUDE_DIR / "rules" / "session-context.md"
    if not ctx_path.exists():
        return None
    ctx_text = ctx_path.read_text(encoding="utf-8", errors="replace")
    parts = []
    facts = _extract_section(ctx_text, "Session Facts")
    if facts:
        parts.append(f"## Session Facts\n\n{facts}")
    if not next_up:
        remaining = _extract_section(ctx_text, "Remaining Work")
        if remaining:
            parts.append(f"## Remaining Work\n\n{remaining}")
    return "\n\n".join(parts) if parts else None


def _build_genome() -> Optional[str]:
    """Build genome pulse section from cached genome data."""
    digest = _import_module("digest_data")
    if not digest:
        return None
    genome = _safe(digest.read_genome_summary)
    if not genome:
        return None
    return (
        f"## Genome Pulse\n\n"
        f"Gen {genome.get('generation', 0)} | "
        f"{genome.get('gene_count', 0)} genes | "
        f"fitness {genome.get('fitness', 0):.3f} {genome.get('trend', '')}"
    )


# ---------------------------------------------------------------------------
# Payload assembler (coordinator — calls section builders)
# ---------------------------------------------------------------------------

def build_transfer_payload(
    project_path: Optional[str] = None,
    topic: Optional[str] = None,
) -> str:
    """Assemble a self-contained markdown transfer payload.

    Coordinator pattern: each section builder is error-isolated.
    Partial results are fine; total failure falls back to session-context.md.
    """
    project = project_path or os.getcwd()

    path_enc = _import_module("path_encoding")
    encoded = path_enc.encode_project_path(project) if path_enc else (
        project.replace("/", "-").replace(".", "-")
    )

    umb = _import_module("unified_memory_brief")
    instance = _safe(umb.load_latest_instance, project) if umb else None

    gen, branch = 0, "main"
    if instance:
        lineage = instance.get("lineage", {})
        gen = lineage.get("lineage_depth", lineage.get("generation", 0))
        branch = lineage.get("branch", "main")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"# GRIP Session Transfer\n\nGenerated: {now} | Gen {gen} | Branch: {branch}"
    if topic:
        header += f"\n\n**Topic Focus:** {topic}"

    next_up = instance.get("next_up", {}) if instance else {}
    summary = None
    if next_up:
        notes = next_up.get("notes", "")
        if notes and (not topic or _matches_topic(notes, topic)):
            summary = f"## Session Summary\n\n{notes}"

    builders = [
        lambda: summary,
        lambda: _build_achievements(umb, instance, topic),
        lambda: _build_active_work(next_up, topic),
        lambda: _build_decisions(instance, topic) if instance else None,
        lambda: _build_memories(umb, project, topic),
        lambda: _build_strategies(umb, project, topic),
        lambda: _build_session_context(next_up),
        lambda: _build_genome(),
    ]

    sections = [header]
    for build in builders:
        result = _safe(build)
        if result:
            sections.append(result)

    sections.append(
        f"---\n\n*Transfer from GRIP Gen {gen}. "
        f"Paste into any AI session for full context.*"
    )

    ctx_path = CLAUDE_DIR / "rules" / "session-context.md"
    if len(sections) <= 3 and ctx_path.exists():
        return ctx_path.read_text(encoding="utf-8", errors="replace")

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _matches_topic(text: str, topic: str) -> bool:
    """Case-insensitive keyword match for topic filtering."""
    return topic.lower() in text.lower()


def _extract_section(text: str, heading: str) -> Optional[str]:
    """Extract content under a ## heading from markdown."""
    lines = text.splitlines()
    in_section = False
    result = []
    for line in lines:
        if line.startswith(f"## {heading}"):
            in_section = True
            continue
        elif line.startswith("## ") and in_section:
            break
        if in_section:
            result.append(line)
    content = "\n".join(result).strip()
    return content if content else None


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------


def _deliver_clipboard(payload: str) -> dict:
    """Copy payload to system clipboard (triggers Universal Clipboard on macOS)."""
    system = platform.system()
    cmd = None
    if system == "Darwin":
        cmd = ["pbcopy"]
    elif system == "Linux":
        if shutil.which("wl-copy"):
            cmd = ["wl-copy"]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-selection", "clipboard"]
        elif shutil.which("xsel"):
            cmd = ["xsel", "--clipboard", "--input"]
    elif system == "Windows":
        cmd = ["clip.exe"]

    if not cmd:
        # Fallback: write to stdout
        print(payload)
        return {
            "ok": True,
            "method": "stdout (clipboard fallback)",
            "detail": "No clipboard command found, printed to stdout",
        }

    try:
        subprocess.run(cmd, input=payload.encode("utf-8"), check=True, timeout=5)
        detail = "Copied to clipboard"
        if system == "Darwin":
            detail += " (Universal Clipboard active — paste within 2 min for cross-device)"
        return {"ok": True, "method": "clipboard", "detail": detail}
    except Exception as e:
        return {"ok": False, "method": "clipboard", "detail": str(e)}


def _deliver_icloud(payload: str) -> dict:
    """Write payload to iCloud Drive for reliable cross-device transfer."""
    icloud_dir = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if not icloud_dir.exists():
        return {"ok": False, "method": "icloud", "detail": "iCloud Drive not found"}

    target = icloud_dir / "grip-transfer.md"
    try:
        target.write_text(payload, encoding="utf-8")
        return {
            "ok": True,
            "method": "icloud",
            "detail": f"Written to {target} (syncs across all Apple devices, no timeout)",
        }
    except Exception as e:
        return {"ok": False, "method": "icloud", "detail": str(e)}


def _deliver_stdout(payload: str) -> dict:
    """Print payload to stdout for piping."""
    print(payload)
    return {"ok": True, "method": "stdout", "detail": "Printed to stdout"}


TRANSPORTS = {
    "clipboard": _deliver_clipboard,
    "icloud": _deliver_icloud,
    "stdout": _deliver_stdout,
}


def deliver(payload: str, method: str = "clipboard") -> dict:
    """Deliver the transfer payload via the specified transport.

    Returns: {"ok": bool, "method": str, "detail": str}
    """
    fn = TRANSPORTS.get(method)
    if not fn:
        return {"ok": False, "method": method, "detail": f"Unknown transport: {method}"}
    return fn(payload)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    """Parse CLI arguments for grip_transfer."""
    parser = argparse.ArgumentParser(
        description="GRIP Session Transfer — cross-session context handoff",
    )
    parser.add_argument(
        "--topic", type=str, default=None,
        help="Filter transfer payload by topic keyword",
    )
    parser.add_argument(
        "--icloud", action="store_true",
        help="Write to iCloud Drive instead of clipboard",
    )
    parser.add_argument(
        "--stdout", action="store_true",
        help="Print to stdout instead of clipboard",
    )
    parser.add_argument(
        "--project", type=str, default=None,
        help="Override project path (default: CWD)",
    )
    return parser.parse_args()


def _report(result: dict, payload: str, topic: Optional[str]) -> None:
    """Print transfer report to stderr."""
    token_estimate = len(payload.split()) * 1.3
    print(f"\n/transfer complete", file=sys.stderr)
    print(f"  Method: {result['method']}", file=sys.stderr)
    print(f"  Status: {'OK' if result['ok'] else 'FAILED'}", file=sys.stderr)
    print(f"  Detail: {result['detail']}", file=sys.stderr)
    print(f"  Size: {len(payload):,} chars (~{int(token_estimate):,} tokens)", file=sys.stderr)
    if topic:
        print(f"  Topic: {topic}", file=sys.stderr)


def main():
    args = _parse_args()
    payload = build_transfer_payload(project_path=args.project, topic=args.topic)

    method = "stdout" if args.stdout else ("icloud" if args.icloud else "clipboard")
    result = deliver(payload, method)

    if not args.stdout:
        _report(result, payload, args.topic)

    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
