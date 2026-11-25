#!/usr/bin/env python3
"""ultragoal_probes.py — the Deliverables machine leg of /ultragoal.

Layer 2 of the ultragoal stack (fold <- probes <- composition/CLI):
checklist parsing + LIVE probe execution. This module IS the exogenous
criterion — checkboxes are telemetry, subprocess exit codes are the
anchor, and only verify(tick=True) may write a tick. Split out of
lib/ultragoal.py per the AGORA DEFER review on the /ultragoal PR (#3914).

Public surface (re-exported by lib/ultragoal.py — import from there):
    ChecklistItem, Probe, ProbeResult, VerifyReport,
    parse_checklist, progress, parse_probes, lint_probe, run_probe,
    verify, next_item
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from ultragoal_fold import fold_brief  # noqa: E402

_CHECKBOX_RE = re.compile(r"^- \[( |x|X)\] (.+)$")
_SECTION_RE = re.compile(r"^## ", re.MULTILINE)
_PREDICATE_SPLIT = re.compile(r" (?:—|--) DONE-predicate: ")


class ChecklistItem(NamedTuple):
    """One Deliverables entry: ticked state + the item text."""

    done: bool
    text: str


class Probe(NamedTuple):
    """A deliverable's executable DONE-predicate (None when missing)."""

    done: bool
    item: str
    cmd: Optional[str]


class ProbeResult(NamedTuple):
    """Outcome of executing one probe — fail-closed on any error."""

    passed: bool
    exit_code: int
    detail: str


class VerifyReport(NamedTuple):
    """Outcome of a full verify pass over the Deliverables checklist."""

    total: int
    passed: int
    newly_done: list
    all_done: bool
    failures: list  # [(item, reason)]


# ---------------------------------------------------------------------------
# checklist parsing + progress
# ---------------------------------------------------------------------------

def _deliverables_block(text: str) -> str:
    """The body of the '## Deliverables' section only (scoped parse)."""
    marker = "## Deliverables"
    start = text.find(marker)
    if start < 0:
        return ""
    rest = text[start + len(marker):]
    nxt = _SECTION_RE.search(rest)
    return rest[: nxt.start()] if nxt else rest


def parse_checklist(text: str) -> list[ChecklistItem]:
    """Checkbox items inside '## Deliverables' — other sections never count."""
    items: list[ChecklistItem] = []
    for ln in _deliverables_block(text).splitlines():
        m = _CHECKBOX_RE.match(ln.strip())
        if m:
            items.append(ChecklistItem(m.group(1) in "xX", m.group(2).strip()))
    return items


def progress(text: str) -> tuple[int, int]:
    """(done, total) over the Deliverables checklist."""
    items = parse_checklist(text)
    return sum(1 for i in items if i.done), len(items)


# ---------------------------------------------------------------------------
# probe execution — the EXOGENOUS criterion (boxes are telemetry; probes
# are the anchor: only verify() may tick, from real exit codes)
# ---------------------------------------------------------------------------

def parse_probes(text: str) -> list[Probe]:
    """Split each Deliverables item into (done, label, probe-cmd|None)."""
    probes: list[Probe] = []
    for it in parse_checklist(text):
        parts = _PREDICATE_SPLIT.split(it.text, maxsplit=1)
        label = parts[0].strip()
        cmd = parts[1].strip() if len(parts) == 2 and parts[1].strip() else None
        probes.append(Probe(it.done, label, cmd))
    return probes


def lint_probe(cmd: Optional[str], brief_name: str = "") -> Optional[str]:
    """L1 vacuous-anchor floor — reason string when a probe proves nothing.

    Denies: missing/empty, bare `true`/`:`, `echo ...` (self-report), and
    probes that reference the brief's own file (self-scoring loop).
    """
    if not cmd:
        return "missing DONE-predicate — item cannot be exogenously verified"
    c = cmd.strip()
    if c in {"true", ":"} or c == "echo" or c.startswith("echo "):
        return f"vacuous probe ({c.split()[0]}) — proves nothing external"
    if brief_name and brief_name in c:
        return "self-referential probe — targets the brief itself"
    return None


def run_probe(cmd: str, timeout: float = 60.0) -> ProbeResult:
    """Execute one DONE-predicate. FAIL-CLOSED: timeout or any exception
    is a failed probe, never a pass, never a raise."""
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=timeout)
        head = (proc.stdout or proc.stderr or "").strip().splitlines()
        return ProbeResult(proc.returncode == 0, proc.returncode,
                           head[0][:120] if head else "")
    except subprocess.TimeoutExpired:
        return ProbeResult(False, -1, f"timeout after {timeout}s")
    except Exception as exc:  # noqa: BLE001 — fail-closed by design
        return ProbeResult(False, -1, f"probe error: {exc}")


def _tick_lines(body: str, full_items: Sequence[str]) -> str:
    """Flip '- [ ]' -> '- [x]' for exactly the given full item texts.

    Single pass over lines with a set lookup (no nested scans)."""
    wanted = {f"- [ ] {item}" for item in full_items}
    out = []
    for ln in body.split("\n"):
        if ln.strip() in wanted:
            wanted.discard(ln.strip())
            ln = ln.replace("- [ ]", "- [x]", 1)
        out.append(ln)
    return fold_brief("\n".join(out))


def verify(path: Path, tick: bool = False, timeout: float = 60.0,
           run: Callable[..., ProbeResult] = run_probe) -> VerifyReport:
    """Re-run EVERY DONE-predicate live; exit-code-of-truth for the goal.

    Ticked boxes are re-verified too (status is telemetry, the referent is
    the anchor). With ``tick``, newly-passing items are written back as
    [x] through the fold; regressions are reported, never silently unticked.
    """
    body = Path(path).read_text(encoding="utf-8")
    probes = parse_probes(body)
    if not probes:
        raise ValueError(f"No '## Deliverables' checklist in {path}")
    newly, failures = [], []
    for p in probes:
        reason = lint_probe(p.cmd, Path(path).name)
        if reason:
            failures.append((p.item, reason))
            continue
        res = run(p.cmd, timeout=timeout)
        if res.passed and not p.done:
            newly.append(f"{p.item} — DONE-predicate: {p.cmd}")
        elif not res.passed:
            failures.append((p.item, f"exit {res.exit_code} {res.detail}".strip()))
    if tick and newly:
        Path(path).write_text(_tick_lines(body, newly), encoding="utf-8")
    passed = len(probes) - len(failures)
    return VerifyReport(len(probes), passed, newly, not failures, failures)


def next_item(path: Path) -> Optional[Probe]:
    """First undone deliverable — the RUN loop's improve target."""
    for p in parse_probes(Path(path).read_text(encoding="utf-8")):
        if not p.done:
            return p
    return None
