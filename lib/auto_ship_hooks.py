"""Post-merge hooks for /auto-ship Stage 6.

Invoked from skills/auto-ship/SKILL.md after a successful PR merge to fire
side-effects (sprint-completion notifications, future hooks) without coupling
the SKILL.md execution flow to in-process state. Each hook is a CLI command
that operates on its own arguments — no shared state, no race conditions
across concurrent /auto-ship invocations.

The CLI never raises into the calling shell — every hook returns exit code 0
even on dispatch failure. Hook outcomes are emitted as a single JSON record
on stderr so /auto-ship's audit log can capture them without a coupled API.

Usage:
    python -m lib.auto_ship_hooks notify-completion \\
        --pr-url https://github.com/grip-platform/GRIP/pull/2409 \\
        --wave-count 3 \\
        --goal "ship 3 backlog tasks"

Exit codes:
    0  Always (informative, never blocking)

Stderr (single line, JSON):
    {"status": "dispatched", "pr_url": ..., "wave_count": ..., "goal": ...,
     "backends": [{"backend": "...", "ok": true|false, "error": null|str}]}
    {"status": "error", "error": "<exception message>"}

Origin: H210 follow-up to PR #2409 — task #1392. Wires
lib/grip_notify.notify_sprint_completion into the actual /auto-ship execution
path so H204 ("≥1 notification fires on the very next /sprint or /rsi-optimal
completion event") has a real call site that fires when /auto-ship's Stage 6
post-merge step runs.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from lib.grip_notify import notify_sprint_completion


def _emit(payload: dict) -> None:
    """Write a single-line JSON record to stderr."""
    print(json.dumps(payload), file=sys.stderr)


def cmd_notify_completion(args: argparse.Namespace) -> int:
    """Dispatch a sprint-completion notification through default_notifier.

    Catches every exception from the dispatch path so a misconfigured
    notifier (missing keychain entry, network down, etc.) cannot fail the
    /auto-ship invocation that called this hook. Notifications are
    informative — sprint completion is the real event, the notification
    just announces it.
    """
    try:
        results = notify_sprint_completion(
            pr_url=args.pr_url,
            wave_count=args.wave_count,
            goal=args.goal,
        )
    except Exception as exc:
        _emit({"status": "error", "error": str(exc)})
        return 0

    backends = [
        {"backend": r.backend, "ok": bool(r.ok), "error": r.error}
        for r in (results or [])
    ]
    _emit({
        "status": "dispatched",
        "pr_url": args.pr_url,
        "wave_count": args.wave_count,
        "goal": args.goal,
        "backends": backends,
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto_ship_hooks",
        description="Post-merge hooks for /auto-ship Stage 6.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_notify = sub.add_parser(
        "notify-completion",
        help="Fire a sprint-completion notification through default_notifier.",
    )
    p_notify.add_argument(
        "--pr-url",
        default=None,
        help="URL of the merged PR. Optional but recommended.",
    )
    p_notify.add_argument(
        "--wave-count",
        type=int,
        default=0,
        help="Number of Fibonacci waves completed. Defaults to 0.",
    )
    p_notify.add_argument(
        "--goal",
        default=None,
        help="Short description of the sprint goal. Optional.",
    )
    p_notify.set_defaults(func=cmd_notify_completion)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
