"""update_team — thin scheduler + no-spam dedup wrapper over /aurora.

The canonical "keep V>>'s team informed of his work" engine is `/aurora`
(`lib/aurora_report.py`). This module adds the only logic aurora does NOT
already provide:

  1. a content fingerprint of "what work happened" (merged-PR numbers +
     commit count), so a scheduled tick can SKIP when nothing is new
     (the no-spam gate);
  2. a safe-default DRY-RUN: bare ``update_team()`` renders to stdout and
     never posts;
  3. a post-gate env var (``GRIP_UPDATE_TEAM_POST=1``) on top of the explicit
     ``post=`` arg, so an installed scheduler is OFF until V>> flips it.

The gather → summarise → render → post pipeline is reused verbatim from
``aurora_report`` — this file reimplements NONE of it (DRY / syscall-doctrine).

Three-layer no-spam
-------------------
  fingerprint-skip   — no new PRs/commits since last post → silent no-op.
  GRIP_UPDATE_TEAM_POST  — safe-default OFF; both the arg AND the env must say go.
  slack_transport.STOP_MARKER — global outbound brake, honoured inside post().
  PAUSED constant    — instant kill-switch for every scheduled post.

Public API
----------
update_team(hours_back=24, post=False, channel=DEFAULT_CHANNEL,
            eli5=False, force=False) -> dict
    {posted: bool, reason: str, fingerprint: str, tldr: str}

CLI
---
  python3 -m lib.update_team [--hours-back N] [--post] [--channel C...]
                             [--eli5] [--force] [--dry-run]
  Always exits 0 (a scheduled job must never look like a crash).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# Path-portability: this runs BOTH as a module (python -m lib.update_team) AND as
# a script (the launchd runner / skill calls `python lib/update_team.py`), where
# sys.path[0] is lib/ not the repo root. Put BOTH on the path so `from claude_dir`
# (lib on path) and `import lib.aurora_report` (root on path) both resolve.
# Canonical idiom per rules/path-portability.md (the script-vs-module trap).
_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from claude_dir import get_claude_dir  # noqa: E402
import lib.aurora_report as aurora_report  # noqa: E402

__all__ = [
    "update_team",
    "run_heartbeat", "run_morning", "run_digest", "run_snapshot", "run_audit",
    "DEFAULT_CHANNEL", "PAUSED",
]

# #grip-updates on the CodeTonight workspace (rules/operational-context.md).
DEFAULT_CHANNEL = "C0EXAMPLE01"

# Both the arg AND this env must say go before a real post fires. Safe-default
# OFF mirrors lib/proactive_tick.py's GRIP_PROACTIVE_TICK gate.
POST_GATE_ENV = "GRIP_UPDATE_TEAM_POST"

# Instant kill-switch. Set True to make every
# scheduled tick a no-op without touching launchd. Reversible: set False.
PAUSED = False
PAUSE_REASON = "update_team PAUSED — set PAUSED = False to resume scheduled posts."


def _state_path() -> Path:
    """Last-fingerprint state file. Resolved fresh so tests can repoint CLAUDE_DIR.

    Lives under state/ which is gitignored (state/**/*.json) — local, per-host.
    """
    return get_claude_dir() / "state" / "update-team" / "last-fingerprint.json"


def _fingerprint(digest) -> str:
    """Content key for "what work happened": sorted merged-PR numbers + commit
    count. Stable across renders; changes iff new work landed.

    PR numbers are load-bearing (the precision invariant) — they are the exact
    signal the dedup turns on, so a new merged PR always changes the key.
    """
    pr_numbers = sorted(pr.number for pr in digest.merged_prs)
    basis = json.dumps(
        {"prs": pr_numbers, "commits": digest.commit_count},
        sort_keys=True,
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _read_last_fingerprint() -> str:
    """Last posted fingerprint, or "" if none/unreadable (fail-soft)."""
    p = _state_path()
    if not p.exists():
        return ""
    try:
        return str(json.loads(p.read_text()).get("fingerprint", ""))
    except (json.JSONDecodeError, OSError):
        return ""


def _write_last_fingerprint(fingerprint: str) -> None:
    """Persist the fingerprint of the work we just posted (best-effort)."""
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fingerprint": fingerprint}, indent=2))


def _result(posted: bool, reason: str, fingerprint: str, tldr: str) -> dict:
    """Uniform return shape for every exit path."""
    return {"posted": posted, "reason": reason,
            "fingerprint": fingerprint, "tldr": tldr}


def update_team(
    hours_back: int = 24,
    post: bool = False,
    channel: str = DEFAULT_CHANNEL,
    eli5: bool = False,
    force: bool = False,
) -> dict:
    """Render V>>'s recent work and (optionally, gated) post it to the team.

    Reuses aurora_report for gather/summarise/render/post — reimplements none.

    Args:
        hours_back: window size in hours (default 24).
        post:       request a real Slack post. Still gated by GRIP_UPDATE_TEAM_POST.
        channel:    target Slack channel id (default #grip-updates).
        eli5:       include the HAL-enriched plain-English narrative (fail-soft).
        force:      bypass the fingerprint dedup (post even if nothing is new).

    Returns:
        {posted, reason, fingerprint, tldr}. Never raises on the post path.
    """
    if PAUSED:
        return _result(False, "paused", "", "")

    digest = aurora_report._safe_gather(hours_back)
    summary = aurora_report._safe_summarise(digest, eli5)
    fingerprint = _fingerprint(digest)
    tldr = summary.tldr

    # Layer 1 — no-spam: skip when no new work since the last post.
    if not force and fingerprint == _read_last_fingerprint():
        return _result(False, "no-new-work-since-last", fingerprint, tldr)

    rendered = aurora_report.report(hours_back=hours_back, target="terminal", eli5=eli5)

    # DRY-RUN default: bare update_team() renders to stdout, never posts.
    if not post:
        print(rendered)
        return _result(False, "dry-run", fingerprint, tldr)

    # Layer 2 — post-gate: the env must also opt in (safe-default OFF).
    if os.environ.get(POST_GATE_ENV) != "1":
        print(rendered)
        return _result(False, "post-gate-closed", fingerprint, tldr)

    # Layer 3 (STOP_MARKER) is enforced inside slack_transport.post — free.
    # Pass ONLY kwargs _do_post declares: a drifted kwarg is a TypeError at
    # post time, the one moment the channel is watching (issue #3812). The
    # real-seam anchor is test_post_reaches_real_do_post_signature.
    aurora_report._do_post(hours_back=hours_back, channel=channel)
    _write_last_fingerprint(fingerprint)
    res = _result(True, "posted", fingerprint, tldr)
    # Cryptographic-causation weave (ADDITIVE + FAIL-OPEN): record signed,
    # replayable provenance for THIS post — a /prove-it grounding of its factual
    # claims + an IDR leaf + a memory-chain node — so "GRIP told the team X at
    # time T, backed by source Y" is itself a tamper-evident, replayable record.
    # The post has ALREADY happened above; provenance is best-effort and NEVER
    # blocks the post path (every failure degrades to ok=False inside the call).
    res["provenance"] = _record_post_provenance(digest, summary)
    return res


def _record_post_provenance(digest, summary) -> dict:
    """Fail-open bridge to lib.update_team_provenance. Import is lazy + guarded so
    a broken/absent provenance module can never affect the post path or this
    module's import. Returns a small record dict; ok=False on any failure."""
    try:
        from lib.update_team_provenance import record_post_provenance
        return record_post_provenance(digest, summary)
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        print(f"[update-team] provenance unavailable (post unaffected): {exc}",
              file=sys.stderr)
        return {"ok": False, "reason": f"error: {type(exc).__name__}"}


# ---------------------------------------------------------------------------
# Mode routers — each wired-next mode dispatches here
# ---------------------------------------------------------------------------

def _safe_read(path: Path, default: str = "") -> str:
    """Read a text file; return *default* on any error (fail-soft)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return default


# ---- --heartbeat --------------------------------------------------------

def _heartbeat_gather() -> dict:
    """Collect genome + gen data for the heartbeat payload (fail-soft).

    Returns a dict with string values; any missing field falls back to "?".
    """
    cd = get_claude_dir()
    result: dict[str, str] = {"gen": "?", "fitness": "?", "genes": "?"}
    genome_path = cd / "data" / "genomes" / "grip.json"
    try:
        data = json.loads(genome_path.read_text(encoding="utf-8"))
        result["gen"] = str(data.get("exported_from_gen", "?"))
        result["genes"] = str(data.get("gene_count", "?"))
        gene_vals = [g.get("fitness", 0.0) for g in data.get("genes", {}).values()]
        if gene_vals:
            result["fitness"] = f"{sum(gene_vals) / len(gene_vals):.3f}"
    except (OSError, json.JSONDecodeError, ZeroDivisionError):
        pass
    return result


def run_heartbeat(
    post: bool = False,
    channel: str = DEFAULT_CHANNEL,
    force: bool = False,
) -> dict:
    """--heartbeat: system pulse via CommsUpdate heartbeat template.

    Posting path: same dry-run default + GRIP_UPDATE_TEAM_POST gate + STOP-brake
    as the default mode.  Interactive (no --post): renders a summary to stdout.
    Fail-soft: unavailable engine → honest one-line note, exit 0.
    """
    if PAUSED:
        return {"posted": False, "reason": "paused"}

    try:
        from lib.comms_update import CommsUpdate, SlackAdapter
    except ImportError as exc:
        note = f"[update-team --heartbeat] comms_update unavailable: {exc}"
        print(note)
        return {"posted": False, "reason": "engine-unavailable", "note": note}

    data = _heartbeat_gather()
    body = f"Gen {data['gen']} · fitness {data['fitness']} · {data['genes']} genes"
    update = CommsUpdate(
        headline="GRIP System Pulse",
        body=body,
        fields={"Gen": data["gen"], "Fitness": data["fitness"], "Genes": data["genes"]},
        footer="GRIP heartbeat",
        severity="info",
        origin="update-team --heartbeat",
        template_type="heartbeat",
    )
    blocks = SlackAdapter().render(update)

    if not post:
        print(f"[heartbeat] {body}")
        return {"posted": False, "reason": "dry-run", "body": body}

    if os.environ.get(POST_GATE_ENV) != "1":
        print(f"[heartbeat] {body}  (post-gate closed)")
        return {"posted": False, "reason": "post-gate-closed", "body": body}

    try:
        import lib.slack_transport as slack_transport  # noqa: PLC0415
        slack_transport.post(
            workspace="codetonight",
            channel_id=channel,
            text=f"GRIP System Pulse — {body}",
            blocks=blocks,
        )
        return {"posted": True, "reason": "posted", "body": body}
    except Exception as exc:  # noqa: BLE001 — fail-open
        note = f"[update-team --heartbeat] post failed (fail-soft): {exc}"
        print(note, file=sys.stderr)
        return {"posted": False, "reason": f"post-error:{type(exc).__name__}", "body": body}


# ---- --morning ----------------------------------------------------------

def _morning_calendar(cd: Path) -> str:
    """Return a calendar section string (fail-soft)."""
    gcal = cd / "skills" / "gcal" / "gcal.py"
    if not gcal.exists():
        return "## Today's Calendar\nskills/gcal/gcal.py not found"
    try:
        import subprocess as _sp  # noqa: PLC0415
        r = _sp.run(
            [sys.executable, str(gcal), "development@codetonight.co.za", "--days", "1"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return "## Today's Calendar\n" + r.stdout.strip()
        hint = (r.stderr.strip()[:120] if r.stderr.strip() else "no error detail")
        return f"## Today's Calendar\ncalendar unavailable (gcal.py exited {r.returncode}: {hint})"
    except Exception as exc:  # noqa: BLE001 — fail-soft
        return f"## Today's Calendar\ncalendar unavailable: {type(exc).__name__}: {exc}"


def _morning_remaining(cd: Path) -> str:
    """Return a remaining-work section string from session-context.md (fail-soft)."""
    ctx = _safe_read(cd / "rules" / "session-context.md")
    if not ctx:
        return "## Remaining Work\nrules/session-context.md not found"
    next_lines: list[str] = []
    in_next = False
    for line in ctx.splitlines():
        if line.startswith(("## Next Step", "## Remaining", "## Next:")):
            in_next = True
        elif line.startswith("## ") and in_next:
            break
        if in_next:
            next_lines.append(line)
    return "\n".join(next_lines) if next_lines else "## Remaining Work\n(no Next Step section)"


def run_morning() -> dict:
    """--morning: start-of-day brief (interactive, no posting).

    Step 1 — calendar events via skills/gcal/gcal.py (fail-soft if no auth).
    Step 2 — remaining work from rules/session-context.md.
    """
    cd = get_claude_dir()
    parts = [_morning_calendar(cd), _morning_remaining(cd)]
    output = "\n\n".join(parts)
    print(output)
    return {"posted": False, "reason": "morning-read", "sections": len(parts)}


# ---- --digest -----------------------------------------------------------

def run_digest(post: bool = False, force: bool = False) -> dict:
    """--digest: team Slack channel digest via scripts/daily-team-digest.py.

    Posting path: same dry-run default + GRIP_UPDATE_TEAM_POST gate as the
    default mode.  The script handles its own Slack token loading.
    Fail-soft: script missing / error → honest note, exit 0.
    """
    if PAUSED:
        return {"posted": False, "reason": "paused"}

    cd = get_claude_dir()
    script = cd / "scripts" / "daily-team-digest.py"
    if not script.exists():
        note = f"[update-team --digest] engine not found: {script}"
        print(note)
        return {"posted": False, "reason": "engine-unavailable", "note": note}

    if not post:
        print(f"[update-team --digest] dry-run — would run: {script}")
        return {"posted": False, "reason": "dry-run"}

    if os.environ.get(POST_GATE_ENV) != "1":
        print("[update-team --digest] post-gate closed (set GRIP_UPDATE_TEAM_POST=1)")
        return {"posted": False, "reason": "post-gate-closed"}

    try:
        import subprocess as _sp  # noqa: PLC0415
        r = _sp.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=120,
        )
        if r.stdout:
            print(r.stdout, end="")
        if r.returncode != 0:
            note = (
                f"[update-team --digest] script exited {r.returncode}: "
                + (r.stderr.strip()[:200] if r.stderr.strip() else "(no stderr)")
            )
            print(note, file=sys.stderr)
            return {"posted": False, "reason": f"script-exit-{r.returncode}", "note": note}
        return {"posted": True, "reason": "posted"}
    except Exception as exc:  # noqa: BLE001 — fail-soft
        note = f"[update-team --digest] failed (fail-soft): {exc}"
        print(note, file=sys.stderr)
        return {"posted": False, "reason": f"error:{type(exc).__name__}", "note": note}


# ---- --snapshot / --status ----------------------------------------------

def run_snapshot() -> dict:
    """--snapshot / --status: live status snapshot (interactive, no posting).

    Reads rules/session-context.md and prints a three-bucket view.
    The full HITL close/delegate/defer wizard is /whoa-update-pls (interactive
    session only); this mode is the read-only snapshot layer.
    """
    cd = get_claude_dir()
    ctx = _safe_read(cd / "rules" / "session-context.md", "(session-context.md not found)")
    print("## GRIP Status Snapshot\n")
    # Trim to a reasonable window to avoid token waste in large context files
    print(ctx[:4000])
    if len(ctx) > 4000:
        print("\n... (truncated — run /whoa-update-pls for full interactive view)")
    print("\n(Full HITL wizard: /whoa-update-pls)")
    return {"posted": False, "reason": "snapshot-read", "chars": len(ctx)}


# ---- --audit / --reflect ------------------------------------------------

def run_audit() -> dict:
    """--audit / --reflect: reasoning audit via lib.reasoning_audit (no posting).

    Catalogues session achievements, classifies each claim, applies falsification
    tests, and prints a plain-language summary.
    Fail-soft: unavailable engine → honest note, exit 0.
    """
    try:
        from lib.reasoning_audit import run_audit as _audit  # noqa: PLC0415
        result = _audit()
        if result.summary:
            print(result.summary)
        else:
            print("[update-team --audit] no achievements found in session context")
        return {
            "posted": False,
            "reason": "audit-complete",
            "total": result.total_claims,
            "strong_passes": result.strong_passes,
        }
    except ImportError as exc:
        note = f"[update-team --audit] reasoning_audit unavailable: {exc}"
        print(note)
        return {"posted": False, "reason": "engine-unavailable", "note": note}
    except Exception as exc:  # noqa: BLE001 — fail-soft
        note = f"[update-team --audit] failed (fail-soft): {exc}"
        print(note)
        return {"posted": False, "reason": f"error:{type(exc).__name__}", "note": note}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scheduled, deduped team work-update over /aurora + mode router.",
    )
    # Default-mode args
    p.add_argument("--hours-back", type=int, default=24,
                   help="window size in hours (default: 24, default mode only)")
    p.add_argument("--post", action="store_true",
                   help="request a real post (still needs GRIP_UPDATE_TEAM_POST=1)")
    p.add_argument("--channel", default=DEFAULT_CHANNEL,
                   help=f"target Slack channel id (default: {DEFAULT_CHANNEL})")
    p.add_argument("--eli5", action="store_true",
                   help="include the plain-English HAL narrative (fail-soft, default mode)")
    p.add_argument("--force", action="store_true",
                   help="bypass the no-new-work dedup (default mode) / force-post (heartbeat/digest)")
    p.add_argument("--dry-run", action="store_true",
                   help="render only; never post (overrides --post)")
    p.add_argument("--all", action="store_true",
                   help="run EVERY mode in sequence (default work-update + heartbeat + "
                        "morning + snapshot + audit + digest); fail-soft, posting modes "
                        "still respect --post + GRIP_UPDATE_TEAM_POST")
    # Mode flags (mutually exclusive in intent; last one wins if multiple)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--heartbeat", action="store_true",
                      help="system pulse (genome / session state) → #grip-updates")
    mode.add_argument("--morning", action="store_true",
                      help="start-of-day brief: calendar + remaining work (interactive)")
    mode.add_argument("--digest", action="store_true",
                      help="team Slack channel-activity digest via daily-team-digest.py")
    mode.add_argument("--snapshot", "--status", "--catch-up", dest="snapshot", action="store_true",
                      help="live status snapshot / catch-me-up (interactive, no posting)")
    mode.add_argument("--audit", "--reflect", dest="audit", action="store_true",
                      help="session reasoning audit via lib/reasoning_audit.py (interactive)")
    return p


def _run_all(args: argparse.Namespace, do_post: bool) -> int:
    """--all: run every mode in sequence, fail-soft (one mode's failure never
    aborts the rest). Posting modes still honour the dry-run/post-gate."""
    results: dict = {}
    modes = [
        ("default (work-update)", lambda: update_team(
            hours_back=args.hours_back, post=do_post, channel=args.channel,
            eli5=args.eli5, force=args.force)),
        ("heartbeat", lambda: run_heartbeat(
            post=do_post, channel=args.channel, force=args.force)),
        ("morning", run_morning),
        ("snapshot", run_snapshot),
        ("audit", run_audit),
        ("digest", lambda: run_digest(post=do_post, force=args.force)),
    ]
    for label, fn in modes:
        print(f"\n{'=' * 60}\n== /update-team --all · {label}\n{'=' * 60}")
        try:
            results[label] = fn()
        except Exception as exc:  # noqa: BLE001 — fail-soft; one mode never aborts --all
            results[label] = {"posted": False, "reason": f"error:{type(exc).__name__}"}
            print(f"[update-team --all] {label} failed (fail-soft): {exc}", file=sys.stderr)
    print("[update-team --all] ran %d modes: %s" % (
        len(results),
        ", ".join("%s=%s" % (k, v.get("reason")) for k, v in results.items())),
        file=sys.stderr)
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    do_post = args.post and not args.dry_run

    # --all: run every mode in sequence (fail-soft) — extracted to keep main lean.
    if args.all:
        return _run_all(args, do_post)

    # Dispatch to the right mode router
    if args.heartbeat:
        res = run_heartbeat(post=do_post, channel=args.channel, force=args.force)
        print(f"[update-team --heartbeat] posted={res['posted']} reason={res['reason']}",
              file=sys.stderr)
    elif args.morning:
        res = run_morning()
        print(f"[update-team --morning] reason={res['reason']}", file=sys.stderr)
    elif args.digest:
        res = run_digest(post=do_post, force=args.force)
        print(f"[update-team --digest] posted={res['posted']} reason={res['reason']}",
              file=sys.stderr)
    elif args.snapshot:
        res = run_snapshot()
        print(f"[update-team --snapshot] reason={res['reason']}", file=sys.stderr)
    elif args.audit:
        res = run_audit()
        print(f"[update-team --audit] reason={res['reason']}", file=sys.stderr)
    else:
        # Default mode: scheduled aurora-based update
        res = update_team(
            hours_back=args.hours_back,
            post=do_post,
            channel=args.channel,
            eli5=args.eli5,
            force=args.force,
        )
        # The render already printed on dry-run/gate-closed paths; surface the verdict.
        print(f"[update-team] posted={res['posted']} reason={res['reason']} "
              f"fp={res['fingerprint']}", file=sys.stderr)

    return 0  # always 0 — a scheduled job must never look like a crash.


if __name__ == "__main__":
    raise SystemExit(main())
