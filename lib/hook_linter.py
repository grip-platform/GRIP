#!/usr/bin/env python3
"""
Hook Linter - Static analysis for GRIP hook scripts.

Checks .sh and .py hook files for common errors and GRIP convention violations.
8 check categories with ERROR/WARN/INFO severity levels.

Usage:
    python3 lib/hook_linter.py                      # lint all hooks
    python3 lib/hook_linter.py --path hooks/startup  # specific directory
    python3 lib/hook_linter.py --json                # JSON output
    python3 lib/hook_linter.py --fix                 # auto-fix simple issues
"""

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


class Severity(Enum):
    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"


@dataclass
class Finding:
    file: str
    line: int
    severity: Severity
    check: str
    message: str

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "severity": self.severity.value,
            "check": self.check,
            "message": self.message,
        }


@dataclass
class FileResult:
    file: str
    findings: list[Finding] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "findings": [f.to_dict() for f in self.findings],
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


@dataclass
class LintSummary:
    total_files: int = 0
    files_with_findings: int = 0
    errors: int = 0
    warnings: int = 0
    infos: int = 0
    skipped: int = 0

    def to_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "files_with_findings": self.files_with_findings,
            "errors": self.errors,
            "warnings": self.warnings,
            "infos": self.infos,
            "skipped": self.skipped,
        }


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

def check_shebang_sh(lines: list[str], filepath: str) -> list[Finding]:
    """Check 1: Every .sh file must have a proper shebang."""
    findings = []
    if not lines:
        findings.append(Finding(
            file=filepath, line=1, severity=Severity.ERROR,
            check="shebang",
            message="File is empty, missing shebang line",
        ))
        return findings

    first = lines[0].rstrip("\n")
    valid_shebangs = ["#!/usr/bin/env bash", "#!/bin/bash"]
    if first not in valid_shebangs:
        findings.append(Finding(
            file=filepath, line=1, severity=Severity.ERROR,
            check="shebang",
            message=f"Invalid or missing shebang. Expected '#!/usr/bin/env bash' or '#!/bin/bash', got: '{first}'",
        ))
    return findings


def check_shebang_py(lines: list[str], filepath: str) -> list[Finding]:
    """Check 8a: Python hooks should have a proper shebang."""
    findings = []
    if not lines:
        findings.append(Finding(
            file=filepath, line=1, severity=Severity.ERROR,
            check="shebang",
            message="File is empty, missing shebang line",
        ))
        return findings

    first = lines[0].rstrip("\n")
    valid_shebangs = ["#!/usr/bin/env python3", "#!/usr/bin/python3"]
    if first not in valid_shebangs:
        findings.append(Finding(
            file=filepath, line=1, severity=Severity.WARN,
            check="shebang",
            message=f"Missing or non-standard Python shebang. Expected '#!/usr/bin/env python3', got: '{first}'",
        ))
    return findings


def check_executable(filepath: str) -> list[Finding]:
    """Check 2: Scripts should have executable permission."""
    findings = []
    try:
        st = os.stat(filepath)
        if not (st.st_mode & stat.S_IXUSR):
            findings.append(Finding(
                file=filepath, line=0, severity=Severity.WARN,
                check="executable",
                message="Script is not executable (missing +x permission)",
            ))
    except OSError:
        pass
    return findings


def check_timeout_usage(lines: list[str], filepath: str) -> list[Finding]:
    """Check 3: Flag any use of `timeout` command (broken on macOS)."""
    findings = []
    # Match: timeout as a command (start of line, after pipe, after &&/||, after $(), in subshell)
    # Avoid matching: gtimeout, comments, variable names containing "timeout"
    timeout_re = re.compile(
        r'(?:^|[;|&(]\s*)'       # start of line/command
        r'(?:command\s+)?'        # optional command prefix
        r'\btimeout\b'            # the word timeout
        r'(?!\s*=)'               # not an assignment like timeout=30
    )
    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        # Skip comments
        if stripped.startswith("#"):
            continue
        if timeout_re.search(line):
            # Exclude gtimeout
            if re.search(r'\bgtimeout\b', line):
                continue
            findings.append(Finding(
                file=filepath, line=i, severity=Severity.ERROR,
                check="timeout",
                message="Use of 'timeout' command (broken on macOS). Use Python subprocess with timeout or background+kill pattern.",
            ))
    return findings


