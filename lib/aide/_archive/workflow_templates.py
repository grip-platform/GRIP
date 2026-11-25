"""
Copilot Workflow Templates
===========================

Parameterised workflow definitions for common GRIP operations.
Each template defines input schema, step sequence, and output contract.
Templates can be instantiated with parameters and fed into the
WorkflowRunner for execution.

Templates:
    sprint-loop: Autonomous sprint with scan-implement-PR-review cycle
    converge: Recursive convergence with depth control
    review-pipeline: PR pre-screen + scoring + council review
    pair-mode: Pair programming session with remote partner
    deploy: Branch push + PR creation + CI check + merge

Usage:
    from lib.aide.workflow_templates import get_template, list_templates, instantiate

    template = get_template("sprint-loop")
    workflow = instantiate("sprint-loop", {"max_issues": 3, "branch_prefix": "feat/"})
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorkflowStep:
    """A single step in a workflow template."""
    id: str
    step_type: str  # fetch, analyze, decide, execute, notify, gate
    description: str
    config: dict = field(default_factory=dict)
    depends_on: list = field(default_factory=list)
    timeout_seconds: int = 120


@dataclass
class WorkflowTemplate:
    """A parameterised workflow definition."""
    name: str
    description: str
    input_schema: dict  # parameter name -> {type, required, default, description}
    steps: list  # list of WorkflowStep
    output_contract: dict = field(default_factory=dict)  # what the workflow produces
    tags: list = field(default_factory=list)

    def validate_inputs(self, params: dict) -> list[str]:
        """Validate provided parameters against input schema."""
        errors = []
        for param_name, schema in self.input_schema.items():
            if schema.get("required", False) and param_name not in params:
                errors.append(f"Missing required parameter: {param_name}")
            if param_name in params:
                expected_type = schema.get("type", "str")
                val = params[param_name]
                type_map = {"str": str, "int": int, "bool": bool, "list": list}
                if expected_type in type_map and not isinstance(val, type_map[expected_type]):
                    errors.append(
                        f"Parameter '{param_name}' must be {expected_type}, got {type(val).__name__}"
                    )
        return errors

    def to_workflow_dict(self, params: dict) -> dict:
        """Convert template + params into a workflow definition dict
        compatible with WorkflowRunner."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": params,
            "steps": [
                {
                    "id": step.id,
                    "type": step.step_type,
                    "description": step.description,
                    "config": {**step.config, **params},
                    "depends_on": step.depends_on,
                    "timeout": step.timeout_seconds,
                }
                for step in self.steps
            ],
            "output_contract": self.output_contract,
        }


# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, WorkflowTemplate] = {}


def _register(template: WorkflowTemplate):
    TEMPLATES[template.name] = template


# Sprint Loop
_register(WorkflowTemplate(
    name="sprint-loop",
    description="Autonomous sprint: scan issues, implement, create PR, review, close",
    input_schema={
        "max_issues": {"type": "int", "required": False, "default": 3,
                       "description": "Maximum issues to process per sprint"},
        "branch_prefix": {"type": "str", "required": False, "default": "feat/",
                          "description": "Branch name prefix for feature branches"},
        "label": {"type": "str", "required": False, "default": "sprint-loop",
                  "description": "GitHub label to filter issues"},
    },
    steps=[
        WorkflowStep(id="scan", step_type="fetch",
                     description="Scan GitHub issues with sprint label",
                     config={"source": "github_issues", "filter": "label"}),
        WorkflowStep(id="prioritise", step_type="analyze",
                     description="Rank issues by complexity and impact",
                     depends_on=["scan"]),
        WorkflowStep(id="implement", step_type="execute",
                     description="Implement fix in worktree isolation",
                     depends_on=["prioritise"],
                     timeout_seconds=600),
        WorkflowStep(id="test", step_type="gate",
                     description="Run test suite, gate on pass",
                     depends_on=["implement"]),
        WorkflowStep(id="review", step_type="analyze",
                     description="Pre-review scoring via MoE cascade",
                     depends_on=["test"]),
        WorkflowStep(id="pr", step_type="execute",
                     description="Create PR and push branch",
                     depends_on=["review"]),
    ],
    output_contract={"prs_created": "list[int]", "issues_closed": "list[int]"},
    tags=["rsi", "autonomous"],
))

# Converge
_register(WorkflowTemplate(
    name="converge",
    description="Recursive convergence loop with depth control",
    input_schema={
        "task": {"type": "str", "required": True,
                 "description": "Task description to converge on"},
        "criterion": {"type": "str", "required": True,
                      "description": "Success criterion for convergence"},
        "max_depth": {"type": "int", "required": False, "default": 3,
                      "description": "Maximum recursion depth"},
    },
    steps=[
        WorkflowStep(id="decompose", step_type="analyze",
                     description="Break task into convergence-measurable sub-goals"),
        WorkflowStep(id="attempt", step_type="execute",
                     description="Execute current approach",
                     depends_on=["decompose"]),
        WorkflowStep(id="measure", step_type="analyze",
                     description="Measure progress against criterion",
                     depends_on=["attempt"]),
        WorkflowStep(id="decide", step_type="decide",
                     description="Converged or recurse deeper",
                     depends_on=["measure"]),
    ],
    output_contract={"converged": "bool", "depth_reached": "int", "result": "str"},
    tags=["reasoning", "recursive"],
))

