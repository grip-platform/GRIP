"""
TrustFramework - extends GRIP's safety gates for company copilot operations.

Implements 5 trust tiers (0=Observe through 4=Commit) that govern
what the copilot can do autonomously vs what requires human approval.
Extends lib/gates/evaluator.py Gate/EvaluationContext pattern.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


COPILOT_DIR = Path(os.environ.get("COPILOT_DIR", str(Path.home() / ".claude" / "data" / "aide")))
GATES_FILE = COPILOT_DIR / "copilot-gates.yaml"


# Trust tier constants
TIER_OBSERVE = 0
TIER_INFORM = 1
TIER_ACT_INTERNAL = 2
TIER_ACT_EXTERNAL = 3
TIER_COMMIT = 4

TIER_NAMES = {
    TIER_OBSERVE: "Observe",
    TIER_INFORM: "Inform",
    TIER_ACT_INTERNAL: "Act Internal",
    TIER_ACT_EXTERNAL: "Act External",
    TIER_COMMIT: "Commit",
}


@dataclass
class TrustDecision:
    """Result of a trust evaluation."""
    allowed: bool
    tier: int
    tier_name: str
    action: str
    reason: str
    requires_confirmation: bool = False
    escalate: bool = False

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "tier": self.tier,
            "tier_name": self.tier_name,
            "action": self.action,
            "reason": self.reason,
            "requires_confirmation": self.requires_confirmation,
            "escalate": self.escalate,
        }


class TrustFramework:
    """
    Trust evaluation engine for company copilot operations.

    Maps actions to trust tiers and evaluates whether autonomous
    execution is permitted. Extends GRIP's safety gate pattern
    with company-specific business rules.
    """

    def __init__(self, gates_path: Optional[Path] = None):
        self.gates_path = gates_path or GATES_FILE
        self._config: Optional[Dict] = None
        self._action_tier_map: Dict[str, int] = {}
        self._load()

    def _load(self):
        """Load trust configuration from YAML."""
        if not self.gates_path.exists():
            logger.info("Trust gates not found at %s, using empty defaults", self.gates_path)
            self._config = {"trust_tiers": [], "company_gates": []}
            return

        try:
            with open(self.gates_path) as f:
                raw = yaml.safe_load(f)
            if not isinstance(raw, dict):
                logger.warning("Trust gates at %s is not a dict (got %s), using defaults",
                               self.gates_path, type(raw).__name__)
                self._config = {"trust_tiers": [], "company_gates": []}
            else:
                self._config = raw
        except (yaml.YAMLError, OSError, ValueError) as e:
            logger.warning("Failed to load trust gates from %s: %s", self.gates_path, e)
            self._config = {"trust_tiers": [], "company_gates": []}

        # Build action -> tier mapping
        for tier_def in self._config.get("trust_tiers", []):
            tier_num = tier_def.get("tier", 0)
            for action in tier_def.get("allowed_actions", []):
                self._action_tier_map[action] = tier_num

    def get_tier_for_action(self, action: str) -> int:
        """Get the trust tier required for an action."""
        return self._action_tier_map.get(action, TIER_COMMIT)

    def check_tier(self, action: str, context: str = "") -> TrustDecision:
        """
        Check if an action is allowed at its trust tier.

        Args:
            action: The action to check (e.g., "dm_owner", "post_team_channel")
            context: Additional context (e.g., "external", "internal")

        Returns:
            TrustDecision with allow/deny and tier info
        """
        tier = self.get_tier_for_action(action)
        tier_name = TIER_NAMES.get(tier, "Unknown")

        # Check company-specific gates first
        gate_result = self._check_company_gates(action, context)
        if gate_result:
            return gate_result

        # Tier 4 always escalates
        if tier == TIER_COMMIT:
            return TrustDecision(
                allowed=False,
                tier=tier,
                tier_name=tier_name,
                action="escalate",
                reason=f"Tier {tier} ({tier_name}): requires human approval",
                escalate=True,
            )

        # Tier 3 requires confirmation
        if tier == TIER_ACT_EXTERNAL:
            return TrustDecision(
                allowed=True,
                tier=tier,
                tier_name=tier_name,
                action="confirm",
                reason=f"Tier {tier} ({tier_name}): requires confirmation",
                requires_confirmation=True,
            )

        # Tiers 0-2 are allowed autonomously
        return TrustDecision(
            allowed=True,
            tier=tier,
            tier_name=tier_name,
            action="allow",
            reason=f"Tier {tier} ({tier_name}): autonomous execution permitted",
        )

    def _check_company_gates(self, action: str, context: str) -> Optional[TrustDecision]:
        """Check company-specific gates for deny/escalate rules."""
        for gate in self._config.get("company_gates", []):
            gate_action = gate.get("action", "")
            gate_id = gate.get("id", "")

            # Simple keyword matching against action and context
            condition = gate.get("condition", "")
            if self._matches_gate(condition, action, context):
                if gate_action == "deny":
                    return TrustDecision(
                        allowed=False,
                        tier=TIER_COMMIT,
                        tier_name="Deny",
                        action="deny",
                        reason=gate.get("reason", f"Gate {gate_id} denied"),
                    )
                elif gate_action == "escalate":
                    return TrustDecision(
                        allowed=False,
                        tier=TIER_COMMIT,
                        tier_name="Escalate",
                        action="escalate",
                        reason=gate.get("reason", f"Gate {gate_id} escalated"),
                        escalate=True,
                    )
        return None

    def _matches_gate(self, condition: str, action: str, context: str) -> bool:
        """Match company gates against context only, not action names.

        Gates match on the context string (intent/category), not the action
        name. This prevents false positives where action names like
        'read_external_docs' or 'delete_cache' would incorrectly trigger
        security gates meant for dangerous operations.
        """
        if not condition or not context:
            return False

        condition_lower = condition.lower()
        context_lower = context.lower()

        # Match keywords against context only
        sensitive_keywords = ("external", "financial", "hiring", "delete", "contract")
        return any(
            kw in condition_lower and kw in context_lower
            for kw in sensitive_keywords
        )

    def get_tier_config(self, tier: int) -> Dict[str, Any]:
        """Get full configuration for a trust tier."""
        for tier_def in self._config.get("trust_tiers", []):
            if tier_def.get("tier") == tier:
                return tier_def
        return {}

    def list_actions(self, tier: int) -> List[str]:
        """List all actions allowed at a specific tier."""
        config = self.get_tier_config(tier)
        return config.get("allowed_actions", [])

    def summary(self) -> Dict[str, Any]:
        """Return trust framework summary."""
        return {
            "tiers": {
                tier: {
                    "name": TIER_NAMES.get(tier, "Unknown"),
                    "actions": self.list_actions(tier),
                }
                for tier in range(5)
            },
            "company_gates": len(self._config.get("company_gates", [])),
            "total_mapped_actions": len(self._action_tier_map),
        }
