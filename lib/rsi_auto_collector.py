#!/usr/bin/env python3
"""
RSI Auto-Collector - Central automation for improvement and metrics logging.

Usage: rsi_auto_collector.py <mode> [args...]

Modes, the command-line contract the hooks call:
    on-commit <hash>                     Log a git commit as an improvement
    on-edit <path> [description]         Log a GRIP infrastructure file edit
    on-event <type> <description>        Log a generic event to metrics
    on-session-end [transcript]          Full learning sweep (detect +
                                         consolidate + rules); the transcript
                                         path is accepted and not read
    learn-event <description> [context]  Bridge: persist event as learning
                                         insight (context defaults to auto_event);
                                         both are scrubbed by the redaction shield
                                         first, and nothing is kept without it
    sync-metrics                         Sync skill-usage.jsonl into metrics.jsonl

A required argument must be present and not empty; arguments past a mode's
last are ignored. Exit status: 0 when the mode ran or was declined, 1 on a
usage error or a failed mode.

Called from hooks (rsi-posttool-hook.py, bash_error_learner_hook.py,
session-end.sh). All operations are non-blocking and safe to run in
background with timeout.

Records land under the GRIP directory from lib/claude_dir.py ($CLAUDE_DIR,
else $HOME/.claude), as a real path. When that directory is a linked git
worktree, whose .git is a file, the collector prints one line to stderr and
records nothing: the learning sweep rewrites learning/insights.jsonl and
rules/learned-insights.md, both tracked, so a run there dirties a feature
branch (GRIP#7599). The install proper records as before.

Version: 2.1.0
"""

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

# Resolved through the shared shim, as lib/learning_detector.py is, so the
# collector's records and the sweep's share one directory and the guard in
# main() checks the directory both would write to. A real path, because the
# post-tool hook passes on-edit real paths.
CLAUDE_DIR = get_claude_dir().resolve()
IMPROVEMENT_LOG = CLAUDE_DIR / "improvement-log.jsonl"
METRICS_FILE = CLAUDE_DIR / "metrics.jsonl"
PATTERN_COUNTS = CLAUDE_DIR / "pattern-counts.json"
SKILL_USAGE = CLAUDE_DIR / "skill-usage.jsonl"
SYNC_STATE = CLAUDE_DIR / "state" / "rsi" / "rsi-sync-state.json"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"
PROG = "rsi_auto_collector.py"

# Commit prefix -> improvement type (same as seed script)
PREFIX_MAP = {
    "feat": "efficiency_improvement",
    "fix": "bug_fix",
    "refactor": "refactor",
    "docs": "documentation_update",
    "security": "gate_added",
    "test": "efficiency_improvement",
    "chore": "refactor",
    "perf": "efficiency_improvement",
    "style": "refactor",
}

# Commit prefix -> trigger
TRIGGER_MAP = {
    "feat": "auto",
    "fix": "friction_encountered",
    "refactor": "friction_encountered",
    "docs": "auto",
    "security": "friction_encountered",
    "test": "auto",
    "chore": "proactive",
    "perf": "proactive",
    "style": "proactive",
}

# Keywords that indicate skill creation
SKILL_KEYWORDS = ["add", "skill", "mode", "agent", "gate", "broly", "kono"]


def log_hook(level: str, message: str):
    """Append to hook log (locked for concurrent session safety)."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        from locked_append import locked_append as _la
        _la(str(HOOK_LOG), f"{ts} [RSI-COLLECTOR][{level}] {message}")
    except ImportError:
        try:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with open(HOOK_LOG, "a") as f:
                f.write(f"{ts} [RSI-COLLECTOR][{level}] {message}\n")
        except Exception:
            pass
    except Exception:
        pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, entry: dict):
    """Append a JSON entry to a JSONL file with exclusive locking."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from locked_append import locked_append as _la
        _la(str(path), json.dumps(entry))
    except ImportError:
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")


