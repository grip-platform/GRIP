#!/usr/bin/env python3
"""Silent Exception Auditor — detect dangerous except-pass patterns.

Scans Python files for bare `except: pass` or `except Exception: pass`
patterns that silently swallow errors. Ranks by risk based on:
  - Data-critical code (imports, file I/O, API calls)
  - Hot paths (hooks, startup, per-tool-call code)
  - Known bug patterns (missing imports caught silently)

Motivation: Two bugs hiding for days due to silent exception swallowing:
  1. Missing timedelta import in auto_converge.py (pattern detection always 0)
  2. KONO diversity data format mismatch (diversity always 0.0)

Usage:
    python3 lib/silent_exception_auditor.py scan          # Full scan
    python3 lib/silent_exception_auditor.py hot-paths     # Only hot-path files
    python3 lib/silent_exception_auditor.py fix-report    # Generate fix recommendations
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Files in hot paths (run on every tool call or session start)
HOT_PATH_PATTERNS = {
    "hooks/context-gate-hook.py",
    "hooks/session-start.sh",
    "hooks/startup/",
    "lib/auto_converge.py",
    "lib/session_save.py",
    "lib/converge_dna.py",
    "lib/kono_substrate.py",
}

# Data-critical keywords that suggest the except block hides real errors
DATA_CRITICAL_PATTERNS = [
    r"import\s",          # Missing import = NameError silently caught
    r"\.read_text\(",     # File read failure silenced
    r"json\.loads?\(",    # JSON parse failure silenced
    r"\.connect\(",       # Connection failure silenced
    r"subprocess\.run",   # Subprocess failure silenced
    r"open\(",            # File open failure silenced
]

# Risk weights
RISK_HOT_PATH = 3
RISK_DATA_CRITICAL = 2
RISK_BROAD_CATCH = 1  # except Exception vs except specific


def _is_hot_path(filepath: str) -> bool:
    """Check if file is in a hot execution path."""
    rel = str(filepath).replace(str(CLAUDE_DIR) + "/", "")
    return any(rel.startswith(p) for p in HOT_PATH_PATTERNS)


def _scan_file(filepath: Path) -> List[Dict]:
    """Scan a single Python file for silent exception patterns."""
    findings = []
    try:
        content = filepath.read_text()
    except (OSError, UnicodeDecodeError):
        return []

    lines = content.split("\n")
    rel_path = str(filepath).replace(str(CLAUDE_DIR) + "/", "")

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Match except blocks followed by pass
        if re.match(r"except\s*(Exception|BaseException)?\s*(as\s+\w+)?\s*:", stripped):
            # Check if next non-empty line is `pass`
            next_lines = []
            for j in range(i + 1, min(i + 3, len(lines))):
                nl = lines[j].strip()
                if nl:
                    next_lines.append(nl)
                    break

            is_silent = next_lines and next_lines[0] == "pass"
            if not is_silent:
                continue

            # Determine risk level
            risk = 0
            risk_factors = []

            # Hot path check
            if _is_hot_path(str(filepath)):
                risk += RISK_HOT_PATH
                risk_factors.append("hot-path")

            # Check try block for data-critical operations
            # Walk backwards to find the try block
            try_content = ""
            for j in range(i - 1, max(i - 20, 0), -1):
                tl = lines[j].strip()
                if tl.startswith("try:"):
                    try_content = "\n".join(lines[j:i])
                    break

            for pattern in DATA_CRITICAL_PATTERNS:
                if re.search(pattern, try_content):
                    risk += RISK_DATA_CRITICAL
                    # Extracted from f-string (backslash in f-string {} is SyntaxError on Python < 3.12)
                    label = pattern.split('(')[0].strip('\\s')
                    risk_factors.append(f"data-critical({label})")
                    break

            # Broad catch (Exception vs specific)
            if "Exception" in stripped or "BaseException" in stripped:
                risk += RISK_BROAD_CATCH
                risk_factors.append("broad-catch")

            findings.append({
                "file": rel_path,
                "line": i + 1,
                "except_text": stripped,
                "risk_score": risk,
                "risk_factors": risk_factors,
                "try_preview": try_content[:200] if try_content else "",
            })

    return findings


def scan_all(directory: Optional[Path] = None) -> List[Dict]:
    """Scan all Python files for silent exception patterns."""
    scan_dir = directory or CLAUDE_DIR / "lib"
    findings = []

    for py_file in sorted(scan_dir.rglob("*.py")):
        findings.extend(_scan_file(py_file))

    # Also scan hooks
    hooks_dir = CLAUDE_DIR / "hooks"
    if hooks_dir.exists():
        for py_file in sorted(hooks_dir.rglob("*.py")):
            findings.extend(_scan_file(py_file))

    # Sort by risk (highest first)
    findings.sort(key=lambda f: -f["risk_score"])
    return findings


def scan_hot_paths() -> List[Dict]:
    """Scan only hot-path files."""
    all_findings = scan_all()
    return [f for f in all_findings if "hot-path" in f.get("risk_factors", [])]


def fix_report(findings: Optional[List[Dict]] = None) -> str:
    """Generate fix recommendations for high-risk findings."""
    if findings is None:
        findings = scan_all()

    lines = []
    lines.append("# Silent Exception Audit Report")
    lines.append(f"Total findings: {len(findings)}")
    lines.append("")

    high = [f for f in findings if f["risk_score"] >= 4]
    medium = [f for f in findings if 2 <= f["risk_score"] < 4]
    low = [f for f in findings if f["risk_score"] < 2]

    lines.append(f"## High Risk ({len(high)})")
    for f in high:
        lines.append(f"- **{f['file']}:{f['line']}** (risk={f['risk_score']}) — {', '.join(f['risk_factors'])}")
        if f["try_preview"]:
            preview = f["try_preview"].split("\n")[1] if "\n" in f["try_preview"] else f["try_preview"]
            lines.append(f"  Context: `{preview.strip()[:80]}`")
        lines.append(f"  Fix: Replace `pass` with `logger.debug(...)` or narrow the except clause")
    lines.append("")

    lines.append(f"## Medium Risk ({len(medium)})")
    for f in medium[:10]:
        lines.append(f"- {f['file']}:{f['line']} (risk={f['risk_score']}) — {', '.join(f['risk_factors'])}")
    lines.append("")

    lines.append(f"## Low Risk ({len(low)})")
    lines.append(f"- {len(low)} findings with risk < 2 (intentional safety catches)")

    return "\n".join(lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"

    if cmd == "scan":
        findings = scan_all()
        print(json.dumps(findings, indent=2))
        print(f"\n--- {len(findings)} silent exception patterns found ---")
        high = sum(1 for f in findings if f["risk_score"] >= 4)
        print(f"High risk: {high} | Medium: {sum(1 for f in findings if 2 <= f['risk_score'] < 4)} | Low: {sum(1 for f in findings if f['risk_score'] < 2)}")
    elif cmd == "hot-paths":
        findings = scan_hot_paths()
        print(json.dumps(findings, indent=2))
        print(f"\n--- {len(findings)} hot-path silent exceptions ---")
    elif cmd == "fix-report":
        report = fix_report()
        print(report)
    else:
        print(f"Usage: {sys.argv[0]} [scan|hot-paths|fix-report]")
        sys.exit(1)


if __name__ == "__main__":
    main()
