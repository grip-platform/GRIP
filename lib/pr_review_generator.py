#!/usr/bin/env python3
"""Interactive PR Review Generator.

Parses unified diffs and generates structured review annotations with
severity levels, suggestions, and strengths. Outputs to multiple formats
including grip_review_annotator and grip_checklist MCP app formats.

Usage:
    python3 lib/pr_review_generator.py                           # staged changes
    python3 lib/pr_review_generator.py --pr 174                  # PR #174
    python3 lib/pr_review_generator.py --format review_annotator # MCP review
    python3 lib/pr_review_generator.py --format checklist        # MCP checklist
    echo "$(git diff)" | python3 lib/pr_review_generator.py      # pipe diff
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ReviewAnnotation:
    file: str
    line: int
    type: str  # "issue", "strength", "question"
    severity: str  # "critical", "important", "minor", "nit"
    description: str
    suggestion: str = ""
    rule: str = ""


@dataclass
class FileReview:
    path: str
    annotations: list  # List[ReviewAnnotation]
    changes_summary: str
    risk_level: str  # "high", "medium", "low"


@dataclass
class PRReview:
    title: str
    files_reviewed: int
    total_annotations: int
    critical_count: int
    important_count: int
    minor_count: int
    nit_count: int
    strengths_count: int
    questions_count: int
    file_reviews: list  # List[FileReview]
    summary: str
    verdict: str  # "approve", "request_changes", "comment"
    generated_at: str


# ---------------------------------------------------------------------------
# Compiled regex patterns (grouped by concern, immutable after import)
# ---------------------------------------------------------------------------

class _DiffPatterns:
    """Compiled regex patterns for diff parsing."""
    __slots__ = (
        'FILE_HEADER', 'HUNK_HEADER', 'RENAME_FROM', 'RENAME_TO',
        'NEW_FILE', 'DELETED_FILE', 'BINARY',
    )

    def __init__(self):
        self.FILE_HEADER = re.compile(r"^diff --git a/(.*) b/(.*)$")
        self.HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
        self.RENAME_FROM = re.compile(r"^rename from (.*)$")
        self.RENAME_TO = re.compile(r"^rename to (.*)$")
        self.NEW_FILE = re.compile(r"^new file mode")
        self.DELETED_FILE = re.compile(r"^deleted file mode")
        self.BINARY = re.compile(r"^Binary files")


class _SecurityPatterns:
    """Compiled regex patterns for security detection."""
    __slots__ = (
        'EVAL', 'EXEC', 'HARDCODED_SECRET', 'SQL_INJECTION_FSTRING',
        'SQL_INJECTION_CONCAT', 'SHELL_INJECTION',
    )

    def __init__(self):
        self.EVAL = re.compile(r"\beval\s*\(")
        self.EXEC = re.compile(r"\bexec\s*\(")
        self.HARDCODED_SECRET = re.compile(
            r"""(?:password|secret|api_key|apikey|token|passwd)\s*=\s*['"][^'"]+['"]""",
            re.IGNORECASE,
        )
        self.SQL_INJECTION_FSTRING = re.compile(r"""f['"](SELECT|INSERT|UPDATE|DELETE)\b.*\{""", re.IGNORECASE)
        self.SQL_INJECTION_CONCAT = re.compile(r"""['"](?:SELECT|INSERT|UPDATE|DELETE)\b.*['"]\s*\+""", re.IGNORECASE)
        self.SHELL_INJECTION = re.compile(r"subprocess\.\w+\(.*shell\s*=\s*True", re.IGNORECASE)


class _ComplexityPatterns:
    """Compiled regex patterns for complexity detection."""
    __slots__ = ('DEF', 'BRANCH')

    def __init__(self):
        self.DEF = re.compile(r"^(\s*)def\s+\w+")
        self.BRANCH = re.compile(r"^\s*(if|elif|for|while)\b")


class _NamingPatterns:
    """Compiled regex patterns for naming convention detection."""
    __slots__ = ('SINGLE_LETTER_ASSIGN', 'MIXED_CASE', 'SCREAMING_CASE', 'CLASS_DEF')
    LOOP_VARS = frozenset({"i", "j", "k", "x", "y", "z", "_"})

    def __init__(self):
        self.SINGLE_LETTER_ASSIGN = re.compile(r"^\s*([a-zA-Z])\s*=\s*")
        self.MIXED_CASE = re.compile(r"\b([a-z]+[A-Z][a-zA-Z]*)\s*=")
        self.SCREAMING_CASE = re.compile(r"^\s{4,}([A-Z][A-Z_0-9]+)\s*=")
        self.CLASS_DEF = re.compile(r"^\s*class\s+")


class _ErrorPatterns:
    """Compiled regex patterns for error handling detection."""
    __slots__ = ('BARE_EXCEPT', 'BROAD_EXCEPT', 'FILE_OPS', 'NETWORK')

    def __init__(self):
        self.BARE_EXCEPT = re.compile(r"^\s*except\s*:")
        self.BROAD_EXCEPT = re.compile(r"^\s*except\s+Exception\s*:")
        self.FILE_OPS = re.compile(r"\b(open|read|write|Path\(.*\)\.(read|write))")
        self.NETWORK = re.compile(r"\b(requests\.|urllib\.|httpx\.|aiohttp\.)")


class _SmellPatterns:
    """Compiled regex patterns for code smell detection."""
    __slots__ = ('MAGIC_NUMBER', 'SAFE_CONTEXTS', 'PRINT', 'COMMENTED_CODE')

    def __init__(self):
        self.MAGIC_NUMBER = re.compile(r"(?<![.\w])(\d{2,})(?![.\w\]\)])")
        self.SAFE_CONTEXTS = re.compile(r"(range|sleep|timeout|port|status|version|indent|width|height|size|max|min|limit|offset|count|index|padding|margin)")
        self.PRINT = re.compile(r"^\s*print\s*\(")
        self.COMMENTED_CODE = re.compile(r"^\s*#\s*(def |class |import |from |if |for |while |return |raise |\w+\s*=)")


class _StrengthPatterns:
    """Compiled regex patterns for strength detection."""
    __slots__ = ('TYPE_HINT_DEF', 'RETURN_HINT', 'DOCSTRING', 'TEST_FILE')

    def __init__(self):
        self.TYPE_HINT_DEF = re.compile(r"^\s*def\s+\w+\(.*:.*\)")
        self.RETURN_HINT = re.compile(r"^\s*def\s+\w+\(.*\)\s*->")
        self.DOCSTRING = re.compile(r'^\s*("""|\'\'\')')
        self.TEST_FILE = re.compile(r"test_\w+\.py|_test\.py")


class _QuestionPatterns:
    """Compiled regex patterns for question detection."""
    __slots__ = ('TODO', 'IMPORT')

    def __init__(self):
        self.TODO = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)
        self.IMPORT = re.compile(r"^\s*(?:import|from)\s+(\S+)")


