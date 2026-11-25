#!/usr/bin/env python3
"""
Gate S: Secure-by-Default Code Gate
====================================

PreToolUse gate that analyzes code being written via Write/Edit
for common security omissions.

HIGH severity patterns (sql_injection, hardcoded_secrets, unsafe_deserialize,
no_input_validation) BLOCK execution mechanically.
MEDIUM severity patterns (exposed_debug, no_auth) emit warnings.

Mechanical enforcement for HIGH severity aligns with GRIP's core thesis:
safety through code, not values that suggest.

Patterns checked (regex-based, configurable):
  - no_auth: HTTP handlers without auth decorators (MEDIUM - warn)
  - sql_injection: String interpolation in SQL queries (HIGH - block)
  - hardcoded_secrets: Inline credentials in code (HIGH - block)
  - unsafe_deserialize: pickle.loads(), yaml.load() without SafeLoader (HIGH - block)
  - no_input_validation: User input passed directly to shell/eval (HIGH - block)
  - exposed_debug: Debug mode in production config (MEDIUM - warn)

Version: 2.0.0
"""

import re
from dataclasses import dataclass


@dataclass
class SecurityWarning:
    """A security warning detected in code."""
    pattern_id: str
    severity: str  # "HIGH", "MEDIUM", "LOW"
    message: str
    suggestion: str
    line_hint: str  # Snippet of matched content for context

# Security patterns: (id, severity, regex, message, suggestion)
SECURITY_PATTERNS = [
    # SQL Injection - string interpolation in queries
    (
        "sql_injection",
        "HIGH",
        re.compile(
            r'(?:execute|cursor\.execute|\.query|\.raw)\s*\(\s*f["\x27]'
            r'|(?:execute|cursor\.execute|\.query|\.raw)\s*\(\s*["\x27].*%s'
            r'|(?:execute|cursor\.execute|\.query|\.raw)\s*\(\s*["\x27].*\{'
            r'|f["\x27](?:SELECT|INSERT|UPDATE|DELETE|DROP)\s'
            r'|["\x27](?:SELECT|INSERT|UPDATE|DELETE|DROP)\s[^"\x27]*\{',
            re.IGNORECASE,
        ),
        "SQL injection risk - string interpolation in query",
        "Use parameterized queries: cursor.execute('query with ? placeholders', (val,))",
    ),
    # Hardcoded secrets
    (
        "hardcoded_secrets",
        "HIGH",
        re.compile(
            r'(?:password|passwd|pwd|secret|api_key|apikey|token|auth_token)\s*=\s*["\x27][^"\x27]{4,}["\x27]',
            re.IGNORECASE,
        ),
        "Hardcoded credential detected in source code",
        "Use environment variables or a dedicated secrets manager",
    ),
    # Unsafe deserialization
    (
        "unsafe_deserialize",
        "HIGH",
        re.compile(
            r'\bpickle\.loads?\s*\('
            r'|\byaml\.load\s*\([^)]*(?!Loader\s*=\s*yaml\.SafeLoader)'
            r'|\bshelve\.open\s*\('
            r'|\bmarshal\.loads?\s*\('
            r'|\bjsonpickle\.decode\s*\(',
        ),
        "Unsafe deserialization - arbitrary code execution risk",
        "Use yaml.safe_load(), json.loads(), or validate input before deserialization",
    ),
    # No input validation - user input to shell/eval
    (
        "no_input_validation",
        "HIGH",
        re.compile(
            r'\bsubprocess\.(?:run|call|Popen|check_output)\s*\(\s*f["\x27]'
            r'|\bos\.system\s*\(\s*f["\x27]'
            r'|\bos\.popen\s*\(\s*f["\x27]'
            r'|\beval\s*\(\s*(?:request|input|data|params|args|user)'
            r'|\bexec\s*\(\s*(?:request|input|data|params|args|user)',
        ),
        "User input passed to shell/eval - command injection risk",
        "Validate and sanitize input, use subprocess with list args, avoid eval()/exec()",
    ),
    # Exposed debug mode
    (
        "exposed_debug",
        "MEDIUM",
        re.compile(
            r'\bDEBUG\s*=\s*True\b'
            r'|\.run\s*\([^)]*debug\s*=\s*True'
            r'|FLASK_DEBUG\s*=\s*["\x27]?1["\x27]?'
            r'|DJANGO_DEBUG\s*=\s*["\x27]?True["\x27]?',
            re.IGNORECASE,
        ),
        "Debug mode enabled - may expose sensitive information in production",
        "Use environment-specific config: DEBUG = os.environ.get('DEBUG', 'False') == 'True'",
    ),
    # Missing auth on HTTP handlers (Flask/FastAPI/Django patterns)
    (
        "no_auth",
        "MEDIUM",
        re.compile(
            r'@app\.(?:route|get|post|put|delete|patch)\s*\('
            r'|@router\.(?:get|post|put|delete|patch)\s*\(',
        ),
        "HTTP handler may lack authentication",
        "Add auth decorator: @login_required, @require_auth, or Depends(get_current_user)",
    ),
]

