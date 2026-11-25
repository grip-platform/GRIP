#!/usr/bin/env python3
"""
GRIP PostCompact hook -- persists session state after compaction.

Fires after Claude Code compacts the conversation. This is the exact right
moment to persist state because:
1. The conversation just lost context (compaction removed older messages)
2. The PreCompact hook captured working state into the summary
3. We now need to persist durable state to disk for recovery

What this hook does:
- Saves session context to rules/session-context.md
- Updates instance JSON next_up field
- Touches the infra-tests-passed marker (preserves across compaction)
- Logs the compaction event for convergence tracking

Input (stdin JSON): {hook_event_name: "PostCompact"}
Output (stdout): Status message (informational only, not injected).

Performance target: <3s. Disk writes only.
Safety: Every operation wraps in try/except. Hook exits 0 always.

Origin: GH issue #1284 (CC 2.1.76 PostCompact hook harness).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude")))

# CLAUDE_DIR answers "where does this install's STATE live" and is deliberately
# redirected -- the test suite points it at a hermetic temp dir so writes land
# there. It does NOT answer "where does the code I import live": lib/ ships
# beside this hook and moves only when the hook moves. Resolving the import
# through CLAUDE_DIR conflated the two, so every run under a redirected
# CLAUDE_DIR died on ModuleNotFoundError before the hook did any work.
# State paths below still use CLAUDE_DIR, which is correct.
LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if not (LIB_DIR / "path_encoding.py").is_file():
    LIB_DIR = CLAUDE_DIR / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from path_encoding import encode_project_path  # noqa: E402

_PROJECT_SLUG = encode_project_path(CLAUDE_DIR)


def persist_session_context() -> str:
    """Update session-context.md with compaction marker."""
    ctx_file = CLAUDE_DIR / "rules" / "session-context.md"
    try:
        if ctx_file.exists():
            content = ctx_file.read_text(encoding="utf-8")
            # Add compaction marker if not already present
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            marker = f"\n## Last Compaction: {ts}\n"
            if "Last Compaction:" in content:
                # Update existing marker
                import re
                content = re.sub(
                    r"\n## Last Compaction: .*\n",
                    marker,
                    content,
                )
            else:
                content += marker
            ctx_file.write_text(content, encoding="utf-8")
            return "session-context updated"
    except Exception as e:
        return f"session-context failed: {e}"
    return "session-context: no file"


def update_instance_next_up() -> str:
    """Update instance JSON with compaction event."""
    instance_dir = CLAUDE_DIR / "state" / "instance"
    try:
        # Find the latest instance file
        instances = sorted(instance_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not instances:
            return "no instance file"
        latest = instances[-1]
        data = json.loads(latest.read_text())
        data["last_compaction"] = datetime.now(timezone.utc).isoformat()
        data["compaction_count"] = data.get("compaction_count", 0) + 1
        latest.write_text(json.dumps(data, indent=2))
        return f"instance updated (compaction #{data['compaction_count']})"
    except Exception as e:
        return f"instance failed: {e}"


def log_compaction_event() -> str:
    """Append compaction event to convergence log."""
    log_file = CLAUDE_DIR / "learning" / "evo-convergence.jsonl"
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "compaction",
            "type": "post_compact_hook",
        }
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return "convergence log appended"
    except Exception as e:
        return f"log failed: {e}"


def persist_project_memory() -> str:
    """Write compaction checkpoint to project memory for cross-session survival."""
    try:
        # Gather current state using the same gatherer PreCompact uses
        sys.path.insert(0, str(LIB_DIR))
        from compaction_state_gatherer import gather_all, format_compact
        state = gather_all()

        # Build checkpoint content
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        checkpoint = {
            "timestamp": ts,
            "type": "post_compaction_checkpoint",
            "state": state,
        }

        # Write to project memory checkpoint file
        checkpoint_dir = CLAUDE_DIR / "state" / "rsi"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_file = checkpoint_dir / "compaction-checkpoint.json"
        checkpoint_file.write_text(json.dumps(checkpoint, indent=2, default=str))

        # Also write compact summary to project session context
        project_memory = (
            CLAUDE_DIR / "projects" / _PROJECT_SLUG
            / "memory" / "session_context.md"
        )
        if project_memory.exists():
            content = project_memory.read_text()
            compact_summary = format_compact(state, max_chars=500)
            marker = f"\n\n## Post-Compaction State ({ts})\n\n{compact_summary}\n"
            # Replace existing post-compaction section or append
            import re
            if "## Post-Compaction State" in content:
                content = re.sub(
                    r"\n\n## Post-Compaction State.*?(?=\n## |\Z)",
                    marker,
                    content,
                    flags=re.DOTALL,
                )
            else:
                content += marker
            project_memory.write_text(content)
            return "project memory + checkpoint updated"
        return "checkpoint written (no project memory file)"
    except Exception as e:
        return f"project memory failed: {e}"


def capture_delight_snapshot() -> str:
    """Capture DELIGHT v2 snapshot at compaction boundary."""
    try:
        sys.path.insert(0, str(CLAUDE_DIR))
        from lib.delight_v2 import capture_snapshot
        snap = capture_snapshot(elapsed_hours=2.0)
        return f"DELIGHT={snap.composite:.3f}"
    except Exception as e:
        return f"delight failed: {e}"


def check_due_hypotheses() -> str:
    """Auto-verify any due hypotheses at compaction boundary."""
    try:
        sys.path.insert(0, str(CLAUDE_DIR))
        from lib.hypothesis_engine import SKIPPED_STATUS, verify
        result = verify()
        rows = result["results"]
        # The constant is imported, not spelled out: a literal here would keep
        # comparing equal to nothing if the engine ever renames it, and would
        # silently reclassify every skipped row as verified.
        skipped = [r for r in rows if r.get("status") == SKIPPED_STATUS]
        done = [r for r in rows if r.get("status") != SKIPPED_STATUS]
        # Reported separately, and never under a "verified" label: a skipped row
        # is due and unwritable, not absent and not verified. Folding it into
        # either bucket would be the same silent swallow one layer up.
        parts = []
        if done:
            parts.append(
                "hypotheses verified: "
                + ", ".join(f"{r['id']}:{r['status']}" for r in done)
            )
        if skipped:
            # Deduplicated: a colliding id contributes one skip record PER ROW,
            # so listing them raw reads as "H900, H900" — which looks like a
            # reporting bug rather than one id that needs one split.
            ids = sorted({r["id"] for r in skipped})
            parts.append(
                "hypotheses SKIPPED (id cannot address one record): "
                + ", ".join(ids)
            )
        return " | ".join(parts) if parts else "no due hypotheses"
    except Exception as e:
        return f"hypothesis check: {e}"


def write_resume_marker() -> str:
    """Write [GRIP POST-COMPACT RESUME] marker to session-context.md.

    This marker is recognised by the session-start protocol and triggers
    automatic context reload on the next prompt after compaction.
    """
    ctx_file = CLAUDE_DIR / "rules" / "session-context.md"
    try:
        if ctx_file.exists():
            content = ctx_file.read_text(encoding="utf-8")
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            # Add resume marker at the top for immediate visibility
            marker = f"[GRIP POST-COMPACT RESUME] Compacted at {ts}. Read state/rsi/compaction-checkpoint.json for full context.\n\n"
            if "[GRIP POST-COMPACT RESUME]" in content:
                import re
                content = re.sub(
                    r"\[GRIP POST-COMPACT RESUME\].*?\n\n",
                    marker,
                    content,
                )
            else:
                content = marker + content
            ctx_file.write_text(content, encoding="utf-8")
            return "resume marker written"
    except Exception as e:
        return f"resume marker failed: {e}"
    return "no session-context file"


def reset_native_context_sidecar() -> str:
    """Invalidate the CC-native context sidecar after compaction.

    The sidecar records context % from before compaction. A fresh post-compact
    session starts near 0%; leaving the old value causes the context gate to
    fire immediately on the first tool call. Setting ts=0 makes every
    max_age check fail, forcing callers to fall back to the JSONL estimate.
    """
    sidecar = CLAUDE_DIR / "state" / "features" / "cc-native-context.json"
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text('{"pct":0,"context_max":1000000,"ts":0}')
        return "native-context sidecar reset"
    except Exception as e:
        return f"sidecar reset failed: {e}"


def main() -> None:
    results = []
    results.append(reset_native_context_sidecar())
    results.append(persist_session_context())
    results.append(update_instance_next_up())
    results.append(persist_project_memory())
    results.append(capture_delight_snapshot())
    results.append(check_due_hypotheses())
    results.append(write_resume_marker())
    results.append(log_compaction_event())

    status = " | ".join(results)
    print(f"[GRIP PostCompact] {status}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
