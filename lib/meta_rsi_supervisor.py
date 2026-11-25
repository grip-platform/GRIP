#!/usr/bin/env python3
"""
Meta-RSI Supervisor - Monitors and steers a worker RSI session autonomously.

Connects to a worker session's control server via WebSocket, streams tool events,
analyzes them for drift/violations/quality issues, and injects steering corrections.

Usage:
    python3 meta-rsi-supervisor.py <session_id> [--log-dir DIR] [--dry-run]
    python3 meta-rsi-supervisor.py auto  # Auto-discover non-self session

Architecture:
    Supervisor ──WebSocket──► session-control-server.py (worker)
                                      ▲
    Worker session hooks ──Unix──────┘
    (session-broadcast.py)

Steering flows:
    1. Worker's PostToolUse hook broadcasts events to control server
    2. Supervisor receives events via WebSocket subscription
    3. Supervisor analyzes event batches for protocol violations
    4. Supervisor injects corrections via steering protocol
    5. Worker's PreToolUse hook (context-gate) picks up injection as [STEERING CONTEXT]
"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

# --- Configuration ---

CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_LOG_DIR = CLAUDE_DIR / "data" / "supervisor"
MONITOR_ID = "meta-rsi-supervisor"

# Analysis thresholds
ANALYSIS_INTERVAL_SEC = 30
ANALYSIS_BATCH_SIZE = 10
BASELINE_REINJECTION_SEC = 300  # Re-inject baseline protocols every 5 min
DRIFT_OPS_THRESHOLD = 50  # Ops without commit = drift warning
THRASH_THRESHOLD = 4  # Same file edited 4+ times = thrashing
BRUTE_FORCE_THRESHOLD = 3  # Same command failing 3+ times
CONTEXT_PRESSURE_OPS = 100  # Total ops without checkpoint

# Protocol rules
BLOCKED_BASH_PATTERNS = {
    "grep ": "Use rg instead of grep",
    "find ": "Use fd instead of find",
    "cat ": "Use Read tool instead of cat",
}

DEPENDENCY_FOLDERS = [
    "node_modules", "venv", "__pycache__", ".next", "dist",
    "build", "target", "vendor", "Pods",
]

DESTRUCTIVE_PATTERNS = [
    "push --force", "push -f", "reset --hard",
    "rm -rf", "clean -fd", "branch -D",
]

# Commit detection (includes merges, pushes, PR operations)
COMMIT_MARKERS = ["git commit", "git merge", "gh pr merge", "git push"]


# --- Logging ---

class SupervisorLog:
    """Append-only log for supervisor events and steering actions."""

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_file = self.log_dir / f"supervisor-{ts}.jsonl"
        self.summary_file = self.log_dir / "latest-summary.json"
        self.stats = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "events_received": 0,
            "analyses_run": 0,
            "steerings_sent": 0,
            "issues_detected": Counter(),
        }

    def event(self, category: str, data: dict):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "category": category,
            **data,
        }
        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def save_summary(self):
        summary = {
            **self.stats,
            "issues_detected": dict(self.stats["issues_detected"]),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        tmp = self.summary_file.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(summary, f, indent=2)
        # replace, not rename: on Windows, rename refuses a target that exists (GRIP#8778)
        tmp.replace(self.summary_file)


# --- Analysis Engine ---

def _issue(kind: str, severity: str, detail: str) -> dict:
    """One detected issue, in the shape the log and the steering text read."""
    return {"type": kind, "severity": severity, "detail": detail}


def _blocked_bash_issues(cmd: str) -> list[dict]:
    """Bash tool substitution: grep, find or cat where rg, fd or Read belongs."""
    issues = []
    for pattern, fix in BLOCKED_BASH_PATTERNS.items():
        if pattern not in cmd:
            continue
        # Allow rg/fd variants
        if (pattern == "grep " and "rg " in cmd) or (pattern == "find " and "fd " in cmd):
            continue
        issues.append(_issue("PROTOCOL", "medium", f"{fix}: {cmd[:100]}"))
    return issues


def _destructive_issues(cmd: str) -> list[dict]:
    """Destructive ops: one issue per destructive pattern the command contains."""
    return [_issue("SAFETY", "critical", f"Destructive git op: {cmd[:100]}")
            for dp in DESTRUCTIVE_PATTERNS if dp in cmd]


def _dependency_read_issues(path: str) -> list[dict]:
    """File access violations: one issue per dependency folder the path is inside."""
    return [_issue("DEPENDENCY_READ", "high", f"Reading dependency folder: {path[:100]}")
            for dep in DEPENDENCY_FOLDERS if f"/{dep}/" in path or path.endswith(f"/{dep}")]


def _is_error_output(tool_output) -> bool:
    """Whether a Bash result reads as a failure."""
    return isinstance(tool_output, str) and ("error" in tool_output.lower() or "Error" in tool_output)


class AnalysisEngine:
    """Stateful analyzer that tracks patterns across event batches."""

    def __init__(self):
        self.ops_since_commit = 0
        self.total_ops = 0
        self.file_edit_counts = Counter()
        self.recent_commands = deque(maxlen=20)
        self.failed_commands = deque(maxlen=10)
        self.last_commit_time = datetime.now()
        self.last_anti_drift_time = None
        self.saw_commit = False
        self.saw_anti_drift_after_commit = True  # Start clean

    def analyze(self, events: list) -> list[dict]:
        """Analyze a batch of events. Returns list of issues found."""
        issues = []
        for e in events:
            issues.extend(self._event_issues(e))
        issues.extend(self._cadence_issues())
        return issues

    def _event_issues(self, e: dict) -> list[dict]:
        """Issues raised by one tool event, after counting it."""
        data = e.get("data", {})
        tool = e.get("event_type", "")
        tool_input = data.get("tool_input", {})
        tool_output = data.get("tool_response", data.get("tool_output", ""))

        self.total_ops += 1
        self.ops_since_commit += 1

        if tool == "Bash":
            return self._bash_issues(tool_input.get("command", ""), tool_output)
        if tool in ("Read", "Glob", "Grep"):
            return _dependency_read_issues(tool_input.get("file_path", tool_input.get("path", "")))
        if tool in ("Write", "Edit"):
            return self._edit_issues(tool_input.get("file_path", ""))
        return []

    def _bash_issues(self, cmd: str, tool_output) -> list[dict]:
        """Protocol violations, destructive ops, commits and brute force for one command."""
        self.recent_commands.append(cmd)
        issues = _blocked_bash_issues(cmd) + _destructive_issues(cmd)
        if any(p in cmd for p in COMMIT_MARKERS):
            self._record_commit()
        issues.extend(self._brute_force_issues(cmd, tool_output))
        return issues

    def _record_commit(self) -> None:
        """A commit starts a new drift window and expects anti-drift output."""
        self.saw_commit = True
        self.saw_anti_drift_after_commit = False
        self.ops_since_commit = 0
        self.last_commit_time = datetime.now()

    def _brute_force_issues(self, cmd: str, tool_output) -> list[dict]:
        """Brute force detection (same command failing repeatedly)."""
        if not _is_error_output(tool_output):
            return []
        self.failed_commands.append(cmd[:80])
        # Check for repeated failures
        recent_fails = list(self.failed_commands)[-BRUTE_FORCE_THRESHOLD:]
        if len(recent_fails) >= BRUTE_FORCE_THRESHOLD and len(set(recent_fails)) == 1:
            return [_issue("BRUTE_FORCE", "high",
                           f"Same command failing {BRUTE_FORCE_THRESHOLD}x: {recent_fails[0]}")]
        return []

    def _edit_issues(self, path: str) -> list[dict]:
        """File edit tracking: thrashing when one file reaches THRASH_THRESHOLD edits."""
        self.file_edit_counts[path] += 1
        if self.file_edit_counts[path] == THRASH_THRESHOLD:
            return [_issue("THRASHING", "medium",
                           f"File edited {THRASH_THRESHOLD}+ times: {Path(path).name}")]
        return []

    def _cadence_issues(self) -> list[dict]:
        """Cadence checks (run once per batch)."""
        issues = []
        # Drift: too many ops without commit
        if self.ops_since_commit >= DRIFT_OPS_THRESHOLD:
            issues.append(_issue("DRIFT", "high",
                                 f"{self.ops_since_commit} ops since last commit. Checkpoint progress."))
        # Context pressure
        if self.total_ops >= CONTEXT_PRESSURE_OPS and self.total_ops % 50 == 0:
            issues.append(_issue("CONTEXT_PRESSURE", "high",
                                 f"{self.total_ops} total ops. Consider context checkpoint."))
        # Anti-drift compliance: commit happened but no What's Up Next.
        # Give 5 events grace period after commit for anti-drift output.
        if self.saw_commit and not self.saw_anti_drift_after_commit and self.ops_since_commit > 5:
            issues.append(_issue("ANTI_DRIFT_SKIP", "medium",
                                 "Commit without 'What's Up Next' output. Run anti-drift protocol."))
            self.saw_anti_drift_after_commit = True  # Only warn once
        return issues


def build_steering_text(issues: list[dict]) -> str:
    """Build a concise steering injection from detected issues."""
    if not issues:
        return ""

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues.sort(key=lambda i: severity_order.get(i["severity"], 99))

    lines = ["[META-RSI SUPERVISOR]"]
    for issue in issues[:5]:  # Max 5 issues per injection
        marker = {"critical": "!!!", "high": "!!", "medium": "!", "low": "."}.get(
            issue["severity"], "."
        )
        lines.append(f"{marker} [{issue['type']}] {issue['detail']}")

    lines.append("")
    lines.append("Address above before proceeding.")
    return "\n".join(lines)


# --- Direct File Injection ---

BASELINE_PROTOCOLS = """[META-RSI SUPERVISOR - PROTOCOL ENFORCEMENT]

