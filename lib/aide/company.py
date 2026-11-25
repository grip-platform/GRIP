"""
CompanyProfile - structured company data model and loader.

Provides typed access to company configuration stored in
~/.claude/data/aide/company.json. Follows the team-digest
JSON pattern for team member config.
"""

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


COPILOT_DIR = Path(os.environ.get("COPILOT_DIR", str(Path.home() / ".claude" / "data" / "aide")))
COMPANY_FILE = COPILOT_DIR / "company.json"


@dataclass
class TeamMember:
    """A team member in the company."""
    name: str
    role: str
    email: str = ""
    slack_id: str = ""
    signature: str = ""
    timezone: str = "UTC"
    permissions: List[str] = field(default_factory=list)
    notification_channels: List[str] = field(default_factory=lambda: ["slack_dm"])
    ntfy_topic: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "TeamMember":
        name = data.get("name", "")
        role = data.get("role", "")
        if not name or not role:
            raise ValueError(
                f"TeamMember requires 'name' and 'role' fields, got name={name!r}, role={role!r}"
            )
        return cls(
            name=name,
            role=role,
            email=data.get("email", ""),
            slack_id=data.get("slack_id", ""),
            signature=data.get("signature", ""),
            timezone=data.get("timezone", "UTC"),
            permissions=data.get("permissions", []),
            notification_channels=data.get("notification_channels", ["slack_dm"]),
            ntfy_topic=data.get("ntfy_topic", ""),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "email": self.email,
            "slack_id": self.slack_id,
            "signature": self.signature,
            "timezone": self.timezone,
            "permissions": self.permissions,
            "notification_channels": self.notification_channels,
            "ntfy_topic": self.ntfy_topic,
        }


@dataclass
class KeyResult:
    """A key result within an OKR."""
    description: str
    current: float = 0.0
    target: float = 1.0
    unit: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "KeyResult":
        return cls(
            description=data.get("description", ""),
            current=data.get("current", 0.0),
            target=data.get("target", 1.0),
            unit=data.get("unit", ""),
        )

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "current": self.current,
            "target": self.target,
            "unit": self.unit,
        }


@dataclass
class Objective:
    """An OKR objective with key results."""
    title: str
    key_results: List[KeyResult] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Objective":
        return cls(
            title=data.get("title", ""),
            key_results=[KeyResult.from_dict(kr) for kr in data.get("key_results", [])],
        )

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "key_results": [kr.to_dict() for kr in self.key_results],
        }


@dataclass
class KPI:
    """A tracked KPI."""
    id: str
    name: str
    current_value: float = 0.0
    target_value: float = 0.0
    unit: str = ""
    frequency: str = "weekly"
    source: str = "manual"

    @classmethod
    def from_dict(cls, data: dict) -> "KPI":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            current_value=data.get("current_value", 0.0),
            target_value=data.get("target_value", 0.0),
            unit=data.get("unit", ""),
            frequency=data.get("frequency", "weekly"),
            source=data.get("source", "manual"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "current_value": self.current_value,
            "target_value": self.target_value,
            "unit": self.unit,
            "frequency": self.frequency,
            "source": self.source,
        }

    @property
    def progress_pct(self) -> float:
        if self.target_value == 0:
            return 0.0
        return min(100.0, (self.current_value / self.target_value) * 100)


class CompanyProfile:
    """
    Structured company data model.

    Loads from ~/.claude/data/aide/company.json and provides
    typed access to company information, team, goals, KPIs,
    preferences, and integration config.
    """

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        data = data or {}
        self.company: Dict[str, str] = data.get("company", {})
        self.team: List[TeamMember] = []
        for m in data.get("team", []):
            try:
                self.team.append(TeamMember.from_dict(m))
            except (ValueError, TypeError) as e:
                logger.warning("Skipping invalid team member entry: %s", e)
        self.contacts: Dict[str, str] = data.get("contacts", {})

        goals = data.get("goals", {})
        self.objectives: List[Objective] = [
            Objective.from_dict(o)
            for o in goals.get("current_quarter", {}).get("objectives", [])
        ]
        self.north_star: str = goals.get("north_star", "")

        self.kpis: List[KPI] = [
            KPI.from_dict(k) for k in data.get("kpis", [])
        ]
        self.preferences: Dict[str, Any] = data.get("preferences", {})
        self.integrations: Dict[str, Any] = data.get("integrations", {})
        self._raw = data

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "CompanyProfile":
        """Load company profile from JSON file."""
        fp = path or COMPANY_FILE
        if not fp.exists():
            logger.info("Company profile not found at %s, using empty defaults", fp)
            return cls()
        try:
            with open(fp) as f:
                return cls(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load company profile from %s: %s", fp, e)
            return cls()

    def save(self, path: Optional[Path] = None):
        """Atomically save company profile to JSON file."""
        fp = path or COMPANY_FILE
        fp.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        dir_path = str(fp.parent)
        fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, str(fp))
        except Exception:
            os.unlink(tmp)
            raise

    def to_dict(self) -> dict:
        return {
            "company": self.company,
            "team": [m.to_dict() for m in self.team],
            "contacts": self.contacts,
            "goals": {
                "current_quarter": {
                    "objectives": [o.to_dict() for o in self.objectives],
                },
                "north_star": self.north_star,
            },
            "kpis": [k.to_dict() for k in self.kpis],
            "preferences": self.preferences,
            "integrations": self.integrations,
        }

    def get_member_by_role(self, role: str) -> Optional[TeamMember]:
        """Find first team member with matching role."""
        for m in self.team:
            if m.role.lower() == role.lower():
                return m
        return None

    def get_member_by_name(self, name: str) -> Optional[TeamMember]:
        """Find team member by name (case-insensitive)."""
        for m in self.team:
            if m.name.lower() == name.lower():
                return m
        return None

    def get_kpi(self, kpi_id: str) -> Optional[KPI]:
        """Find KPI by ID."""
        for k in self.kpis:
            if k.id == kpi_id:
                return k
        return None

    def get_founder(self) -> Optional[TeamMember]:
        """Find founder/CEO/owner team member."""
        for role in ("founder", "ceo", "owner"):
            m = self.get_member_by_role(role)
            if m:
                return m
        return self.team[0] if self.team else None

    @property
    def digest_hour(self) -> int:
        return self.preferences.get("digest_hour", 22)

    @property
    def briefing_hour(self) -> int:
        return self.preferences.get("briefing_hour", 7)

    @property
    def review_day(self) -> str:
        return self.preferences.get("review_day", "Friday")

    @property
    def timezone(self) -> str:
        return self.preferences.get("timezone", "UTC")

    @property
    def slack_workspace(self) -> str:
        return self.integrations.get("slack", {}).get("workspace", "")

    @property
    def slack_channels(self) -> List[Dict[str, str]]:
        return self.integrations.get("slack", {}).get("channels", [])

    @property
    def github_org(self) -> str:
        return self.integrations.get("github", {}).get("org", "")

    @property
    def github_repos(self) -> List[str]:
        return self.integrations.get("github", {}).get("repos", [])