def _read_jsonl(path: Path, last: int = 0) -> list:
    """Parseable entries of a JSONL file, oldest first; [] when it is missing.

    last > 0 reads only the final `last` lines. Lines that are not JSON are
    skipped.
    """
    if not path.exists():
        return []
    lines = path.read_text().strip().split("\n")
    entries = []
    for line in lines[-last:] if last else lines:
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def load_existing_descriptions() -> set:
    """Load existing improvement descriptions for dedup."""
    return {entry.get("description", "") for entry in _read_jsonl(IMPROVEMENT_LOG)}


def parse_commit_prefix(message: str) -> tuple:
    """Parse commit message into (prefix, description)."""
    if ":" not in message:
        return None, message
    prefix_part = message.split(":", 1)[0].strip().lower()
    description = message.split(":", 1)[1].strip()
    base_prefix = prefix_part.split("(")[0]
    if base_prefix in PREFIX_MAP:
        return base_prefix, description
    return None, message


def determine_type(prefix: str, description: str) -> str:
    """Determine improvement type with skill_created override."""
    if prefix == "feat":
        desc_lower = description.lower()
        for keyword in SKILL_KEYWORDS:
            if keyword in desc_lower:
                return "skill_created"
    return PREFIX_MAP.get(prefix, "efficiency_improvement")


def run_git(args: list) -> str:
    """Run git command in CLAUDE_DIR."""
    try:
        result = subprocess.run(
            ["git", "-C", str(CLAUDE_DIR)] + args,
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def extract_component(commit_hash: str) -> str:
    """Extract component from changed files."""
    files_output = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash])
    if not files_output:
        return "unknown"
    files = files_output.strip().split("\n")
    dirs = set()
    for f in files:
        parts = f.split("/")
        dirs.add(parts[0] if len(parts) > 1 else f)
    if not dirs:
        return "unknown"
    priority = ["lib", "hooks", "skills", "rules", "bin", "scripts", "docs", "mcp-servers"]
    sorted_dirs = sorted(dirs, key=lambda d: priority.index(d) if d in priority else 99)
    return ",".join(sorted_dirs[:3])


# ============================================================================
# MODE: on-commit
# ============================================================================

def on_commit(commit_hash: str):
    """Log a git commit as an improvement entry."""
    message = run_git(["log", "--format=%s", "-1", commit_hash])
    if not message:
        log_hook("WARN", f"Could not read commit {commit_hash}")
        return

    prefix, description = parse_commit_prefix(message)
    if prefix is None:
        log_hook("INFO", f"Skipping non-conventional commit: {message[:50]}")
        return

    # Dedup check
    if description in load_existing_descriptions():
        log_hook("INFO", f"Duplicate commit description, skipping: {description[:50]}")
        return

    entry = _commit_entry(commit_hash, prefix, description)
    append_jsonl(IMPROVEMENT_LOG, entry)
    # Also log to metrics.jsonl
    append_jsonl(METRICS_FILE, {
        "timestamp": entry["timestamp"],
        "event": "improvement_logged",
        "improvement_type": entry["type"],
        "component": entry["component"],
        "source": "auto_commit",
    })
    log_hook("SUCCESS", f"Logged commit {commit_hash[:8]}: {entry['type']} ({entry['component']})")


def _commit_entry(commit_hash: str, prefix: str, description: str) -> dict:
    """The improvement-log entry for a conventional commit."""
    return {
        "timestamp": run_git(["log", "--format=%aI", "-1", commit_hash]) or now_iso(),
        "type": determine_type(prefix, description),
        "component": extract_component(commit_hash),
        "description": description,
        "trigger": TRIGGER_MAP.get(prefix, "auto"),
        "source": "auto_commit",
        "commit": commit_hash[:8],
    }


# ============================================================================
# MODE: on-edit
# ============================================================================