# Single instances — backward-compatible module-level names
_DIFF_PAT = _DiffPatterns()
_SEC_PAT = _SecurityPatterns()
_CMPLX_PAT = _ComplexityPatterns()
_NAME_PAT = _NamingPatterns()
_ERR_PAT = _ErrorPatterns()
_SMELL_PAT = _SmellPatterns()
_STR_PAT = _StrengthPatterns()
_Q_PAT = _QuestionPatterns()

# Backward-compatible aliases (callers referencing these names still work)
_FILE_HEADER_RE = _DIFF_PAT.FILE_HEADER
_HUNK_HEADER_RE = _DIFF_PAT.HUNK_HEADER
_RENAME_FROM_RE = _DIFF_PAT.RENAME_FROM
_RENAME_TO_RE = _DIFF_PAT.RENAME_TO
_NEW_FILE_RE = _DIFF_PAT.NEW_FILE
_DELETED_FILE_RE = _DIFF_PAT.DELETED_FILE
_BINARY_RE = _DIFF_PAT.BINARY


def parse_unified_diff(diff_text: str) -> list:
    """Parse unified diff into file hunks with line numbers.

    Returns a list of dicts, each with keys:
        file_a, file_b, is_new, is_deleted, is_rename, is_binary, hunks
    Each hunk has keys:
        old_start, new_start, lines
    Each line is a dict: type ("+", "-", " "), content, old_line, new_line
    """
    if not diff_text or not diff_text.strip():
        return []

    files = []
    current_file = None
    current_hunk = None
    old_line = 0
    new_line = 0

    for raw_line in diff_text.splitlines():
        # New file header
        m = _FILE_HEADER_RE.match(raw_line)
        if m:
            if current_file is not None:
                if current_hunk is not None:
                    current_file["hunks"].append(current_hunk)
                    current_hunk = None
                files.append(current_file)
            current_file = {
                "file_a": m.group(1),
                "file_b": m.group(2),
                "is_new": False,
                "is_deleted": False,
                "is_rename": False,
                "is_binary": False,
                "hunks": [],
            }
            continue

        if current_file is None:
            continue

        if _NEW_FILE_RE.match(raw_line):
            current_file["is_new"] = True
            continue

        if _DELETED_FILE_RE.match(raw_line):
            current_file["is_deleted"] = True
            continue

        rm = _RENAME_FROM_RE.match(raw_line)
        if rm:
            current_file["is_rename"] = True
            current_file["file_a"] = rm.group(1)
            continue

        rt = _RENAME_TO_RE.match(raw_line)
        if rt:
            current_file["file_b"] = rt.group(1)
            continue

        if _BINARY_RE.match(raw_line):
            current_file["is_binary"] = True
            continue

        hm = _HUNK_HEADER_RE.match(raw_line)
        if hm:
            if current_hunk is not None:
                current_file["hunks"].append(current_hunk)
            old_line = int(hm.group(1))
            new_line = int(hm.group(2))
            current_hunk = {
                "old_start": old_line,
                "new_start": new_line,
                "lines": [],
            }
            continue

        if current_hunk is None:
            continue

        if raw_line.startswith("+"):
            current_hunk["lines"].append({
                "type": "+",
                "content": raw_line[1:],
                "old_line": None,
                "new_line": new_line,
            })
            new_line += 1
        elif raw_line.startswith("-"):
            current_hunk["lines"].append({
                "type": "-",
                "content": raw_line[1:],
                "old_line": old_line,
                "new_line": None,
            })
            old_line += 1
        elif raw_line.startswith(" "):
            current_hunk["lines"].append({
                "type": " ",
                "content": raw_line[1:],
                "old_line": old_line,
                "new_line": new_line,
            })
            old_line += 1
            new_line += 1

    if current_file is not None:
        if current_hunk is not None:
            current_file["hunks"].append(current_hunk)
        files.append(current_file)

    return files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _added_lines(hunks: list) -> list:
    """Extract added lines with file-relative new_line numbers."""
    result = []
    for hunk in hunks:
        for ln in hunk.get("lines", []):
            if ln["type"] == "+":
                result.append(ln)
    return result


