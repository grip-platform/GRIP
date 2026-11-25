"""Tests for lib/verify_runner — verify-block runner + hypothesis integration."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


def _fixture(*lines: str) -> str:
    return "---\n" + "\n".join(lines) + "\n---\n\nBody.\n"


def _write_pass_task(tmp_path: Path, sentinel: Path) -> str:
    p = tmp_path / "pass.md"
    p.write_text(_fixture(
        "id: t-pass", "title: Pass task",
        "verify: |", "  set -e", f"  touch {sentinel}",
    ))
    return str(p)


def _write_fail_task(tmp_path: Path) -> str:
    p = tmp_path / "fail.md"
    p.write_text(_fixture(
        "id: t-fail", "title: Fail task",
        "verify: |",
        "  set -e",
        "  echo 'diagnostic message before exit' >&2",
        "  test -f /nonexistent/path/that/cannot/exist",
    ))
    return str(p)


@pytest.fixture
def fake_he():
    """Mock hypothesis_engine so tests don't write real records."""
    with patch.dict(sys.modules):
        fake = MagicMock()
        registered = []

        def register(claim, metric, prediction, pr_number, baseline):
            h = MagicMock()
            h.claim = claim
            h.metric = metric
            h.prediction = prediction
            h.status = "pending"
            h.actual_value = None
            registered.append(h)
            return h

        fake.register = register
        fake._update_hypothesis = MagicMock()
        fake._log_verification = MagicMock()
        fake._registered = registered
        sys.modules["hypothesis_engine"] = fake
        # Force re-import of runner under the mock
        sys.modules.pop("verify_runner", None)
        import verify_runner
        yield fake, verify_runner


def test_pass_task_returns_zero_and_records_confirmed(fake_he, tmp_path):
    he, runner = fake_he
    sentinel = tmp_path / "pass-sentinel"
    task = _write_pass_task(tmp_path, sentinel)
    rc = runner.run_task(task, log_path=str(tmp_path / "pass.log"))
    assert rc == 0
    assert sentinel.exists()
    assert len(he._registered) == 1
    h = he._registered[0]
    assert h.status == "confirmed"
    assert h.actual_value == 1.0
    assert h.claim == "Pass task"


def test_fail_task_returns_nonzero_and_records_refuted(fake_he, tmp_path):
    he, runner = fake_he
    task = _write_fail_task(tmp_path)
    log = tmp_path / "fail.log"
    rc = runner.run_task(task, log_path=str(log))
    assert rc != 0
    assert log.exists()
    assert len(he._registered) == 1
    h = he._registered[0]
    assert h.status == "refuted"
    assert h.actual_value == 0.0
    assert h.notes  # log tail attached


def test_no_verify_block_returns_three(fake_he, tmp_path):
    he, runner = fake_he
    task = tmp_path / "noverify.md"
    task.write_text(_fixture("id: nv", "title: NoVerify", "priority: normal"))
    rc = runner.run_task(str(task))
    assert rc == 3
    assert len(he._registered) == 0
