"""Overnight stack-dogfood loop: configuration, schedule, and the fire gate.

This module decides *whether* the loop may run and *in what mode* per product.
It deliberately does NOT spawn agents or open pull requests. The fix preparation
and the draft-PR step belong to a human-run runner that reuses the existing
overnight wave infrastructure, so this module stays a small, testable gate.

Three safety properties live here:

* The fire gate reuses ``overnight_loop.should_fire`` and adds the global
  outbound-stop brake, a dedicated STOP marker, and the weekly-spend throttle,
  so every operator halt wins over the schedule.
* ``wave_mode`` can only ever return ``draft-pr`` for a PUBLIC product (GRASP,
  HAPPI). HAL and GRIP are forced to ``brief`` regardless of the config, so a
  config typo can never arm an autonomous PR on a bootstrap-authority repo.
* The default council speakers include ``astra`` alongside deepseek, xai and
  groq, so the dogfood ideation seats all four.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent  # lib/
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import overnight_loop  # noqa: E402
import throttle_flag  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402

# Only these products may ever receive an autonomous draft PR. HAL and GRIP hold
# the merge machinery and safety gates (bootstrap-authority), so the overnight
# loop prepares a brief for them and never edits their code unattended.
PUBLIC_PRODUCTS = ("grasp", "happi")

LOOP_LABEL = "com.grip.stack-dogfood"
LOG_REL = "logs/stack-dogfood.jsonl"
STOP_REL = "state/stack-dogfood/STOP"
STATE_REL = "state/stack-dogfood/loop.json"


@dataclass(frozen=True)
class LoopConfig:
    enabled: bool = True
    schedule_hour: int = 2
    schedule_minute: int = 15
    products: tuple[str, ...] = ("grasp", "happi", "hal", "grip")
    speakers: tuple[str, ...] = ("deepseek", "xai", "astra", "groq")
    draft_pr_products: tuple[str, ...] = ("grasp", "happi")
    max_prs_per_run: int = 1

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "schedule_hour": self.schedule_hour,
            "schedule_minute": self.schedule_minute,
            "products": list(self.products),
            "speakers": list(self.speakers),
            "draft_pr_products": list(self.draft_pr_products),
            "max_prs_per_run": self.max_prs_per_run,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LoopConfig":
        base = cls()
        return cls(
            enabled=bool(data.get("enabled", base.enabled)),
            schedule_hour=int(data.get("schedule_hour", base.schedule_hour)),
            schedule_minute=int(data.get("schedule_minute", base.schedule_minute)),
            products=tuple(data.get("products", base.products)),
            speakers=tuple(data.get("speakers", base.speakers)),
            draft_pr_products=tuple(data.get("draft_pr_products", base.draft_pr_products)),
            max_prs_per_run=int(data.get("max_prs_per_run", base.max_prs_per_run)),
        )


@dataclass(frozen=True)
class RunDecision:
    fire: bool
    reason: str


def wave_mode(config: LoopConfig, product: str) -> str:
    """The wave mode for ``product``: ``draft-pr`` only for a PUBLIC product the
    config opts in; everything else is ``brief``. HAL and GRIP can never be
    ``draft-pr`` here — the PUBLIC_PRODUCTS floor outranks the config."""
    if product in PUBLIC_PRODUCTS and product in config.draft_pr_products:
        return "draft-pr"
    return "brief"


def local_civil_time(now_epoch: float) -> datetime:
    """``now_epoch`` as the machine's LOCAL wall-clock time (a naive datetime).

    ``datetime.fromtimestamp`` with no tz argument returns local civil time,
    which is the clock launchd's ``StartCalendarInterval`` fires against. Every
    schedule read -- the window hour here and the skip-reason minute in
    ``escalation._live_gate_skip_reason`` -- goes through this one helper, so the
    "naive means local, and local is deliberate" fact lives in ONE place rather
    than inline at each caller (GRIP#9275, GRIP#9277). ``_iso`` and the branch
    stamp read UTC instead, by design: a log line and a branch name are
    machine-independent, but the schedule must match launchd's local clock.
    """
    return datetime.fromtimestamp(now_epoch)


def within_schedule_window(config: LoopConfig, now_epoch: float) -> bool:
    """True iff ``now_epoch`` falls in the configured schedule HOUR, read in
    LOCAL time via ``local_civil_time``.

    launchd ``StartCalendarInterval`` fires in local time and does NOT skip a
    missed run: when the Mac was asleep at the scheduled minute, launchd starts
    the job on the next wake, which can land in working hours. A late wake reads
    a different hour and returns False, so the live-escalation caller declines
    the off-schedule run and the check phase stands as the fallback. Comparing
    in UTC would reject every on-time fire on any machine not at UTC+0
    (GRIP#9275).
    """
    return local_civil_time(now_epoch).hour == config.schedule_hour


def load_config(path) -> LoopConfig:
    try:
        return LoopConfig.from_dict(json.loads(Path(path).read_text()))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return LoopConfig()


def stop_marker(claude_dir) -> Path:
    return Path(claude_dir) / STOP_REL


def loop_state_path(claude_dir) -> Path:
    return Path(claude_dir) / STATE_REL


def write_loop_state(config: LoopConfig, claude_dir) -> Path:
    path = loop_state_path(claude_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(config.to_dict(), indent=2) + "\n")
    tmp.replace(path)
    return path


def should_run(config: LoopConfig, *, now_epoch: float, lock_held: bool = False,
               deadline_epoch: float | None = None, claude_dir=None) -> RunDecision:
    """Fire gate. Every operator halt (throttle, outbound stop, STOP marker)
    short-circuits before the schedule, so a halt always wins."""
    claude_dir = claude_dir or get_claude_dir()
    if throttle_flag.is_set():
        return RunDecision(False, "weekly-spend throttle engaged")
    cfg = {"enabled": config.enabled, "deadline_epoch": deadline_epoch}
    decision = overnight_loop.should_fire(
        cfg,
        now_epoch=now_epoch,
        lock_held=lock_held,
        stop_present=stop_marker(claude_dir).exists(),
        outbound_stop=overnight_loop.outbound_stop_engaged(),
    )
    return RunDecision(decision.fire, decision.reason)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _log(claude_dir, summary: dict) -> None:
    path = Path(claude_dir) / LOG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(summary) + "\n")


def run_once(config: LoopConfig, *, now_epoch: float, claude_dir=None,
             force: bool = False) -> dict:
    """One CHECK-PHASE tick. If the gate fires (or ``force``), probe and persist a
    fresh report per product and refresh loop.json; otherwise record the skip. One
    JSON line is appended to the loop log either way.

    This is the safe phase of the overnight loop: it opens NO pull request and
    spawns NO agent. The ideate and draft-PR phases are a separate, reviewed
    escalation, so arming this runner can only ever read state and write reports.
    """
    claude_dir = claude_dir or get_claude_dir()
    decision = should_run(config, now_epoch=now_epoch, claude_dir=claude_dir)
    if not (decision.fire or force):
        summary = {"at": _iso(now_epoch), "fired": False, "reason": decision.reason}
        _log(claude_dir, summary)
        return summary
    from .report import build_report, write_report
    written = []
    for product in config.products:
        report = build_report(product)
        write_report(report, claude_dir)
        written.append({"product": product, "residual": report.residual()})
    write_loop_state(config, claude_dir)
    reason = "forced" if force and not decision.fire else decision.reason
    summary = {"at": _iso(now_epoch), "fired": True, "reason": reason, "products": written}
    _log(claude_dir, summary)
    return summary
