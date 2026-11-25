"""The command line: ``python -m lib.radar.cli --once [--dry-run] [--seed] [--status] [--source X]``.

``bin/grip-radar`` wraps this under the GRIP venv. The launchd plist runs
``--once`` every 30 minutes. One of ``--once``, ``--dry-run``, ``--seed``,
``--status`` or ``--source`` is required, so a bare call prints usage rather
than sending a message.

Config: ``config/radar.json`` (committed defaults) deep-merged with the
gitignored ``$CLAUDE_DIR/config/radar.local.json`` (per-machine values; a key in
the local file replaces the default, a nested object merges). ``RADAR_FIXTURES=
<dir>`` swaps every source's I/O for the JSON files in that directory, which is
how a reply is rehearsed without a real one. The directory holds
``gmail_search.json`` (a list of thread receipts as ``gmail_search`` returns
them), ``gmail_thread.json`` (one thread with its message receipts) and
``gh.json`` (an API path with the query string stripped, mapped to its JSON
body, plus an optional ``__status__`` object mapping a path to a failing HTTP
status). ``tests/fixtures/radar/seed/`` is a complete example; ``reply/`` adds
a reply to the thread and ``activity/`` a day of GitHub movement.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from claude_dir import get_claude_dir

from .notify import default_send_fn
from .sources import SOURCES
from .state import append_log
from .tick import Runtime, run_tick, status_report

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "radar.json"


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path | None = None, local: Path | None = None) -> dict:
    config = json.loads((path or DEFAULT_CONFIG).read_text(encoding="utf-8"))
    local = local or get_claude_dir() / "config" / "radar.local.json"
    if local.exists():
        config = deep_merge(config, json.loads(local.read_text(encoding="utf-8")))
    return config


def build_runtime(args: argparse.Namespace) -> Runtime:
    config = load_config(Path(args.config) if args.config else None)
    claude_dir = get_claude_dir()
    fixtures = os.environ.get("RADAR_FIXTURES")
    deps = {name: (mod.fixture_deps(Path(fixtures)) if fixtures else mod.default_deps(config))
            for name, mod in SOURCES.items()}
    return Runtime(
        config=config,
        state_path=Path(args.state) if args.state else claude_dir / "state" / "radar" / "state.json",
        log_path=Path(args.log) if args.log else claude_dir / "logs" / "radar.jsonl",
        deps=deps,
        send_fn=default_send_fn(),
        stop_marker=claude_dir / "state" / "session" / "outbound-stop.json",
    )


EPILOG = """\
replay mode:
  RADAR_FIXTURES=<dir> makes every source read its I/O from that directory instead
  of the network, so a reply or a day of activity can be rehearsed without a real
  one. The directory holds:
    gmail_search.json   thread receipts, as lib/grasp_google.gmail_search returns them
    gmail_thread.json   one thread with its message receipts (gmail_thread)
    gh.json             a gh api path (query stripped) -> its JSON body; "__status__"
                        maps a path to a failing HTTP status
    http.json           a URL (query stripped) -> a file name here or an inline body,
                        for the IETF, web-mention and Vercel sources; "__status__" as above
    sc_totals.json, sc_query.json, sc_status.json   Search Console answers and forced
                        failures per property
  tests/fixtures/radar/seed/ is a complete example for the mail and GitHub sources;
  reply/ adds a reply to the thread, activity/ a day of GitHub movement, pr2/ answers
  for every other source. Example:
    RADAR_FIXTURES=tests/fixtures/radar/reply bin/grip-radar --once --dry-run --source mail_replies
"""


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grip-radar", description=__doc__.split("\n\n")[0], epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--once", action="store_true", help="run one tick now (what the launchd plist passes)")
    p.add_argument("--dry-run", action="store_true", help="print what the tick would send; write nothing")
    p.add_argument("--seed", action="store_true", help="record what exists today, alert on none of it")
    p.add_argument("--status", action="store_true", help="per source: mode, last run, next due, last skip")
    p.add_argument("--source", choices=sorted(SOURCES), help="run only this source now, due or not")
    p.add_argument("--config", help="defaults file (default: config/radar.json in this checkout)")
    p.add_argument("--state", help="state file (default: $CLAUDE_DIR/state/radar/state.json)")
    p.add_argument("--log", help="log file (default: $CLAUDE_DIR/logs/radar.jsonl)")
    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    if not (args.once or args.dry_run or args.seed or args.status or args.source):
        p.error("choose one of --once, --dry-run, --seed, --status or --source NAME")
    scheduled = args.once and not (args.dry_run or args.seed or args.status or args.source)  # the plist's call
    try:
        rt = build_runtime(args)
        if args.status:
            print(status_report(rt))
            return 0
        run_tick(rt, dry_run=args.dry_run, seed=args.seed, only=args.source)
    except Exception as exc:  # noqa: BLE001 -- a scheduled tick must never look like a crash to launchd
        print(f"[radar] error: {type(exc).__name__}: {exc}", file=sys.stderr)
        log_path = Path(args.log) if args.log else get_claude_dir() / "logs" / "radar.jsonl"
        append_log(log_path, {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "source": "tick", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        return 0 if scheduled else 1  # a hand-run command reports its failure; launchd never sees a crash
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
