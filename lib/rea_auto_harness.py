#!/usr/bin/env python3
"""REA Auto-Harness -- the keep-up loop for the REA tool server (PR 2).

A thin configuration of `lib/cc_auto_harness.py`'s release-absorption loop for
the REA ("Reverse Engineer Anything") MCP tool server (`npm` package
``rea-agents``). The upstream releases every few hours through Release-Please
with tags like ``rea-agents-6.3.0`` and CHANGELOG-shaped bodies; this module
keeps GRIP level with them by fetching, scoring and tracking each feature line.

It reuses, never reimplements, the pure helpers from ``lib/cc_auto_harness.py``
(``_check_feed``, ``_versions_in``, ``_write_release``, ``_parse_release_features``,
``_version_key``, ``RefreshError``, and the ``_fetch_releases_from_github`` seam)
and ``lib/cc_feature_scanner.py`` (``_keyword_pattern``, ``resolve_feature_title``,
``_harness_key``, ``HIGH_VALUE_CUTOFF``), and the REA CLI surface from
``lib/rea_cli.py``. The cc module globals (``RELEASES_DIR``, ``HARNESS_STATE_FILE``,
...) are intentionally NOT refactored into a shared spec -- they are pinned by
name in ten test files.

Decisions (smallest-reasonable, recorded rather than guessed):
- ``_run_rea(argv, timeout_s=60.0)`` takes an optional ``timeout_s`` (default 60)
  so it can serve directly as ``rea_cli``'s ``(argv, timeout_s)`` runner seam;
  the brief's default ``timeout=60`` is unchanged.
- Scoring reads ``KEYWORD_SCORES`` at CALL time (compiling each group's pattern
  lazily inside ``_score_text``), not at import. That is what lets a test patch
  ``KEYWORD_SCORES`` to ``[]`` and observe scoring fall to zero. ``["fix"] -> 5``
  is a keyword group (there is no separate ``BUG_FIX_SCORE`` here): a
  ``Fixed ...`` bullet scores 5 because "fix" is a keyword.
- ``refresh_releases(fetch=None)``: ``fetch`` is a zero-arg callable returning
  the raw release-dict list, standing in for
  ``_fetch_releases_from_github(repo=REA_REPO)``. No archive handling: REA has no
  session pruner, so a live-only directory is the honest smallest shape. A feed
  that is not a list (a GitHub error object) raises ``RefreshError``.
- Tags: 32 of REA's 34 releases are ``rea-agents-N.N.N``; the two oldest
  (2026-07-12) are ``rea-N.N.N``. ``_TAG_PREFIX_RE`` strips either prefix and a
  leading ``v``; the first live refresh wrote ``vrea-0.2.0.json`` before this.
- ``get_unharnessed(since_version=...)``: ``unharnessed`` and ``dashboard``
  default to lines from releases NEWER than the pinned version, because the pin
  is the release GRIP integrated wholesale (GRIP#7964 classified all 139 tools).
  The first live run listed 406 undecided lines, none newer than 6.3.0 -- that
  is history, not work; ``unharnessed --all`` shows it.
- ``record_status`` stores the raw doctor dict under ``doctor``, sets
  ``doctor_ok`` to the report's ``healthy`` field (None when the doctor is
  unavailable -- a missing binary or a timeout is recorded, never raised), and
  counts providers from REA's own provider metadata: ``missing_analysis_engine``
  rows in ``scope_checks`` plus ``providerInspections`` entries that are
  ``available`` (see ``_doctor_summary``).

Every ``mark`` and ``defer`` also seals the decision into the IDR forest and
the memory chain through ``lib/harness_ledger_seal.py``, the one helper both
this ledger and the parent ``cc_feature_scanner`` ledger call after their
write (GRIP#7969). The ledger keeps the operator's prose as written; the leaf
carries it after the redaction shield, or withholds it and says so. Because
the module now writes signed records, ``__main__`` re-executes into a capable
interpreter first (``pq_reexec``), and the file carries a shebang so the
``tests/test_pq_reexec.py`` walk can see it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# The sibling modules are imported from THIS checkout, never from the shared
# tree. Putting the checkout root first on sys.path makes `from lib.X import ...`
# resolve to this worktree's copy -- the same guard cc_auto_harness uses so a
# checkout under test does not silently load `~/.claude`'s scanner.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if not sys.path or sys.path[0] != _REPO_ROOT:
    sys.path.insert(0, _REPO_ROOT)

from lib.cc_auto_harness import (  # noqa: E402
    RefreshError,
    _check_feed,
    _fetch_releases_from_github,
    _parse_release_features,
    _version_key,
    _versions_in,
    _write_release,
)
from lib.cc_feature_scanner import (  # noqa: E402
    HIGH_VALUE_CUTOFF,
    _harness_key,
    _keyword_pattern,
    resolve_feature_title,
)
from lib import rea_cli  # noqa: E402
from lib.atomic_write import atomic_write  # noqa: E402
from lib.claude_dir import get_claude_dir  # noqa: E402
from lib.file_lock import FileLock  # noqa: E402
from lib.harness_ledger_seal import seal_harness_decision  # noqa: E402

# ── Module constants ───────────────────────────────────────────────────

REA_REPO = "morluto/rea"
# Measured on the live feed 2026-10-10: 32 of REA's 34 release tags are
# rea-agents-N.N.N; the two oldest (2026-07-12) are rea-N.N.N. Either prefix and
# a leading "v" strip to the bare version. The first live refresh stripped only
# the long prefix and wrote vrea-0.2.0.json.
_TAG_PREFIX_RE = re.compile(r"^(?:rea-agents-|rea-)?v?")

CLAUDE_DIR = get_claude_dir()
REA_RELEASES_DIR = CLAUDE_DIR / "data" / "rea-releases"
REA_HARNESS_STATE_FILE = REA_RELEASES_DIR / "harness-state.json"
REA_STATUS_FILE = CLAUDE_DIR / "data" / "rea-harness-status.jsonl"

# Each keyword group maps to a relevance score for REA auto-harnessing, highest
# match wins. Read at call time (see the module docstring) so a test can patch
# it to [] and watch scoring collapse.
KEYWORD_SCORES: list[tuple[list[str], int, str]] = [
    (
        ["tool", "mcp", "server", "schema"],
        25,
        "Tool/MCP/server/schema: the REA MCP surface GRIP attaches",
    ),
    (
        ["doctor", "provider", "ready", "install", "version"],
        25,
        "Doctor/provider/install: install & readiness, the keep-up target",
    ),
    (
        ["binary", "inspect", "disasm", "decompil", "symbol", "string", "mach", "elf", "pe"],
        20,
        "Binary inspection: reverse-engineering core (Hopper/Ghidra/IDA)",
    ),
    (
        ["agent", "workflow", "report", "evidence"],
        15,
        "Agent/workflow/report: higher-level REA surfaces",
    ),
    (
        ["ui", "web", "docs"],
        5,
        "UI/web/docs: low priority for the headless MCP attach",
    ),
    (
        ["fix"],
        5,
        "Fix: a bug-fix bullet is low harnessing priority",
    ),
]


# ── Scoring ────────────────────────────────────────────────────────────

def _score_text(combined: str) -> tuple[int, str]:
    """Highest keyword-group score for a feature line, and its rationale."""
    best_score = 0
    best_rationale = "No keyword match"
    for keywords, score, rationale in KEYWORD_SCORES:
        if score > best_score and _keyword_pattern(keywords).search(combined):
            best_score = score
            best_rationale = rationale
    return best_score, best_rationale


def _split_ledger(ledger: dict) -> tuple[set[str], dict[str, str]]:
    """Harnessed keys, and deferred keys mapped to their reasons."""
    harnessed = {key for key, rec in ledger.items() if rec.get("status") != "deferred"}
    deferred = {
        key: str(rec.get("reason") or rec.get("how") or "no reason recorded")
        for key, rec in ledger.items()
        if rec.get("status") == "deferred"
    }
    return harnessed, deferred


def _score_feature(
    feature: dict,
    version: str,
    harnessed_keys: set[str],
    deferred: dict[str, str],
) -> dict:
    """Score one feature line, marking it harnessed/deferred when the ledger says so."""
    key = _harness_key(version, feature.get("title", ""))
    if key in harnessed_keys:
        return {**feature, "version": version, "score": 0,
                "rationale": "Already harnessed", "harnessed": True, "deferred": False}
    if key in deferred:
        return {**feature, "version": version, "score": 0,
                "rationale": f"Deferred: {deferred[key]}", "harnessed": False, "deferred": True}
    combined = f"{feature.get('title', '')} {feature.get('description', '')}".lower()
    score, rationale = _score_text(combined)
    return {**feature, "version": version, "score": score,
            "rationale": rationale, "harnessed": False, "deferred": False}


# ── Release files ──────────────────────────────────────────────────────

def _read_release(path: Path) -> dict | None:
    """Load one release JSON, or None when it is unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _scored_release(version: str, harnessed_keys: set[str], deferred: dict[str, str]) -> list[dict]:
    """Every feature line of one cached release, scored; [] when unreadable."""
    data = _read_release(REA_RELEASES_DIR / f"v{version}.json")
    if data is None:
        return []
    release_version = data.get("version", version)
    return [_score_feature(feature, release_version, harnessed_keys, deferred)
            for feature in data.get("features", [])]