def _all_lines(hunks: list) -> list:
    """All lines from all hunks."""
    result = []
    for hunk in hunks:
        result.extend(hunk.get("lines", []))
    return result


def _line_num(ln: dict) -> int:
    return ln.get("new_line") or ln.get("old_line") or 0


# ---------------------------------------------------------------------------
# Detection: Security
# ---------------------------------------------------------------------------

def detect_security_issues(hunks: list) -> list:
    annotations = []
    for ln in _added_lines(hunks):
        content = ln["content"]
        line_no = _line_num(ln)

        if _SEC_PAT.EVAL.search(content):
            annotations.append(ReviewAnnotation(
                file="", line=line_no, type="issue", severity="critical",
                description="Avoid eval/exec - code injection risk",
                suggestion="Use ast.literal_eval() or a safe parser instead",
                rule="security",
            ))

        if _SEC_PAT.EXEC.search(content):
            annotations.append(ReviewAnnotation(
                file="", line=line_no, type="issue", severity="critical",
                description="Avoid eval/exec - code injection risk",
                suggestion="Refactor to avoid dynamic code execution",
                rule="security",
            ))

        if _SEC_PAT.HARDCODED_SECRET.search(content):
            annotations.append(ReviewAnnotation(
                file="", line=line_no, type="issue", severity="critical",
                description="Hardcoded credential detected",
                suggestion="Use environment variables or a secrets manager",
                rule="security",
            ))

        if _SEC_PAT.SQL_INJECTION_FSTRING.search(content) or _SEC_PAT.SQL_INJECTION_CONCAT.search(content):
            annotations.append(ReviewAnnotation(
                file="", line=line_no, type="issue", severity="critical",
                description="SQL injection risk - use parameterized queries",
                suggestion="Use parameterized queries (cursor.execute(sql, params))",
                rule="security",
            ))

        if _SEC_PAT.SHELL_INJECTION.search(content):
            annotations.append(ReviewAnnotation(
                file="", line=line_no, type="issue", severity="critical",
                description="Shell injection risk",
                suggestion="Use subprocess with a list of args instead of shell=True",
                rule="security",
            ))

    return annotations


# ---------------------------------------------------------------------------
# Detection: Complexity
# ---------------------------------------------------------------------------