def check_error_handling(lines: list[str], filepath: str) -> list[Finding]:
    """Check 5: Check for set -e or set -euo pipefail near top of script."""
    findings = []
    # Look in first 20 non-comment, non-blank lines
    set_e_re = re.compile(r'^\s*set\s+-[a-zA-Z]*e')
    found = False
    check_lines = 0
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        check_lines += 1
        if set_e_re.match(stripped):
            found = True
            break
        if check_lines >= 20:
            break

    if not found:
        findings.append(Finding(
            file=filepath, line=1, severity=Severity.WARN,
            check="error-handling",
            message="Missing 'set -e' or 'set -euo pipefail' near top of script",
        ))
    return findings


def check_undefined_variables(lines: list[str], filepath: str) -> list[Finding]:
    """Check 4: Basic check for variables used but never assigned/sourced."""
    findings = []

    # Collect assigned variables
    assigned = set()
    # Common shell builtins and environment variables
    builtins = {
        "HOME", "USER", "PATH", "PWD", "OLDPWD", "SHELL", "TERM", "EDITOR",
        "LANG", "LC_ALL", "IFS", "PPID", "BASH_SOURCE", "BASH_LINENO",
        "FUNCNAME", "LINENO", "RANDOM", "SECONDS", "SHLVL", "UID", "EUID",
        "HOSTNAME", "OSTYPE", "MACHTYPE", "HOSTTYPE", "BASH", "BASH_VERSION",
        "BASH_VERSINFO", "GROUPS", "DIRSTACK", "PIPESTATUS", "BASH_REMATCH",
        "REPLY", "OPTARG", "OPTIND", "OPTERR", "COLUMNS", "LINES",
        "TMPDIR", "DISPLAY", "SSH_AUTH_SOCK", "SSH_AGENT_PID",
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
        "CLAUDE_DIR", "GRIP_DIR", "GRIP_TMP_DIR", "GRIP_PYTHON",
        "LIB_DIR", "SCRIPT_DIR", "STARTUP_DIR", "PYTHONPATH",
        "GRIP_INSTANCE_ID", "GRIP_PAIR_AUTH", "GRIP_MODEL_LOCK",
        "CI", "GITHUB_ACTIONS", "GITHUB_TOKEN",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
        "#", "?", "@", "*", "!", "-", "$", "_",
    }
    assigned.update(builtins)

    # Assignment patterns
    assign_re = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)=')
    local_re = re.compile(r'\b(?:local|declare|export|readonly|typeset)\s+([A-Za-z_][A-Za-z0-9_]*)')
    for_re = re.compile(r'\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b')
    read_re = re.compile(r'\bread\s+(?:-[a-zA-Z]+\s+)*([A-Za-z_][A-Za-z0-9_]*)')
    source_re = re.compile(r'(?:^\s*source\s+|\.\s+)')

    has_source = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for m in assign_re.finditer(line):
            assigned.add(m.group(1))
        for m in local_re.finditer(line):
            assigned.add(m.group(1))
        for m in for_re.finditer(line):
            assigned.add(m.group(1))
        for m in read_re.finditer(line):
            assigned.add(m.group(1))
        if source_re.search(line):
            has_source = True

    # If the script sources other files, skip this check entirely
    # (too many false positives from sourced variables)
    if has_source:
        return findings

    # Check for used-but-not-assigned
    use_re = re.compile(r'\$\{?([A-Za-z_][A-Za-z0-9_]*)')
    reported = set()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for m in use_re.finditer(line):
            var = m.group(1)
            if var not in assigned and var not in reported:
                reported.add(var)
                findings.append(Finding(
                    file=filepath, line=i, severity=Severity.WARN,
                    check="undefined-var",
                    message=f"Variable '${var}' used but never assigned in this file",
                ))
    return findings


def check_shellcheck_patterns(lines: list[str], filepath: str) -> list[Finding]:
    """Check 6: Flag common shell issues."""
    findings = []

    # Unquoted variable in test (outside [[ ]] which handles it)
    # Match [ $var ... ] but not [[ $var ... ]]
    single_bracket_re = re.compile(r'(?<!\[)\[\s+\$[A-Za-z_]')
    # Missing -- before variable paths in common commands
    path_cmds = re.compile(
        r'\b(cat|head|tail|wc|sort|chmod|chown|rm|cp|mv|mkdir|touch|stat|file|readlink)\s+'
        r'"\$[A-Za-z_]'
    )
    # Missing -- pattern: command followed by unquoted $variable (potential flag interpretation)
    missing_dashdash_re = re.compile(
        r'\b(cat|head|tail|sort|chmod|chown|rm|cp|mv|mkdir|touch|stat|file|readlink)\s+'
        r'\$[A-Za-z_][A-Za-z0-9_]*\b'
    )

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        if single_bracket_re.search(line):
            findings.append(Finding(
                file=filepath, line=i, severity=Severity.WARN,
                check="shellcheck",
                message="Unquoted variable in single bracket test [ ]. Use [[ ]] or quote the variable.",
            ))

        if missing_dashdash_re.search(line):
            # Check if -- is already present before the variable
            if not re.search(r'\b(cat|head|tail|sort|chmod|chown|rm|cp|mv|mkdir|touch|stat|file|readlink)\s+--\s', line):
                findings.append(Finding(
                    file=filepath, line=i, severity=Severity.INFO,
                    check="shellcheck",
                    message="Consider using '--' before variable path to prevent flag interpretation.",
                ))

    return findings