1. ANTI-DRIFT: After EVERY git commit, output What's Up Next (remaining work, open PRs, next action). NEVER skip.
2. CONFIDENCE GATE: When confidence < 99.9999999%, HALT and AskUserQuestion.
3. FILE READ OPT: Batch reads Phase 1. Zero re-reads. Trust mental model.
4. BASH SAFETY: rg not grep, fd not find, Read not cat.
5. CONTEXT GATE: At 80% context, HALT and serialize.
6. DEPENDENCY GUARDIAN: NEVER read node_modules, venv, __pycache__, dist, build.
7. COMMIT STANDARDS: No AI attribution. No emoji. Professional enterprise.
8. CHECKPOINT: Commit every 30-50 ops. Don't accumulate uncommitted work.
9. CONCISE: No preamble, no postamble. Action-first.
10. TEST BEFORE COMMIT: Run relevant tests. Keep the suite green.

Supervisor is watching. Corrections injected on violation."""


def inject_steering_file(session_id: str, text: str) -> bool:
    """Write steering context directly to the injection file.

    The context-gate-hook reads this file on every PreToolUse and surfaces
    it as [STEERING CONTEXT]. The file is consumed (cleared) after reading.
    """
    from _compat import grip_injection_path
    injection_path = Path(grip_injection_path(session_id))
    try:
        tmp = injection_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            f.write(text)
        os.chmod(str(tmp), 0o600)
        # replace, not rename: on Windows, rename refuses a target that exists (GRIP#8778)
        tmp.replace(injection_path)
        return True
    except OSError:
        return False


# --- Session Discovery ---

def discover_sessions() -> list[dict]:
    """Use session_lister to find active sessions."""
    # The hyphenated session-lister.py was deleted in dcc92e559 (2026-03-06).
    lister = CLAUDE_DIR / "lib" / "session_lister.py"
    if not lister.exists():
        return []
    try:
        result = subprocess.run(
            [sys.executable, str(lister)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return []


def get_self_session_id() -> str | None:
    """Try to determine this session's ID."""
    return os.environ.get("CLAUDE_SESSION_ID")


