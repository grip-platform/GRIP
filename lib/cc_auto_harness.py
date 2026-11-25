"""CC Auto-Harness Convergence Runner.

Fractal convergence engine that evaluates unharnessed Claude Code features
and generates integration plans for GRIP. Outer loop scans releases; inner
loops converge on individual features.

Usage:
    python3 lib/cc_auto_harness.py top [N]        # Top N unharnessed features (default 10)
    python3 lib/cc_auto_harness.py assess          # Detailed assessment of top features
    python3 lib/cc_auto_harness.py refresh         # Fetch new releases from GitHub; exit 1 on failure
    python3 lib/cc_auto_harness.py dashboard       # Full dashboard with tiers
    python3 lib/cc_auto_harness.py validate <id>   # Validate harness for a feature
    python3 lib/cc_auto_harness.py validate-all    # Validate all harness_*.py files
    python3 lib/cc_auto_harness.py coverage        # Coverage report by score tier
"""

import json
import os
import py_compile
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# The sibling scanner is imported from THIS checkout, never from CLAUDE_DIR.
# Putting CLAUDE_DIR (the shared tree) first on sys.path made a checkout under
# test load the shared tree's scanner instead of its own. It keeps the name
# `lib.cc_feature_scanner`, which every other caller and test imports, so the
# process holds ONE scanner module: importing it as bare `cc_feature_scanner`
# made a second copy, and a patch applied to one was invisible to the other.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if not sys.path or sys.path[0] != _REPO_ROOT:
    sys.path.insert(0, _REPO_ROOT)

from lib.cc_feature_scanner import (  # noqa: E402
    HIGH_VALUE_CUTOFF,
    adjust_score_from_validation,
    get_unharnessed,
    mark_harnessed,
    scan_releases,
    suggest_max_depth,
    RELEASES_DIR,
)

VALIDATION_FILE = CLAUDE_DIR / "data" / "harness-validations.jsonl"

# ── Validation Feedback Loop ───────────────────────────────────────────


def _record_validation(feature_id: str, passed: bool, details: str) -> dict:
    """Append a validation result to the JSONL log and return the record."""
    record = {
        "feature_id": feature_id,
        "passed": passed,
        "details": details,
        "timestamp": datetime.now().isoformat(),
    }
    VALIDATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VALIDATION_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _sanitise_feature_id(feature_id: str) -> str:
    """Convert feature_id to filesystem-safe name."""
    return re.sub(r"[^a-z0-9]+", "_", feature_id.lower()).strip("_")


def _check_syntax(harness_file: Path) -> list[str]:
    """py_compile syntax check. Returns list of errors (empty = pass)."""
    if not harness_file.exists():
        return [f"Harness file not found: {harness_file}"]
    try:
        py_compile.compile(str(harness_file), doraise=True)
        return []
    except py_compile.PyCompileError as exc:
        return [f"Syntax error: {exc}"]


def _run_harness_tests(safe_name: str) -> list[str]:
    """Run pytest on matching test file. Returns list of errors."""
    test_file = CLAUDE_DIR / "tests" / f"test_harness_{safe_name}.py"
    if not test_file.exists():
        return []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-x", "-q"],
            capture_output=True, text=True, timeout=60, cwd=str(CLAUDE_DIR),
        )
        return [f"Tests failed: {result.stdout[-500:]}"] if result.returncode != 0 else []
    except subprocess.TimeoutExpired:
        return ["Tests timed out after 60s"]
    except Exception as exc:
        return [f"Test run error: {exc}"]


def validate_harness(feature_id: str, harness_path: str | None = None) -> dict:
    """Validate harness: syntax check + tests. Records and feeds back to scoring."""
    safe_name = _sanitise_feature_id(feature_id)
    if harness_path is None:
        harness_path = str(CLAUDE_DIR / "lib" / f"harness_{safe_name}.py")

    errors = _check_syntax(Path(harness_path)) + _run_harness_tests(safe_name)
    passed = len(errors) == 0
    details = "All checks passed" if passed else "; ".join(errors)

    record = _record_validation(feature_id, passed, details)
    adjust_score_from_validation(feature_id, passed)
    return record