def detect_complexity_issues(hunks: list) -> list:
    annotations = []
    added = _added_lines(hunks)
    if not added:
        return annotations

    # Track functions in added lines
    func_start = None
    func_indent = None
    func_lines = 0
    func_line_no = 0
    branch_count = 0

    def _flush_func():
        nonlocal func_start, func_indent, func_lines, func_line_no, branch_count
        if func_start is not None:
            if func_lines > 50:
                annotations.append(ReviewAnnotation(
                    file="", line=func_line_no, type="issue", severity="important",
                    description=f"Function too long ({func_lines} lines), consider splitting",
                    suggestion="Extract helper functions for distinct responsibilities",
                    rule="complexity",
                ))
            if branch_count > 5:
                annotations.append(ReviewAnnotation(
                    file="", line=func_line_no, type="issue", severity="important",
                    description="High cyclomatic complexity",
                    suggestion="Simplify control flow or extract sub-functions",
                    rule="complexity",
                ))
        func_start = None
        func_indent = None
        func_lines = 0
        func_line_no = 0
        branch_count = 0

    for ln in added:
        content = ln["content"]
        dm = _CMPLX_PAT.DEF.match(content)
        if dm:
            _flush_func()
            func_start = True
            func_indent = len(dm.group(1))
            func_line_no = _line_num(ln)
            func_lines = 1
            branch_count = 0
            continue

        if func_start is not None:
            # Check if still inside the function
            stripped = content.rstrip()
            if stripped == "":
                func_lines += 1
                continue
            cur_indent = len(content) - len(content.lstrip())
            if cur_indent <= func_indent and stripped and not stripped.startswith("#"):
                # Left the function
                _flush_func()
            else:
                func_lines += 1
                if _CMPLX_PAT.BRANCH.match(content):
                    branch_count += 1

    _flush_func()

    # Deep nesting detection
    for ln in added:
        content = ln["content"]
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip())
        # 4 levels deep = 16 spaces (or 4 tabs)
        if indent >= 16 and content.strip() and not content.strip().startswith("#"):
            annotations.append(ReviewAnnotation(
                file="", line=_line_num(ln), type="issue", severity="important",
                description="Deep nesting detected",
                suggestion="Extract inner logic into helper functions or use early returns",
                rule="complexity",
            ))

    return annotations


# ---------------------------------------------------------------------------
# Detection: Naming
# ---------------------------------------------------------------------------

def detect_naming_issues(hunks: list) -> list:
    annotations = []
    added = _added_lines(hunks)
    in_loop = False

    for ln in added:
        content = ln["content"]
        line_no = _line_num(ln)

        # Track loop context
        if re.match(r"^\s*(for|while)\b", content):
            in_loop = True
        elif content.strip() and not content.startswith(" ") and not content.startswith("\t"):
            in_loop = False

        # Single letter vars
        m = _NAME_PAT.SINGLE_LETTER_ASSIGN.match(content)
        if m:
            var = m.group(1)
            if var not in _NAME_PAT.LOOP_VARS or not in_loop:
                if var not in _NAME_PAT.LOOP_VARS:
                    annotations.append(ReviewAnnotation(
                        file="", line=line_no, type="issue", severity="minor",
                        description=f"Unclear variable name '{var}'",
                        suggestion="Use a descriptive name that conveys intent",
                        rule="naming",
                    ))

        # mixedCase in Python (skip class defs)
        if not _NAME_PAT.CLASS_DEF.match(content):
            mc = _NAME_PAT.MIXED_CASE.search(content)
            if mc:
                annotations.append(ReviewAnnotation(
                    file="", line=line_no, type="issue", severity="minor",
                    description="Use snake_case per PEP 8",
                    suggestion=f"Rename '{mc.group(1)}' to snake_case",
                    rule="naming",
                ))

        # SCREAMING_CASE not at module level
        sc = _NAME_PAT.SCREAMING_CASE.match(content)
        if sc:
            annotations.append(ReviewAnnotation(
                file="", line=line_no, type="issue", severity="minor",
                description="SCREAMING_CASE should be module-level constants",
                suggestion=f"Move '{sc.group(1)}' to module level or use lowercase",
                rule="naming",
            ))

    return annotations


# ---------------------------------------------------------------------------
# Detection: Error handling
# ---------------------------------------------------------------------------

