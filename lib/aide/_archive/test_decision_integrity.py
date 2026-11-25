"""Tests for decision log integrity: purge, validation, dedup, indexing."""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from aide.decision_logger import DecisionLogger
from aide.decision_purge import identify_test_entries, purge
from aide.atomic_io import read_jsonl
from audit_chain import sign_entry


def _make_decision_entry(what: str, why: str = "reason", decision_id: str = "abc12345",
                         ts: str = "", ritual_id: str = ""):
    """Helper to create a decision entry."""
    if not ts:
        ts = datetime.now(timezone.utc).isoformat()
    entry = {
        "id": decision_id,
        "timestamp": ts,
        "ritual_id": ritual_id,
        "step_id": "",
        "decision": {"what": what, "why": why, "alternatives": [], "confidence": 0.8},
        "context": {},
        "outcome": None,
    }
    return sign_entry(entry)


def _make_outcome_entry(decision_id: str, outcome: str = "it worked", success: bool = True):
    """Helper to create an outcome entry."""
    entry = {
        "id": f"{decision_id}-outcome",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "outcome",
        "decision_id": decision_id,
        "outcome": outcome,
        "success": success,
    }
    return sign_entry(entry)


def _write_entries(path: Path, entries: list):
    """Write entries to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


# --- Task 1: Purge Tests ---

class TestPurge:
    def test_purge_removes_test_data(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        entries = [
            _make_decision_entry("test-0", decision_id="t0"),
            _make_decision_entry("test-5", decision_id="t5"),
            _make_decision_entry("Deploy new API version to staging", decision_id="real1"),
        ]
        _write_entries(log, entries)

        result = purge(log_path=log, dry_run=False)
        assert result["test_decisions_purged"] == 2
        remaining = read_jsonl(log)
        test_whats = [e.get("decision", {}).get("what", "") for e in remaining if e.get("type") != "outcome"]
        assert not any("test-" in w for w in test_whats)

    def test_purge_preserves_real_decisions(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        entries = [
            _make_decision_entry("test-3", decision_id="t3"),
            _make_decision_entry("Migrate database schema to v2", decision_id="real1"),
            _make_decision_entry("Enable feature flag for rollout", decision_id="real2"),
        ]
        _write_entries(log, entries)

        result = purge(log_path=log, dry_run=False)
        remaining = read_jsonl(log)
        real_ids = [e.get("id") for e in remaining if e.get("type") != "outcome"]
        assert "real1" in real_ids
        assert "real2" in real_ids
        assert result["remaining"] == 2

    def test_purge_removes_orphaned_outcomes(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        entries = [
            _make_decision_entry("test-7", decision_id="t7"),
            _make_outcome_entry("t7", outcome="test outcome"),
            _make_decision_entry("Scale workers to handle load", decision_id="real1"),
            _make_outcome_entry("real1", outcome="scaled successfully"),
        ]
        _write_entries(log, entries)

        result = purge(log_path=log, dry_run=False)
        assert result["orphaned_outcomes_purged"] == 1
        remaining = read_jsonl(log)
        # real decision + real outcome remain
        assert len(remaining) == 2
        outcome_ids = [e.get("decision_id") for e in remaining if e.get("type") == "outcome"]
        assert "real1" in outcome_ids
        assert "t7" not in outcome_ids


# --- Task 2: Validation + Dedup Tests ---

class TestValidation:
    def test_validate_rejects_short_what(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        _write_entries(log, [])
        dl = DecisionLogger(log_path=log)

        errors = dl.validate_decision("short", "valid reason")
        assert any("too short" in e for e in errors)

    def test_validate_rejects_test_pattern(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        _write_entries(log, [])
        dl = DecisionLogger(log_path=log)

        for pattern in ["test-0", "test_1", "Test9"]:
            errors = dl.validate_decision(pattern, "some reason")
            assert any("test pattern" in e for e in errors), f"Should reject {pattern!r}"

    def test_validate_rejects_empty_why(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        _write_entries(log, [])
        dl = DecisionLogger(log_path=log)

        errors = dl.validate_decision("A valid decision description", "")
        assert any("empty" in e for e in errors)

        errors2 = dl.validate_decision("A valid decision description", "   ")
        assert any("empty" in e for e in errors2)

    def test_dedup_rejects_duplicate_within_5min(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        entries = [
            _make_decision_entry("Deploy new API version to staging", decision_id="d1", ts=recent_ts),
        ]
        _write_entries(log, entries)

        dl = DecisionLogger(log_path=log)
        result = dl.log_decision(
            what="Deploy new API version to staging",
            why="Need to test",
        )
        assert result is None  # Rejected as duplicate

    def test_dedup_allows_after_5min(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        entries = [
            _make_decision_entry("Deploy new API version to staging", decision_id="d1", ts=old_ts),
        ]
        _write_entries(log, entries)

        dl = DecisionLogger(log_path=log)
        result = dl.log_decision(
            what="Deploy new API version to staging",
            why="Need to test again after fixes",
        )
        assert result is not None  # Accepted since >5 min


# --- Task 3: Index + Link + Stats Tests ---

class TestIndex:
    def test_index_fast_lookup(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        entries = [
            _make_decision_entry("Upgrade Redis cluster to version 7", decision_id="d1"),
            _make_outcome_entry("d1", outcome="upgrade successful"),
            _make_decision_entry("Switch CDN provider to CloudFront", decision_id="d2"),
        ]
        _write_entries(log, entries)

        dl = DecisionLogger(log_path=log)
        result = dl.get_decision_with_outcome("d1")
        assert result is not None
        assert result["outcome"]["outcome"] == "upgrade successful"

        # d2 has no outcome
        result2 = dl.get_decision_with_outcome("d2")
        assert result2 is not None
        assert result2.get("outcome") is None

    def test_resolution_stats_breakdown(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        now = datetime.now(timezone.utc)
        entries = [
            _make_decision_entry("Implement caching layer for API", decision_id="d1",
                                 ts=(now - timedelta(hours=2)).isoformat()),
            _make_outcome_entry("d1"),
            _make_decision_entry("Refactor auth middleware completely", decision_id="d2",
                                 ts=(now - timedelta(hours=12)).isoformat()),
            _make_decision_entry("Migrate to new logging framework", decision_id="d3",
                                 ts=(now - timedelta(hours=50)).isoformat()),
            _make_decision_entry("Archive old deployment scripts", decision_id="d4",
                                 ts=(now - timedelta(hours=100)).isoformat()),
        ]
        _write_entries(log, entries)

        dl = DecisionLogger(log_path=log)
        stats = dl.resolution_stats()
        assert stats["total_decisions"] == 4
        assert stats["resolved"] == 1
        assert stats["unresolved"] == 3
        assert "by_age_bucket" in stats
        assert stats["by_age_bucket"]["<24h"] == 1   # d2
        assert stats["by_age_bucket"]["24-72h"] == 1  # d3
        assert stats["by_age_bucket"][">72h"] == 1    # d4

    def test_link_outcomes_finds_unlinked(self, tmp_path):
        log = tmp_path / "decision-log.jsonl"
        entries = [
            _make_decision_entry("Roll out feature flag globally", decision_id="d1"),
            _make_decision_entry("Update rate limiting config", decision_id="d2"),
            _make_outcome_entry("d1", outcome="rollout complete"),
            # d2 has no outcome - unlinked
        ]
        _write_entries(log, entries)

        dl = DecisionLogger(log_path=log)
        linked = dl.link_outcomes()
        # d1 has a matching outcome, d2 does not
        assert linked == 1