# A bare `~/.claude` — the directory itself, NOT a `~/.claude/...` prefix.
#
# The dot is escaped, and that matters: an unescaped `.` is regex "any
# character", so the original `r'~/.claude(?!/)'` also matched `~/Xclaude`,
# `~/-claude`, `~/9claude` and `~/ claude`. Verified against Python's re:
# the unescaped form matches all four, the escaped form none of them, and
# both still match the real `~/.claude`.
#
# The `(?!/)` is deliberate and is what keeps this quiet. `~/.claude/hooks/x.py`
# is GRIP's normal, correct way to write a hook command and is not flagged;
# only the bare directory literal is, because nothing expands it when it is
# used as a value rather than as a path prefix.
TILDE_CLAUDE_RE = re.compile(r'~/\.claude(?!/)')

# JSON keys whose values really are paths or commands. Everything else in a
# settings file — descriptions, documentation strings, matchers, arbitrary
# metadata — is prose, and a tilde in prose is not a path bug.
PATH_VALUE_KEYS = frozenset({"command"})


def _config_finding(filepath: str, severity: Severity, message: str) -> Finding:
    """Build a config-scoped finding.

    Line is always 0: a parsed document has no lines, and re-searching the
    raw text to invent one would reintroduce exactly the text-matching this
    check exists to replace. The dotted key path in the message locates the
    value more precisely than a line number would anyway.
    """
    return Finding(file=filepath, line=0, severity=severity,
                   check="grip-convention", message=message)


def check_grip_conventions(lines: list[str], filepath: str) -> list[Finding]:
    """Check 7: GRIP path portability conventions (line-oriented)."""
    findings = []

    # Check for hardcoded user paths
    hardcoded_re = re.compile(r'(?:/Users/[^/]+/|/home/[^/]+/|C:\\\\Users\\\\)')

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        if hardcoded_re.search(line):
            findings.append(Finding(
                file=filepath, line=i, severity=Severity.ERROR,
                check="grip-convention",
                message="Hardcoded user path detected. Use $HOME or $CLAUDE_DIR for portability.",
            ))

    return findings