def calc_port(session_id: str) -> int:
    """Calculate deterministic TCP port for a session."""
    h = hashlib.sha256(session_id.encode()).hexdigest()
    return 19000 + (int(h[:8], 16) % 1000)


# --- Main Supervisor Loop ---

def _report_issues(issues: list[dict], session_id: str, log: SupervisorLog, dry_run: bool) -> None:
    """Log every issue, then steer the worker through the injection file unless dry-run."""
    for issue in issues:
        log.event("issue", issue)
        log.stats["issues_detected"][issue["type"]] += 1
        print(f"  >>> [{issue['severity'].upper()}] {issue['type']}: {issue['detail'][:80]}")

    # Inject steering via direct file write
    if dry_run:
        return
    steering_text = build_steering_text(issues)
    if not steering_text:
        return
    if inject_steering_file(session_id, steering_text):
        log.stats["steerings_sent"] += 1
        log.event("steering_sent", {
            "text": steering_text,
        })
        print(f"  <<< STEERED ({len(issues)} issues)")
    else:
        print(f"  !!! STEER FAILED (file write error)")


async def supervisor_loop(
    session_id: str,
    log: SupervisorLog,
    dry_run: bool = False,
):
    """Main supervisor loop: connect, subscribe, analyze, steer."""
    port = calc_port(session_id)
    url = f"ws://127.0.0.1:{port}"
    engine = AnalysisEngine()

    print(f"[SUPERVISOR] Target: {session_id[:8]}... (port {port})")
    print(f"[SUPERVISOR] Log: {log.log_file}")
    print(f"[SUPERVISOR] Dry run: {dry_run}")
    print(f"[SUPERVISOR] Connecting to {url}...")

    log.event("startup", {
        "target_session": session_id,
        "port": port,
        "dry_run": dry_run,
    })

    retry_delay = 2
    max_retry = 60

    while True:
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                print(f"[SUPERVISOR] Connected.")
                retry_delay = 2  # Reset on success

                # Subscribe to all high-impact events (observe only - steering via file)
                await ws.send(json.dumps({
                    "type": "subscribe",
                    "monitor_id": MONITOR_ID,
                    "events": ["Bash", "Write", "Edit", "Read", "Glob", "Grep", "Skill"],
                }))

                if dry_run:
                    print(f"[SUPERVISOR] Observe-only mode (dry-run).")
                else:
                    # Inject baseline protocols immediately
                    inject_steering_file(session_id, BASELINE_PROTOCOLS)
                    print(f"[SUPERVISOR] Baseline protocols injected via file.")
                    log.event("steering", {"status": "baseline_injected"})

                # Event processing loop
                event_buffer = []
                last_analysis = datetime.now()
                last_summary_save = datetime.now()
                last_baseline_inject = datetime.now()

                async for raw in ws:
                    msg = json.loads(raw)

                    if msg["type"] == "heartbeat":
                        continue

                    if msg["type"] == "event":
                        event_buffer.append(msg)
                        log.stats["events_received"] += 1

                        ts = datetime.now().strftime("%H:%M:%S")
                        tool = msg.get("event_type", "?")
                        preview = ""
                        if tool == "Bash":
                            cmd = msg.get("data", {}).get("tool_input", {}).get("command", "")
                            preview = f" | {cmd[:60]}"
                        elif tool in ("Write", "Edit"):
                            path = msg.get("data", {}).get("tool_input", {}).get("file_path", "")
                            preview = f" | {Path(path).name if path else '?'}"
                        print(f"  [{ts}] {tool}{preview}")

                    # Analyze when batch full or interval elapsed
                    should_analyze = (
                        len(event_buffer) >= ANALYSIS_BATCH_SIZE
                        or (event_buffer and datetime.now() - last_analysis > timedelta(seconds=ANALYSIS_INTERVAL_SEC))
                    )

                    if should_analyze and event_buffer:
                        issues = engine.analyze(event_buffer)
                        log.stats["analyses_run"] += 1
                        event_buffer.clear()
                        last_analysis = datetime.now()

                        if issues:
                            _report_issues(issues, session_id, log, dry_run)

                    # Save summary periodically
                    if datetime.now() - last_summary_save > timedelta(minutes=2):
                        log.save_summary()
                        last_summary_save = datetime.now()

                    # Re-inject baseline protocols periodically
                    if (not dry_run and
                        datetime.now() - last_baseline_inject > timedelta(seconds=BASELINE_REINJECTION_SEC)):
                        inject_steering_file(session_id, BASELINE_PROTOCOLS)
                        log.event("baseline_reinjected", {})
                        last_baseline_inject = datetime.now()
                        print(f"  <<< BASELINE re-injected")

        except (ConnectionRefusedError, OSError) as e:
            print(f"[SUPERVISOR] Connection failed: {e}. Retrying in {retry_delay}s...")
            log.event("connection_error", {"error": str(e), "retry_in": retry_delay})
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry)

        except websockets.exceptions.ConnectionClosed as e:
            print(f"[SUPERVISOR] Connection closed: {e}. Reconnecting in 5s...")
            log.event("connection_closed", {"reason": str(e)})
            await asyncio.sleep(5)

        except KeyboardInterrupt:
            print(f"\n[SUPERVISOR] Shutting down gracefully.")
            log.save_summary()
            break

        except Exception as e:
            print(f"[SUPERVISOR] Unexpected error: {e}. Retrying in {retry_delay}s...")
            log.event("error", {"error": str(e)})
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry)


