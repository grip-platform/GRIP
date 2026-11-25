"""Tests for lib/copilot/workflow_templates.py"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from aide.workflow_templates import (
    WorkflowTemplate,
    WorkflowStep,
    get_template,
    list_templates,
    instantiate,
    TEMPLATES,
)


class TestWorkflowStep:
    def test_defaults(self):
        step = WorkflowStep(id="test", step_type="fetch", description="Test step")
        assert step.timeout_seconds == 120
        assert step.depends_on == []
        assert step.config == {}


class TestWorkflowTemplate:
    def test_validate_required_missing(self):
        t = WorkflowTemplate(
            name="test", description="Test",
            input_schema={"task": {"type": "str", "required": True}},
            steps=[],
        )
        errors = t.validate_inputs({})
        assert any("Missing required" in e for e in errors)

    def test_validate_type_mismatch(self):
        t = WorkflowTemplate(
            name="test", description="Test",
            input_schema={"count": {"type": "int", "required": True}},
            steps=[],
        )
        errors = t.validate_inputs({"count": "not_an_int"})
        assert any("must be int" in e for e in errors)

    def test_validate_valid_params(self):
        t = WorkflowTemplate(
            name="test", description="Test",
            input_schema={"task": {"type": "str", "required": True}},
            steps=[],
        )
        errors = t.validate_inputs({"task": "hello"})
        assert errors == []

    def test_to_workflow_dict(self):
        t = WorkflowTemplate(
            name="test", description="Test",
            input_schema={"x": {"type": "str"}},
            steps=[WorkflowStep(id="s1", step_type="fetch", description="Fetch data")],
        )
        d = t.to_workflow_dict({"x": "val"})
        assert d["name"] == "test"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["id"] == "s1"
        assert d["steps"][0]["config"]["x"] == "val"


class TestBuiltInTemplates:
    def test_all_five_templates_exist(self):
        names = set(TEMPLATES.keys())
        expected = {"sprint-loop", "converge", "review-pipeline", "pair-mode", "deploy"}
        assert expected.issubset(names)

    def test_sprint_loop_has_steps(self):
        t = get_template("sprint-loop")
        assert t is not None
        assert len(t.steps) >= 4

    def test_converge_requires_task(self):
        t = get_template("converge")
        errors = t.validate_inputs({})
        assert any("task" in e for e in errors)

    def test_converge_requires_criterion(self):
        t = get_template("converge")
        errors = t.validate_inputs({"task": "fix bug"})
        assert any("criterion" in e for e in errors)

    def test_review_pipeline_requires_pr_number(self):
        t = get_template("review-pipeline")
        errors = t.validate_inputs({})
        assert any("pr_number" in e for e in errors)


class TestPublicAPI:
    def test_list_templates(self):
        templates = list_templates()
        assert len(templates) >= 5
        names = [t["name"] for t in templates]
        assert "sprint-loop" in names

    def test_get_template_exists(self):
        t = get_template("deploy")
        assert t is not None
        assert t.name == "deploy"

    def test_get_template_missing(self):
        assert get_template("nonexistent") is None

    def test_instantiate_valid(self):
        wf = instantiate("converge", {"task": "fix bug", "criterion": "tests pass"})
        assert wf["name"] == "converge"
        assert "steps" in wf
        assert wf["parameters"]["task"] == "fix bug"

    def test_instantiate_applies_defaults(self):
        wf = instantiate("sprint-loop", {})
        assert wf["parameters"]["max_issues"] == 3
        assert wf["parameters"]["branch_prefix"] == "feat/"

    def test_instantiate_unknown_template(self):
        with pytest.raises(ValueError, match="Unknown template"):
            instantiate("nonexistent", {})

    def test_instantiate_invalid_params(self):
        with pytest.raises(ValueError, match="Invalid parameters"):
            instantiate("converge", {"task": 123, "criterion": "ok"})
