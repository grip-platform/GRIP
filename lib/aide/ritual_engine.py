"""
RitualEngine - time-aware scheduler for copilot workflows.

Manages ritual lifecycle:
  1. Load active.json (next fire times)
  2. Check which rituals are due
  3. Execute via WorkflowRunner
  4. Update next_fire times
  5. Record to history.jsonl

Design: Single scheduler (15-min tick) via launchd instead of per-ritual agents.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

import yaml

from aide.company import CompanyProfile
from aide.workflow_runner import WorkflowRunner, WorkflowResult
from aide.adaptive_engine import RoutineLearner
from aide.paths import DATA_DIR


RITUALS_DIR = DATA_DIR / "rituals"
ACTIVE_FILE = RITUALS_DIR / "active.json"
HISTORY_FILE = RITUALS_DIR / "history.jsonl"
WORKFLOWS_DIR = DATA_DIR / "workflows"

# Day name mapping
DAY_NAMES = {
    0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu",
    4: "Fri", 5: "Sat", 6: "Sun",
}
DAY_NUMBERS = {v: k for k, v in DAY_NAMES.items()}


class RitualState:
    """State of a single ritual."""

    def __init__(
        self,
        ritual_id: str,
        enabled: bool = True,
        next_fire: str = "",
        last_fire: str = "",
        fire_count: int = 0,
        last_status: str = "pending",
    ):
        self.ritual_id = ritual_id
        self.enabled = enabled
        self.next_fire = next_fire
        self.last_fire = last_fire
        self.fire_count = fire_count
        self.last_status = last_status

    @classmethod
    def from_dict(cls, ritual_id: str, data: dict) -> "RitualState":
        return cls(
            ritual_id=ritual_id,
            enabled=data.get("enabled", True),
            next_fire=data.get("next_fire", ""),
            last_fire=data.get("last_fire", ""),
            fire_count=data.get("fire_count", 0),
            last_status=data.get("last_status", "pending"),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "next_fire": self.next_fire,
            "last_fire": self.last_fire,
            "fire_count": self.fire_count,
            "last_status": self.last_status,
        }

    @property
    def is_due(self) -> bool:
        """Check if this ritual is due for execution."""
        if not self.enabled or not self.next_fire:
            return False
        try:
            fire_dt = datetime.fromisoformat(self.next_fire)
            return datetime.now(timezone.utc) >= fire_dt
        except ValueError:
            return False


class RitualEngine:
    """
    Time-aware scheduler for copilot workflows.

    Manages the lifecycle of ritual workflows:
    - Calendar-based scheduling (specific day/time)
    - Interval-based scheduling (every N hours)
    - Execution via WorkflowRunner
    - History tracking in JSONL
    """

    def __init__(
        self,
        company: Optional[CompanyProfile] = None,
        runner: Optional[WorkflowRunner] = None,
        learner: Optional[RoutineLearner] = None,
    ):
        self.company = company or CompanyProfile.load()
        self.runner = runner or WorkflowRunner(company=self.company)
        self.learner = learner or RoutineLearner()
        self.states: Dict[str, RitualState] = {}
        self._load_states()

    def _load_states(self):
        """Load ritual states from active.json."""
        if not ACTIVE_FILE.exists():
            logger.info("No active.json found, initializing states from workflows")
            self._initialize_states()
            return

        try:
            with open(ACTIVE_FILE) as f:
                data = json.load(f)

            for ritual_id, state_data in data.get("rituals", {}).items():
                self.states[ritual_id] = RitualState.from_dict(ritual_id, state_data)
            logger.debug("Loaded %d ritual states from active.json", len(self.states))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load active.json (%s), re-initializing", e)
            self._initialize_states()

        # Bootstrap any rituals with missing fire times
        self.bootstrap_fire_times()


    def bootstrap_fire_times(self):
        """Bootstrap next_fire for any enabled ritual with empty fire time.
        
        Self-healing: if active.json has rituals with empty next_fire,
        compute them from the workflow schedule. Called after _load_states().
        """
        updated = False
        for rid, state in self.states.items():
            if state.enabled and not state.next_fire:
                try:
                    wf = self.runner.load_workflow(rid)
                    schedule = wf.get("schedule", {})
                    state.next_fire = self._calculate_next_fire(schedule)
                    logger.debug("Bootstrapped next_fire for ritual %s", rid)
                    updated = True
                except FileNotFoundError:
                    logger.warning(
                        "Workflow file missing for ritual %s, disabling to prevent zombie",
                        rid,
                    )
                    state.enabled = False
                    updated = True
        if updated:
            self._save_states()

    def _initialize_states(self):
        """Initialize states from available workflow files."""
        if not WORKFLOWS_DIR.exists():
            logger.info("Workflows directory not found: %s", WORKFLOWS_DIR)
            return

        for fp in WORKFLOWS_DIR.glob("*.yaml"):
            ritual_id = fp.stem
            try:
                with open(fp) as f:
                    wf = yaml.safe_load(f)
                schedule = wf.get("schedule", {})
                next_fire = self._calculate_next_fire(schedule)
                self.states[ritual_id] = RitualState(
                    ritual_id=ritual_id,
                    next_fire=next_fire,
                )
            except Exception as e:
                logger.warning("Failed to initialize ritual %s: %s", ritual_id, e)
                continue

        logger.info("Initialized %d rituals from workflow files", len(self.states))
        self._save_states()

    def _save_states(self):
        """Atomically save ritual states."""
        RITUALS_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "rituals": {rid: state.to_dict() for rid, state in self.states.items()},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        fd, tmp = tempfile.mkstemp(dir=str(RITUALS_DIR), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, str(ACTIVE_FILE))
        except Exception:
            os.unlink(tmp)
            raise

    def get_due_rituals(self) -> List[str]:
        """Get list of ritual IDs that are currently due."""
        return [rid for rid, state in self.states.items() if state.is_due]

    def execute_ritual(self, ritual_id: str, dry_run: bool = False) -> Optional[WorkflowResult]:
        """
        Execute a single ritual.

        Args:
            ritual_id: Workflow ID to execute
            dry_run: If True, trace without side effects

        Returns:
            WorkflowResult or None if ritual not found
        """
        if ritual_id not in self.states:
            return None

        state = self.states[ritual_id]
        now = datetime.now(timezone.utc)

        # Execute workflow
        # Use debug for routine execution, info for state changes
        logger.debug("Executing ritual %s (fire_count=%d, dry_run=%s)", ritual_id, state.fire_count, dry_run)
        result = self.runner.execute(
            ritual_id,
            context={"ritual_fire_time": now.isoformat()},
            dry_run=dry_run,
        )
        logger.debug("Ritual %s completed: %s (%d steps)", ritual_id, result.status, len(result.steps))

        if not dry_run:
            self._update_state_after_execution(ritual_id, state, result, now)

        return result

    def _update_state_after_execution(
        self,
        ritual_id: str,
        state: "RitualState",
        result: "WorkflowResult",
        now: datetime,
    ) -> None:
        """Persist state, history, and learner update after a ritual fires."""
        state.last_fire = now.isoformat()
        state.fire_count += 1
        state.last_status = result.status

        try:
            wf = self.runner.load_workflow(ritual_id)
            state.next_fire = self._calculate_next_fire(wf.get("schedule", {}), from_time=now)
        except FileNotFoundError:
            logger.warning(
                "Workflow file missing for ritual %s after execution, disabling",
                ritual_id,
            )
            state.enabled = False

        self._save_states()
        self._record_history(ritual_id, result)
        self.learner.record(
            f"{ritual_id}_execution",
            cadence="interval",
            hour_local=now.hour,
        )

    def tick(self, dry_run: bool = False) -> List[Tuple[str, WorkflowResult]]:
        """
        Execute all due rituals. Called every 15 minutes by scheduler.

        Returns:
            List of (ritual_id, result) tuples
        """
        results = []
        due = self.get_due_rituals()

        for ritual_id in due:
            result = self.execute_ritual(ritual_id, dry_run=dry_run)
            if result:
                results.append((ritual_id, result))

        return results

    def enable_ritual(self, ritual_id: str):
        """Enable a ritual."""
        if ritual_id in self.states:
            self.states[ritual_id].enabled = True
            # Recalculate next fire
            try:
                wf = self.runner.load_workflow(ritual_id)
                schedule = wf.get("schedule", {})
                self.states[ritual_id].next_fire = self._calculate_next_fire(schedule)
            except FileNotFoundError:
                pass
            self._save_states()
            # State change: use info level
            logger.info("Ritual enabled: %s", ritual_id)

    def disable_ritual(self, ritual_id: str):
        """Disable a ritual."""
        if ritual_id in self.states:
            self.states[ritual_id].enabled = False
            self._save_states()
            # State change: use info level
            logger.info("Ritual disabled: %s", ritual_id)

    def list_rituals(self) -> List[Dict]:
        """List all rituals with their status."""
        rituals = []
        for rid, state in self.states.items():
            rituals.append({
                "id": rid,
                "enabled": state.enabled,
                "next_fire": state.next_fire,
                "last_fire": state.last_fire,
                "fire_count": state.fire_count,
                "last_status": state.last_status,
                "is_due": state.is_due,
            })
        return rituals

    def _calculate_next_fire(
        self,
        schedule: Dict,
        from_time: Optional[datetime] = None,
    ) -> str:
        """Calculate next fire time from schedule config.

        Handles two schedule types:
        1. interval: Fire every N hours from now
        2. calendar: Fire at specific hour:minute on specific days

        Calendar Timezone Conversion:
        -----------------------------
        The schedule specifies a local hour (e.g., 7:00 SAST = Africa/Johannesburg).
        We convert to UTC for storage:

            utc_hour = local_hour - tz_offset

        Example: 7:00 SAST (UTC+2) -> 5:00 UTC
            utc_hour = 7 - 2 = 5

        Day Rollover Handling:
        ----------------------
        When UTC conversion crosses midnight, we adjust the day:

            utc_hour_raw = local_hour - tz_offset
            utc_hour = utc_hour_raw % 24  # Normalize to 0-23
            day_shift = utc_hour_raw // 24  # -1 if wrapped backward

        Examples:
        - 2:00 SAST (UTC+2) -> 0:00 UTC (same day)
          utc_hour_raw = 2 - 2 = 0, day_shift = 0
        - 1:00 SAST (UTC+2) -> 23:00 UTC (previous day)
          utc_hour_raw = 1 - 2 = -1, utc_hour = 23, day_shift = -1
        - 20:00 NYC (UTC-5) -> 1:00 UTC (next day)
          utc_hour_raw = 20 - (-5) = 25, utc_hour = 1, day_shift = 1

        DST Note:
        ---------
        This implementation uses fixed UTC offsets (no DST handling).
        For DST-aware timezones, the fire time may shift by 1 hour during
        DST transitions. This is acceptable for ritual scheduling where
        exact minute precision is not critical.

        Args:
            schedule: Dict with 'type' (interval/calendar), 'hour', 'minute',
                     'days', 'timezone', 'hours' (for interval)
            from_time: Starting point for calculation (default: now UTC)

        Returns:
            ISO 8601 timestamp of next fire time in UTC
        """
        now = from_time or datetime.now(timezone.utc)
        sched_type = schedule.get("type", "calendar")

        if sched_type == "interval":
            hours = schedule.get("hours", 24)
            if not isinstance(hours, (int, float)) or hours <= 0:
                logger.warning("Invalid interval hours=%r, defaulting to 24", hours)
                hours = 24
            next_dt = now + timedelta(hours=hours)
            return next_dt.isoformat()

        elif sched_type == "calendar":
            hour = schedule.get("hour", 7)
            minute = schedule.get("minute", 0)
            days = schedule.get("days", ["Mon", "Tue", "Wed", "Thu", "Fri"])

            # Validate schedule parameters
            if not isinstance(hour, int) or not (0 <= hour <= 23):
                logger.warning("Invalid schedule hour=%r, defaulting to 7", hour)
                hour = 7
            if not isinstance(minute, int) or not (0 <= minute <= 59):
                logger.warning("Invalid schedule minute=%r, defaulting to 0", minute)
                minute = 0

            # Get timezone offset
            tz_name = schedule.get("timezone", "UTC")
            if tz_name.startswith("${"):
                tz_name = self.company.timezone
            tz_offset = self._get_tz_offset(tz_name)

            # Convert local hour to UTC, handling day rollover via modular arithmetic
            # Safely handles negative offsets (e.g., UTC-12) and large positive offsets
            utc_hour_raw = hour - tz_offset
            utc_hour = utc_hour_raw % 24  # Always 0-23 in Python (floor modulo)
            # day_shift: -1 if wrapped backward past midnight, +1 if forward
            if utc_hour_raw < 0:
                day_shift = -((abs(utc_hour_raw) - 1) // 24 + 1)
            elif utc_hour_raw >= 24:
                day_shift = utc_hour_raw // 24
            else:
                day_shift = 0

            # Find next matching day/time
            candidate = now.replace(
                hour=utc_hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if day_shift:
                candidate += timedelta(days=day_shift)

            # Normalize to UTC
            if candidate.tzinfo is None:
                candidate = candidate.replace(tzinfo=timezone.utc)

            # If today's time has passed, start from tomorrow
            if candidate <= now:
                candidate += timedelta(days=1)

            # Find next matching day
            for _ in range(8):  # Max 7 days forward
                day_name = DAY_NAMES.get(candidate.weekday(), "")
                if day_name in days:
                    return candidate.isoformat()
                candidate += timedelta(days=1)

            # Fallback: no matching day found in 8 iterations
            logger.warning(
                "No matching day found for schedule days=%r, falling back to tomorrow",
                days,
            )
            return (now + timedelta(days=1)).replace(
                hour=hour, minute=minute, second=0
            ).isoformat()

        return (now + timedelta(hours=24)).isoformat()

    def _get_tz_offset(self, tz_name: str) -> int:
        """Simple timezone UTC offset lookup."""
        offsets = {
            "Africa/Johannesburg": 2,
            "Africa/Dar_es_Salaam": 3,
            "Africa/Cairo": 2,
            "Africa/Lagos": 1,
            "Africa/Nairobi": 3,
            "America/New_York": -5,
            "America/Los_Angeles": -8,
            "Europe/London": 0,
            "UTC": 0,
        }
        offset = offsets.get(tz_name)
        if offset is None:
            logger.warning("Unknown timezone '%s', defaulting to UTC (offset 0)", tz_name)
            return 0
        return offset

    @staticmethod
    def _step_payload_ok(output: Any) -> Optional[bool]:
        """Whether a step's returned payload explicitly reports success.

        ``True``/``False`` when the payload is a dict carrying an ``ok`` key --
        the shape every ``LocalDataAdapter`` resource returns. ``None`` when the
        step returned anything else, so "did not say" stays distinguishable from
        "said it failed"; a step that legitimately returns a list or a string
        must never be recorded as a failure.

        Deliberately a BOOLEAN and not the payload. These rows persist to disk
        and a fetch step can return arbitrary fetched content, which
        ``rules/no-sensitive-info-in-grip.md`` keeps out of stored records. The
        boolean is the whole of what an audit needs.
        """
        if isinstance(output, dict) and "ok" in output:
            return bool(output["ok"])
        return None

    def _record_history(self, ritual_id: str, result: WorkflowResult):
        """Append execution record to history.jsonl."""
        RITUALS_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "ritual_id": ritual_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": result.status,
            "steps": len(result.steps),
            "escalated": result.was_escalated,
            "duration_steps": [s.step_id for s in result.steps],
            # PURELY ADDITIVE. Until now a row recorded the NUMBER of steps and
            # their ids, never their outcomes -- so a step that returned
            # {"ok": False} left no trace, and the run's "success" could not be
            # contradicted by anything on disk. GRIP#5848: 74 recorded runs,
            # 74 successes, zero failures ever, with team-standup among them
            # while its data step could not import its module at all.
            # The existing keys are untouched so no reader can break.
            "step_results": [
                {
                    "step_id": st.step_id,
                    "status": st.status,
                    "payload_ok": self._step_payload_ok(st.output),
                }
                for st in result.steps
            ],
        }
        from .atomic_io import atomic_append_jsonl
        atomic_append_jsonl(HISTORY_FILE, entry)