# --- Entry Point ---

def main():
    parser = argparse.ArgumentParser(description="Meta-RSI Supervisor")
    parser.add_argument("session_id", help="Target session ID or 'auto' for auto-discovery")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Observe only, no steering")
    args = parser.parse_args()

    # Resolve session ID
    if args.session_id == "auto":
        sessions = discover_sessions()
        self_id = get_self_session_id()
        candidates = [s for s in sessions if s["session_id"] != self_id and s["alive"]]
        if not candidates:
            print("ERROR: No other active sessions found.")
            sys.exit(1)
        # Pick longest-running session
        target = max(candidates, key=lambda s: s.get("uptime_minutes", 0))
        session_id = target["session_id"]
        print(f"[SUPERVISOR] Auto-discovered: {session_id[:8]}... "
              f"(branch: {target.get('branch', '?')}, uptime: {target.get('uptime_display', '?')})")
    else:
        session_id = args.session_id

    log = SupervisorLog(args.log_dir)

    try:
        asyncio.run(supervisor_loop(session_id, log, dry_run=args.dry_run))
    except KeyboardInterrupt:
        pass
    finally:
        log.save_summary()
        print(f"\n[SUPERVISOR] Final stats:")
        print(f"  Events: {log.stats['events_received']}")
        print(f"  Analyses: {log.stats['analyses_run']}")
        print(f"  Steerings: {log.stats['steerings_sent']}")
        print(f"  Issues: {dict(log.stats['issues_detected'])}")
        print(f"  Log: {log.log_file}")


if __name__ == "__main__":
    main()
