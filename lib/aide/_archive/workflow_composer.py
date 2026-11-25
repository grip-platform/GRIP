"""
Copilot Workflow Composer — build custom copilot workflows from blocks.

Users compose workflows from triggers, conditions, actions, and notifications.
Workflows are saved as YAML and can be activated in the copilot scheduler.

Usage:
    python3 lib/copilot/workflow_composer.py list           # List templates
    python3 lib/copilot/workflow_composer.py validate <file> # Validate workflow
    python3 lib/copilot/workflow_composer.py compose         # Interactive compose
    python3 lib/copilot/workflow_composer.py export <name>   # Export workflow
"""

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
WORKFLOWS_DIR = CLAUDE_DIR / "data" / "aide" / "workflows"


# === Block Types ===

@dataclass
class TriggerBlock:
    """When the workflow fires."""
    trigger_type: str  # schedule, event, manual, webhook
    config: dict = field(default_factory=dict)
    # schedule: {"cron": "0 9 * * 1-5"}
    # event: {"on": "pr_merged", "filter": {"branch": "main"}}
    # manual: {}
    # webhook: {"path": "/trigger/my-workflow"}

    def validate(self) -> list:
        errors = []
        valid_types = ("schedule", "event", "manual", "webhook")
        if self.trigger_type not in valid_types:
            errors.append(f"Invalid trigger type: {self.trigger_type}. Must be one of {valid_types}")
        if self.trigger_type == "schedule" and "cron" not in self.config:
            errors.append("Schedule trigger requires 'cron' in config")
        if self.trigger_type == "event" and "on" not in self.config:
            errors.append("Event trigger requires 'on' in config")
        return errors


@dataclass
class ConditionBlock:
    """Gate that must pass before actions execute."""
    condition_type: str  # git_state, time_window, metric_threshold, branch_match
    config: dict = field(default_factory=dict)
    negate: bool = False

    def validate(self) -> list:
        errors = []
        valid_types = ("git_state", "time_window", "metric_threshold", "branch_match",
                       "file_exists", "env_var", "day_of_week")
        if self.condition_type not in valid_types:
            errors.append(f"Invalid condition type: {self.condition_type}. Must be one of {valid_types}")
        return errors


@dataclass
class ActionBlock:
    """What the workflow does."""
    action_type: str  # run_script, git_command, generate_report, send_notification, api_call
    config: dict = field(default_factory=dict)
    timeout: int = 120  # seconds
    retry: int = 0
    continue_on_error: bool = False

    def validate(self) -> list:
        errors = []
        valid_types = ("run_script", "git_command", "generate_report",
                       "send_notification", "api_call", "create_issue",
                       "update_wiki", "run_tests", "deploy")
        if self.action_type not in valid_types:
            errors.append(f"Invalid action type: {self.action_type}. Must be one of {valid_types}")
        if self.action_type == "run_script" and "script" not in self.config:
            errors.append("run_script action requires 'script' in config")
        if self.timeout < 1 or self.timeout > 3600:
            errors.append("Timeout must be between 1 and 3600 seconds")
        if self.retry < 0 or self.retry > 5:
            errors.append("Retry must be between 0 and 5")
        return errors


@dataclass
class NotificationBlock:
    """How results are communicated."""
    channel: str  # slack, github_comment, log, email
    config: dict = field(default_factory=dict)
    on_success: bool = True
    on_failure: bool = True

    def validate(self) -> list:
        errors = []
        valid_channels = ("slack", "github_comment", "log", "email", "webhook")
        if self.channel not in valid_channels:
            errors.append(f"Invalid channel: {self.channel}. Must be one of {valid_channels}")
        if self.channel == "slack" and "channel" not in self.config:
            errors.append("Slack notification requires 'channel' in config")
        return errors


# === Composed Workflow ===