def on_edit(file_path: str, description: str = ""):
    """Log a GRIP infrastructure file edit as an improvement."""
    path = Path(file_path)

    # Classify by path
    rel = str(path).replace(str(CLAUDE_DIR) + "/", "")
    if rel.startswith("skills/"):
        imp_type = "skill_created"
    elif rel.startswith("rules/"):
        imp_type = "rule_added"
    elif rel.startswith("hooks/"):
        imp_type = "refactor"
    elif rel.startswith("lib/"):
        imp_type = "efficiency_improvement"
    elif rel.startswith("modes/"):
        imp_type = "refactor"
    else:
        imp_type = "efficiency_improvement"

    # Extract component (top-level dir)
    parts = rel.split("/")
    component = parts[0] if parts else "unknown"

    if not description:
        description = f"Modified {rel}"

    entry = {
        "timestamp": now_iso(),
        "type": imp_type,
        "component": component,
        "description": description,
        "trigger": "auto",
        "source": "auto_edit",
    }
    append_jsonl(IMPROVEMENT_LOG, entry)
    log_hook("INFO", f"Logged edit: {imp_type} ({rel})")


# ============================================================================
# MODE: on-event
# ============================================================================

def on_event(event_type: str, description: str):
    """Log a generic event to metrics.jsonl."""
    entry = {
        "timestamp": now_iso(),
        "event": event_type,
        "description": description,
        "source": "auto_event",
    }
    append_jsonl(METRICS_FILE, entry)
    log_hook("INFO", f"Logged event: {event_type}")


# ============================================================================
# MODE: on-session-end
# ============================================================================

def on_session_end(transcript_path: str):
    """Full learning sweep: detect patterns, consolidate insights, generate rules.

    Runs on session end to close the dynamic learning loop:
    1. Analyse improvement-log for component/type patterns
    2. Check pattern-counts for 3x YSH threshold
    3. Ingest pending insights into learning pipeline
    4. Consolidate similar insights (YSH merge)
    5. Generate learned rules from approved/proven insights

    transcript_path is accepted because session-end.sh passes it; nothing
    here reads it.
    """
    recent = _read_jsonl(IMPROVEMENT_LOG, last=50)
    if recent:
        append_jsonl(METRICS_FILE, _sweep_metric(recent))
    ysh_ready = _ysh_ready_patterns()
    learning_summary = _learning_sweep()

    # Log combined session-end metric
    append_jsonl(METRICS_FILE, {
        "timestamp": now_iso(),
        "event": "session_end_learning_sweep",
        "improvement_entries": len(recent),
        "ysh_ready_patterns": len(ysh_ready),
        "learning": learning_summary,
        "source": "auto_session_end",
    })
    log_hook("SUCCESS", f"Session-end sweep complete: {len(recent)} improvements, {learning_summary}")


def _sweep_metric(recent: list) -> dict:
    """Phase 1: which components and improvement types recent entries show."""
    components = Counter(e.get("component", "unknown") for e in recent)
    types = Counter(e.get("type", "unknown") for e in recent)
    return {
        "timestamp": now_iso(),
        "event": "session_end_sweep",
        "recent_entries": len(recent),
        "top_components": dict(components.most_common(5)),
        "type_distribution": dict(types),
        "source": "auto_session_end",
    }


def _ysh_ready_patterns() -> list:
    """Phase 2: patterns seen 3+ times that have no skill yet (YSH threshold)."""
    if not PATTERN_COUNTS.exists():
        return []
    try:
        data = json.loads(PATTERN_COUNTS.read_text())
        ready = [name for name, info in data.items()
                 if name != "_meta" and info.get("count", 0) >= 3
                 and not info.get("skill_created", False)]
    except (json.JSONDecodeError, KeyError):
        return []
    if ready:
        log_hook("SUCCESS", f"RSI AUTO-GEN READY: {len(ready)} patterns at 3x: {', '.join(ready[:5])}")
        append_jsonl(METRICS_FILE, {
            "timestamp": now_iso(),
            "event": "rsi_auto_gen_ready",
            "patterns": ready,
            "count": len(ready),
            "source": "auto_session_end",
        })
    return ready


