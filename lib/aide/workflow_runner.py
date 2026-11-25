"""
WorkflowRunner - declarative YAML DSL executor for copilot workflows.

Executes workflow definitions with step types:
  fetch   - Get data from integration (no side effects)
  analyze - Claude analysis of inputs (no side effects)
  decide  - Make decision, log to audit trail
  notify  - Send output to channel (sends Slack DM/message)
  escalate - Halt and ask human
  learn   - Record for adaptive engine

Variable resolution: ${company.X.Y}, ${step_id.output}, ${now}, ${preferences.X}
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load .env for ANTHROPIC_API_KEY if not already in environment
if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude")) / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                break

logger = logging.getLogger(__name__)

import sys

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

if str(_CLAUDE_DIR) not in sys.path:
    sys.path.insert(0, str(_CLAUDE_DIR))
# Single shared import-safe facade — no per-callsite wrapper duplication
# (AGORA PR #2974 #2: duplicated _resolve_model would inevitably diverge).
# Guarded import (AGORA PR #2974 #2b): if the facade module is absent,
# degrade to a verbatim-fallback resolver so the workflow runner still
# imports — the "never crashes" guarantee is end-to-end, not partial.
try:
    from lib.hal_model_resolver_safe import safe_resolve as _resolve_model  # noqa: E402
except Exception:  # facade unavailable — degrade, never block the runner
    def _resolve_model(family: str, fallback: str) -> str:  # noqa: E301
        return fallback

import yaml

from aide.company import CompanyProfile
from aide.trust import TrustFramework
from aide.decision_logger import DecisionLogger
from aide.escalation import EscalationEngine
from aide.integrations import IntegrationHub, NotificationRouter
from aide.paths import DATA_DIR as _COPILOT_DATA_DIR

# Optional KONO import for episodic memory storage (R6)
try:
    from kono_substrate import MemorySubstrate
    _kono_available = True
except ImportError:
    _kono_available = False


WORKFLOWS_DIR = Path(os.environ.get("COPILOT_WORKFLOWS_DIR", str(_COPILOT_DATA_DIR / "workflows")))


class StepResult:
    """Result of executing a workflow step."""

    def __init__(
        self,
        step_id: str,
        status: str = "success",
        output: Any = None,
        error: str = "",
        skipped: bool = False,
        escalated: bool = False,
    ):
        self.step_id = step_id
        self.status = status
        self.output = output
        self.error = error
        self.skipped = skipped
        self.escalated = escalated

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "skipped": self.skipped,
            "escalated": self.escalated,
        }


class WorkflowResult:
    """Result of executing a complete workflow."""

    def __init__(self, workflow_id: str):
        self.workflow_id = workflow_id
        self.steps: List[StepResult] = []
        self.status: str = "pending"
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None

    def add_step(self, result: StepResult):
        self.steps.append(result)

    @property
    def success(self) -> bool:
        return self.status == "success"

    @property
    def was_escalated(self) -> bool:
        return any(s.escalated for s in self.steps)

    def to_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "steps": [s.to_dict() for s in self.steps],
            "escalated": self.was_escalated,
        }


class WorkflowRunner:
    """
    Executes declarative YAML workflows with trust checking.

    Each step is evaluated against the TrustFramework before execution.
    Side effects (notify, decide) require appropriate trust tier.
    """

    def __init__(
        self,
        company: Optional[CompanyProfile] = None,
        trust: Optional[TrustFramework] = None,
        logger: Optional[DecisionLogger] = None,
        hub: Optional[IntegrationHub] = None,
        escalation: Optional[EscalationEngine] = None,
    ):
        self.company = company or CompanyProfile.load()
        self.trust = trust or TrustFramework()
        self.logger = logger or DecisionLogger()
        self.hub = hub or IntegrationHub()
        self.escalation = escalation or EscalationEngine()
        self.notification_router = NotificationRouter(slack=self.hub.slack)

    def load_workflow(self, workflow_id: str) -> Dict:
        """Load a workflow definition from YAML."""
        fp = WORKFLOWS_DIR / f"{workflow_id}.yaml"
        if not fp.exists():
            raise FileNotFoundError(f"Workflow not found: {fp}")
        with open(fp) as f:
            return yaml.safe_load(f)

    def list_workflows(self) -> List[Dict]:
        """List all available workflows."""
        workflows = []
        if not WORKFLOWS_DIR.exists():
            return workflows
        for fp in sorted(WORKFLOWS_DIR.glob("*.yaml")):
            try:
                with open(fp) as f:
                    wf = yaml.safe_load(f)
                workflow_block = wf.get("workflow", {})
                steps = workflow_block.get("steps", []) if isinstance(workflow_block, dict) else []
                workflows.append({
                    "id": wf.get("id", fp.stem),
                    "name": wf.get("name", fp.stem),
                    "description": wf.get("description", ""),
                    "version": wf.get("version", ""),
                    "schedule": wf.get("schedule", {}),
                    "step_count": len(steps),
                    "recipients": wf.get("recipients", []),
                })
            except Exception as e:
                logger.warning("Failed to load workflow %s: %s", fp.name, e)
                continue
        return workflows

    # Valid step types supported by _execute_step
    VALID_STEP_TYPES = {"fetch", "analyze", "decide", "notify", "escalate", "learn", "create_issue", "sprint_loop", "rsi_loop", "record_outcome"}

    def validate_workflows(self) -> List[Dict]:
        """Pre-flight validation of all workflow YAML files."""
        issues: List[Dict] = []
        if not WORKFLOWS_DIR.exists():
            return issues
        for fp in sorted(WORKFLOWS_DIR.glob("*.yaml")):
            issues.extend(self._validate_workflow_file(fp))
        return issues

    def _validate_workflow_file(self, fp: Path) -> List[Dict]:
        """Validate a single workflow YAML file for structural correctness."""
        try:
            with open(fp) as f:
                wf = yaml.safe_load(f)
        except Exception as e:
            return [{"file": fp.name, "error": f"YAML parse error: {e}"}]

        if not isinstance(wf, dict):
            return [{"file": fp.name, "error": "Root element is not a dict"}]

        wf_id = wf.get("id", fp.stem)
        workflow_block = wf.get("workflow")
        if not isinstance(workflow_block, dict):
            return [{"file": fp.name, "id": wf_id, "error": "Missing or invalid 'workflow' block"}]

        steps = workflow_block.get("steps")
        if not isinstance(steps, list) or len(steps) == 0:
            msg = "workflow.steps is not a list" if not isinstance(steps, list) else "workflow.steps is empty"
            return [{"file": fp.name, "id": wf_id, "error": msg}]

        issues: List[Dict] = []
        step_ids: set = set()
        for i, step in enumerate(steps):
            issues.extend(self._validate_step(step, i, fp.name, wf_id, step_ids))
        return issues

    def _validate_step(
        self,
        step: Dict,
        index: int,
        filename: str,
        wf_id: str,
        step_ids: set,
    ) -> List[Dict]:
        """Validate a single workflow step. Returns list of issues found."""
        issues: List[Dict] = []

        if not isinstance(step, dict):
            issues.append({"file": filename, "id": wf_id, "error": f"Step {index} is not a dict"})
            return issues

        # Accept both 'name' and 'id' — matches execute() behavior
        step_id = step.get("name", step.get("id"))
        if not step_id:
            issues.append({"file": filename, "id": wf_id, "error": f"Step {index} missing 'name' or 'id'"})
            return issues

        if step_id in step_ids:
            issues.append({"file": filename, "id": wf_id, "step": step_id, "error": "Duplicate step id"})
        step_ids.add(step_id)

        step_type = step.get("type")
        if not step_type:
            issues.append({"file": filename, "id": wf_id, "step": step_id, "error": "Missing 'type'"})
        elif step_type not in self.VALID_STEP_TYPES:
            issues.append({
                "file": filename, "id": wf_id, "step": step_id,
                "error": f"Unknown type '{step_type}' (valid: {', '.join(sorted(self.VALID_STEP_TYPES))})",
            })

        # Validate decide steps have what/why to prevent ghost decisions
        if step_type == "decide":
            params = step.get("params", {}) if isinstance(step.get("params"), dict) else {}
            has_what = (
                step.get("what") or step.get("description")
                or params.get("what") or params.get("description")
            )
            if not has_what:
                issues.append({
                    "file": filename, "id": wf_id, "step": step_id,
                    "error": "decide step missing 'what' or 'description' (causes ghost decisions)",
                })

        # Validate record_outcome steps reference a decide step
        if step_type == "record_outcome":
            ref_input = step.get("input")
            if isinstance(ref_input, str):
                ref_input = [ref_input]
            if not ref_input:
                issues.append({
                    "file": filename, "id": wf_id, "step": step_id,
                    "error": "record_outcome step missing 'input' (must reference a decide step)",
                })

        # Validate input refs point to earlier steps
        input_refs = step.get("input", [])
        if isinstance(input_refs, str):
            input_refs = [input_refs]
        if isinstance(input_refs, list):
            for ref in input_refs:
                if ref not in step_ids:
                    issues.append({
                        "file": filename, "id": wf_id, "step": step_id,
                        "error": f"Input ref '{ref}' not found in prior steps",
                    })

        return issues

    def execute(
        self,
        workflow_id: str,
        context: Optional[Dict] = None,
        dry_run: bool = False,
    ) -> WorkflowResult:
        """
        Execute a workflow.

        Args:
            workflow_id: Workflow ID (filename without .yaml)
            context: Additional context variables
            dry_run: If True, trace steps without side effects

        Returns:
            WorkflowResult with step results
        """
        wf = self.load_workflow(workflow_id)
        result = WorkflowResult(workflow_id)
        result.started_at = datetime.now(timezone.utc).isoformat()
        result.status = "running"

        # Build execution context
        exec_ctx = self._build_context(context)
        step_outputs: Dict[str, Any] = {}

        steps = wf.get("workflow", {}).get("steps", [])

        for step in steps:
            step_id = step.get("name", step.get("id", "unknown"))

            # Check skip_if condition
            skip_if = step.get("skip_if", "")
            if skip_if and self._eval_condition(skip_if, exec_ctx, step_outputs):
                step_result = StepResult(step_id, status="skipped", skipped=True)
                result.add_step(step_result)
                continue

            # Check trust level
            trust_level = step.get("trust_level", 0)
            step_type = step.get("type", "fetch")
            trust_action = self._step_type_to_action(step_type, trust_level)
            trust_decision = self.trust.check_tier(trust_action)

            if not trust_decision.allowed and not dry_run:
                if trust_decision.escalate:
                    step_result = StepResult(
                        step_id,
                        status="escalated",
                        escalated=True,
                        error=trust_decision.reason,
                    )
                    result.add_step(step_result)
                    result.status = "escalated"
                    result.completed_at = datetime.now(timezone.utc).isoformat()
                    return result
                else:
                    step_result = StepResult(
                        step_id,
                        status="denied",
                        error=trust_decision.reason,
                    )
                    result.add_step(step_result)
                    continue

            # Execute step
            try:
                if dry_run:
                    step_result = StepResult(
                        step_id,
                        status="dry_run",
                        output=f"[DRY RUN] Would execute {step_type}: {step.get('params', {})}",
                    )
                else:
                    logger.debug("Executing step %s (%s) in %s", step_id, step_type, workflow_id)
                    step_result = self._execute_step(
                        step, exec_ctx, step_outputs, workflow_id
                    )
                    logger.debug("Step %s completed: %s", step_id, step_result.status)
            except Exception as e:
                on_error = step.get("on_error", "continue")
                step_result = StepResult(step_id, status="error", error=str(e))
                logger.error("Step %s failed in %s: %s (on_error=%s)", step_id, workflow_id, e, on_error)

                if on_error == "halt":
                    result.add_step(step_result)
                    result.status = "error"
                    result.completed_at = datetime.now(timezone.utc).isoformat()
                    return result

            # Store output for variable resolution
            if step_result.output is not None:
                step_outputs[step_id] = step_result.output

            result.add_step(step_result)

            # R6: Store episodic memory of step completion
            if not dry_run:
                self._store_episodic(
                    workflow_id=workflow_id,
                    step_id=step_id,
                    step_type=step.get("type", "unknown"),
                    status=step_result.status,
                    output=step_result.output,
                    error=step_result.error,
                )

            # Record outcome for decide steps (#312)
            if not dry_run and step.get("type") == "decide":
                decision_id = (step_result.output or {}).get("decision_id")
                if decision_id:
                    self.logger.record_outcome(
                        decision_id,
                        outcome=f"Step {step_id}: {step_result.status}",
                        success=step_result.status in ("success", "dry_run"),
                    )

        result.status = "success"
        result.completed_at = datetime.now(timezone.utc).isoformat()
        return result

    def dry_run(self, workflow_id: str) -> WorkflowResult:
        """Trace a workflow without side effects."""
        return self.execute(workflow_id, dry_run=True)

    def _build_context(self, extra: Optional[Dict] = None) -> Dict:
        """Build execution context with company data and variables."""
        ctx = {
            "company": self.company.to_dict(),
            "preferences": self.company.preferences,
            "now": datetime.now(timezone.utc).isoformat(),
            "integrations": self.hub.status(),
        }
        if extra:
            ctx.update(extra)
        return ctx

    def _execute_step(
        self,
        step: Dict,
        ctx: Dict,
        step_outputs: Dict,
        workflow_id: str,
    ) -> StepResult:
        """Execute a single workflow step."""
        step_id = step.get("name", step.get("id", "unknown"))
        step_type = step.get("type", "fetch")
        # Merge step-level keys into params (YAML steps put properties at top level)
        _skip_keys = {"name", "id", "type", "input", "skip_if", "trust_level", "on_error", "params"}
        params = {k: v for k, v in step.items() if k not in _skip_keys}
        params.update(step.get("params", {}))
        input_refs = step.get("input", [])

        # Resolve variable references in params
        resolved_params = self._resolve_vars(params, ctx, step_outputs)

        # Gather inputs from previous steps
        inputs = {}
        if isinstance(input_refs, list):
            for ref in input_refs:
                if ref in step_outputs:
                    inputs[ref] = step_outputs[ref]
                else:
                    logger.warning(
                        "Step '%s' references input '%s' but no such step output exists",
                        step_id, ref,
                    )
        elif isinstance(input_refs, str):
            if input_refs in step_outputs:
                inputs[input_refs] = step_outputs[input_refs]
            else:
                logger.warning(
                    "Step '%s' references input '%s' but no such step output exists",
                    step_id, input_refs,
                )

        _dispatch = {
            "fetch": lambda: self._exec_fetch(step_id, resolved_params),
            "analyze": lambda: self._exec_analyze(step_id, resolved_params, inputs),
            "decide": lambda: self._exec_decide(step_id, resolved_params, inputs, workflow_id),
            "notify": lambda: self._exec_notify(step_id, resolved_params, inputs),
            "escalate": lambda: self._exec_escalate(step_id, resolved_params, inputs),
            "learn": lambda: self._exec_learn(step_id, resolved_params, inputs),
            "create_issue": lambda: self._exec_create_issue(step_id, resolved_params, inputs),
            "sprint_loop": lambda: self._exec_sprint_loop(step_id, resolved_params, inputs),
            "rsi_loop": lambda: self._exec_rsi_loop(step_id, resolved_params, inputs),
            "record_outcome": lambda: self._exec_record_outcome(step_id, resolved_params, inputs),
        }
        handler = _dispatch.get(step_type)
        if handler is None:
            return StepResult(step_id, status="error", error=f"Unknown step type: {step_type}")
        return handler()

    def _exec_fetch(self, step_id: str, params: Dict) -> StepResult:
        """Execute a fetch step - get data from integration."""
        source = params.get("source", "local")
        resource = params.get("resource", "")
        fetch_params = params.get("params", {})

        adapter = self.hub.get(source)
        if not adapter:
            return StepResult(step_id, status="error", error=f"Unknown source: {source}")

        if not adapter.available():
            return StepResult(step_id, status="error", error=f"{source} not available")

        result = adapter.fetch(resource, fetch_params)
        if isinstance(result, dict) and not result.get("ok", True):
            error_msg = result.get("error", "fetch returned not-ok")
            logger.warning("Fetch %s/%s failed: %s", source, resource, error_msg)
            return StepResult(step_id, status="error", output=result, error=error_msg)
        return StepResult(step_id, output=result)

    def _analyze_via_api(self, step_id: str, sys_ctx: str, full_prompt: str) -> tuple:
        """Try direct Anthropic API. Returns (result, source) or (None, None)."""
        api_key_var = "ANTHROPIC_" + "API_KEY"
        if not os.environ.get(api_key_var):
            return None, None
        try:
            import anthropic
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=_resolve_model(
                    "anthropic/claude-sonnet-", "claude-sonnet-4-6"),
                max_tokens=2048,
                system=sys_ctx,
                messages=[{"role": "user", "content": full_prompt}],
            )
            if response.content:
                return response.content[0].text, "api"
            return "[Analysis returned empty response]", "fallback"
        except Exception as e:
            logger.warning("Analyze %s API failed, trying CLI fallback: %s", step_id, e)
            return None, None

    def _analyze_via_cli(self, step_id: str, sys_ctx: str, full_prompt: str) -> tuple:
        """Try Claude CLI fallback. Returns (result, source)."""
        import subprocess
        cli_prompt = f"System: {sys_ctx}\n\n{full_prompt}"
        try:
            # run_text, not subprocess.run(text=True): proc.stdout IS the
            # analysis result returned to the workflow, and LLM prose carries
            # characters cp1252 cannot round-trip.
            from lib._compat import run_text
            proc = run_text(
                ["claude", "--print",
                 "-m", _resolve_model(
                     "anthropic/claude-sonnet-", "claude-sonnet-4-6"),
                 "--fallback-model", _resolve_model(
                     "anthropic/claude-haiku-", "claude-haiku-4-5"),
                 cli_prompt],
                capture_output=True, timeout=120,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip(), "cli"
            logger.warning("Analyze %s CLI returned %d: %s", step_id, proc.returncode, proc.stderr[:200])
            return f"[Analysis unavailable: CLI returned {proc.returncode}]", "fallback"
        except FileNotFoundError:
            logger.warning("Analyze %s: 'claude' CLI not found in PATH", step_id)
            return "[Analysis unavailable: claude CLI not in PATH]", "fallback"
        except subprocess.TimeoutExpired:
            logger.warning("Analyze %s: CLI timed out after 120s", step_id)
            return "[Analysis unavailable: CLI timeout]", "fallback"
        except Exception as e:
            logger.error("Analyze %s: unexpected error: %s", step_id, e)
            return f"[Analysis unavailable: {e}]", "fallback"

    def _exec_analyze(self, step_id: str, params: Dict, inputs: Dict) -> StepResult:
        """Execute an analyze step via Claude API with CLI fallback."""
        prompt = params.get("prompt", "")
        company_name = self.company.company.get("name", "a company")
        sys_ctx = f"You are a company copilot for {company_name}. Provide concise, actionable analysis."
        full_prompt = f"{prompt}\n\nData:\n{json.dumps(inputs, indent=2, default=str)}"

        result, source = self._analyze_via_api(step_id, sys_ctx, full_prompt)
        if result is None:
            result, source = self._analyze_via_cli(step_id, sys_ctx, full_prompt)

        status = "success" if source in ("api", "cli") else "degraded"
        return StepResult(step_id, status=status, output={
            "prompt": prompt, "result": result, "source": source,
            "inputs": inputs, "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _exec_decide(
        self, step_id: str, params: Dict, inputs: Dict, workflow_id: str
    ) -> StepResult:
        """Execute a decide step - log decision to audit trail.

        Falls back to 'description' and 'criteria' from YAML when
        explicit 'what'/'why' params are absent (fixes ghost entries).
        """
        # Primary: explicit params. Fallback: YAML description/criteria
        what = params.get("what") or params.get("description", "Decision")
        criteria = params.get("criteria", [])
        why = params.get("why") or (
            f"Criteria: {', '.join(str(c) for c in criteria)}" if criteria else ""
        )
        alternatives = params.get("alternatives", [])
        confidence_threshold = params.get("confidence_threshold")
        confidence = params.get("confidence", 0.8)
        if confidence_threshold is not None:
            confidence = float(confidence_threshold)

        # Validate what AFTER fallback resolution
        if not what or len(what.strip()) < 10:
            logger.warning("Decide step rejected: 'what' too short: %r", what)
            return StepResult(step_id, output={"error": "Decision text too short", "decision_id": None})

        decision_id = self.logger.log_decision(
            what=what,
            why=why,
            alternatives=alternatives,
            confidence=confidence,
            ritual_id=workflow_id,
            step_id=step_id,
            context={"inputs_summary": list(inputs.keys())},
        )

        return StepResult(step_id, output={
            "decision_id": decision_id,
            "what": what,
            "confidence": confidence,
        })

    def _exec_notify(self, step_id: str, params: Dict, inputs: Dict) -> StepResult:
        """Execute a notify step - multi-channel delivery via NotificationRouter."""
        channel_type = params.get("channel", "dm")
        recipient_role = params.get("recipient", "founder")
        message = params.get("message", "")
        # Optional explicit URL from workflow YAML (overrides auto-resolved URL)
        self._notify_url_override = params.get("url", "")

        # Resolve recipient
        member = self._resolve_recipient(recipient_role)
        if not member:
            return StepResult(step_id, status="error", error=f"No member with role: {recipient_role}")

        # Build message from inputs if template not provided
        if not message and inputs:
            message = self._format_notification(inputs)

        # Skip delivery if message is empty after formatting
        if not message:
            return StepResult(step_id, output={"skipped": "empty_message"})

        # Channel routing
        if channel_type == "channel":
            return self._notify_team_channel(step_id, params, message, member.name)
        elif channel_type == "dm":
            return self._notify_dm(step_id, message, member)
        else:
            return StepResult(step_id, status="error", error=f"Unknown channel type: {channel_type}")

    def _resolve_recipient(self, recipient_role: str) -> Optional[Any]:
        """Resolve recipient by role."""
        member = None
        if recipient_role:
            member = self.company.get_member_by_role(recipient_role)
            if not member and recipient_role == "founder":
                member = self.company.get_founder()
        return member

    def _notify_team_channel(self, step_id: str, params: Dict, message: str, member_name: str) -> StepResult:
        """Post notification to team Slack channel."""
        channel_id = params.get("channel_id", "")
        if not channel_id:
            return StepResult(step_id, status="error", error="No channel_id for channel notify")

        from .integrations import text_to_blocks
        result = self.hub.slack.post_channel(
            channel_id, message, blocks=text_to_blocks(message),
        )
        success = result.get("ok", False)

        # Register as Copilot thread for reply continuity
        if success and result.get("ts"):
            from .thread_registry import CopilotThreadRegistry
            CopilotThreadRegistry.register(
                channel_id=channel_id,
                thread_ts=result["ts"],
                origin="ritual",
                ritual_id=params.get("ritual_id"),
                context={"workflow": params.get("workflow_id"), "recipient": member_name},
            )

        return StepResult(
            step_id,
            status="success" if success else "error",
            output={
                "delivered": success,
                "recipient": member_name,
                "channel": "slack_channel",
                "thread_ts": result.get("ts"),
            },
            error="" if success else result.get("error", "Failed to send Slack message"),
        )

    def _resolve_notification_url(self, step_id: str, member: Any) -> str:
        """Resolve the best URL for notification click-to-open.

        Priority chain:
        0. Explicit URL override from workflow YAML params (if set)
        1. Slack deep link (opens Slack app to DM) if member has slack_id
        2. Dashboard URL from GRIP_DASHBOARD_URL env var
        3. GitHub repo as final fallback
        """
        override = getattr(self, "_notify_url_override", "")
        if override:
            return override
        slack_id = getattr(member, "slack_id", None)
        if slack_id:
            return f"slack://user?id={slack_id}"
        dashboard = os.environ.get("GRIP_DASHBOARD_URL", "")
        if dashboard:
            return dashboard
        return "https://github.com/grip-platform/GRIP"

    def _notify_dm_fallback(self, step_id: str, message: str, member: Any, original_error: Exception) -> StepResult:
        """Fallback to direct Slack DM when NotificationRouter fails."""
        from .integrations import text_to_blocks
        result = self.hub.slack.send_dm(
            member.slack_id, message,
            blocks=text_to_blocks(message),
        )
        success = result.get("ok", False)

        if success and result.get("ts") and result.get("channel"):
            from .thread_registry import CopilotThreadRegistry
            CopilotThreadRegistry.register(
                channel_id=result["channel"],
                thread_ts=result["ts"],
                origin="dm_fallback",
                context={"recipient": member.name},
            )

        return StepResult(
            step_id,
            status="success" if success else "error",
            output={"delivered": success, "recipient": member.name,
                    "channel": "slack_dm_fallback", "thread_ts": result.get("ts")},
            error=f"NotificationRouter failed ({original_error}), used fallback" if success
                  else f"Both router and fallback failed: {original_error}",
        )

    def _notify_dm(self, step_id: str, message: str, member: Any) -> StepResult:
        """Send DM via multi-channel NotificationRouter with Slack fallback."""
        slack_id = getattr(member, "slack_id", None)
        if not slack_id:
            return StepResult(step_id, status="error",
                              error=f"Cannot notify: no slack_id for {getattr(member, 'name', 'unknown')}")

        try:
            member_dict = member.to_dict() if hasattr(member, 'to_dict') else {}
            channels = member_dict.get("notification_channels", ["slack_dm", "desktop"])
            results = self.notification_router.deliver(
                recipient_name=member.name, recipient_slack_id=member.slack_id,
                title="GRIP Copilot", message=message, channels=channels,
                ntfy_topic=member_dict.get("ntfy_topic", ""),
                url=self._resolve_notification_url(step_id, member),
                notification_group=f"grip-copilot-{step_id}",
            )
            any_success = any(v for v in results.values())
            return StepResult(
                step_id, status="success" if any_success else "error",
                output={"delivered": any_success, "recipient": member.name, "channels": results},
                error="" if any_success else "All notification channels failed",
            )
        except Exception as e:
            logger.warning("NotificationRouter failed for %s: %s", member.name, e)
            return self._notify_dm_fallback(step_id, message, member, e)

    def _exec_escalate(self, step_id: str, params: Dict, inputs: Dict) -> StepResult:
        """Execute an escalate step - halt and require human input."""
        reason = params.get("reason", "Human input required")
        return StepResult(
            step_id,
            status="escalated",
            escalated=True,
            output={"reason": reason, "inputs": inputs},
        )

    def _exec_learn(self, step_id: str, params: Dict, inputs: Dict) -> StepResult:
        """Execute a learn step - record for adaptive engine."""
        # Adaptive learning integration happens at the ritual engine level
        return StepResult(step_id, output={
            "observation": params.get("observation", ""),
            "category": params.get("category", "general"),
            "inputs_recorded": list(inputs.keys()),
        })

    def _exec_create_issue(self, step_id: str, params: Dict, inputs: Dict) -> StepResult:
        """Execute a create_issue step - create GitHub issue via gh CLI."""
        import subprocess
        title = params.get("title", "")
        body = params.get("body", "")
        labels = params.get("labels", ["sprint-loop"])

        if not title:
            # Try to build title from inputs
            for key, val in inputs.items():
                if isinstance(val, dict) and val.get("title"):
                    title = val["title"]
                    break
            if not title:
                return StepResult(step_id, status="error", error="No title provided for issue")

        cmd = ["gh", "issue", "create", "--title", title, "--body", body]
        for label in labels:
            cmd.extend(["--label", label])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                issue_url = result.stdout.strip()
                return StepResult(step_id, output={"url": issue_url, "title": title})
            return StepResult(step_id, status="error", error=result.stderr.strip())
        except subprocess.TimeoutExpired:
            return StepResult(step_id, status="error", error="gh issue create timed out")

    def _exec_sprint_loop(self, step_id: str, params: Dict, inputs: Dict) -> StepResult:
        """Execute a sprint_loop step - scan and report GitHub issues for sprint pickup."""
        import subprocess
        label = params.get("label", "sprint-loop")
        limit = params.get("limit", 10)

        try:
            result = subprocess.run(
                ["gh", "issue", "list", "--label", label, "--state", "open",
                 "--json", "number,title,labels", "--limit", str(limit)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                issues = json.loads(result.stdout) if result.stdout.strip() else []
                return StepResult(step_id, output={
                    "issues": issues,
                    "count": len(issues),
                    "label": label,
                })
            return StepResult(step_id, status="error", error=result.stderr.strip())
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            return StepResult(step_id, status="error", error=str(e))

    def _rsi_mine_all(self, step_id: str, rsi_script: str) -> StepResult:
        """Run all three RSI mining operations and aggregate results."""
        import subprocess
        results = {}
        for mine_type in ["mine-ralph", "mine-issues", "mine-memory"]:
            try:
                r = subprocess.run(
                    ["python3", rsi_script, mine_type],
                    capture_output=True, text=True, timeout=30,
                )
                results[mine_type] = r.stdout.strip() if r.returncode == 0 else r.stderr.strip()
            except (subprocess.TimeoutExpired, OSError) as e:
                results[mine_type] = f"error: {e}"
        return StepResult(step_id, output={"action": "mine-all", "results": results})

    def _rsi_build_cmd(self, action: str, rsi_script: str, params: Dict, inputs: Dict) -> list:
        """Build CLI command for an RSI action. Returns empty list for unknown actions."""
        simple_actions = {"list-queue", "summary", "clear-stop"}
        if action in simple_actions:
            return ["python3", rsi_script, action]
        if action == "stop":
            return ["python3", rsi_script, "stop", params.get("reason", "workflow_requested")]
        if action == "record-score":
            return ["python3", rsi_script, "record-score",
                    str(params.get("score", inputs.get("score", "0")))]
        if action == "feed-ralph":
            cmd = ["python3", rsi_script, "feed-ralph",
                   str(params.get("pr_number", inputs.get("pr_number", "0"))),
                   str(params.get("score", inputs.get("score", "0")))]
            comments = params.get("comments", inputs.get("comments", []))
            if isinstance(comments, list):
                cmd.extend(comments)
            return cmd
        return []

    def _rsi_run_cmd(self, step_id: str, action: str, cmd: list) -> StepResult:
        """Execute an RSI CLI command and parse results."""
        import subprocess
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            output_text = result.stdout.strip()
            if result.returncode != 0:
                if action in ("record-score", "feed-ralph") and result.returncode == 1:
                    return StepResult(step_id, output={"action": action, "stalled": True, "message": output_text})
                return StepResult(step_id, status="error", error=result.stderr.strip() or output_text)
            try:
                parsed = json.loads(output_text)
                return StepResult(step_id, output={"action": action, **parsed})
            except (json.JSONDecodeError, TypeError):
                return StepResult(step_id, output={"action": action, "message": output_text})
        except subprocess.TimeoutExpired:
            return StepResult(step_id, status="error", error=f"RSI {action} timed out (60s)")
        except OSError as e:
            return StepResult(step_id, status="error", error=str(e))

    def _exec_rsi_loop(self, step_id: str, params: Dict, inputs: Dict) -> StepResult:
        """Execute an rsi_loop step — run one RSI improvement cycle."""
        action = params.get("action", "summary")
        rsi_script = str(Path.home() / ".claude" / "lib" / "rsi_orchestrator.py")

        if action == "mine-all":
            return self._rsi_mine_all(step_id, rsi_script)

        cmd = self._rsi_build_cmd(action, rsi_script, params, inputs)
        if not cmd:
            return StepResult(step_id, status="error", error=f"Unknown RSI action: {action}")
        return self._rsi_run_cmd(step_id, action, cmd)

    def _exec_record_outcome(self, step_id: str, params: Dict, inputs: Dict) -> StepResult:
        """Execute a record_outcome step — close the feedback loop on a decision.

        Params:
            decision_id: ID of the original decision (or resolved from prior step)
            outcome: Description of what happened
            success: Whether the outcome was successful (default: True)
        """
        decision_id = params.get("decision_id", "")
        if not decision_id:
            for inp_data in inputs.values():
                if isinstance(inp_data, dict) and "decision_id" in inp_data:
                    decision_id = inp_data["decision_id"]
                    break

        if not decision_id:
            return StepResult(step_id, status="error", error="No decision_id to record outcome for")

        outcome = params.get("outcome", "")
        if not outcome:
            summaries = []
            for key, val in inputs.items():
                if isinstance(val, dict):
                    if val.get("status") == "error":
                        summaries.append(f"{key}: FAILED - {val.get('error', '')[:100]}")
                    else:
                        summaries.append(f"{key}: OK")
            outcome = "; ".join(summaries) if summaries else "Completed"

        success = params.get("success", True)
        self.logger.record_outcome(decision_id, outcome, success=success)

        return StepResult(step_id, output={
            "decision_id": decision_id,
            "outcome": outcome,
            "success": success,
        })

    def _resolve_vars(self, obj: Any, ctx: Dict, step_outputs: Dict) -> Any:
        """Recursively resolve ${var} references in params."""
        if isinstance(obj, str):
            return self._resolve_string_vars(obj, ctx, step_outputs)
        elif isinstance(obj, dict):
            return {k: self._resolve_vars(v, ctx, step_outputs) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_vars(item, ctx, step_outputs) for item in obj]
        return obj

    def _resolve_string_vars(self, s: str, ctx: Dict, step_outputs: Dict) -> str:
        """Resolve ${var.path} in a string."""
        def replacer(match):
            var_path = match.group(1)
            # Check step outputs first
            parts = var_path.split(".")
            if parts[0] in step_outputs:
                return self._deep_get(step_outputs, parts)
            # Then check context
            result = self._deep_get(ctx, parts)
            if result == "":
                logger.debug("Unresolved variable ${%s} in template", var_path)
            return result

        return re.sub(r"\$\{([^}]+)\}", replacer, s)

    def _deep_get(self, data: Dict, keys: List[str]) -> str:
        """Navigate nested dict by key path."""
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key, "")
            else:
                return str(current)
        return str(current)

    def _eval_condition(self, condition: str, ctx: Dict, step_outputs: Dict) -> bool:
        """Evaluate a simple skip_if condition."""
        resolved = self._resolve_string_vars(condition, ctx, step_outputs)
        return resolved.lower() in ("true", "1", "yes")

    def _format_dict_entry(self, key: str, value: dict) -> list:
        """Format a dict-typed step input as labelled bullet lines."""
        lines = [f"*{key}*:"]
        for k, v in value.items():
            lines.append(f"  • {k}: {v}")
        return lines

    def _format_notification(self, inputs: Dict) -> str:
        """Format workflow step inputs into a notification message.

        Returns empty string if no meaningful content to display.
        Callers must check for empty return before routing to avoid
        sending blank notifications (the 'notification spam' fix).
        """
        parts = []
        for key, value in inputs.items():
            if isinstance(value, dict):
                parts.extend(self._format_dict_entry(key, value))
            elif isinstance(value, str) and len(value) > 0:
                parts.append(f"*{key}*: {value}")
        return "\n\n".join(parts) if parts else ""

    def check_escalations(self, data: Dict[str, Any]) -> List[Dict]:
        """
        Run escalation rules against provided data and deliver alerts.

        Args:
            data: Dict with keys 'prs', 'ci', 'health_history'

        Returns:
            List of triggered alerts
        """
        alerts = self.escalation.evaluate_all(data)

        for alert in alerts:
            member = self._resolve_recipient(
                self.escalation.defaults.get("recipient_role", "founder")
            )
            if member:
                severity_prefix = {
                    "critical": "[CRITICAL]",
                    "urgent": "[URGENT]",
                    "warning": "[WARNING]",
                    "info": "[INFO]",
                }.get(alert.get("severity", "info"), "[INFO]")

                message = f"{severity_prefix} {alert['message']}"
                channels = alert.get("channels", ["slack_dm"])

                try:
                    self.notification_router.deliver(
                        recipient_name=member.name,
                        recipient_slack_id=member.slack_id,
                        title=f"GRIP Escalation: {alert.get('rule_id', 'unknown')}",
                        message=message,
                        channels=channels,
                        ntfy_topic=getattr(member, "ntfy_topic", ""),
                    )
                    self.escalation.record_fired(alert["rule_id"])
                except Exception as e:
                    logger.error("Escalation delivery failed for %s: %s", alert.get("rule_id"), e)

        return alerts

    def _store_episodic(
        self,
        workflow_id: str,
        step_id: str,
        step_type: str,
        status: str,
        output: Any = None,
        error: str = "",
    ) -> None:
        """
        Store episodic memory of a workflow step completion via KONO.

        Records what was attempted, what succeeded/failed, and basic metrics
        so future sessions can recall past workflow execution patterns.

        Args:
            workflow_id: Workflow that was running
            step_id: Step that completed
            step_type: Type of step (fetch, analyze, etc.)
            status: Outcome status (success, error, escalated, etc.)
            output: Step output summary (truncated for storage)
            error: Error message if failed
        """
        if not _kono_available:
            return

        try:
            substrate = MemorySubstrate()
            output_summary = ""
            if output is not None:
                raw = json.dumps(output, default=str)
                output_summary = raw[:500] if len(raw) > 500 else raw

            content = (
                f"Workflow '{workflow_id}' step '{step_id}' ({step_type}): {status}."
            )
            if error:
                content += f" Error: {error}"
            if output_summary:
                content += f" Output: {output_summary}"

            substrate.store_session_memory(
                content=content,
                memory_type="episodic",
            )
            substrate.close()
        except Exception as e:
            logger.debug("Episodic memory storage failed (non-fatal): %s", e)

    def _step_type_to_action(self, step_type: str, trust_level: int) -> str:
        """Map step type to trust action name."""
        mapping = {
            "fetch": "read_company_profile",
            "analyze": "read_company_profile",
            "decide": "log_decision",
            "notify": "dm_owner" if trust_level <= 1 else "post_team_channel",
            "escalate": "dm_owner",
            "learn": "update_preferences",
            "create_issue": "dm_owner",
            "sprint_loop": "read_company_profile",
            "rsi_loop": "dm_owner",  # RSI actions can modify state
        }
        return mapping.get(step_type, "read_company_profile")
