"""Tests for lib/verify_extractor — the YAML verify-block extractor."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from verify_extractor import extract_verify_block  # noqa: E402


def _fixture(*lines: str) -> str:
    return "---\n" + "\n".join(lines) + "\n---\n\nBody.\n"


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def test_pass_block_extracted(tmp_path):
    path = _write(tmp_path, "pass.md", _fixture(
        "id: smoke-pass", "title: Pass smoke",
        "verify: |", "  set -e", "  echo ok",
    ))
    assert extract_verify_block(path) == "set -e\necho ok\n"


def test_no_verify_block_returns_none(tmp_path):
    path = _write(tmp_path, "noverify.md", _fixture(
        "id: smoke-no", "title: No block", "priority: normal",
    ))
    assert extract_verify_block(path) is None


def test_block_terminates_on_un_indented_key(tmp_path):
    path = _write(tmp_path, "term.md", _fixture(
        "id: smoke-term", "title: Termination",
        "verify: |", "  set -e", "  echo line1", "  echo line2",
        "priority: high",
    ))
    block = extract_verify_block(path)
    assert block == "set -e\necho line1\necho line2\n"
    assert "priority" not in block


def test_block_terminates_on_closing_fence(tmp_path):
    path = _write(tmp_path, "fence.md", _fixture(
        "id: smoke-fence", "title: Fence",
        "verify: |", "  set -e", "  exit 1",
    ))
    block = extract_verify_block(path)
    assert block == "set -e\nexit 1\n"
    assert "Body" not in block


def test_missing_file_returns_none(tmp_path):
    assert extract_verify_block(str(tmp_path / "ghost.md")) is None


def test_blank_lines_inside_block_preserved(tmp_path):
    # Blank lines inside the verify block stay in
    content = (
        "---\n"
        "id: blank\n"
        "title: Blank\n"
        "verify: |\n"
        "  set -e\n"
        "  \n"
        "  echo after-blank\n"
        "---\n"
        "Body.\n"
    )
    path = _write(tmp_path, "blank.md", content)
    block = extract_verify_block(path)
    assert "set -e" in block
    assert "echo after-blank" in block