def scan_releases() -> list[dict]:
    """Scan every cached REA release file and return scored feature lines."""
    REA_RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    harnessed_keys, deferred = _split_ledger(state.get("harnessed", {}))
    scored: list[dict] = []
    for version in sorted(_versions_in(REA_RELEASES_DIR), key=_version_key):
        scored.extend(_scored_release(version, harnessed_keys, deferred))
    scored.sort(key=lambda f: f["score"], reverse=True)
    return scored


def get_unharnessed(min_score: int = HIGH_VALUE_CUTOFF, since_version: str | None = None) -> list[dict]:
    """Undecided feature lines scoring at or above ``min_score``; with
    ``since_version``, only lines from releases strictly NEWER than it (the
    keep-up view: the pinned release was integrated wholesale)."""
    floor = _version_key(since_version) if since_version else None
    return [
        f for f in scan_releases()
        if not f["harnessed"] and not f.get("deferred") and f["score"] >= min_score
        and (floor is None or _version_key(f["version"]) > floor)
    ]


# ── Refresh (fetch + write) ────────────────────────────────────────────

def _rea_release_record(rel: dict) -> dict:
    """One REA release in the on-disk JSON shape the scanner reads."""
    tag = rel.get("tag_name") or ""
    version = _TAG_PREFIX_RE.sub("", tag, count=1)
    body = rel.get("body") or ""
    features = _parse_release_features(body)
    return {
        "version": version,
        "date": (rel.get("published_at") or "")[:10],
        "features": features,
        "feature_count": len(features),
        "raw_body": body[:3000],
    }