def get_validation_history(feature_id: str | None = None) -> list[dict]:
    """Query past validation results.

    Args:
        feature_id: If provided, filter to this feature only.
                    If None, return all validation records.

    Returns:
        List of validation result dicts, newest first.
    """
    if not VALIDATION_FILE.exists():
        return []

    records: list[dict] = []
    with open(VALIDATION_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if feature_id is None or record.get("feature_id") == feature_id:
                records.append(record)

    records.reverse()
    return records


def get_validation_feedback(feature_id: str, max_failures: int = 3) -> dict:
    """Get structured feedback from past validation failures for re-generation.

    Implements the Google ClusterFuzz pattern: failed validation produces
    structured error context that feeds into the next harness generation
    attempt. Without this feedback loop, harness generation repeats the
    same mistakes.

    Returns:
        Dict with: has_failures, attempt_count, errors (list), guidance (str).
        guidance is a formatted string suitable for LLM prompts.
    """
    history = get_validation_history(feature_id)
    failures = [r for r in history if not r.get("passed", True)][:max_failures]

    if not failures:
        return {
            "has_failures": False, "attempt_count": 0,
            "errors": [], "guidance": "",
        }

    errors = [f.get("details", "unknown error") for f in failures]
    guidance_lines = [
        f"Previous harness attempt #{i+1} failed: {err}"
        for i, err in enumerate(errors)
    ]
    guidance = (
        "VALIDATION FEEDBACK — previous attempts failed. "
        "Avoid these errors:\n" + "\n".join(guidance_lines)
    )

    return {
        "has_failures": True,
        "attempt_count": len(failures),
        "errors": errors,
        "guidance": guidance,
    }


def validate_and_retry(feature_id: str, max_attempts: int = 3) -> dict:
    """Validation feedback loop: validate, re-attempt on failure.

    Google pattern (ClusterFuzz → OSS-Fuzz-Gen feedback). Each failed
    validation produces structured feedback for the next attempt.
    Returns the final validation result after up to max_attempts.
    """
    for attempt in range(1, max_attempts + 1):
        result = validate_harness(feature_id)
        if result.get("passed"):
            result["attempts"] = attempt
            return result

        # Record feedback for potential re-generation
        feedback = get_validation_feedback(feature_id)
        result["feedback"] = feedback
        result["attempt"] = attempt

        if attempt == max_attempts:
            result["exhausted"] = True
            return result

    return result


# ── Feature Assessment Categories ──────────────────────────────────────

INTEGRATION_TYPES = {
    "hook": "New or enhanced GRIP hook",
    "skill": "New GRIP skill or skill enhancement",
    "agent": "Agent definition or agent capability",
    "config": "Settings/configuration surface",
    "lib": "Core library enhancement",
    "workflow": "RSI/sprint workflow integration",
    "monitor": "Observability/monitoring integration",
    "skip": "Not worth harnessing (CC-internal or no GRIP surface)",
}


def assess_feature(feature: dict) -> dict:
    """Assess a single feature for integration type and effort.

    Returns an assessment dict with integration_type, effort, and rationale.
    Uses keyword heuristics — no LLM call needed for initial triage.
    """
    title = feature.get("title", "").lower()
    score = feature.get("score", 0)

    # Determine integration type from title keywords
    if any(kw in title for kw in ["hook", "pretooluse", "posttooluse", "lifecycle"]):
        int_type = "hook"
    elif any(kw in title for kw in ["agent", "subagent", "team", "worker", "spawn"]):
        int_type = "agent"
    elif any(kw in title for kw in ["task", "sprint", "pr ", "review", "commit"]):
        int_type = "workflow"
    elif any(kw in title for kw in ["memory", "persist", "session", "state", "resume"]):
        int_type = "lib"
    elif any(kw in title for kw in ["mcp", "tool", "server", "protocol"]):
        int_type = "skill"
    elif any(kw in title for kw in ["setting", "config", "env var", "flag"]):
        int_type = "config"
    elif any(kw in title for kw in ["metric", "token", "count", "monitor", "debug"]):
        int_type = "monitor"
    elif any(kw in title for kw in ["terminal", "render", "display", "ui ", "rtl"]):
        int_type = "skip"
    else:
        int_type = "lib" if score >= 20 else "skip"

    # Estimate effort (T-shirt sizing)
    depth = suggest_max_depth(feature)
    if depth >= 4:
        effort = "L"  # Large: new feature or significant integration
    elif depth >= 3:
        effort = "M"  # Medium: moderate integration
    else:
        effort = "S"  # Small: config change or light wiring

    return {
        **feature,
        "integration_type": int_type,
        "integration_desc": INTEGRATION_TYPES.get(int_type, "Unknown"),
        "effort": effort,
        "depth": depth,
    }


def get_top_features(n: int = 10, min_score: int = HIGH_VALUE_CUTOFF) -> list[dict]:
    """Get top N unharnessed features with assessments."""
    features = get_unharnessed(min_score=min_score)
    assessed = [assess_feature(f) for f in features[:n * 2]]  # assess more, filter
    # Filter out skips, take top N
    actionable = [a for a in assessed if a["integration_type"] != "skip"]
    return actionable[:n]


def _dashboard_header(all_features: list, harnessed: list, high_value: list) -> list[str]:
    """Generate dashboard header and coverage summary."""
    total = len(all_features)
    h_count = len(harnessed)
    return [
        "CC Auto-Harness Dashboard", "=" * 60,
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"CC Version: {_get_cc_version()}",
        f"Releases tracked: {len(set(f['version'] for f in all_features))}", "",
        f"Total features: {total}",
        f"Harnessed: {h_count} ({h_count/total*100:.0f}%)" if total else "",
        f"High-value unharnessed (score >= {HIGH_VALUE_CUTOFF}): {len(high_value)}", "",
    ]


def _dashboard_tiers(unharnessed: list) -> list[str]:
    """Generate score tier breakdown."""
    lines = ["Score Tiers (unharnessed only)", "-" * 40]
    tiers: dict[int, int] = {}
    for f in unharnessed:
        tier = f["score"] // 5 * 5
        tiers[tier] = tiers.get(tier, 0) + 1
    for tier in sorted(tiers, reverse=True):
        lines.append(f"  {tier:>3}-{tier+4}: {tiers[tier]:>3} {'#' * min(tiers[tier], 40)}")
    lines.append("")
    return lines


def _dashboard_top_features() -> list[str]:
    """Generate top 10 actionable features section."""
    lines = ["Top 10 Actionable Features", "-" * 60]
    for i, f in enumerate(get_top_features(10), 1):
        lines.append(f"  {i:>2}. [{f['score']:>2}] v{f['version']} [{f['integration_type']}:{f['effort']}]")
        lines.append(f"      {f['title'][:80]}")
        lines.append(f"      -> {f['integration_desc']}")
        lines.append("")
    return lines


def _dashboard_edge(h_count: int, high_value: list) -> list[str]:
    """Generate GRIP competitive edge section."""
    lines = ["", "GRIP Competitive Edge", "-" * 40,
             f"  Harnessed: {h_count} CC features enhanced by GRIP",
             f"  Runway: {len(high_value)} high-value features available to harness"]
    if h_count > 0 and len(high_value) > 0:
        lines.append(f"  Edge ratio: {h_count / (h_count + len(high_value)) * 100:.0f}% (higher = more ahead)")
    lines.append("  Strategy: harness faster than Anthropic absorbs GRIP features")
    return lines


def get_dashboard() -> str:
    """Generate the full auto-harness dashboard."""
    all_features = scan_releases()
    unharnessed = get_unharnessed(min_score=0)
    high_value = get_unharnessed(min_score=HIGH_VALUE_CUTOFF)
    harnessed = [f for f in all_features if f["harnessed"]]

    lines = _dashboard_header(all_features, harnessed, high_value)
    lines.extend(_dashboard_tiers(unharnessed))
    lines.extend(_dashboard_top_features())
    lines.extend(_dashboard_edge(len(harnessed), high_value))
    return "\n".join(lines)


_CATEGORY_PATTERNS = [
    (r"^#{1,3}\s*(New|Features?|Added)", "feature"),
    (r"^#{1,3}\s*(Fix|Bug)", "fix"),
    (r"^#{1,3}\s*(Break|Deprecat)", "breaking"),
    (r"^#{1,3}\s*(Improve|Enhance|Change)", "improvement"),
]


def _classify_line(line: str) -> str | None:
    """Classify a markdown heading into a category. Returns None if not a heading."""
    for pattern, cat in _CATEGORY_PATTERNS:
        if re.match(pattern, line, re.I):
            return cat
    return None


def _parse_release_features(body: str) -> list[dict]:
    """Parse features from release body markdown."""
    features = []
    category = "improvement"
    for line in body.split("\n"):
        line = line.strip()
        classified = _classify_line(line)
        if classified:
            category = classified
        m = re.match(r"^[-*]\s+(.+)", line)
        if m and len(m.group(1).strip()) > 5:
            features.append({"title": m.group(1).strip(), "description": "", "category": category})
    return features


class RefreshError(RuntimeError):
    """The release fetch failed, or returned nothing that could be trusted.

    Raised instead of returning an empty result. Until 2026-10-09 every failure
    here was turned into "Refreshed 0 releases" with exit status 0, so the
    heartbeat's 2026-09-28 run logged a fetch that never happened as a success.
    """


# launchd starts the heartbeat with PATH=/usr/bin:/bin:/usr/sbin:/sbin, which
# omits Homebrew, so the bare name `gh` raised ENOENT under the agent while it
# worked in every shell (reproduced 2026-10-09). These are the two Homebrew
# prefixes; on Linux and Windows `gh` is found on PATH and they are never read.
GH_FALLBACK_PATHS = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")
FETCH_TIMEOUT_S = 120  # the full feed is ~3.6 MB and took 3.5 s on 2026-10-09
_RELEASE_FILE_RE = re.compile(r"^v(\d[\w.\-]*)\.json$")


def _gh_binary() -> str | None:
    """The `gh` executable: PATH first, then the Homebrew prefixes."""
    from shutil import which

    found = which("gh")
    if found:
        return found
    return next((p for p in GH_FALLBACK_PATHS if os.access(p, os.X_OK)), None)


def _run_gh(argv: list[str]) -> subprocess.CompletedProcess:
    """The single subprocess seam for the fetch, so tests replace it whole."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=FETCH_TIMEOUT_S)


def _proxy_note() -> str:
    """Name the proxy in a failure, because a proxy that refuses GitHub is a
    known cause (the old HAL wire on port 4011 answers `Forbidden`)."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    return f" through HTTPS_PROXY={proxy}" if proxy else ""


def _decode_pages(raw: str) -> list[dict]:
    """Decode `gh api --paginate` output: one JSON array, or several back to back.

    The previous parser joined pages by replacing every `][` with `,`, which
    also rewrote that sequence inside release bodies. A raw decoder reads each
    array where it stands and leaves the bodies alone.
    """
    decoder = json.JSONDecoder()
    releases: list[dict] = []
    i, end = 0, len(raw)
    while i < end:
        if raw[i].isspace():
            i += 1
            continue
        page, i = decoder.raw_decode(raw, i)
        releases.extend(page if isinstance(page, list) else [page])
    return releases


def _fetch_releases_from_github(repo: str = "anthropics/claude-code") -> list[dict]:
    """Fetch every release from GitHub. Raises RefreshError naming the cause."""
    gh = _gh_binary()
    if gh is None:
        raise RefreshError("gh CLI not found on PATH or at " + ", ".join(GH_FALLBACK_PATHS))
    try:
        result = _run_gh([gh, "api", f"repos/{repo}/releases", "--paginate"])
    except subprocess.TimeoutExpired as exc:
        raise RefreshError(f"gh api timed out after {FETCH_TIMEOUT_S}s{_proxy_note()}") from exc
    except OSError as exc:
        raise RefreshError(f"gh could not be run: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[:300]
        raise RefreshError(f"gh api exited {result.returncode}{_proxy_note()}: {detail}")
    try:
        return _decode_pages(result.stdout)
    except ValueError as exc:
        raise RefreshError(f"gh api returned unparseable JSON: {exc}") from exc


def _version_key(version: str) -> tuple[int, ...]:
    """Numeric sort key, so 2.1.100 sorts after 2.1.99."""
    return tuple(int(part) for part in re.findall(r"\d+", version))


def _versions_in(directory: Path) -> set[str]:
    """Versions with a release file directly inside ``directory``."""
    matches = (_RELEASE_FILE_RE.match(p.name) for p in directory.glob("v*.json"))
    return {m.group(1) for m in matches if m}


def _write_release(path: Path, record: dict) -> bool:
    """Write ``record`` unless the file already holds exactly it; True if written.

    Atomic, so an interrupted refresh cannot leave a truncated release behind.
    """
    from lib.atomic_write import atomic_write

    text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass  # missing or unreadable: write it
    atomic_write(path, text)
    return True


def _release_record(rel: dict) -> dict:
    """One release in the on-disk JSON shape the scanner reads."""
    body = rel.get("body") or ""
    features = _parse_release_features(body)
    return {
        "version": (rel.get("tag_name") or "").lstrip("v"),
        "date": (rel.get("published_at") or "")[:10],
        "features": features,
        "feature_count": len(features),
        "raw_body": body[:3000],
    }


def _check_feed(fetched: list[dict], known: set[str]) -> str:
    """Return the feed's newest version, or raise if the feed looks wrong.

    An empty feed, or one whose newest release is OLDER than a file already on
    disk, means the fetch did not reach the real feed. A feed whose newest
    release is already on disk is simply up to date, which is not a failure:
    the heartbeat runs every two hours and Claude Code does not ship that often.
    """
    if not fetched:
        raise RefreshError("gh api returned no releases")
    newest = max((r["version"] for r in fetched), key=_version_key)
    newest_known = max(known, key=_version_key, default=None)
    if newest_known and _version_key(newest) < _version_key(newest_known):
        raise RefreshError(f"the feed's newest release v{newest} is older than v{newest_known} on disk")
    return newest


def refresh_releases() -> dict:
    """Fetch the release feed; write new releases and repair changed live ones.

    A release with no file is written. A LIVE file is rewritten when it differs
    from the feed: an upstream edit to the notes, or a file an interrupted write
    left damaged. An ARCHIVED release is never touched, because writing it would
    put a live copy back beside everything the session pruner archived.

    Returns {"fetched": int, "written": [...], "updated": [...], "newest": str},
    versions oldest first. Raises RefreshError when the fetch fails or the feed
    looks wrong.
    """
    fetched = [_release_record(r) for r in _fetch_releases_from_github()]
    fetched = [r for r in fetched if r["version"]]
    live = _versions_in(RELEASES_DIR)
    archived = _versions_in(RELEASES_DIR / "archive")
    newest = _check_feed(fetched, live | archived)
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    written, updated = [], []
    for record in fetched:
        version = record["version"]
        if version in archived and version not in live:
            continue
        if _write_release(RELEASES_DIR / f"v{version}.json", record):
            (updated if version in live else written).append(version)
    return {"fetched": len(fetched), "written": sorted(written, key=_version_key),
            "updated": sorted(updated, key=_version_key), "newest": newest}


def _get_cc_version() -> str:
    """Get installed Claude Code version."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


# ── CLI ────────────────────────────────────────────────────────────────

def _cmd_top() -> None:
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    for i, f in enumerate(get_top_features(n), 1):
        print(f"{i:>2}. [{f['score']:>2}] v{f['version']} [{f['integration_type']}:{f['effort']}] {f['title'][:80]}")


def _cmd_assess() -> None:
    for f in get_top_features(15):
        print(f"v{f['version']} [{f['score']}] [{f['integration_type']}:{f['effort']}]")
        print(f"  {f['title']}\n  -> {f['integration_desc']}\n  Convergence depth: {f['depth']}\n")


def _cmd_validate() -> None:
    if len(sys.argv) < 3:
        print("Usage: cc_auto_harness.py validate <feature_id> [harness_path]")
        sys.exit(1)
    result = validate_harness(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    print(f"[{'PASS' if result['passed'] else 'FAIL'}] {sys.argv[2]}: {result['details']}")


def _cmd_history() -> None:
    fid = sys.argv[2] if len(sys.argv) > 2 else None
    records = get_validation_history(fid)
    if not records:
        print("No validation history found.")
        return
    for r in records[:20]:
        print(f"[{'PASS' if r['passed'] else 'FAIL'}] {r['feature_id']} ({r['timestamp']}): {r['details'][:80]}")


def _cmd_validate_all() -> None:
    harness_files = list((CLAUDE_DIR / "lib").glob("harness_*.py"))
    if not harness_files:
        print("No harness files found in lib/.")
        return
    passed = failed = 0
    for hf in harness_files:
        result = validate_harness(hf.stem.replace("harness_", ""), str(hf))
        passed += result["passed"]
        failed += not result["passed"]
        print(f"[{'PASS' if result['passed'] else 'FAIL'}] {hf.name}: {result['details'][:80]}")
    print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")


_SCORE_TIERS = [
    (30, "high"),
    (20, "medium"),
    (10, "low"),
    (0, "minimal"),
]


def coverage_report() -> dict:
    """Report harness coverage by score tier.

    Returns a dict with total, harnessed_count, deferred_count, unharnessed_count,
    coverage_pct, and tier_breakdown (undecided features grouped by score tier).
    Deferred features are decided, so they count in neither the undecided total
    nor the tiers.
    """
    all_features = scan_releases()
    harnessed = [f for f in all_features if f.get("harnessed")]
    deferred = [f for f in all_features if f.get("deferred")]
    unharnessed = [f for f in all_features if not f.get("harnessed") and not f.get("deferred")]

    tier_breakdown: dict[str, int] = {label: 0 for _, label in _SCORE_TIERS}
    for feat in unharnessed:
        score = feat.get("score", 0)
        label = next((lbl for threshold, lbl in _SCORE_TIERS if score >= threshold), "minimal")
        tier_breakdown[label] += 1

    total = len(all_features)
    h_count = len(harnessed)
    return {
        "total": total,
        "harnessed_count": h_count,
        "deferred_count": len(deferred),
        "unharnessed_count": len(unharnessed),
        "coverage_pct": round(h_count / total * 100, 1) if total else 0.0,
        "tier_breakdown": tier_breakdown,
    }


def _cmd_coverage() -> None:
    report = coverage_report()
    print(f"Harness Coverage: {report['harnessed_count']}/{report['total']} ({report['coverage_pct']}%)")
    print(f"Unharnessed: {report['unharnessed_count']}")
    print("\nUnharnessed by score tier:")
    for tier, count in report["tier_breakdown"].items():
        print(f"  {tier:>8}: {count}")


def _cmd_refresh() -> None:
    """Exit 1 with the cause on stderr when the feed cannot be fetched or trusted."""
    try:
        summary = refresh_releases()
    except RefreshError as exc:
        print(f"refresh failed: {exc}", file=sys.stderr)
        sys.exit(1)
    new, repaired = summary["written"], summary["updated"]
    listed = ": " + ", ".join(f"v{v}" for v in new) if new else ""
    fixed = f"; repaired {len(repaired)}: " + ", ".join(f"v{v}" for v in repaired) if repaired else ""
    status = "" if new or repaired else " (up to date)"
    print(f"Fetched {summary['fetched']} releases, newest v{summary['newest']}; "
          f"wrote {len(new)} new{listed}{fixed}{status}")


_CLI_COMMANDS = {
    "top": _cmd_top, "assess": _cmd_assess, "refresh": _cmd_refresh,
    "validate": _cmd_validate, "history": _cmd_history, "validate-all": _cmd_validate_all,
    "coverage": _cmd_coverage, "dashboard": lambda: print(get_dashboard()),
}


def main() -> None:
    """CLI entry point."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dashboard"
    handler = _CLI_COMMANDS.get(cmd)
    if handler:
        handler()
    else:
        print(f"Usage: cc_auto_harness.py {{{','.join(_CLI_COMMANDS)}}} [args]")
        sys.exit(1)


if __name__ == "__main__":
    main()