def _learning_sweep() -> dict:
    """Phases 3-5: ingest pending insights, consolidate them, write learned rules."""
    summary = {"ingested": 0, "consolidated": 0, "rules": 0}
    try:
        ld = _learning_detector()
        ingest_result = ld.ingest_pending_insights()
        summary["ingested"] = ingest_result.get("processed", 0)
        summary["candidates"] = ingest_result.get("candidates", 0)
        summary["consolidated"] = ld.consolidate_insights().get("consolidated", 0)
        summary["rules"] = ld.generate_learned_rules().get("rules_written", 0)
        if any(v > 0 for v in summary.values()):
            log_hook("SUCCESS",
                f"Learning sweep: ingested={summary['ingested']}, "
                f"consolidated={summary['consolidated']}, "
                f"rules={summary['rules']}")
    except ImportError:
        log_hook("WARN", "learning-detector not available for session-end sweep")
    except Exception as e:
        log_hook("ERROR", f"Learning sweep failed: {e}")
    return summary


def _learning_detector():
    """The learning detector module, lib/learning_detector.py.

    Imported by its underscore name: the hyphenated one is not a valid module
    identifier and silently ImportError'd, leaving the sweep and the
    learn-event bridge dead (fixed 2026-06-24). import_module is looked up at
    call time, so a test that patches importlib.import_module reaches here.
    """
    from importlib import import_module
    return import_module("learning_detector")


# ============================================================================
# MODE: learn-event
# ============================================================================

def _shielded(text: str):
    """text scrubbed whole by lib/review_redaction.py, or None when that cannot run.

    shield_text is the redaction shield's fail-closed entry: it raises
    ShieldFailed rather than return text it could not scrub, and a failed
    import lands here too. None tells the caller to write and send nothing.
    """
    try:
        from review_redaction import shield_text
        return shield_text(text)
    except Exception:  # noqa: BLE001 -- without a shield, nothing is kept
        return None


def learn_event(description: str, context: str = "auto_event"):
    """Bridge: persist an event description as a learning insight.

    Calls learning_detector.persist_insight() to store the event in
    insights.jsonl for cross-session learning. Also triggers
    auto-consolidation when insight count exceeds threshold.

    This is the key dynamic learning bridge — events detected by
    the PostToolUse hook flow here and into the learning pipeline
    without requiring manual /learn invocation.

    Hooks hand it captured text, such as tool output and error messages, so
    the description and context are scrubbed whole by the redaction shield
    before anything is written or broadcast. When the shield cannot run, the
    capture is dropped.
    """
    text, label = _shielded(description), _shielded(context)
    if text is None or label is None:
        log_hook("WARN", "redaction shield unavailable: learn-event capture dropped, "
                         "nothing written or sent")
        return
    try:
        ld = _learning_detector()
        insight_id = ld.persist_insight(text, context=label)
        log_hook("SUCCESS", f"Persisted learning insight {insight_id}: {text[:60]}")

        # Auto-consolidate when threshold exceeded
        un_ingested = sum(1 for e in ld._load_insights() if not e.get("ingested"))
        if un_ingested >= 15:
            result = ld.consolidate_insights()
            if result.get("consolidated", 0) > 0:
                log_hook("INFO", f"Auto-consolidated {result['consolidated']} insights into {result['clusters']} clusters")

        # Log to metrics
        append_jsonl(METRICS_FILE, {
            "timestamp": now_iso(),
            "event": "dynamic_learning",
            "insight_id": insight_id,
            "context": label,
            "source": "auto_learn_bridge",
        })
    except ImportError:
        log_hook("WARN", "learning-detector not available, skipping insight persistence")
    except Exception as e:
        log_hook("ERROR", f"learn_event failed: {e}")


# ============================================================================
# MODE: sync-metrics
# ============================================================================