# Auth decorator patterns that indicate auth IS present
AUTH_INDICATOR_PATTERNS = re.compile(
    r'@login_required|@require_auth|@authenticated|@auth_required'
    r'|@permission_required|@jwt_required|@token_required'
    r'|Depends\s*\(\s*(?:get_current_user|verify_token|auth)'
    r'|@csrf_protect|@requires_auth',
    re.IGNORECASE,
)


def analyze_code(content: str) -> list[SecurityWarning]:
    """
    Analyze code content for security omissions.

    Returns list of SecurityWarning objects. Empty list = no issues found.
    """
    if not content or len(content) < 10:
        return []

    warnings = []
    lines = content.split("\n")

    for pattern_id, severity, regex, message, suggestion in SECURITY_PATTERNS:
        matches = list(regex.finditer(content))
        if not matches:
            continue

        # Special case: no_auth pattern - suppress if auth decorators are nearby
        if pattern_id == "no_auth":
            if AUTH_INDICATOR_PATTERNS.search(content):
                continue

        for match in matches:
            # Find the line number for context
            pos = match.start()
            line_num = content[:pos].count("\n") + 1
            line_hint = lines[line_num - 1].strip()[:80] if line_num <= len(lines) else ""

            warnings.append(SecurityWarning(
                pattern_id=pattern_id,
                severity=severity,
                message=message,
                suggestion=suggestion,
                line_hint=line_hint,
            ))

    return warnings


def format_warnings(warnings: list[SecurityWarning]) -> str:
    """Format warnings for systemMessage output."""
    if not warnings:
        return ""

    parts = ["[SECURE CODE] Security analysis detected potential issues:\n"]
    for w in warnings:
        parts.append(
            f"  [{w.severity}] {w.message}\n"
            f"    Context: {w.line_hint}\n"
            f"    Fix: {w.suggestion}\n"
        )

    return "\n".join(parts)


def enforce_code(content: str) -> tuple[str, str] | None:
    """Mechanical enforcement gate for code security.

    Returns ("deny", reason) for HIGH severity violations.
    Returns None if no HIGH severity violations found.

    MEDIUM severity violations are logged but not blocking.
    """
    warnings = analyze_code(content)
    if not warnings:
        return None

    high_violations = [w for w in warnings if w.severity == "HIGH"]
    if not high_violations:
        return None

    details = "; ".join(
        f"{w.pattern_id}: {w.message}" for w in high_violations
    )
    return (
        "deny",
        f"[GUARDRAIL: SECURE CODE] {len(high_violations)} HIGH severity "
        f"security violation(s) detected: {details}. "
        f"Fix before proceeding."
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print("Secure Code Gate - Test Suite")
        print("=" * 40)

        test_cases = [
            ('f"SEL' + 'ECT * FR' + 'OM tbl WHERE id={uid}"', "sql_injection"),
            ("pickle.loads(data)", "unsafe_deserialize"),
            ('subprocess.run(f"rm {user_input}")', "no_input_validation"),
            ("DEBUG = True", "exposed_debug"),
            ('@app.route("/api/items")', "no_auth"),
        ]

        for code, expected_pattern in test_cases:
            results = analyze_code(code)
            found = any(r.pattern_id == expected_pattern for r in results)
            status = "PASS" if found else "FAIL"
            print(f"  [{status}] {expected_pattern}: {code[:50]}")

        # Negative test: auth present should suppress no_auth
        code_with_auth = '@login_required\n@app.route("/api/items")'
        results = analyze_code(code_with_auth)
        no_auth_found = any(r.pattern_id == "no_auth" for r in results)
        status = "PASS" if not no_auth_found else "FAIL"
        print(f"  [{status}] no_auth suppressed when auth present")

    elif len(sys.argv) > 1 and sys.argv[1] == "analyze":
        if len(sys.argv) > 2:
            content = open(sys.argv[2]).read()
        else:
            content = sys.stdin.read()

        results = analyze_code(content)
        if results:
            print(format_warnings(results))
        else:
            print("No security issues detected.")
    else:
        print("Usage:")
        print("  python3 secure_code_gate.py test                - Run test suite")
        print("  python3 secure_code_gate.py analyze [file]      - Analyze code")