@dataclass
class ComposedWorkflow:
    """A complete workflow composed from blocks."""
    name: str
    description: str = ""
    version: str = "1.0"
    triggers: list = field(default_factory=list)
    conditions: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    notifications: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: str = ""
    enabled: bool = True

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def validate(self) -> list:
        """Validate the entire workflow. Returns list of error strings."""
        errors = []
        if not self.name:
            errors.append("Workflow must have a name")
        if not self.name.replace("-", "").replace("_", "").isalnum():
            errors.append("Workflow name must be alphanumeric with hyphens/underscores only")
        if not self.triggers:
            errors.append("Workflow must have at least one trigger")
        if not self.actions:
            errors.append("Workflow must have at least one action")
        for t in self.triggers:
            errors.extend(t.validate())
        for c in self.conditions:
            errors.extend(c.validate())
        for a in self.actions:
            errors.extend(a.validate())
        for n in self.notifications:
            errors.extend(n.validate())
        return errors

    def to_dict(self) -> dict:
        """Convert to serializable dict."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "metadata": self.metadata,
            "triggers": [asdict(t) for t in self.triggers],
            "conditions": [asdict(c) for c in self.conditions],
            "actions": [asdict(a) for a in self.actions],
            "notifications": [asdict(n) for n in self.notifications],
        }

    def to_yaml_str(self) -> str:
        """Convert to YAML-like string (no pyyaml dependency)."""
        lines = [
            f"name: {self.name}",
            f"description: {self.description}",
            f"version: {self.version}",
            f"enabled: {str(self.enabled).lower()}",
            "",
            "triggers:",
        ]
        for t in self.triggers:
            lines.append(f"  - type: {t.trigger_type}")
            for k, v in t.config.items():
                lines.append(f"    {k}: {v}")
        if self.conditions:
            lines.append("")
            lines.append("conditions:")
            for c in self.conditions:
                neg = " (negated)" if c.negate else ""
                lines.append(f"  - type: {c.condition_type}{neg}")
                for k, v in c.config.items():
                    lines.append(f"    {k}: {v}")
        lines.append("")
        lines.append("actions:")
        for a in self.actions:
            lines.append(f"  - type: {a.action_type}")
            lines.append(f"    timeout: {a.timeout}")
            if a.retry > 0:
                lines.append(f"    retry: {a.retry}")
            for k, v in a.config.items():
                lines.append(f"    {k}: {v}")
        if self.notifications:
            lines.append("")
            lines.append("notifications:")
            for n in self.notifications:
                lines.append(f"  - channel: {n.channel}")
                lines.append(f"    on_success: {str(n.on_success).lower()}")
                lines.append(f"    on_failure: {str(n.on_failure).lower()}")
                for k, v in n.config.items():
                    lines.append(f"    {k}: {v}")
        return "\n".join(lines)


# === Workflow Templates ===

TEMPLATES = {
    "morning-standup": ComposedWorkflow(
        name="morning-standup",
        description="Generate standup notes every weekday morning",
        triggers=[TriggerBlock("schedule", {"cron": "0 9 * * 1-5"})],
        conditions=[ConditionBlock("day_of_week", {"days": [0, 1, 2, 3, 4]})],
        actions=[
            ActionBlock("generate_report", {"type": "standup_notes"}),
        ],
        notifications=[
            NotificationBlock("slack", {"channel": "general"}),
        ],
    ),
    "pr-review-digest": ComposedWorkflow(
        name="pr-review-digest",
        description="Daily digest of open PRs needing review",
        triggers=[TriggerBlock("schedule", {"cron": "0 10 * * 1-5"})],
        conditions=[],
        actions=[
            ActionBlock("generate_report", {"type": "pr_digest"}),
        ],
        notifications=[
            NotificationBlock("slack", {"channel": "dev"}),
        ],
    ),
    "deploy-on-merge": ComposedWorkflow(
        name="deploy-on-merge",
        description="Auto-deploy when PR merges to main",
        triggers=[TriggerBlock("event", {"on": "pr_merged", "branch": "main"})],
        conditions=[ConditionBlock("branch_match", {"pattern": "main"})],
        actions=[
            ActionBlock("run_tests", {"suite": "integration"}),
            ActionBlock("deploy", {"target": "production"}),
        ],
        notifications=[
            NotificationBlock("slack", {"channel": "deploys"}, on_success=True, on_failure=True),
        ],
    ),
    "weekly-health-check": ComposedWorkflow(
        name="weekly-health-check",
        description="Weekly project health report with red-team analysis",
        triggers=[TriggerBlock("schedule", {"cron": "0 16 * * 5"})],
        conditions=[],
        actions=[
            ActionBlock("generate_report", {"type": "health_dashboard"}),
            ActionBlock("generate_report", {"type": "red_team_review"}),
        ],
        notifications=[
            NotificationBlock("slack", {"channel": "general"}),
        ],
    ),
    "stale-branch-cleanup": ComposedWorkflow(
        name="stale-branch-cleanup",
        description="Clean up branches merged more than 7 days ago",
        triggers=[TriggerBlock("schedule", {"cron": "0 12 * * 1"})],
        conditions=[],
        actions=[
            ActionBlock("git_command", {"command": "branch-cleanup", "days": 7}),
        ],
        notifications=[
            NotificationBlock("log", {}),
        ],
    ),
    "backlog-rerank": ComposedWorkflow(
        name="backlog-rerank",
        description="Re-rank backlog issues every 6 hours",
        triggers=[TriggerBlock("schedule", {"cron": "0 */6 * * *"})],
        conditions=[],
        actions=[
            ActionBlock("run_script", {"script": "python3 lib/backlog_prioritiser.py rank"}),
            ActionBlock("run_script", {"script": "python3 lib/backlog_prioritiser.py label --apply"}),
        ],
        notifications=[
            NotificationBlock("log", {}),
        ],
    ),
    "client-report-friday": ComposedWorkflow(
        name="client-report-friday",
        description="Generate client delivery report every Friday afternoon",
        triggers=[TriggerBlock("schedule", {"cron": "0 16 * * 5"})],
        conditions=[ConditionBlock("day_of_week", {"days": [4]})],
        actions=[
            ActionBlock("generate_report", {"type": "client_report"}),
        ],
        notifications=[
            NotificationBlock("slack", {"channel": "management"}),
            NotificationBlock("email", {"to": "client@example.com"}),
        ],
    ),
    "test-on-push": ComposedWorkflow(
        name="test-on-push",
        description="Run tests on every push to feature branches",
        triggers=[TriggerBlock("event", {"on": "push"})],
        conditions=[
            ConditionBlock("branch_match", {"pattern": "main"}, negate=True),
        ],
        actions=[
            ActionBlock("run_tests", {"suite": "unit", "parallel": True}),
        ],
        notifications=[
            NotificationBlock("github_comment", {}, on_success=False, on_failure=True),
        ],
    ),
}


# === Composer Functions ===

def list_templates() -> list:
    """List available workflow templates."""
    return [
        {"name": name, "description": wf.description}
        for name, wf in TEMPLATES.items()
    ]


def get_template(name: str) -> Optional[ComposedWorkflow]:
    """Get a template by name."""
    return TEMPLATES.get(name)


def compose_from_blocks(
    name: str,
    description: str,
    triggers: list,
    conditions: list = None,
    actions: list = None,
    notifications: list = None,
) -> ComposedWorkflow:
    """Compose a workflow from individual blocks."""
    return ComposedWorkflow(
        name=name,
        description=description,
        triggers=triggers,
        conditions=conditions or [],
        actions=actions or [],
        notifications=notifications or [],
    )


def validate_workflow(workflow: ComposedWorkflow) -> dict:
    """Validate a workflow, returning {valid: bool, errors: list}."""
    errors = workflow.validate()
    return {"valid": len(errors) == 0, "errors": errors}


def save_workflow(workflow: ComposedWorkflow) -> Path:
    """Save a composed workflow to the workflows directory."""
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKFLOWS_DIR / f"{workflow.name}.json"
    with open(path, "w") as f:
        json.dump(workflow.to_dict(), f, indent=2)
    return path


def load_workflow(name: str) -> Optional[ComposedWorkflow]:
    """Load a workflow from the workflows directory."""
    path = WORKFLOWS_DIR / f"{name}.json"
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return _dict_to_workflow(data)


def _dict_to_workflow(data: dict) -> ComposedWorkflow:
    """Convert a dict back to a ComposedWorkflow."""
    triggers = [TriggerBlock(**t) for t in data.get("triggers", [])]
    conditions = [ConditionBlock(**c) for c in data.get("conditions", [])]
    actions = [ActionBlock(**a) for a in data.get("actions", [])]
    notifications = [NotificationBlock(**n) for n in data.get("notifications", [])]
    return ComposedWorkflow(
        name=data["name"],
        description=data.get("description", ""),
        version=data.get("version", "1.0"),
        triggers=triggers,
        conditions=conditions,
        actions=actions,
        notifications=notifications,
        metadata=data.get("metadata", {}),
        created_at=data.get("created_at", ""),
        enabled=data.get("enabled", True),
    )


def list_saved_workflows() -> list:
    """List all saved workflows."""
    if not WORKFLOWS_DIR.exists():
        return []
    workflows = []
    for path in sorted(WORKFLOWS_DIR.glob("*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            # Only include composed workflows (have 'triggers' key)
            if "triggers" in data:
                workflows.append({
                    "name": data.get("name", path.stem),
                    "description": data.get("description", ""),
                    "enabled": data.get("enabled", True),
                })
        except (json.JSONDecodeError, KeyError):
            continue
    return workflows


def clone_template(template_name: str, new_name: str, overrides: dict = None) -> ComposedWorkflow:
    """Clone a template with optional overrides."""
    template = get_template(template_name)
    if not template:
        raise ValueError(f"Template not found: {template_name}")

    data = template.to_dict()
    data["name"] = new_name
    data["created_at"] = datetime.now(timezone.utc).isoformat()

    if overrides:
        for key, value in overrides.items():
            if key in data:
                data[key] = value

    return _dict_to_workflow(data)


# === Formatters ===

def format_templates_text() -> str:
    """Format templates as plain text."""
    lines = ["Workflow Templates", "=" * 50, ""]
    for name, wf in TEMPLATES.items():
        trigger_types = ", ".join(t.trigger_type for t in wf.triggers)
        action_types = ", ".join(a.action_type for a in wf.actions)
        lines.append(f"  {name}")
        lines.append(f"    {wf.description}")
        lines.append(f"    Triggers: {trigger_types}")
        lines.append(f"    Actions: {action_types}")
        lines.append("")
    return "\n".join(lines)


def format_templates_slack() -> str:
    """Format templates as Slack mrkdwn."""
    lines = ["*Workflow Templates*", ""]
    for name, wf in TEMPLATES.items():
        trigger_types = ", ".join(t.trigger_type for t in wf.triggers)
        lines.append(f"  *{name}*")
        lines.append(f"  _{wf.description}_")
        lines.append(f"  Triggers: `{trigger_types}`")
        lines.append("")
    return "\n".join(lines)


def format_templates_json() -> str:
    """Format templates as JSON."""
    data = {
        "templates": [
            {
                "name": name,
                "description": wf.description,
                "triggers": [t.trigger_type for t in wf.triggers],
                "actions": [a.action_type for a in wf.actions],
                "conditions": [c.condition_type for c in wf.conditions],
                "notifications": [n.channel for n in wf.notifications],
            }
            for name, wf in TEMPLATES.items()
        ]
    }
    return json.dumps(data, indent=2)


def format_workflow_detail(workflow: ComposedWorkflow) -> str:
    """Format a single workflow in detail."""
    validation = validate_workflow(workflow)
    status = "VALID" if validation["valid"] else f"INVALID ({len(validation['errors'])} errors)"

    lines = [
        f"Workflow: {workflow.name}",
        f"Description: {workflow.description}",
        f"Version: {workflow.version}",
        f"Status: {status}",
        f"Enabled: {workflow.enabled}",
        "",
        "Triggers:",
    ]
    for t in workflow.triggers:
        lines.append(f"  - {t.trigger_type}: {json.dumps(t.config)}")

    if workflow.conditions:
        lines.append("")
        lines.append("Conditions:")
        for c in workflow.conditions:
            neg = " (NOT)" if c.negate else ""
            lines.append(f"  - {c.condition_type}{neg}: {json.dumps(c.config)}")

    lines.append("")
    lines.append("Actions:")
    for a in workflow.actions:
        retry_str = f" (retry: {a.retry})" if a.retry > 0 else ""
        lines.append(f"  - {a.action_type}{retry_str}: {json.dumps(a.config)}")

    if workflow.notifications:
        lines.append("")
        lines.append("Notifications:")
        for n in workflow.notifications:
            flags = []
            if n.on_success:
                flags.append("success")
            if n.on_failure:
                flags.append("failure")
            lines.append(f"  - {n.channel} (on: {', '.join(flags)})")

    if not validation["valid"]:
        lines.append("")
        lines.append("Validation Errors:")
        for err in validation["errors"]:
            lines.append(f"  - {err}")

    return "\n".join(lines)


# === CLI ===

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        print(format_templates_text())
    elif cmd == "templates":
        fmt = sys.argv[2] if len(sys.argv) > 2 else "text"
        formatters = {"text": format_templates_text, "slack": format_templates_slack, "json": format_templates_json}
        print(formatters.get(fmt, format_templates_text)())
    elif cmd == "validate":
        name = sys.argv[2] if len(sys.argv) > 2 else ""
        wf = load_workflow(name) or get_template(name)
        if not wf:
            print(f"Workflow not found: {name}")
            sys.exit(1)
        result = validate_workflow(wf)
        print(f"Valid: {result['valid']}")
        for err in result["errors"]:
            print(f"  Error: {err}")
    elif cmd == "export":
        name = sys.argv[2] if len(sys.argv) > 2 else ""
        wf = get_template(name)
        if not wf:
            print(f"Template not found: {name}")
            sys.exit(1)
        print(wf.to_yaml_str())
    elif cmd == "saved":
        for wf in list_saved_workflows():
            status = "enabled" if wf["enabled"] else "disabled"
            print(f"  [{status}] {wf['name']}: {wf['description']}")
    else:
        print(f"Unknown command: {cmd}")
        print("Commands: list, templates [text|slack|json], validate <name>, export <name>, saved")
        sys.exit(1)


if __name__ == "__main__":
    main()
