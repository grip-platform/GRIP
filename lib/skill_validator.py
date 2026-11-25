#!/usr/bin/env python3
"""
skill_validator.py -- Structural validation harness for skill outputs (#332).

Validates that skill outputs match expected schemas: required sections present,
no empty sections, valid link format, code blocks with language tags. Returns
a score 0-100 based on structural completeness.

Generalizable to any skill, with built-in schemas for common patterns
(markdown sections, JSON, checklist) and a specific schema for the
investigate-ticket skill.

Usage:
    from lib.skill_validator import validate_skill_output, BUILTIN_SCHEMAS

    result = validate_skill_output("investigate-ticket", output_text)
    print(result.score, result.errors, result.warnings)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Result of validating a skill output against a schema."""
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: int = 100


# ---------------------------------------------------------------------------
# Built-in schemas
# ---------------------------------------------------------------------------

BUILTIN_SCHEMAS: dict[str, dict[str, Any]] = {
    "investigate-ticket": {
        "required_sections": [
            "CONFIDENCE",
            "RECOMMENDED RESPONSE",
            "INVESTIGATION DETAILS",
        ],
        "optional_sections": [
            "Customer",
            "Symptoms",
            "Database State",
            "Root Cause",
        ],
        "require_code_lang_tags": False,
        "require_valid_links": True,
    },
    "markdown-report": {
        "required_sections": ["## Summary"],
        "optional_sections": [],
        "require_code_lang_tags": True,
        "require_valid_links": True,
    },
    "checklist": {
        "required_sections": [],
        "optional_sections": [],
        "require_checklist_items": True,
        "require_valid_links": False,
        "require_code_lang_tags": False,
    },
    "json-output": {
        "required_sections": [],
        "optional_sections": [],
        "require_valid_json": True,
        "require_valid_links": False,
        "require_code_lang_tags": False,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_CODE_BLOCK_OPEN_RE = re.compile(r"^```(\w*)\s*$", re.MULTILINE)
_SECTION_RE = re.compile(r"^#{1,6}\s+(.+)", re.MULTILINE)
_CHECKLIST_RE = re.compile(r"^- \[[ xX]\] ", re.MULTILINE)


def _find_sections(text: str) -> list[str]:
    """Return all markdown heading texts and UPPERCASE: label lines.

    Returns both the stripped heading text ("Summary") and the full
    heading prefix ("## Summary") so callers can match either form.
    """
    sections: list[str] = []
    for m in _SECTION_RE.finditer(text):
        stripped = m.group(1).strip()
        full = m.group(0).strip()  # e.g. "## Summary"
        sections.append(stripped)
        if full != stripped:
            sections.append(full)
    # Also detect LABEL: style sections (e.g. CONFIDENCE: 85%)
    label_re = re.compile(r"^([A-Z][A-Z _]+):", re.MULTILINE)
    for m in label_re.finditer(text):
        sections.append(m.group(1).strip())
    return sections


def _section_has_content(text: str, section_name: str) -> bool:
    """Check whether a section has non-whitespace content after its heading."""
    # Strip markdown heading prefix if present (e.g. "## Summary" -> "Summary")
    clean_name = re.sub(r"^#{1,6}\s+", "", section_name).strip()

    # Try markdown heading first
    pattern = re.compile(
        r"^#{1,6}\s+" + re.escape(clean_name) + r"\s*\n(.*?)(?=^#{1,6}\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if m:
        return bool(m.group(1).strip())
    # Try LABEL: style -- use word-boundary-aware substring match
    # e.g. "RECOMMENDED RESPONSE" matches "RECOMMENDED RESPONSE TO CUSTOMER:"
    pattern2 = re.compile(
        re.escape(clean_name) + r"[^:\n]*:\s*(.*?)(?=\n[A-Z][A-Z _]+:|\Z)",
        re.DOTALL,
    )
    m2 = pattern2.search(text)
    if m2:
        return bool(m2.group(1).strip())
    return False


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------

def validate_skill_output(
    skill_name: str,
    output: str,
    schema: dict[str, Any] | None = None,
) -> ValidationResult:
    """Validate a skill's output against a schema.

    Args:
        skill_name: Name of the skill (used to look up built-in schema).
        output: The raw text output from the skill.
        schema: Optional custom schema dict. If provided, overrides built-in.

    Returns:
        ValidationResult with score, errors, and warnings.
    """
    if schema is None:
        schema = BUILTIN_SCHEMAS.get(skill_name, {})

    result = ValidationResult()
    deductions = 0
    max_points = 100

    # --- Required sections ---------------------------------------------------
    required = schema.get("required_sections", [])
    found_sections = _find_sections(output)
    found_upper = [s.upper() for s in found_sections]

    for section in required:
        if not any(section.upper() in f for f in found_upper):
            result.errors.append(f"Missing required section: {section}")
            deductions += 20

    # --- Empty sections -------------------------------------------------------
    all_sections = required + schema.get("optional_sections", [])
    for section in all_sections:
        # Only warn if section heading exists but body is empty
        if any(section.upper() in f for f in found_upper):
            if not _section_has_content(output, section):
                result.warnings.append(f"Empty section: {section}")
                deductions += 5

    # --- Valid links ----------------------------------------------------------
    if schema.get("require_valid_links", False):
        for m in _LINK_RE.finditer(output):
            link_text, url = m.group(1), m.group(2)
            if not url.strip():
                result.errors.append(f"Empty link URL for [{link_text}]")
                deductions += 10
            elif not re.match(r"^https?://|^/|^#|^mailto:", url):
                result.warnings.append(f"Possibly invalid link: {url}")
                deductions += 3

    # --- Code block language tags ---------------------------------------------
    if schema.get("require_code_lang_tags", False):
        # Iterate matched pairs: even-indexed = opening, odd-indexed = closing
        matches = list(_CODE_BLOCK_OPEN_RE.finditer(output))
        for idx, m in enumerate(matches):
            if idx % 2 == 0 and not m.group(1):
                result.warnings.append("Code block missing language tag")
                deductions += 5

    # --- Checklist items ------------------------------------------------------
    if schema.get("require_checklist_items", False):
        if not _CHECKLIST_RE.search(output):
            result.errors.append("No checklist items found (expected - [ ] or - [x])")
            deductions += 25

    # --- Valid JSON -----------------------------------------------------------
    if schema.get("require_valid_json", False):
        try:
            json.loads(output)
        except (json.JSONDecodeError, ValueError) as exc:
            result.errors.append(f"Invalid JSON: {exc}")
            deductions += 40

    # --- Score ----------------------------------------------------------------
    result.score = max(0, max_points - deductions)
    result.valid = len(result.errors) == 0
    return result