def detect_error_handling(hunks: list) -> list:
    annotations = []
    added = _added_lines(hunks)

    in_try = False
    for ln in added:
        content = ln["content"]
        line_no = _line_num(ln)

        if re.match(r"^\s*try\s*:", content):
            in_try = True

        if _ERR_PAT.BARE_EXCEPT.match(content):
            annotations.append(ReviewAnnotation(
                file="", line=line_no, type="issue", severity="important",
                description="Bare except catches too broadly",
                suggestion="Catch specific exceptions (e.g., ValueError, IOError)",
                rule="error_handling",
            ))
            in_try = False

        if _ERR_PAT.BROAD_EXCEPT.match(content):
            annotations.append(ReviewAnnotation(
                file="", line=line_no, type="issue", severity="important",
                description="Bare except catches too broadly",
                suggestion="Catch specific exceptions instead of Exception",
                rule="error_handling",
            ))
            in_try = False

        # Missing error handling for I/O
        if not in_try:
            if _ERR_PAT.FILE_OPS.search(content) or _ERR_PAT.NETWORK.search(content):
                annotations.append(ReviewAnnotation(
                    file="", line=line_no, type="issue", severity="important",
                    description="Missing error handling for I/O",
                    suggestion="Wrap I/O operations in try/except",
                    rule="error_handling",
                ))

    return annotations


# ---------------------------------------------------------------------------
# Detection: Code smells
# ---------------------------------------------------------------------------

def detect_code_smells(hunks: list) -> list:
    annotations = []
    added = _added_lines(hunks)

    # Magic numbers
    for ln in added:
        content = ln["content"]
        line_no = _line_num(ln)

        # Skip obvious non-magic contexts
        if _SMELL_PAT.SAFE_CONTEXTS.search(content):
            continue
        if re.match(r"^\s*#", content):
            continue

        for m in _SMELL_PAT.MAGIC_NUMBER.finditer(content):
            num = int(m.group(1))
            if num > 1 and num not in (10, 100, 1000):  # common bases
                annotations.append(ReviewAnnotation(
                    file="", line=line_no, type="issue", severity="nit",
                    description="Consider naming this constant",
                    suggestion=f"Extract {num} into a named constant",
                    rule="code_smell",
                ))
                break  # one per line

    # Commented-out code
    consecutive_code_comments = 0
    comment_start_line = 0
    for ln in added:
        content = ln["content"]
        if _SMELL_PAT.COMMENTED_CODE.match(content):
            if consecutive_code_comments == 0:
                comment_start_line = _line_num(ln)
            consecutive_code_comments += 1
        else:
            if consecutive_code_comments >= 3:
                annotations.append(ReviewAnnotation(
                    file="", line=comment_start_line, type="issue", severity="nit",
                    description="Remove dead code",
                    suggestion="Delete commented-out code; use version control to recover if needed",
                    rule="code_smell",
                ))
            consecutive_code_comments = 0

    if consecutive_code_comments >= 3:
        annotations.append(ReviewAnnotation(
            file="", line=comment_start_line, type="issue", severity="nit",
            description="Remove dead code",
            suggestion="Delete commented-out code; use version control to recover if needed",
            rule="code_smell",
        ))

    # print() in non-CLI code
    for ln in added:
        content = ln["content"]
        line_no = _line_num(ln)
        if _SMELL_PAT.PRINT.match(content):
            annotations.append(ReviewAnnotation(
                file="", line=line_no, type="issue", severity="nit",
                description="Use logging instead of print()",
                suggestion="Replace print() with logging.info() or logging.debug()",
                rule="code_smell",
            ))

    return annotations


# ---------------------------------------------------------------------------
# Detection: Strengths
# ---------------------------------------------------------------------------

def detect_strengths(hunks: list, file_path: str = "") -> list:
    annotations = []
    added = _added_lines(hunks)

    found_type_hint = False
    found_docstring = False
    found_error_handling = False
    prev_was_def = False

    for ln in added:
        content = ln["content"]
        line_no = _line_num(ln)

        if _STR_PAT.TYPE_HINT_DEF.match(content) or _STR_PAT.RETURN_HINT.match(content):
            if not found_type_hint:
                annotations.append(ReviewAnnotation(
                    file="", line=line_no, type="strength", severity="minor",
                    description="Good: Type hints improve readability",
                    rule="strengths",
                ))
                found_type_hint = True

        if prev_was_def and _STR_PAT.DOCSTRING.match(content):
            if not found_docstring:
                annotations.append(ReviewAnnotation(
                    file="", line=line_no, type="strength", severity="minor",
                    description="Good: Well-documented function",
                    rule="strengths",
                ))
                found_docstring = True

        prev_was_def = bool(re.match(r"^\s*def\s+", content))

        if re.match(r"^\s*try\s*:", content):
            if not found_error_handling:
                annotations.append(ReviewAnnotation(
                    file="", line=line_no, type="strength", severity="minor",
                    description="Good: Proper error handling",
                    rule="strengths",
                ))
                found_error_handling = True

    # Test file detection
    if file_path and _STR_PAT.TEST_FILE.search(file_path):
        annotations.append(ReviewAnnotation(
            file="", line=1, type="strength", severity="minor",
            description="Good: Tests included with changes",
            rule="strengths",
        ))

    return annotations