# Review Pipeline
_register(WorkflowTemplate(
    name="review-pipeline",
    description="PR pre-screen with MoE cascade scoring and council review",
    input_schema={
        "pr_number": {"type": "int", "required": True,
                      "description": "GitHub PR number to review"},
        "use_local": {"type": "bool", "required": False, "default": True,
                      "description": "Use local Llama scorer first"},
    },
    steps=[
        WorkflowStep(id="fetch_diff", step_type="fetch",
                     description="Fetch PR diff from GitHub"),
        WorkflowStep(id="heuristic", step_type="gate",
                     description="L0 heuristic pre-screen",
                     depends_on=["fetch_diff"]),
        WorkflowStep(id="score", step_type="analyze",
                     description="L1 dimensional scoring via cascade",
                     depends_on=["heuristic"]),
        WorkflowStep(id="council", step_type="decide",
                     description="L2 council diagnosis if score < 80",
                     depends_on=["score"]),
        WorkflowStep(id="notify", step_type="notify",
                     description="Post review result to PR",
                     depends_on=["council"]),
    ],
    output_contract={"composite_score": "int", "verdict": "str", "suggestions": "list"},
    tags=["review", "moe"],
))

# Pair Mode
_register(WorkflowTemplate(
    name="pair-mode",
    description="Pair programming session with remote partner via Tailscale",
    input_schema={
        "partner": {"type": "str", "required": True,
                    "description": "Partner name (e.g., 'toopie', 'andre')"},
        "task": {"type": "str", "required": False, "default": "",
                 "description": "Optional task description for the session"},
    },
    steps=[
        WorkflowStep(id="connect", step_type="execute",
                     description="Establish SSH + WebSocket connection"),
        WorkflowStep(id="sync_context", step_type="fetch",
                     description="Share codebase context with partner",
                     depends_on=["connect"]),
        WorkflowStep(id="work", step_type="execute",
                     description="Collaborative implementation",
                     depends_on=["sync_context"],
                     timeout_seconds=3600),
        WorkflowStep(id="commit", step_type="execute",
                     description="Joint commit with co-author attribution",
                     depends_on=["work"]),
    ],
    output_contract={"commits": "list[str]", "session_duration": "int"},
    tags=["collaboration", "pair"],
))

# Deploy
_register(WorkflowTemplate(
    name="deploy",
    description="Branch push, PR creation, CI check, and merge workflow",
    input_schema={
        "branch": {"type": "str", "required": True,
                   "description": "Branch name to deploy"},
        "base": {"type": "str", "required": False, "default": "main",
                 "description": "Base branch for PR"},
        "auto_merge": {"type": "bool", "required": False, "default": False,
                       "description": "Merge automatically if CI passes"},
    },
    steps=[
        WorkflowStep(id="push", step_type="execute",
                     description="Push branch to remote"),
        WorkflowStep(id="create_pr", step_type="execute",
                     description="Create PR with conventional commit title",
                     depends_on=["push"]),
        WorkflowStep(id="ci_check", step_type="gate",
                     description="Wait for CI checks to complete",
                     depends_on=["create_pr"],
                     timeout_seconds=300),
        WorkflowStep(id="merge", step_type="decide",
                     description="Merge if CI passes and auto_merge enabled",
                     depends_on=["ci_check"]),
    ],
    output_contract={"pr_number": "int", "merged": "bool", "ci_status": "str"},
    tags=["deployment", "ci"],
))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_templates() -> list[dict]:
    """List all available workflow templates."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "tags": t.tags,
            "params": list(t.input_schema.keys()),
        }
        for t in TEMPLATES.values()
    ]


def get_template(name: str) -> Optional[WorkflowTemplate]:
    """Get a workflow template by name."""
    return TEMPLATES.get(name)


def instantiate(name: str, params: dict) -> dict:
    """Instantiate a template with parameters.

    Returns a workflow definition dict compatible with WorkflowRunner.
    Raises ValueError if template not found or params invalid.
    """
    template = TEMPLATES.get(name)
    if template is None:
        raise ValueError(f"Unknown template: {name}. Available: {list(TEMPLATES.keys())}")

    # Apply defaults for missing optional params
    resolved = {}
    for param_name, schema in template.input_schema.items():
        if param_name in params:
            resolved[param_name] = params[param_name]
        elif "default" in schema:
            resolved[param_name] = schema["default"]

    errors = template.validate_inputs(resolved)
    if errors:
        raise ValueError(f"Invalid parameters: {'; '.join(errors)}")

    return template.to_workflow_dict(resolved)