def refresh_releases(fetch=None) -> dict:
    """Fetch the REA feed and write new release files.

    ``fetch`` is a zero-arg callable returning the raw release-dict list; when
    None, ``_fetch_releases_from_github(repo=REA_REPO)`` is used. Returns
    ``{"newest": str, "wrote": int, "skipped": int}``; an older-than-known feed
    raises ``RefreshError`` (mirroring cc's ``_check_feed``).
    """
    raw = _fetch_releases_from_github(repo=REA_REPO) if fetch is None else fetch()
    if not isinstance(raw, list):  # a GitHub error object is a dict (GRIP#7970)
        raise RefreshError(f"REA release feed returned {type(raw).__name__}, expected a list")
    records = [r for r in (_rea_release_record(x) for x in raw) if r["version"]]
    known = _versions_in(REA_RELEASES_DIR)
    newest = _check_feed(records, known)
    REA_RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    wrote = skipped = 0
    for record in records:
        if _write_release(REA_RELEASES_DIR / f"v{record['version']}.json", record):
            wrote += 1
        else:
            skipped += 1
    return {"newest": newest, "wrote": wrote, "skipped": skipped}


# ── Harness state (mark / defer) ───────────────────────────────────────

def _load_state() -> dict:
    if REA_HARNESS_STATE_FILE.exists():
        try:
            return json.loads(REA_HARNESS_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"harnessed": {}}


def _save_state(state: dict) -> None:
    REA_HARNESS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        REA_HARNESS_STATE_FILE,
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
    )


@contextmanager
def _state_txn():
    """Load, yield, then save the ledger under one exclusive sidecar lock."""
    REA_HARNESS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(REA_HARNESS_STATE_FILE, timeout=10.0, sidecar=True):
        state = _load_state()
        before = json.dumps(state, sort_keys=True)
        yield state
        if json.dumps(state, sort_keys=True) != before:
            _save_state(state)


def _titles_by_version() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for feature in scan_releases():
        grouped.setdefault(feature["version"], []).append(feature["title"])
    return grouped


