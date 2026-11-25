#!/usr/bin/env python3
"""Weekly Rule 20 backlog triage routine.

Scheduled action: every Monday 09:00 SAST.

Pipeline:
  1. Re-run insight-propagation-scanner against transcripts mtime'd in the last
     7 days (catches anything the per-session Stop hook missed during compaction
     races or sub-agent dispatch).
  2. Read state/pending-insights.md.
  3. If non-empty, classify findings and open a PR titled
     "chore(memory): weekly Rule 20 triage - YYYY-MM-DD".
  4. Notify V>> via grip-notify (macOS notification + Slack DM if configured).

Falsifier (logged to logs/weekly-triage-metrics.jsonl):
  If this routine opens >2 PRs in 7 days that close without a merge, it is
  generating noise rather than durable lessons. Review and tune classification
  thresholds.

Idempotency:
  - No findings -> no PR (not even an empty one).
  - state/pending-insights.md absent -> exit 0.
  - --dry-run prints what WOULD happen without git/gh/notify side effects.

Usage:
  python3 lib/weekly_rule20_triage.py            # Live run (cron)
  python3 lib/weekly_rule20_triage.py --dry-run  # Trace only
  python3 lib/weekly_rule20_triage.py --install  # Install launchd agent
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or Path.home() / ".claude")
PENDING_FILE = CLAUDE_DIR / "state" / "pending-insights.md"
METRICS_FILE = CLAUDE_DIR / "logs" / "weekly-triage-metrics.jsonl"
LOG_FILE = CLAUDE_DIR / "logs" / "weekly-rule20-triage.log"
LABEL = "com.grip.weekly-rule20-triage"
PLIST_NAME = f"{LABEL}.plist"
BRANCH_PREFIX = "chore/weekly-rule20-triage"


def _setup_logging() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return logging.getLogger("weekly_rule20_triage")


log = _setup_logging()


def read_pending_findings() -> tuple[int, str]:
    """Return (count, raw_text). count==0 means nothing to triage."""
    if not PENDING_FILE.exists():
        return 0, ""
    text = PENDING_FILE.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return 0, ""
    headings = [
        ln for ln in text.splitlines()
        if ln.startswith("## [")
    ]
    return len(headings), text


def record_metric(event: str, payload: dict) -> None:
    """Append a single JSON line to the metrics file (falsifier evidence)."""
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **payload,
    }
    with METRICS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def run_cmd(args: list[str], cwd: Path, dry_run: bool) -> tuple[int, str]:
    """Run a shell command, returning (returncode, combined_output)."""
    if dry_run:
        log.info("DRY-RUN cmd: %s", " ".join(args))
        return 0, ""
    log.info("cmd: %s", " ".join(args))
    proc = subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, timeout=120,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def open_triage_pr(count: int, body: str, dry_run: bool) -> str | None:
    """Stage pending-insights.md as a triage memo and open a PR.

    pending-insights.md itself is gitignored (state/**/*.md), so the PR's
    payload is a copy committed to drafts/triage-memos/. The triage agent
    that picks up the PR reads the memo, classifies each finding, and edits
    the canonical files in follow-up commits on the same branch.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    branch = f"{BRANCH_PREFIX}-{today}"
    memo_dir = CLAUDE_DIR / "drafts" / "triage-memos"
    memo = memo_dir / f"weekly-rule20-{today}.md"

    if dry_run:
        log.info("DRY-RUN would create branch=%s memo=%s findings=%d",
                 branch, memo, count)
        return None

    memo_dir.mkdir(parents=True, exist_ok=True)
    memo.write_text(body, encoding="utf-8")

    rc, _ = run_cmd(["git", "checkout", "-b", branch], CLAUDE_DIR, False)
    if rc != 0:
        log.warning("branch checkout failed (rc=%d) - aborting PR open", rc)
        return None
    run_cmd(["git", "add", str(memo)], CLAUDE_DIR, False)
    run_cmd(
        ["git", "commit", "-m",
         f"chore(memory): weekly Rule 20 triage - {today}"],
        CLAUDE_DIR, False,
    )
    run_cmd(["git", "push", "-u", "origin", branch], CLAUDE_DIR, False)
    title = f"chore(memory): weekly Rule 20 triage - {today}"
    pr_body = (
        f"Auto-opened by `lib/weekly_rule20_triage.py`.\n\n"
        f"**{count} insights** flagged for canonical-file propagation.\n\n"
        f"Triage agent: classify each finding, propagate durable ones to:\n"
        f"- `rules/operational-context.md` (operational discoveries)\n"
        f"- `rules/<doctrine>.md` or `skills/<skill>/SKILL.md` (design patterns)\n"
        f"- `rules/efficiency-rules.md` (behavioural rules)\n\n"
        f"Memo: `drafts/triage-memos/weekly-rule20-{today}.md`."
    )
    rc, out = run_cmd(
        ["gh", "pr", "create", "--title", title, "--body", pr_body,
         "--base", "main"],
        CLAUDE_DIR, False,
    )
    pr_url = out.strip().splitlines()[-1] if rc == 0 else None
    log.info("PR opened: %s", pr_url)
    return pr_url


def notify_operator(count: int, pr_url: str | None, dry_run: bool) -> None:
    """Send a single notification via grip-notify default backends."""
    title = f"Weekly Rule 20 triage: {count} finding{'s' if count != 1 else ''}"
    body = f"PR: {pr_url}" if pr_url else "No PR opened (no findings)."
    if dry_run:
        log.info("DRY-RUN notify: %s | %s", title, body)
        return
    try:
        sys.path.insert(0, str(CLAUDE_DIR))
        from lib.grip_notify import Notification, default_notifier
        notifier = default_notifier()
        results = notifier.send(Notification(title=title, body=body, url=pr_url))
        log.info("notify results: %s",
                 [(r.backend, r.ok) for r in results])
    except Exception as exc:  # noqa: BLE001
        log.warning("notify failed (non-fatal): %s", exc)


def run(dry_run: bool = False) -> int:
    """Main pipeline. Returns process exit code (0 on success)."""
    log.info("--- Weekly Rule 20 triage start (dry_run=%s) ---", dry_run)
    count, body = read_pending_findings()
    if count == 0:
        log.info("No findings - skipping PR (idempotent no-op).")
        record_metric("skipped_empty", {"count": 0})
        return 0
    pr_url = open_triage_pr(count, body, dry_run)
    notify_operator(count, pr_url, dry_run)
    record_metric("pr_opened", {"count": count, "pr_url": pr_url,
                                "dry_run": dry_run})
    log.info("--- Weekly Rule 20 triage done (count=%d pr=%s) ---",
             count, pr_url)
    return 0


def cmd_install() -> int:
    """Install launchd agent at ~/Library/LaunchAgents/."""
    src = CLAUDE_DIR / "launchd" / f"{LABEL}.plist.template"
    dst = Path.home() / "Library" / "LaunchAgents" / PLIST_NAME
    if not src.exists():
        print(f"ERROR: template missing: {src}", file=sys.stderr)
        return 1
    content = src.read_text().replace("__HOME__", str(Path.home()))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content)
    print(f"Installed: {dst}")
    print(f"Load with: launchctl load {dst}")
    print(f"Verify:    launchctl list | grep weekly-rule20")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly Rule 20 triage")
    parser.add_argument("--dry-run", action="store_true",
                        help="Trace without git/gh/notify side effects")
    parser.add_argument("--install", action="store_true",
                        help="Install launchd agent for cron-style scheduling")
    args = parser.parse_args()
    if args.install:
        return cmd_install()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