def sync_metrics():
    """Sync skill-usage.jsonl entries into metrics.jsonl since last sync."""
    if not SKILL_USAGE.exists():
        return
    lines = SKILL_USAGE.read_text().strip().split("\n")
    if lines == [""]:
        return

    # Process only new lines since last sync
    new_entries = 0
    for line in lines[_last_synced_line():]:
        metric = _usage_metric(line) if line else None
        if metric:
            append_jsonl(METRICS_FILE, metric)
            new_entries += 1

    SYNC_STATE.write_text(json.dumps({
        "last_line": len(lines),
        "last_sync": now_iso(),
        "entries_synced": new_entries,
    }))
    if new_entries > 0:
        log_hook("INFO", f"Synced {new_entries} skill-usage entries to metrics.jsonl")


def _last_synced_line() -> int:
    """How many skill-usage lines an earlier sync already copied."""
    if not SYNC_STATE.exists():
        return 0
    try:
        return json.loads(SYNC_STATE.read_text()).get("last_line", 0)
    except (json.JSONDecodeError, KeyError):
        return 0


def _usage_metric(line: str):
    """The metrics entry for one skill-usage line, or None when it is not JSON."""
    try:
        usage = json.loads(line)
    except json.JSONDecodeError:
        return None
    return {
        "timestamp": usage.get("date", now_iso()),
        "event": "skills_triggered",
        "skill": usage.get("skill", "unknown"),
        "trigger_source": usage.get("source", "unknown"),
        "source": "auto_sync",
    }


# ============================================================================
# MAIN
# ============================================================================

class UsageError(Exception):
    """argv names no known mode, or leaves out an argument the mode requires."""


# mode -> (handler name, required arguments, defaults for the optional ones,
# usage). Handlers are named, not held, so main() dispatches to whatever the
# module holds at call time.
MODES = {
    "on-commit": ("on_commit", 1, (), "<commit_hash>"),
    "on-edit": ("on_edit", 1, ("",), "<file_path> [description]"),
    "on-event": ("on_event", 2, (), "<event_type> <description>"),
    "on-session-end": ("on_session_end", 0, ("",), "[transcript]"),
    "learn-event": ("learn_event", 1, ("auto_event",), "<description> [context]"),
    "sync-metrics": ("sync_metrics", 0, (), ""),
}


def parse_args(argv: list) -> tuple:
    """Map argv after the program name to (handler name, arguments).

    This is the contract the hooks are held to. Raises UsageError carrying the
    usage text when the mode is unknown or a required argument is missing or
    empty; optional arguments take their defaults.
    """
    if not argv:
        raise UsageError(f"Usage: {PROG} <mode> [args...]\nModes: {', '.join(MODES)}")
    mode = argv[0]
    if mode not in MODES:
        raise UsageError(f"Unknown mode: {mode}")
    name, required, defaults, usage = MODES[mode]
    given = list(argv[1:1 + required + len(defaults)])
    if len(given) < required or not all(given[:required]):
        raise UsageError(f"Usage: {PROG} {mode} {usage}".rstrip())
    return name, given + list(defaults[len(given) - required:])


def is_worktree_checkout(root: Path) -> bool:
    """True when root is a linked git worktree: its .git is a file, not a directory."""
    return (root / ".git").is_file()


def main():
    """Run the mode sys.argv names; exit 1 on a usage error or a failed mode."""
    try:
        name, args = parse_args(sys.argv[1:])
    except UsageError as e:
        print(e)
        sys.exit(1)
    if is_worktree_checkout(CLAUDE_DIR):
        print(f"{PROG}: {CLAUDE_DIR} is a linked git worktree, so {sys.argv[1]} "
              "records nothing there (its learning files are tracked)", file=sys.stderr)
        return
    try:
        globals()[name](*args)
    except Exception as e:
        log_hook("ERROR", f"Mode '{sys.argv[1]}' failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