def _record_decision(version: str, feature_title: str, fields: dict) -> dict:
    """Write one ledger decision under the feature's canonical title."""
    grouped = _titles_by_version()
    canonical, matched_as = resolve_feature_title(feature_title, grouped.get(version, []))
    stored_title = canonical or feature_title
    record = {
        "version": version,
        "title": stored_title,
        **fields,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "matched_as": matched_as,
    }
    if canonical is None:
        record["unresolved"] = True
    key = _harness_key(version, stored_title)
    with _state_txn() as state:
        state.setdefault("harnessed", {})[key] = record
    # The ledger line is a claim; the leaf and the memory record make it a
    # signed decision. Sealing raises if either cannot be read back, so a
    # decision never sits in the ledger silently unsealed (GRIP#7969).
    seal = seal_harness_decision("rea", key, record)
    return {"canonical": canonical, "matched_as": matched_as, "record": record, "seal": seal}


def mark_harnessed(version: str, feature_title: str, how: str) -> dict:
    """Record a feature as harnessed, keyed on its canonical title."""
    return _record_decision(version, feature_title, {"how": how})


def mark_deferred(version: str, feature_title: str, reason: str) -> dict:
    """Record a decision NOT to harness a feature, with the reason."""
    if not reason.strip():
        raise ValueError("a deferral needs a reason")
    return _record_decision(version, feature_title,
                            {"status": "deferred", "reason": reason.strip()})


# ── Subprocess seam ────────────────────────────────────────────────────