def iter_config_path_values(node, trail: str = ""):
    """Walk a parsed config and yield (dotted_key, value) for path values only.

    Yields a string value only when its own key is in PATH_VALUE_KEYS, so a
    tilde in a description, a comment-style `_doc` field or any unrelated
    metadata is never seen by the caller. That scoping is the whole point:
    it separates "this path will not resolve" from "someone wrote a tilde in
    a sentence".
    """
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{trail}.{key}" if trail else str(key)
            if isinstance(value, str):
                if key in PATH_VALUE_KEYS:
                    yield child, value
            else:
                yield from iter_config_path_values(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_config_path_values(value, f"{trail}[{index}]")


def check_config_path_values(filepath: str) -> list[Finding]:
    """Check 7b: path portability of JSON CONFIG VALUES, not raw file text.

    Why this is not a text scan. A tilde can appear anywhere in a settings
    file — inside a description, inside a documentation field explaining
    what a glob does — and none of those are paths. GRIP's own
    settings.json is the proof: it contains exactly one `~/` in the whole
    file, and it sits in a `_claudeMdExcludes_doc` prose field. A grep
    reports that as a broken path; parsing the JSON and looking only at
    command values reports, correctly, nothing. Confirmed by A>> on
    Windows, where the raw-text check surfaced it through `grip doctor`.
    """
    findings = []

    try:
        config = json.loads(Path(filepath).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return [_config_finding(filepath, Severity.WARN,
                                f"Cannot read config file: {exc}")]
    except json.JSONDecodeError as exc:
        return [_config_finding(filepath, Severity.ERROR,
                                f"Malformed JSON, cannot check paths: {exc}")]

    for key, value in iter_config_path_values(config):
        if TILDE_CLAUDE_RE.search(value):
            findings.append(_config_finding(
                filepath, Severity.WARN,
                f"Config value {key} uses a bare '~/.claude' path. Nothing "
                f"expands the tilde when the value is used directly; use "
                f"$HOME/.claude or $CLAUDE_DIR."))

    return findings


def check_python_main_guard(lines: list[str], filepath: str) -> list[Finding]:
    """Check 8b: Python hooks should have if __name__ == '__main__' guard."""
    findings = []

    has_guard = False
    has_executable_code = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if '__name__' in line and '__main__' in line:
            has_guard = True
            break

    # Check if there's top-level executable code (not just imports/defs/classes)
    in_docstring = False
    in_block = False  # inside a def/class/for/if block
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Track multi-line docstrings
        if '"""' in stripped or "'''" in stripped:
            count = stripped.count('"""') + stripped.count("'''")
            if count % 2 == 1:
                in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue

        # Check indentation: only top-level (0 indent) lines matter
        # Indented lines are inside def/class/for/if bodies
        indent = len(line) - len(line.lstrip())
        if indent > 0:
            continue

        # Skip imports, class/function definitions, decorators, type hints
        if re.match(r'^(import |from |def |class |@|#|if __name__|""")', stripped):
            continue
        # Skip assignments at module level (constants, dataclasses, etc.)
        if re.match(r'^[A-Z_][A-Z0-9_]*\s*=', stripped):
            continue
        # Found executable code
        has_executable_code = True
        break

    if has_executable_code and not has_guard:
        findings.append(Finding(
            file=filepath, line=1, severity=Severity.WARN,
            check="python-main-guard",
            message="Python hook has top-level executable code without 'if __name__ == \"__main__\"' guard",
        ))

    return findings


# ---------------------------------------------------------------------------
# Auto-fix implementations
# ---------------------------------------------------------------------------

def fix_shebang_sh(filepath: str, lines: list[str]) -> bool:
    """Add bash shebang if missing."""
    if not lines or lines[0].rstrip("\n") not in ["#!/usr/bin/env bash", "#!/bin/bash"]:
        new_lines = ["#!/usr/bin/env bash\n"] + lines
        with open(filepath, "w") as f:
            f.writelines(new_lines)
        return True
    return False


def fix_shebang_py(filepath: str, lines: list[str]) -> bool:
    """Add python3 shebang if missing."""
    if not lines or lines[0].rstrip("\n") not in ["#!/usr/bin/env python3", "#!/usr/bin/python3"]:
        new_lines = ["#!/usr/bin/env python3\n"] + lines
        with open(filepath, "w") as f:
            f.writelines(new_lines)
        return True
    return False


def fix_executable(filepath: str) -> bool:
    """Add +x permission."""
    try:
        st = os.stat(filepath)
        if not (st.st_mode & stat.S_IXUSR):
            os.chmod(filepath, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            return True
    except OSError:
        pass
    return False


# ---------------------------------------------------------------------------
# Linter engine
# ---------------------------------------------------------------------------

# Settings files the config check applies to, matched by exact name.
#
# Deliberately a two-name allowlist, not the `.json` extension. Adding the
# extension to is_hook_file would drag every JSON in the tree — package.json,
# the audit dumps, test fixtures — into the sweep: a large blast radius for a
# narrow check about hook command paths.
CONFIG_FILE_NAMES = frozenset({"settings.json", "settings.local.json"})


def is_hook_file(path: Path) -> bool:
    """Determine if a file is a hook script we should lint."""
    if path.suffix in (".sh", ".py"):
        return True
    return False


def is_config_file(path: Path) -> bool:
    """Determine if a file is a GRIP settings file we should check."""
    return path.name in CONFIG_FILE_NAMES


def _skip_reason(path: Path) -> Optional[str]:
    """Why this file must not be linted, or None to carry on.

    Config files are exempt from the hook-file extension test: they are
    checked by parsing rather than by reading lines, so `.json` is a
    legitimate suffix here even though it is not a hook script.
    """
    if not path.exists():
        return "File does not exist"
    if not path.is_file():
        return "Not a regular file"
    if is_config_file(path):
        return None
    if not is_hook_file(path):
        return f"Not a hook file (extension: {path.suffix})"
    # Skip __pycache__ and similar
    if "__pycache__" in str(path) or path.name.startswith("."):
        return "Excluded directory or hidden file"
    return None


def _lint_shell(lines: list[str], filepath: str, fix: bool) -> list[Finding]:
    findings = (
        check_shebang_sh(lines, filepath)
        + check_executable(filepath)
        + check_timeout_usage(lines, filepath)
        + check_error_handling(lines, filepath)
        + check_undefined_variables(lines, filepath)
        + check_shellcheck_patterns(lines, filepath)
        + check_grip_conventions(lines, filepath)
    )
    if fix:
        fix_shebang_sh(filepath, lines)
        fix_executable(filepath)
    return findings


def _lint_python(lines: list[str], filepath: str, fix: bool) -> list[Finding]:
    findings = (
        check_shebang_py(lines, filepath)
        + check_executable(filepath)
        + check_python_main_guard(lines, filepath)
        + check_grip_conventions(lines, filepath)
    )
    if fix:
        fix_shebang_py(filepath, lines)
        fix_executable(filepath)
    return findings


def lint_file(filepath: str, fix: bool = False) -> FileResult:
    """Run all checks on a single file."""
    result = FileResult(file=filepath)
    path = Path(filepath)

    reason = _skip_reason(path)
    if reason is not None:
        result.skipped = True
        result.skip_reason = reason
        return result

    # Config files are parsed, not read line by line, so they return here
    # before the readlines() below and never meet the text-oriented checks.
    if is_config_file(path):
        result.findings.extend(check_config_path_values(filepath))
        return result

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError) as e:
        result.skipped = True
        result.skip_reason = f"Cannot read file: {e}"
        return result

    if path.suffix == ".sh":
        result.findings.extend(_lint_shell(lines, filepath, fix))
    elif path.suffix == ".py":
        result.findings.extend(_lint_python(lines, filepath, fix))

    return result


def discover_hooks(root: str) -> list[str]:
    """Find all hook files under the given root directory."""
    hooks = []
    root_path = Path(root)

    if root_path.is_file():
        return [str(root_path)]

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Skip excluded directories
        dirnames[:] = [
            d for d in dirnames
            if d not in ("__pycache__", ".git", "node_modules", "venv", ".venv")
        ]
        for fname in sorted(filenames):
            fpath = os.path.join(dirpath, fname)
            # Settings files come along by exact name so a sweep reaches the
            # config check; every other .json stays out (CONFIG_FILE_NAMES).
            if is_hook_file(Path(fpath)) or is_config_file(Path(fpath)):
                hooks.append(fpath)

    return sorted(hooks)


def lint_hooks(
    root: str,
    fix: bool = False,
    output_json: bool = False,
) -> tuple[list[FileResult], LintSummary]:
    """Lint all hooks under root directory."""
    files = discover_hooks(root)
    results = []
    summary = LintSummary()

    for filepath in files:
        result = lint_file(filepath, fix=fix)
        results.append(result)
        summary.total_files += 1

        if result.skipped:
            summary.skipped += 1
            continue

        if result.findings:
            summary.files_with_findings += 1

        for finding in result.findings:
            if finding.severity == Severity.ERROR:
                summary.errors += 1
            elif finding.severity == Severity.WARN:
                summary.warnings += 1
            elif finding.severity == Severity.INFO:
                summary.infos += 1

    return results, summary


def format_text(results: list[FileResult], summary: LintSummary) -> str:
    """Format results as human-readable text."""
    output = []

    for result in results:
        if result.skipped:
            continue
        if not result.findings:
            continue

        output.append(f"\n{result.file}")
        for f in result.findings:
            line_info = f":{f.line}" if f.line > 0 else ""
            output.append(f"  {f.severity.value:5s} {f.check} (line {f.line}): {f.message}")

    output.append("")
    output.append(f"Summary: {summary.total_files} files scanned, "
                  f"{summary.files_with_findings} with findings")
    output.append(f"  {summary.errors} errors, {summary.warnings} warnings, "
                  f"{summary.infos} infos, {summary.skipped} skipped")

    return "\n".join(output)


def format_json(results: list[FileResult], summary: LintSummary) -> str:
    """Format results as JSON."""
    data = {
        "results": [r.to_dict() for r in results if not r.skipped],
        "summary": summary.to_dict(),
    }
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hook Linter - Static analysis for GRIP hook scripts",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Path to lint (file or directory). Defaults to hooks/ directory.",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix simple issues (add shebang, set +x)",
    )

    args = parser.parse_args()

    # Determine root path
    if args.path:
        root = args.path
    else:
        # Default: look for hooks/ relative to CLAUDE_DIR or script location
        claude_dir = os.environ.get("CLAUDE_DIR", os.path.join(os.environ["HOME"], ".claude"))
        root = os.path.join(claude_dir, "hooks")

    if not os.path.exists(root):
        print(f"Error: path does not exist: {root}", file=sys.stderr)
        return 1

    results, summary = lint_hooks(root, fix=args.fix, output_json=args.output_json)

    if args.output_json:
        print(format_json(results, summary))
    else:
        print(format_text(results, summary))

    # Exit code: 1 if errors found
    return 1 if summary.errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
