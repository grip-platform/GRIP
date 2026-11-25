"""Build, persist, and read a product's dogfood report, and derive tick findings.

A report is a product's census (one cell per harness/surface) plus its checks.
Each cell also becomes a ``product:harness`` check carrying the probe detail, so
the persisted report explains every MISSING without re-probing. Product-health
checks use a ``product::name`` key (double colon) to tell the two apart.

The always-on tick reads the persisted reports; it never probes (that is the
overnight loop's job), so the tick stays a fast deterministic floor.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import products
from .loop import LOG_REL
from .model import Cell, Census, Check, Finding, Report, Status


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_report(product_name: str, *, now_iso: str | None = None) -> Report:
    """Probe every target and run every check for ``product_name``."""
    mod = products.load(product_name)
    stamp = now_iso or _now_iso()
    cells: list[Cell] = []
    cell_checks: list[Check] = []
    for target in mod.targets():
        status, detail = target.probe()
        cells.append(Cell(product_name, target.harness, status))
        cell_checks.append(
            Check(f"{product_name}:{target.harness}", status is Status.REGISTERED, detail)
        )
    checks = tuple(cell_checks) + tuple(mod.checks())
    return Report(product_name, stamp, tuple(cells), checks)


def state_dir(claude_dir) -> Path:
    return Path(claude_dir) / "state" / "stack-dogfood"


def state_path(claude_dir, product: str) -> Path:
    return state_dir(claude_dir) / f"{product}.json"


def write_report(report: Report, claude_dir) -> Path:
    path = state_path(claude_dir, report.product)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report.to_dict(), indent=2) + "\n")
    tmp.replace(path)
    return path


def read_report(claude_dir, product: str) -> Report | None:
    path = state_path(claude_dir, product)
    try:
        return Report.from_dict(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def _missing_finding(report: Report) -> Finding | None:
    residual = report.residual()
    if not residual:
        return None
    names = ", ".join(c.harness for c in Census(report.cells).missing())
    return Finding(
        f"dogfood:{report.product}:missing",
        f"{report.product}: {residual} cell(s) not installed ({names})",
    )


def _health_findings(report: Report) -> list[Finding]:
    out: list[Finding] = []
    for check in report.failed_checks():
        if "::" not in check.key:
            continue  # a per-cell check; covered by the residual finding
        out.append(
            Finding(f"dogfood:{check.key}", f"{check.key} failed: {check.detail}")
        )
    return out


def findings_for(report: Report) -> list[Finding]:
    """Every tick finding derivable from ONE report: the residual (missing
    cells) finding, then one per failed product-health check. The overnight
    escalation reads this per product to decide whether a product has anything
    to fix; ``tick_findings`` folds it over every persisted report."""
    out: list[Finding] = []
    miss = _missing_finding(report)
    if miss is not None:
        out.append(miss)
    out.extend(_health_findings(report))
    return out


def _json_row(line: str) -> dict:
    try:
        row = json.loads(line)
    except ValueError:
        return {}
    return row if isinstance(row, dict) else {}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]


# A blocked run older than this is not reported. While it is newer, the finding
# stays true, so the tick's daily digest keeps listing a stuck run (AGORA on
# GRIP#9801). The window is one day shorter than grip_tick.REPORTED_KEEP_S (30
# days), so the run ages out well before the tick forgets its key and is never
# announced twice (AGORA on GRIP#9795), whatever resolution the tick's stamps
# have (GRIP#9808). A test keeps it shorter.
BLOCKED_RUN_WINDOW_S = 29 * 86400


def _run_date(at) -> str:
    """The run's date from its ``at`` stamp, or "an unknown date" when the
    stamp is missing or damaged, rather than a fragment of it (GRIP#9808)."""
    try:
        return datetime.fromisoformat(str(at)).date().isoformat()
    except ValueError:
        return "an unknown date"


def _too_old(at, now: float) -> bool:
    """True when the run's ``at`` stamp is older than the window. A stamp that
    cannot be read is not too old: the run is reported rather than hidden."""
    try:
        return now - datetime.fromisoformat(str(at)).timestamp() > BLOCKED_RUN_WINDOW_S
    except ValueError:
        return False


def blocked_run_findings(claude_dir, now: float | None = None) -> list[Finding]:
    """One finding per item the newest live night run left blocked.

    The night job exits 0 when an item is blocked, so the tick's launchd check
    never fires, and the census reports do not record it: from 17 to 24
    November 2026 eight night runs opened no pull request and the tick said
    nothing (GRIP#9470). The key carries the run's time, so each blocked run is
    reported once, and stays in the tick's daily digest only while it is within
    BLOCKED_RUN_WINDOW_S of the run. The finding names the run's date. The reason
    travels only as a digest, as the tick's other error findings do, because a
    leak-sweep reason can quote the text it caught; the full reason stays in
    the log. A byte that is not UTF-8 is replaced, not raised, so one damaged
    line cannot fail the whole dogfood check."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    try:
        text = (Path(claude_dir) / LOG_REL).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    row = next((r for r in map(_json_row, reversed(text.splitlines()))
                if r.get("phase") == "escalate" and r.get("live")), None)
    if row is None or _too_old(row.get("at"), now):
        return []
    return [Finding(f"dogfood:blocked:{item.get('product')}:{row.get('at')}",
                    f"{item.get('product')} night run of {_run_date(row.get('at'))} "
                    f"blocked at {item.get('blocked_on')} "
                    f"(reason digest {_digest(str(item.get('reason') or ''))}; full reason in {LOG_REL})")
            for item in row.get("items") or ()
            if isinstance(item, dict) and item.get("action") == "blocked"]


def tick_findings(claude_dir, now: float | None = None) -> list[Finding]:
    """Read every persisted report and surface residuals and failed checks,
    then any item the newest live night run left blocked."""
    out: list[Finding] = []
    for name in products.NAMES:
        report = read_report(claude_dir, name)
        if report is None:
            continue
        out.extend(findings_for(report))
    out.extend(blocked_run_findings(claude_dir, now))
    return out
