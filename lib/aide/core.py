"""
AideCore - top-level orchestrator for the company aide (formerly CopilotCore).

Responsibilities:
- Intent routing: scheduled ritual vs user request
- Context assembly: company + preferences + recent decisions + KPI status
- Execution dispatch: workflow runner with trust checks
- Response formatting: Slack mrkdwn, CLI text, or MCP App JSON
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from aide.company import CompanyProfile
from aide.trust import TrustFramework
from aide.decision_logger import DecisionLogger
from aide.workflow_runner import WorkflowRunner
from aide.ritual_engine import RitualEngine
from aide.adaptive import AdaptiveLearning
from aide.integrations import IntegrationHub


class AideCore:
    """
    Top-level orchestrator that ties together all copilot subsystems.

    Intent categories:
    - ritual_management: "change morning brief", "add ritual"
    - company_update: "update KPIs", "add team member"
    - decision_support: "should we hire?", "pricing model?"
    - kpi_query: "how are we tracking?"
    - red_team: "challenge this", "what am I missing?"
    """

    def __init__(self, company_path: Optional[Path] = None, log_path: Optional[Path] = None):
        self.company = CompanyProfile.load(company_path)
        self.trust = TrustFramework()
        self.logger = DecisionLogger(log_path=log_path)
        self.hub = IntegrationHub()
        self.learner = AdaptiveLearning()
        self.runner = WorkflowRunner(
            company=self.company,
            trust=self.trust,
            logger=self.logger,
            hub=self.hub,
        )
        # NOT learner=self.learner. Two different things are called "learner"
        # here and they share no API: self.learner is an AdaptiveLearning
        # (preferences -- observe/correct/get_active_preferences), while
        # RitualEngine's learner parameter is annotated RoutineLearner and is
        # used for exactly ONE call, `.record(name, cadence=, hour_local=)` at
        # ritual_engine.py:266. AdaptiveLearning has no `record`, so injecting it
        # made every non-dry-run ritual execution raise AttributeError -- AFTER
        # the run had already been written to state and history as "success".
        # Omitting it lets RitualEngine default to a real RoutineLearner.
        self.ritual_engine = RitualEngine(
            company=self.company,
            runner=self.runner,
        )

    def handle(self, intent: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Route and handle an intent.

        Args:
            intent: The intent category
            params: Intent-specific parameters

        Returns:
            Response dict with result data
        """
        params = params or {}

        handlers = {
            "ritual_management": self._manage_rituals,
            "company_update": self._update_company,
            "decision_support": self._make_decision,
            "kpi_query": self._query_kpis,
            "red_team": self._red_team,
            "status": self._get_status,
            "tick": self._tick,
        }

        handler = handlers.get(intent)
        if not handler:
            logger.warning("Unknown intent: %s", intent)
            return {"error": f"Unknown intent: {intent}", "available": list(handlers.keys())}

        logger.info("Handling intent: %s", intent)
        return handler(params)

    def _manage_rituals(self, params: Dict) -> Dict[str, Any]:
        """Manage ritual lifecycle."""
        action = params.get("action", "list")

        if action == "list":
            return {
                "rituals": self.ritual_engine.list_rituals(),
                "workflows": self.runner.list_workflows(),
            }

        # All other actions require ritual_id
        ritual_id = params.get("ritual_id", "")
        if not ritual_id:
            return {"error": f"ritual_id required for action '{action}'"}

        if action == "enable":
            self.ritual_engine.enable_ritual(ritual_id)
            logger.info("Ritual enabled: %s", ritual_id)
            return {"status": "enabled", "ritual_id": ritual_id}
        elif action == "disable":
            self.ritual_engine.disable_ritual(ritual_id)
            logger.info("Ritual disabled: %s", ritual_id)
            return {"status": "disabled", "ritual_id": ritual_id}
        elif action == "force":
            result = self.ritual_engine.execute_ritual(ritual_id)
            if result:
                logger.info("Ritual force-executed: %s -> %s", ritual_id, result.status)
                return result.to_dict()
            return {"error": f"Ritual not found: {ritual_id}"}
        elif action == "dry_run":
            result = self.ritual_engine.execute_ritual(ritual_id, dry_run=True)
            if result:
                return result.to_dict()
            return {"error": f"Ritual not found: {ritual_id}"}

        return {"error": f"Unknown ritual action: {action}"}

    def _update_company(self, params: Dict) -> Dict[str, Any]:
        """Update company profile data."""
        # Trust check
        trust = self.trust.check_tier("update_kpi_value")
        if not trust.allowed:
            return {"error": trust.reason, "escalate": trust.escalate}

        update_type = params.get("type", "")

        if update_type == "kpi":
            kpi_id = params.get("kpi_id", "")
            value = params.get("value", 0)
            kpi = self.company.get_kpi(kpi_id)
            if kpi:
                kpi.current_value = value
                self.company.save()
                return {"status": "updated", "kpi": kpi.to_dict()}
            return {"error": f"KPI not found: {kpi_id}"}

        elif update_type == "team_member":
            from aide.company import TeamMember
            member_data = params.get("member", {})
            member = TeamMember.from_dict(member_data)
            self.company.team.append(member)
            self.company.save()
            return {"status": "added", "member": member.to_dict()}

        elif update_type == "preference":
            key = params.get("key", "")
            value = params.get("value")
            if key:
                self.company.preferences[key] = value
                self.company.save()
                # Also update adaptive learning
                self.learner.correct(f"company:{key}", value, "Company profile update")
                return {"status": "updated", "key": key, "value": value}

        return {"error": f"Unknown update type: {update_type}"}

    def _make_decision(self, params: Dict) -> Dict[str, Any]:
        """Support decision-making with structured analysis."""
        question = params.get("question", "")
        context = params.get("context", {})

        # Validate raw question before wrapping with prefix
        if not question or len(question.strip()) < 10:
            return {
                "error": "Decision question too short (minimum 10 characters)",
                "status": "rejected",
            }

        # Assemble decision context
        decision_context = self._assemble_context()
        decision_context.update(context)

        # Log the decision request
        decision_id = self.logger.log_decision(
            what=f"Decision requested: {question}",
            why="User-initiated decision support",
            confidence=0.5,
            context=decision_context,
        )

        return {
            "decision_id": decision_id,
            "question": question,
            "context": {
                "company_stage": self.company.company.get("stage", ""),
                "kpi_summary": self._kpi_summary(),
                "recent_decisions": len(self.logger.get_recent_decisions(5)),
                "active_preferences": len(self.learner.get_active_preferences()),
            },
        }

    def _query_kpis(self, params: Dict) -> Dict[str, Any]:
        """Query KPI status."""
        kpis = []
        for kpi in self.company.kpis:
            kpis.append({
                **kpi.to_dict(),
                "progress_pct": kpi.progress_pct,
                "on_track": kpi.progress_pct >= 70,
            })

        return {
            "kpis": kpis,
            "summary": self._kpi_summary(),
        }

    def _red_team(self, params: Dict) -> Dict[str, Any]:
        """Red-team a topic or decision."""
        topic = params.get("topic", "")
        context = self._assemble_context()

        return {
            "topic": topic,
            "context": context,
            "recent_decisions": self.logger.get_recent_decisions(5),
            "unresolved": self.logger.get_unresolved_decisions(),
            "prompt": (
                f"Red-team analysis for: {topic}\n"
                f"Company stage: {self.company.company.get('stage', 'unknown')}\n"
                f"Consider: blind spots, risks, alternatives, what could go wrong"
            ),
        }

    def _get_status(self, params: Dict) -> Dict[str, Any]:
        """Get copilot system status."""
        return {
            "company": self.company.company.get("name", "(not configured)"),
            "team_size": len(self.company.team),
            "kpis": len(self.company.kpis),
            "integrations": self.hub.detailed_status(),
            "rituals": self.ritual_engine.list_rituals(),
            "decisions": self.logger.stats(),
            "learning": self.learner.summary(),
            "trust": self.trust.summary(),
        }

    def _tick(self, params: Dict) -> Dict[str, Any]:
        """Execute scheduler tick - run all due rituals."""
        dry_run = params.get("dry_run", False)
        start = time.monotonic()
        results = self.ritual_engine.tick(dry_run=dry_run)
        elapsed_ms = round((time.monotonic() - start) * 1000)

        if results:
            logger.info("Tick executed %d rituals in %dms", len(results), elapsed_ms)

        return {
            "executed": len(results),
            "elapsed_ms": elapsed_ms,
            "results": [
                {"ritual_id": rid, **result.to_dict()}
                for rid, result in results
            ],
        }

    def _assemble_context(self) -> Dict[str, Any]:
        """Assemble full context for analysis/decisions."""
        return {
            "company": self.company.to_dict(),
            "recent_decisions": [
                d.get("decision", {}) for d in self.logger.get_recent_decisions(5)
            ],
            "kpi_summary": self._kpi_summary(),
            "active_preferences": [
                p.to_dict() for p in self.learner.get_active_preferences()
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _kpi_summary(self) -> Dict[str, Any]:
        """Generate KPI summary stats."""
        if not self.company.kpis:
            return {"total": 0, "on_track": 0, "at_risk": 0}

        on_track = sum(1 for k in self.company.kpis if k.progress_pct >= 70)
        at_risk = sum(1 for k in self.company.kpis if k.progress_pct < 50)

        return {
            "total": len(self.company.kpis),
            "on_track": on_track,
            "at_risk": at_risk,
            "avg_progress": sum(k.progress_pct for k in self.company.kpis) / len(self.company.kpis),
        }

    def format_slack(self, data: Dict) -> str:
        """Format response data as Slack mrkdwn."""
        parts = []

        if "kpis" in data and isinstance(data["kpis"], list):
            parts.append("*KPI Status*")
            for kpi in data["kpis"]:
                status = "on_track" if kpi.get("on_track") else "at_risk"
                icon = ":white_check_mark:" if status == "on_track" else ":warning:"
                parts.append(
                    f"{icon} *{kpi.get('name', '?')}*: {kpi.get('current_value', 0)}/{kpi.get('target_value', 0)} "
                    f"{kpi.get('unit', '')} ({kpi.get('progress_pct', 0):.0f}%)"
                )

        if "rituals" in data:
            parts.append("\n*Rituals*")
            for r in data["rituals"]:
                status_icon = ":green_circle:" if r.get("enabled") else ":red_circle:"
                due_tag = " (DUE)" if r.get("is_due") else ""
                parts.append(f"{status_icon} {r.get('id', '?')}{due_tag} - fired {r.get('fire_count', 0)}x")

        if "decisions" in data and isinstance(data["decisions"], dict):
            stats = data["decisions"]
            parts.append(
                f"\n*Decisions*: {stats.get('decisions', 0)} logged, "
                f"{stats.get('outcomes', 0)} resolved "
                f"({stats.get('resolution_rate', 0):.0%} resolution)"
            )

        if "learning" in data and isinstance(data["learning"], dict):
            learn = data["learning"]
            parts.append(
                f"\n*Learning*: {learn.get('total_preferences', 0)} preferences "
                f"({learn.get('permanent', 0)} permanent, "
                f"{learn.get('active', 0)} active)"
            )

        if "trust" in data and isinstance(data["trust"], dict):
            trust = data["trust"]
            parts.append(
                f"\n*Trust*: {trust.get('company_gates', 0)} gates, "
                f"{trust.get('total_mapped_actions', 0)} mapped actions"
            )

        return "\n".join(parts) if parts else json.dumps(data, indent=2, default=str)


# Back-compat alias — the class was renamed CopilotCore -> AideCore (2026-06-21).
# Keeps `from aide.core import CopilotCore` (and the shimmed
# `from lib.copilot.core import CopilotCore`) working for existing callers.
CopilotCore = AideCore
