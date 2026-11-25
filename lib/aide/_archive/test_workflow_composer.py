"""Tests for the copilot workflow composer."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).parent.parent / "lib" / "aide"))
from aide.workflow_composer import (
    TriggerBlock,
    ConditionBlock,
    ActionBlock,
    NotificationBlock,
    ComposedWorkflow,
    TEMPLATES,
    list_templates,
    get_template,
    compose_from_blocks,
    validate_workflow,
    save_workflow,
    load_workflow,
    list_saved_workflows,
    clone_template,
    format_templates_text,
    format_templates_slack,
    format_templates_json,
    format_workflow_detail,
    _dict_to_workflow,
)


# === TriggerBlock Tests ===

class TestTriggerBlock:

    def test_valid_schedule(self):
        t = TriggerBlock("schedule", {"cron": "0 9 * * 1-5"})
        assert t.validate() == []

    def test_invalid_type(self):
        t = TriggerBlock("invalid_type")
        errors = t.validate()
        assert len(errors) == 1
        assert "Invalid trigger type" in errors[0]

    def test_schedule_missing_cron(self):
        t = TriggerBlock("schedule", {})
        errors = t.validate()
        assert any("cron" in e for e in errors)

    def test_event_missing_on(self):
        t = TriggerBlock("event", {})
        errors = t.validate()
        assert any("'on'" in e for e in errors)

    def test_valid_event(self):
        t = TriggerBlock("event", {"on": "pr_merged"})
        assert t.validate() == []

    def test_manual_trigger(self):
        t = TriggerBlock("manual", {})
        assert t.validate() == []

    def test_webhook_trigger(self):
        t = TriggerBlock("webhook", {"path": "/hook"})
        assert t.validate() == []


# === ConditionBlock Tests ===

class TestConditionBlock:

    def test_valid_condition(self):
        c = ConditionBlock("git_state", {"has_uncommitted": True})
        assert c.validate() == []

    def test_invalid_type(self):
        c = ConditionBlock("nonexistent")
        errors = c.validate()
        assert len(errors) == 1

    def test_negate_flag(self):
        c = ConditionBlock("branch_match", {"pattern": "main"}, negate=True)
        assert c.negate is True
        assert c.validate() == []

    def test_all_valid_types(self):
        valid = ("git_state", "time_window", "metric_threshold", "branch_match",
                 "file_exists", "env_var", "day_of_week")
        for t in valid:
            c = ConditionBlock(t)
            assert c.validate() == [], f"Failed for {t}"


# === ActionBlock Tests ===

class TestActionBlock:

    def test_valid_action(self):
        a = ActionBlock("run_script", {"script": "echo hello"})
        assert a.validate() == []

    def test_invalid_type(self):
        a = ActionBlock("nonexistent")
        errors = a.validate()
        assert len(errors) >= 1

    def test_run_script_missing_script(self):
        a = ActionBlock("run_script", {})
        errors = a.validate()
        assert any("script" in e for e in errors)

    def test_timeout_bounds(self):
        a = ActionBlock("run_tests", {}, timeout=0)
        assert len(a.validate()) >= 1

        a2 = ActionBlock("run_tests", {}, timeout=3601)
        assert len(a2.validate()) >= 1

    def test_retry_bounds(self):
        a = ActionBlock("run_tests", {}, retry=-1)
        assert len(a.validate()) >= 1

        a2 = ActionBlock("run_tests", {}, retry=6)
        assert len(a2.validate()) >= 1

    def test_valid_retry(self):
        a = ActionBlock("run_tests", {}, retry=3)
        assert a.validate() == []

    def test_continue_on_error(self):
        a = ActionBlock("deploy", {}, continue_on_error=True)
        assert a.continue_on_error is True

    def test_all_valid_types(self):
        valid = ("run_script", "git_command", "generate_report",
                 "send_notification", "api_call", "create_issue",
                 "update_wiki", "run_tests", "deploy")
        for t in valid:
            config = {"script": "test"} if t == "run_script" else {}
            a = ActionBlock(t, config)
            assert a.validate() == [], f"Failed for {t}"


# === NotificationBlock Tests ===

class TestNotificationBlock:

    def test_valid_slack(self):
        n = NotificationBlock("slack", {"channel": "general"})
        assert n.validate() == []

    def test_slack_missing_channel(self):
        n = NotificationBlock("slack", {})
        errors = n.validate()
        assert any("channel" in e for e in errors)

    def test_invalid_channel(self):
        n = NotificationBlock("carrier_pigeon")
        errors = n.validate()
        assert len(errors) >= 1

    def test_on_success_only(self):
        n = NotificationBlock("log", {}, on_success=True, on_failure=False)
        assert n.on_success is True
        assert n.on_failure is False

    def test_all_valid_channels(self):
        valid = ("slack", "github_comment", "log", "email", "webhook")
        for ch in valid:
            config = {"channel": "test"} if ch == "slack" else {}
            n = NotificationBlock(ch, config)
            assert n.validate() == [], f"Failed for {ch}"


# === ComposedWorkflow Tests ===

class TestComposedWorkflow:

    def test_valid_workflow(self):
        wf = ComposedWorkflow(
            name="test-workflow",
            triggers=[TriggerBlock("manual")],
            actions=[ActionBlock("run_tests", {})],
        )
        assert wf.validate() == []

    def test_missing_name(self):
        wf = ComposedWorkflow(
            name="",
            triggers=[TriggerBlock("manual")],
            actions=[ActionBlock("run_tests", {})],
        )
        errors = wf.validate()
        assert any("name" in e for e in errors)

    def test_invalid_name_chars(self):
        wf = ComposedWorkflow(
            name="test workflow!",
            triggers=[TriggerBlock("manual")],
            actions=[ActionBlock("run_tests", {})],
        )
        errors = wf.validate()
        assert any("alphanumeric" in e for e in errors)

    def test_missing_triggers(self):
        wf = ComposedWorkflow(name="test", triggers=[], actions=[ActionBlock("run_tests", {})])
        errors = wf.validate()
        assert any("trigger" in e for e in errors)

    def test_missing_actions(self):
        wf = ComposedWorkflow(name="test", triggers=[TriggerBlock("manual")], actions=[])
        errors = wf.validate()
        assert any("action" in e for e in errors)

    def test_cascading_validation(self):
        wf = ComposedWorkflow(
            name="test",
            triggers=[TriggerBlock("bad_type")],
            actions=[ActionBlock("bad_type", {})],
        )
        errors = wf.validate()
        assert len(errors) >= 2

    def test_auto_created_at(self):
        wf = ComposedWorkflow(name="test")
        assert wf.created_at != ""

    def test_to_dict(self):
        wf = ComposedWorkflow(
            name="test-wf",
            description="A test",
            triggers=[TriggerBlock("manual")],
            actions=[ActionBlock("run_tests", {})],
        )
        d = wf.to_dict()
        assert d["name"] == "test-wf"
        assert len(d["triggers"]) == 1
        assert d["triggers"][0]["trigger_type"] == "manual"

    def test_to_yaml_str(self):
        wf = ComposedWorkflow(
            name="test-wf",
            description="A test",
            triggers=[TriggerBlock("schedule", {"cron": "0 9 * * *"})],
            actions=[ActionBlock("run_tests", {"suite": "unit"})],
        )
        yaml = wf.to_yaml_str()
        assert "name: test-wf" in yaml
        assert "schedule" in yaml
        assert "cron:" in yaml

    def test_to_yaml_with_conditions(self):
        wf = ComposedWorkflow(
            name="test",
            triggers=[TriggerBlock("manual")],
            conditions=[ConditionBlock("branch_match", {"pattern": "main"}, negate=True)],
            actions=[ActionBlock("deploy", {})],
        )
        yaml = wf.to_yaml_str()
        assert "conditions:" in yaml
        assert "(NOT)" in yaml or "(negated)" in yaml

    def test_to_yaml_with_notifications(self):
        wf = ComposedWorkflow(
            name="test",
            triggers=[TriggerBlock("manual")],
            actions=[ActionBlock("deploy", {})],
            notifications=[NotificationBlock("slack", {"channel": "dev"})],
        )
        yaml = wf.to_yaml_str()
        assert "notifications:" in yaml
        assert "slack" in yaml


# === Template Tests ===

class TestTemplates:

    def test_all_templates_valid(self):
        for name, wf in TEMPLATES.items():
            errors = wf.validate()
            assert errors == [], f"Template '{name}' invalid: {errors}"

    def test_minimum_template_count(self):
        assert len(TEMPLATES) >= 6

    def test_list_templates(self):
        templates = list_templates()
        assert len(templates) >= 6
        for t in templates:
            assert "name" in t
            assert "description" in t

    def test_get_template(self):
        wf = get_template("morning-standup")
        assert wf is not None
        assert wf.name == "morning-standup"

    def test_get_nonexistent_template(self):
        assert get_template("nonexistent") is None

    def test_no_duplicate_names(self):
        names = list(TEMPLATES.keys())
        assert len(names) == len(set(names))


# === Composer Functions Tests ===

class TestComposerFunctions:

    def test_compose_from_blocks(self):
        wf = compose_from_blocks(
            name="custom-wf",
            description="My workflow",
            triggers=[TriggerBlock("manual")],
            actions=[ActionBlock("run_tests", {})],
        )
        assert wf.name == "custom-wf"
        assert wf.validate() == []

    def test_validate_valid(self):
        wf = ComposedWorkflow(
            name="test",
            triggers=[TriggerBlock("manual")],
            actions=[ActionBlock("run_tests", {})],
        )
        result = validate_workflow(wf)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_validate_invalid(self):
        wf = ComposedWorkflow(name="", triggers=[], actions=[])
        result = validate_workflow(wf)
        assert result["valid"] is False
        assert len(result["errors"]) >= 2


# === Save/Load Tests ===

class TestSaveLoad:

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("copilot.workflow_composer.WORKFLOWS_DIR", Path(tmpdir)):
                wf = ComposedWorkflow(
                    name="test-save",
                    description="Test save",
                    triggers=[TriggerBlock("manual")],
                    actions=[ActionBlock("run_tests", {})],
                    notifications=[NotificationBlock("log", {})],
                )
                path = save_workflow(wf)
                assert path.exists()

                loaded = load_workflow("test-save")
                assert loaded is not None
                assert loaded.name == "test-save"
                assert len(loaded.triggers) == 1
                assert len(loaded.actions) == 1

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("copilot.workflow_composer.WORKFLOWS_DIR", Path(tmpdir)):
                assert load_workflow("nonexistent") is None

    def test_list_saved_workflows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("copilot.workflow_composer.WORKFLOWS_DIR", Path(tmpdir)):
                wf1 = ComposedWorkflow(
                    name="wf1",
                    description="First",
                    triggers=[TriggerBlock("manual")],
                    actions=[ActionBlock("deploy", {})],
                )
                wf2 = ComposedWorkflow(
                    name="wf2",
                    description="Second",
                    triggers=[TriggerBlock("manual")],
                    actions=[ActionBlock("run_tests", {})],
                )
                save_workflow(wf1)
                save_workflow(wf2)

                saved = list_saved_workflows()
                assert len(saved) == 2
                names = [w["name"] for w in saved]
                assert "wf1" in names
                assert "wf2" in names

    def test_list_saved_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("copilot.workflow_composer.WORKFLOWS_DIR", Path(tmpdir)):
                assert list_saved_workflows() == []


# === Clone Tests ===

class TestClone:

    def test_clone_template(self):
        wf = clone_template("morning-standup", "my-standup")
        assert wf.name == "my-standup"
        assert len(wf.triggers) > 0
        assert wf.validate() == []

    def test_clone_with_overrides(self):
        wf = clone_template("morning-standup", "my-standup",
                           overrides={"description": "My custom standup"})
        assert wf.description == "My custom standup"

    def test_clone_nonexistent(self):
        try:
            clone_template("nonexistent", "new")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "not found" in str(e)


# === Formatter Tests ===

class TestFormatters:

    def test_format_text(self):
        output = format_templates_text()
        assert "Workflow Templates" in output
        assert "morning-standup" in output

    def test_format_slack(self):
        output = format_templates_slack()
        assert "*Workflow Templates*" in output
        assert "**" not in output

    def test_format_json(self):
        output = format_templates_json()
        data = json.loads(output)
        assert "templates" in data
        assert len(data["templates"]) >= 6

    def test_format_workflow_detail(self):
        wf = get_template("morning-standup")
        output = format_workflow_detail(wf)
        assert "morning-standup" in output
        assert "Triggers:" in output
        assert "Actions:" in output
        assert "VALID" in output

    def test_format_invalid_detail(self):
        wf = ComposedWorkflow(name="bad", triggers=[], actions=[])
        output = format_workflow_detail(wf)
        assert "INVALID" in output
        assert "Validation Errors" in output


# === Dict Roundtrip Tests ===

class TestRoundtrip:

    def test_dict_roundtrip(self):
        wf = ComposedWorkflow(
            name="roundtrip",
            description="Test roundtrip",
            triggers=[TriggerBlock("schedule", {"cron": "0 9 * * *"})],
            conditions=[ConditionBlock("branch_match", {"pattern": "main"}, negate=True)],
            actions=[ActionBlock("deploy", {"target": "prod"}, timeout=300, retry=2)],
            notifications=[NotificationBlock("slack", {"channel": "dev"}, on_failure=True)],
            metadata={"team": "core"},
        )
        d = wf.to_dict()
        restored = _dict_to_workflow(d)
        assert restored.name == wf.name
        assert len(restored.triggers) == len(wf.triggers)
        assert restored.triggers[0].trigger_type == "schedule"
        assert restored.conditions[0].negate is True
        assert restored.actions[0].timeout == 300
        assert restored.actions[0].retry == 2
        assert restored.notifications[0].on_failure is True
        assert restored.metadata == {"team": "core"}