def _run_rea(argv: list[str], timeout_s: float = 60.0) -> subprocess.CompletedProcess:
    """The single subprocess seam, shaped to ``rea_cli``'s ``(argv, timeout_s)``
    runner so tests replace it whole and need no REA installed."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)


# ── Drift / doctor / status ────────────────────────────────────────────

def latest_version() -> str | None:
    """The newest cached release version, or None when nothing is cached."""
    versions = _versions_in(REA_RELEASES_DIR)
    return max(versions, key=_version_key) if versions else None


def _installed_behind(installed: str | None, latest: str | None) -> bool | None:
    """True when installed is older than latest; False when equal/newer; None unknown."""
    if installed is None or latest is None:
        return None
    return _version_key(installed) < _version_key(latest)


def drift(runner=None) -> dict:
    """Installed vs pinned vs latest, for a status line to print."""
    r = _run_rea if runner is None else runner
    d = rea_cli.version_drift(r)
    latest = latest_version()
    return {
        "installed": d["installed"],
        "pinned": d["pinned"],
        "latest": latest,
        "installed_behind_latest": _installed_behind(d["installed"], latest),
    }


def doctor(runner=None) -> dict:
    """``rea doctor --json`` as a dict, reusing ``lib.rea_cli.doctor``."""
    return rea_cli.doctor(_run_rea if runner is None else runner)


def _doctor_summary(report: dict | None) -> tuple[bool | None, int, int]:
    """(healthy, providers_ready, providers_total) from a doctor report.

    A provider is identified by REA's own metadata, never by a bare
    ``classification == "healthy"``: ``scope_checks`` also carries node, host,
    registration and skill rows (Codex review of GRIP#7967). A
    ``missing_analysis_engine`` row IS a provider that is absent; a
    ``providerInspections`` entry with ``available`` true IS a provider that is
    ready. Measured on 6.3.0 (audit-wide): scope_checks names hopper, ghidra
    and ida-registration as missing engines while providerInspections lists
    only ghidra and ida, so neither source alone is complete. A provider cannot
    be both missing and available, so the union never double-counts.
    """
    if not isinstance(report, dict):
        return None, 0, 0
    missing = {c.get("name") for c in report.get("scope_checks") or []
               if c.get("classification") == "missing_analysis_engine" and c.get("name")}
    ready = {p.get("id") for p in report.get("providerInspections") or []
             if p.get("available") and p.get("id")}
    return report.get("healthy"), len(ready), len(missing | ready)


def record_status(runner=None) -> dict:
    """Append one JSONL status row to ``REA_STATUS_FILE`` and return it."""
    r = _run_rea if runner is None else runner
    d = rea_cli.version_drift(r)
    try:
        report = rea_cli.doctor(r)
    except rea_cli.ReaError:
        report = None
    doctor_ok, ready, total = _doctor_summary(report)
    row = {
        "ts": datetime.now().isoformat(),
        "installed": d["installed"],
        "pinned": d["pinned"],
        "latest": latest_version(),
        "doctor_ok": doctor_ok,
        "providers_ready": ready,
        "providers_total": total,
        "doctor": report,
    }
    REA_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REA_STATUS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


# ── CLI ────────────────────────────────────────────────────────────────

def _last_status_row() -> dict | None:
    """The newest row from ``REA_STATUS_FILE``, or None when absent."""
    if not REA_STATUS_FILE.exists():
        return None
    last = None
    with open(REA_STATUS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def _cmd_refresh(_args: list[str]) -> None:
    summary = refresh_releases()
    print(f"newest v{summary['newest']}; wrote {summary['wrote']}; skipped {summary['skipped']}")


def _since_pin(args: list[str]) -> str | None:
    """None (everything) when ``--all`` is passed; otherwise the pinned version,
    so the default view is what has shipped SINCE GRIP's integration."""
    return None if "--all" in args else rea_cli.pinned_version()


def _cmd_unharnessed(args: list[str]) -> None:
    since = _since_pin(args)
    rows = get_unharnessed(since_version=since)
    for f in rows:
        print(f"[{f['score']:>3}] v{f['version']} {f['title']}")
    if not rows and since is not None:
        print(f"nothing undecided newer than the v{since} pin (--all lists the back-catalogue)")


def _print_seal(outcome: dict) -> None:
    seal = outcome["seal"]
    print(f"  sealed: {seal['idr_id']} ({seal['custody']})")


def _cmd_mark(args: list[str]) -> None:
    version, title, how = args[0], args[1], " ".join(args[2:])
    outcome = mark_harnessed(version, title, how)
    print(f"Marked: {version} - {title}")
    _print_seal(outcome)


def _cmd_defer(args: list[str]) -> None:
    version, title, reason = args[0], args[1], " ".join(args[2:])
    outcome = mark_deferred(version, title, reason)
    print(f"Deferred: {version} - {title}")
    _print_seal(outcome)


def _cmd_drift(_args: list[str]) -> None:
    print(json.dumps(drift(), indent=2))


def _cmd_doctor(_args: list[str]) -> None:
    print(json.dumps(doctor(), indent=2))


def _cmd_status(_args: list[str]) -> None:
    print(json.dumps(record_status(), indent=2))


def _cmd_dashboard(_args: list[str]) -> None:
    d = drift()
    since_pin = get_unharnessed(since_version=d["pinned"])
    everything = get_unharnessed()
    last = _last_status_row()
    print("REA Auto-Harness Dashboard")
    print("-" * 60)
    print(f"Installed: {d['installed']}  Pinned: {d['pinned']}  Latest: {d['latest']}")
    print(f"Installed behind latest: {d['installed_behind_latest']}")
    print(f"Unharnessed newer than the pin (score >= {HIGH_VALUE_CUTOFF}): {len(since_pin)}")
    print(f"Back-catalogue undecided (at or before the pin, history): {len(everything) - len(since_pin)}")
    if last:
        print(f"Last status: {last.get('ts')} "
              f"doctor_ok={last.get('doctor_ok')} "
              f"providers {last.get('providers_ready')}/{last.get('providers_total')}")


_DISPATCH = {
    "refresh": _cmd_refresh,
    "unharnessed": _cmd_unharnessed,
    "mark": _cmd_mark,
    "defer": _cmd_defer,
    "drift": _cmd_drift,
    "doctor": _cmd_doctor,
    "status": _cmd_status,
    "dashboard": _cmd_dashboard,
}


def main(argv) -> None:
    """CLI entry point: ``rea_auto_harness.py <refresh|unharnessed|mark|defer|drift|doctor|status|dashboard>``."""
    cmd = argv[1] if len(argv) > 1 else "dashboard"
    handler = _DISPATCH.get(cmd)
    if handler is None:
        print(f"Usage: rea_auto_harness.py {{{','.join(_DISPATCH)}}} [args]", file=sys.stderr)
        sys.exit(1)
    handler(argv[2:])


if __name__ == "__main__":
    # `mark` and `defer` write signed records: re-execute into an interpreter
    # that can dual-sign before any write (rules/never-system-python-for-grip.md).
    from lib.pq_reexec import reexec_if_incapable as _reexec

    _reexec(Path(__file__))
    main(sys.argv)
