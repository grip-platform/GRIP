"""
Escalation rule engine for copilot workflows.

Evaluates configurable escalation rules against live data sources
(GitHub PRs, CI status, health checks) and triggers notifications
when thresholds are breached.

Rules are defined in data/aide/escalation.json.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import DATA_DIR

logger = logging.getLogger(__name__)

ESCALATION_FILE = DATA_DIR / "escalation.json"
COOLDOWN_FILE = DATA_DIR / "escalation-cooldowns.json"


class EscalationRule:
    """A single escalation rule with trigger conditions."""

    def __init__(self, data: Dict[str, Any]):
        self.id = data.get("id", "")
        self.name = data.get("name", "")
        self.description = data.get("description", "")
        self.trigger = data.get("trigger", {})
        self.severity = data.get("severity", "info")
        self.channels = data.get("channels", ["slack_dm"])
        self.message_template = data.get("message_template", "")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "severity": self.severity,
            "trigger": self.trigger,
        }


class EscalationEngine:
    """
    Evaluates escalation rules and manages cooldowns.

    Cooldowns prevent the same rule from firing repeatedly.
    Each rule has a configurable cooldown period (default 120 min).
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or ESCALATION_FILE
        self.rules: List[EscalationRule] = []
        self.defaults: Dict[str, Any] = {}
        self._cooldowns: Dict[str, str] = {}
        self._load_config()
        self._load_cooldowns()

    def _load_config(self):
        """Load escalation rules from JSON config."""
        if not self.config_path.exists():
            logger.info("No escalation config at %s", self.config_path)
            return
        try:
            with open(self.config_path) as f:
                data = json.load(f)
            self.rules = [EscalationRule(r) for r in data.get("rules", [])]
            self.defaults = data.get("defaults", {})
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load escalation config: %s", e)

    def _load_cooldowns(self):
        """Load cooldown state from disk."""
        if not COOLDOWN_FILE.exists():
            return
        try:
            with open(COOLDOWN_FILE) as f:
                self._cooldowns = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._cooldowns = {}

    def _save_cooldowns(self):
        """Persist cooldown state to disk."""
        COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(COOLDOWN_FILE, "w") as f:
                json.dump(self._cooldowns, f, indent=2)
        except OSError as e:
            logger.warning("Failed to save cooldowns: %s", e)

    def is_on_cooldown(self, rule_id: str) -> bool:
        """Check if a rule is currently on cooldown."""
        last_fired = self._cooldowns.get(rule_id)
        if not last_fired:
            return False
        try:
            fired_at = datetime.fromisoformat(last_fired)
            cooldown_min = self.defaults.get("cooldown_minutes", 120)
            return datetime.now(timezone.utc) < fired_at + timedelta(minutes=cooldown_min)
        except ValueError:
            return False

    def record_fired(self, rule_id: str):
        """Record that a rule has fired (starts cooldown)."""
        self._cooldowns[rule_id] = datetime.now(timezone.utc).isoformat()
        self._save_cooldowns()

    def evaluate_stale_prs(self, prs: List[Dict]) -> List[Dict]:
        """Evaluate stale PR rules against PR data."""
        rule = self._get_rule("stale-pr")
        if not rule or self.is_on_cooldown(rule.id):
            return []

        threshold_hours = rule.trigger.get("threshold", 48)
        now = datetime.now(timezone.utc)
        alerts = []

        for pr in prs:
            updated = pr.get("updatedAt", "")
            if not updated:
                continue
            try:
                updated_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                hours_since = (now - updated_at).total_seconds() / 3600
                if hours_since > threshold_hours:
                    alerts.append({
                        "rule_id": rule.id,
                        "severity": rule.severity,
                        "channels": rule.channels,
                        "message": rule.message_template.format(
                            number=pr.get("number", "?"),
                            title=pr.get("title", "Unknown"),
                            hours=int(hours_since),
                            threshold=threshold_hours,
                        ),
                    })
            except (ValueError, KeyError):
                continue

        return alerts

    def evaluate_ci_failures(self, ci_data: Dict) -> List[Dict]:
        """Evaluate CI failure rules against CI status data."""
        rule = self._get_rule("failed-ci")
        if not rule or self.is_on_cooldown(rule.id):
            return []

        threshold = rule.trigger.get("threshold", 2)
        consecutive = ci_data.get("consecutive_failures", 0)
        if consecutive < threshold:
            return []

        return [{
            "rule_id": rule.id,
            "severity": rule.severity,
            "channels": rule.channels,
            "message": rule.message_template.format(
                count=consecutive,
                branch=ci_data.get("branch", "unknown"),
                last_error=ci_data.get("last_error", "no details"),
            ),
        }]

    def evaluate_health_failures(self, history: List[Dict]) -> List[Dict]:
        """Evaluate health check failure rules against recent history."""
        rule = self._get_rule("health-check-failures")
        if not rule or self.is_on_cooldown(rule.id):
            return []

        threshold = rule.trigger.get("threshold", 3)
        window_hours = rule.trigger.get("window_hours", 1)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

        recent_failures = []
        for entry in history:
            ts = entry.get("timestamp", "")
            status = entry.get("status", "")
            if status != "success":
                try:
                    entry_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if entry_time >= cutoff:
                        recent_failures.append(entry)
                except (ValueError, KeyError):
                    continue

        if len(recent_failures) < threshold:
            return []

        issues = ", ".join(f.get("error", "unknown") for f in recent_failures[:3])
        return [{
            "rule_id": rule.id,
            "severity": rule.severity,
            "channels": rule.channels,
            "message": rule.message_template.format(
                count=len(recent_failures),
                window=window_hours,
                issues=issues,
            ),
        }]

    def evaluate_all(self, data: Dict[str, Any]) -> List[Dict]:
        """
        Evaluate all escalation rules against provided data.

        Args:
            data: Dict with keys 'prs', 'ci', 'health_history'

        Returns:
            List of triggered alert dicts with rule_id, severity, channels, message
        """
        alerts = []

        if "prs" in data:
            alerts.extend(self.evaluate_stale_prs(data["prs"]))

        if "ci" in data:
            alerts.extend(self.evaluate_ci_failures(data["ci"]))

        if "health_history" in data:
            alerts.extend(self.evaluate_health_failures(data["health_history"]))

        return alerts

    def list_rules(self) -> List[Dict]:
        """List all configured escalation rules."""
        return [r.to_dict() for r in self.rules]

    def _get_rule(self, rule_id: str) -> Optional[EscalationRule]:
        """Find rule by ID."""
        for r in self.rules:
            if r.id == rule_id:
                return r
        return None
