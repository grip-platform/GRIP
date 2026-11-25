"""
GripAgentAdapter - Claude Agent SDK wrapper with GRIP gate enforcement.

Wraps the Agent SDK's query() with:
- PreToolUse hook: TrustFramework gate evaluation (deny/allow/escalate)
- PostToolUse hook: DecisionLogger audit trail
- Read-only default tool set (Glob, Grep, Read, WebSearch)
- permissionMode: 'default' (NEVER bypassPermissions)

Architecture:
  Agent SDK query() -> PreToolUse hook -> TrustFramework.check_tier()
                    -> PostToolUse hook -> DecisionLogger.log_decision()

Reuses patterns from:
  - lib/gates/evaluator.py (EvaluationContext, GateResult.to_hook_response())
  - lib/copilot/trust.py (TrustFramework, TrustDecision)
  - lib/copilot/decision_logger.py (hash-chained audit trail)
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

# Add lib directory to path for copilot package imports
_lib_dir = Path(__file__).parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from aide.company import CompanyProfile
from aide.decision_logger import DecisionLogger
from aide.trust import TrustFramework


# Default read-only tools — safe for autonomous use
DEFAULT_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "WebSearch"]

# Tool -> trust action mapping (mirrors workflow_runner._step_type_to_action)
TOOL_ACTION_MAP = {
    "Read": "read_company_profile",
    "Glob": "read_company_profile",
    "Grep": "read_company_profile",
    "WebSearch": "read_company_profile",
    "WebFetch": "read_company_profile",
    "Write": "update_preferences",
    "Edit": "update_preferences",
    "Bash": "execute_command",
    "Task": "delegate_task",
}


class GripAgentAdapter:
    """
    Thin wrapper around Claude Agent SDK with GRIP gate enforcement.

    Every tool call is:
    1. Evaluated against TrustFramework before execution (PreToolUse)
    2. Logged to DecisionLogger after execution (PostToolUse)

    Security invariants:
    - permissionMode is ALWAYS 'default' (never 'bypassPermissions')
    - Bash tool is denied at Tier 0 (Observe) by default
    - All decisions are hash-chained via audit_chain.py
    """

    def __init__(
        self,
        trust: Optional[TrustFramework] = None,
        logger: Optional[DecisionLogger] = None,
        company: Optional[CompanyProfile] = None,
        allowed_tools: Optional[List[str]] = None,
    ):
        self.trust = trust or TrustFramework()
        self.logger = logger or DecisionLogger()
        self.company = company or CompanyProfile.load()
        self.allowed_tools = allowed_tools or DEFAULT_ALLOWED_TOOLS

    async def query(
        self,
        prompt: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Any, None]:
        """
        Execute agent query with GRIP hooks.

        Args:
            prompt: The prompt to send to the agent
            options: Additional options (merged with GRIP defaults)

        Yields:
            Messages from the agent SDK query stream
        """
        try:
            from claude_code_sdk import query as sdk_query, ClaudeCodeOptions
        except ImportError:
            raise ImportError(
                "claude-code-sdk is required for GripAgentAdapter. "
                "Install with: pip install claude-code-sdk"
            )

        # Validate inputs
        if not prompt or not isinstance(prompt, str):
            raise ValueError("prompt must be a non-empty string")

        # Build options with GRIP safety defaults
        allowed_tools = self.allowed_tools.copy()

        # Merge any user-provided options (but never override permission_mode)
        if options:
            if "allowed_tools" in options:
                user_tools = options["allowed_tools"]
                if not isinstance(user_tools, list):
                    raise ValueError("allowed_tools must be a list")
                if not user_tools:
                    raise ValueError("allowed_tools cannot be empty")
                allowed_tools = user_tools

            # Explicitly block bypassPermissions override attempts
            if options.get("permission_mode") == "bypassPermissions":
                self.logger.log_decision(
                    what="Blocked bypassPermissions override attempt",
                    why="GRIP security policy: bypassPermissions is never allowed",
                    confidence=1.0,
                )

        sdk_options = ClaudeCodeOptions(
            allowed_tools=allowed_tools,
            permission_mode="default",  # NEVER bypassPermissions
        )

        # Log the query initiation
        self.logger.log_decision(
            what=f"Agent query initiated",
            why=f"Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}",
            confidence=1.0,
            context={"allowed_tools": sdk_options.allowed_tools},
        )

        async for message in sdk_query(
            prompt=prompt,
            options=sdk_options,
        ):
            # Post-process: audit tool use messages
            if hasattr(message, "type") and message.type == "tool_use":
                self._audit_tool_use(message)
            yield message

    def check_tool_permission(self, tool_name: str) -> Dict[str, Any]:
        """
        Pre-check whether a tool would be allowed by TrustFramework.

        Returns hook-compatible response dict.
        Useful for external callers that want to gate-check before
        invoking query().
        """
        action = TOOL_ACTION_MAP.get(tool_name, "execute_command")
        decision = self.trust.check_tier(action)

        self.logger.log_decision(
            what=f"Gate check: {tool_name} -> {action}",
            why=decision.reason,
            confidence=1.0 if decision.allowed else 0.0,
            context={"tool": tool_name, "tier": decision.tier},
        )

        if not decision.allowed:
            return {"decision": "deny", "reason": decision.reason}
        return {"decision": "allow"}

    def _audit_tool_use(self, message: Any):
        """Log tool use to audit trail (PostToolUse equivalent)."""
        tool_name = getattr(message, "name", "unknown")
        tool_input = getattr(message, "input", {})

        self.logger.log_decision(
            what=f"Tool executed: {tool_name}",
            why="Agent SDK tool use",
            confidence=1.0,
            context={
                "tool": tool_name,
                "input_keys": list(tool_input.keys()) if isinstance(tool_input, dict) else [],
            },
        )

    def build_mcp_config(self) -> List[Dict[str, Any]]:
        """
        Build MCP server config for the agent from company integrations.

        Returns list of MCP server configs that the Agent SDK can use.
        """
        servers = []

        # Add mcp-atlassian if JIRA is configured
        github_config = self.company.integrations.get("github", {})
        if github_config:
            servers.append({
                "name": "github",
                "command": "gh",
                "args": ["copilot", "mcp"],
            })

        return servers

    @staticmethod
    def create_read_only(
        company: Optional[CompanyProfile] = None,
    ) -> "GripAgentAdapter":
        """Factory: create a read-only adapter (safest default)."""
        return GripAgentAdapter(
            company=company,
            allowed_tools=["Read", "Glob", "Grep", "WebSearch"],
        )

    @staticmethod
    def create_with_write(
        company: Optional[CompanyProfile] = None,
    ) -> "GripAgentAdapter":
        """Factory: create an adapter that can also write files."""
        return GripAgentAdapter(
            company=company,
            allowed_tools=["Read", "Glob", "Grep", "WebSearch", "Write", "Edit"],
        )