# ---------------------------------------------------------------------------
# Detection: Questions
# ---------------------------------------------------------------------------

def detect_questions(hunks: list) -> list:
    annotations = []
    added = _added_lines(hunks)

    # Large deletions
    all_lines = _all_lines(hunks)
    deleted_count = sum(1 for ln in all_lines if ln["type"] == "-")
    if deleted_count > 50:
        first_deleted = next((ln for ln in all_lines if ln["type"] == "-"), None)
        if first_deleted:
            annotations.append(ReviewAnnotation(
                file="", line=_line_num(first_deleted), type="question",
                severity="minor",
                description="Was this intentionally removed?",
                suggestion=f"{deleted_count} lines deleted - verify this is intentional",
                rule="questions",
            ))

    for ln in added:
        content = ln["content"]
        line_no = _line_num(ln)

        # TODOs
        tm = _Q_PAT.TODO.search(content)
        if tm:
            annotations.append(ReviewAnnotation(
                file="", line=line_no, type="question", severity="minor",
                description="Is this TODO tracked in an issue?",
                suggestion="Create a tracking issue or resolve the TODO",
                rule="questions",
            ))

        # New imports
        im = _Q_PAT.IMPORT.match(content)
        if im:
            mod = im.group(1).split(".")[0]
            # Skip stdlib
            if mod not in {
                "os", "sys", "re", "json", "typing", "dataclasses",
                "pathlib", "collections", "functools", "itertools",
                "datetime", "time", "math", "hashlib", "logging",
                "unittest", "pytest", "subprocess", "shutil", "copy",
                "io", "abc", "enum", "contextlib", "textwrap",
                "argparse", "tempfile", "glob", "string", "operator",
                "inspect", "traceback", "warnings", "signal",
                "threading", "multiprocessing", "asyncio", "socket",
                "http", "urllib", "csv", "xml", "html", "email",
                "base64", "secrets", "uuid", "random", "struct",
                "ctypes", "platform", "pprint",
            }:
                annotations.append(ReviewAnnotation(
                    file="", line=line_no, type="question", severity="minor",
                    description=f"Is {mod} a new dependency?",
                    suggestion="Verify this dependency is in requirements.txt/pyproject.toml",
                    rule="questions",
                ))

    return annotations


# ---------------------------------------------------------------------------
# Risk & verdict
# ---------------------------------------------------------------------------

def calculate_risk(annotations: list) -> str:
    critical = sum(1 for a in annotations if a.severity == "critical")
    important = sum(1 for a in annotations if a.severity == "important")
    if critical > 0:
        return "high"
    if important >= 3:
        return "high"
    if important > 0:
        return "medium"
    return "low"


def determine_verdict(annotations: list) -> str:
    issues = [a for a in annotations if a.type == "issue"]
    critical = sum(1 for a in issues if a.severity == "critical")
    important = sum(1 for a in issues if a.severity == "important")
    if critical > 0:
        return "request_changes"
    if important >= 3:
        return "request_changes"
    if important > 0 or any(a.severity in ("minor", "nit") for a in issues):
        return "comment"
    return "approve"


# ---------------------------------------------------------------------------
# File review
# ---------------------------------------------------------------------------

def review_file(file_path: str, hunks: list) -> FileReview:
    all_annotations = []

    detectors = [
        detect_security_issues,
        detect_complexity_issues,
        detect_naming_issues,
        detect_error_handling,
        detect_code_smells,
    ]

    for detector in detectors:
        for ann in detector(hunks):
            ann.file = file_path
            all_annotations.append(ann)

    for ann in detect_strengths(hunks, file_path):
        ann.file = file_path
        all_annotations.append(ann)

    for ann in detect_questions(hunks):
        ann.file = file_path
        all_annotations.append(ann)

    added = sum(1 for h in hunks for ln in h.get("lines", []) if ln["type"] == "+")
    removed = sum(1 for h in hunks for ln in h.get("lines", []) if ln["type"] == "-")
    summary = f"+{added}/-{removed} lines"

    return FileReview(
        path=file_path,
        annotations=all_annotations,
        changes_summary=summary,
        risk_level=calculate_risk(all_annotations),
    )


# ---------------------------------------------------------------------------
# Full review
# ---------------------------------------------------------------------------

def analyze_review(diff_text: str, title: str = "PR Review") -> PRReview:
    parsed = parse_unified_diff(diff_text)

    file_reviews = []
    all_annotations = []

    for file_data in parsed:
        path = file_data["file_b"]
        if file_data["is_binary"]:
            fr = FileReview(
                path=path,
                annotations=[],
                changes_summary="binary file",
                risk_level="low",
            )
            file_reviews.append(fr)
            continue

        fr = review_file(path, file_data["hunks"])
        file_reviews.append(fr)
        all_annotations.extend(fr.annotations)

    issues = [a for a in all_annotations if a.type == "issue"]
    strengths = [a for a in all_annotations if a.type == "strength"]
    questions = [a for a in all_annotations if a.type == "question"]

    critical = sum(1 for a in issues if a.severity == "critical")
    important = sum(1 for a in issues if a.severity == "important")
    minor = sum(1 for a in issues if a.severity == "minor")
    nit = sum(1 for a in issues if a.severity == "nit")

    verdict = determine_verdict(all_annotations)
    summary_parts = []
    if critical:
        summary_parts.append(f"{critical} critical issue(s)")
    if important:
        summary_parts.append(f"{important} important issue(s)")
    if len(strengths) > 0:
        summary_parts.append(f"{len(strengths)} strength(s) noted")
    if not summary_parts:
        summary_parts.append("No significant issues found")

    return PRReview(
        title=title,
        files_reviewed=len(file_reviews),
        total_annotations=len(all_annotations),
        critical_count=critical,
        important_count=important,
        minor_count=minor,
        nit_count=nit,
        strengths_count=len(strengths),
        questions_count=len(questions),
        file_reviews=file_reviews,
        summary="; ".join(summary_parts),
        verdict=verdict,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_text(review: PRReview) -> str:
    lines = []
    lines.append(f"PR Review: {review.title}")
    lines.append("=" * 60)
    lines.append(f"Verdict: {review.verdict.upper()}")
    lines.append(f"Files: {review.files_reviewed} | Annotations: {review.total_annotations}")
    lines.append(f"Critical: {review.critical_count} | Important: {review.important_count} | Minor: {review.minor_count} | Nit: {review.nit_count}")
    lines.append(f"Strengths: {review.strengths_count} | Questions: {review.questions_count}")
    lines.append(f"Summary: {review.summary}")
    lines.append("")

    for fr in review.file_reviews:
        lines.append(f"--- {fr.path} ({fr.changes_summary}) [risk: {fr.risk_level}] ---")
        for ann in fr.annotations:
            prefix = {"issue": "!!", "strength": "++", "question": "??"}[ann.type]
            lines.append(f"  {prefix} L{ann.line} [{ann.severity}] {ann.description}")
            if ann.suggestion:
                lines.append(f"     -> {ann.suggestion}")
        if not fr.annotations:
            lines.append("  (no annotations)")
        lines.append("")

    return "\n".join(lines)


def format_slack(review: PRReview) -> str:
    parts = []
    verdict_emoji = {"approve": "white_check_mark", "request_changes": "x", "comment": "speech_balloon"}
    emoji = verdict_emoji.get(review.verdict, "memo")

    parts.append(f":{emoji}: *PR Review: {review.title}*")
    parts.append(f"Verdict: *{review.verdict.upper()}*")
    parts.append(f"Files: {review.files_reviewed} | Critical: {review.critical_count} | Important: {review.important_count}")
    parts.append(f"_{review.summary}_")
    parts.append("")

    for fr in review.file_reviews:
        if not fr.annotations:
            continue
        parts.append(f"*{fr.path}* ({fr.changes_summary})")
        for ann in fr.annotations:
            if ann.type == "strength":
                parts.append(f"  :star: {ann.description}")
            elif ann.type == "question":
                parts.append(f"  :question: L{ann.line}: {ann.description}")
            elif ann.severity == "critical":
                parts.append(f"  :rotating_light: L{ann.line}: {ann.description}")
            elif ann.severity == "important":
                parts.append(f"  :warning: L{ann.line}: {ann.description}")
            else:
                parts.append(f"  :pencil2: L{ann.line}: {ann.description}")
        parts.append("")

    return "\n".join(parts)


def format_json(review: PRReview) -> str:
    data = asdict(review)
    return json.dumps(data, indent=2)


def format_review_annotator(review: PRReview) -> dict:
    all_annotations = []
    for fr in review.file_reviews:
        all_annotations.extend(fr.annotations)

    return {
        "title": f"Review: {review.title}",
        "artifact": {
            "type": "code",
            "content": "",  # caller can inject diff_text[:5000]
            "language": "diff",
        },
        "annotations": [
            {
                "id": f"ann-{i}",
                "type": ann.type,
                "severity": ann.severity,
                "description": ann.description,
                "location": f"{ann.file}:{ann.line}",
                "suggestion": ann.suggestion,
            }
            for i, ann in enumerate(all_annotations)
        ],
    }


def format_checklist(review: PRReview) -> dict:
    all_annotations = []
    for fr in review.file_reviews:
        all_annotations.extend(fr.annotations)

    critical_items = [
        {"id": f"crit-{i}", "label": a.description, "description": f"{a.file}:{a.line}"}
        for i, a in enumerate(a for a in all_annotations if a.type == "issue" and a.severity == "critical")
    ]
    important_items = [
        {"id": f"imp-{i}", "label": a.description, "description": f"{a.file}:{a.line}"}
        for i, a in enumerate(a for a in all_annotations if a.type == "issue" and a.severity == "important")
    ]
    minor_nit_items = [
        {"id": f"min-{i}", "label": a.description, "description": f"{a.file}:{a.line}"}
        for i, a in enumerate(a for a in all_annotations if a.type == "issue" and a.severity in ("minor", "nit"))
    ]
    strength_items = [
        {"id": f"str-{i}", "label": a.description, "description": f"{a.file}:{a.line}", "checked": True}
        for i, a in enumerate(a for a in all_annotations if a.type == "strength")
    ]

    sections = []
    if critical_items:
        sections.append({"heading": "Critical Issues", "items": critical_items})
    if important_items:
        sections.append({"heading": "Important Issues", "items": important_items})
    if minor_nit_items:
        sections.append({"heading": "Minor/Nit", "items": minor_nit_items})
    if strength_items:
        sections.append({"heading": "Strengths", "items": strength_items})

    return {
        "title": f"Review Checklist: {review.title}",
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _get_diff(args) -> tuple:
    """Return (diff_text, title)."""
    import argparse

    parser = argparse.ArgumentParser(description="PR Review Generator")
    parser.add_argument("--pr", type=int, help="PR number to review")
    parser.add_argument("--format", choices=["text", "slack", "json", "review_annotator", "checklist"],
                        default="text", help="Output format")
    parser.add_argument("--diff", type=str, help="Diff text (if not piped)")
    parsed = parser.parse_args(args)

    diff_text = ""
    title = "PR Review"

    # Try stdin pipe
    if not sys.stdin.isatty():
        diff_text = sys.stdin.read()
        title = "Piped Diff Review"
    elif parsed.pr:
        try:
            result = subprocess.run(
                ["gh", "pr", "diff", str(parsed.pr)],
                capture_output=True, text=True, timeout=30,
            )
            diff_text = result.stdout
            # Get PR title
            title_result = subprocess.run(
                ["gh", "pr", "view", str(parsed.pr), "--json", "title", "-q", ".title"],
                capture_output=True, text=True, timeout=10,
            )
            if title_result.stdout.strip():
                title = title_result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print("Error: gh CLI not available or timed out", file=sys.stderr)
            sys.exit(1)
    elif parsed.diff:
        diff_text = parsed.diff
    else:
        # Default: staged changes
        try:
            result = subprocess.run(
                ["git", "diff", "--staged"],
                capture_output=True, text=True, timeout=10,
            )
            diff_text = result.stdout
            if not diff_text.strip():
                result = subprocess.run(
                    ["git", "diff"],
                    capture_output=True, text=True, timeout=10,
                )
                diff_text = result.stdout
            title = "Local Changes Review"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print("Error: git not available", file=sys.stderr)
            sys.exit(1)

    if not diff_text.strip():
        print("No diff content to review.", file=sys.stderr)
        sys.exit(0)

    return diff_text, title, parsed.format


def main():
    diff_text, title, fmt = _get_diff(sys.argv[1:])

    review = analyze_review(diff_text, title)

    if fmt == "text":
        print(format_text(review))
    elif fmt == "slack":
        print(format_slack(review))
    elif fmt == "json":
        print(format_json(review))
    elif fmt == "review_annotator":
        result = format_review_annotator(review)
        result["artifact"]["content"] = diff_text[:5000]
        print(json.dumps(result, indent=2))
    elif fmt == "checklist":
        print(json.dumps(format_checklist(review), indent=2))


if __name__ == "__main__":
    main()
