#!/usr/bin/env python3
"""PreToolUse hook: DENY Write/Edit of sensitive (financial / PII) content under ~/.claude/prep/ or exports/.

CONTENT-SCOPED, not blanket-directory-block.
Rationale: prep/ also holds non-financial sprint plans and exported docs. A blanket deny
on the directory would over-block legitimate work, violating knowledge-maturation-functor's
over-block caution. This guard checks BOTH path AND content — sensitive shapes under
these directories are denied; non-sensitive writes are allowed through.

Sensitive = financial markers (invoice, IBAN, SWIFT, rate card, ...) OR third-party PII
(email addresses, international phone numbers, national ID numbers, passport / DOB / SSN
labels). Obvious fakes/placeholders (example.com, AKIA...EXAMPLE, runs of zeroes) and the
repo owner's own identity domains (@codetonight.co.za) are exempt so example/demo docs and
the operator's own contact details are not blocked.

This reconciles AGORA's risk-asymmetry "block now" recommendation with the
knowledge-maturation-functor's warning against over-blocking: severity justifies early
mechanical enforcement (rule-now clause), but the scope is tightly content-scoped.

Path-exempt (never gate): this file itself, rules/no-sensitive-info-in-grip.md,
tests/test_sensitive_info_guard.py, docs/migrations/**, memory files (they must
quote the sensitive patterns to document them).

I/O contract: mirrors hooks/guardrail-evasion-gate.py exactly.
Fail-open: any exception → return 0 (allow). A guard bug must never brick Write/Edit.
Audit: every deny appended to ~/.claude/logs/sensitive-info-guard.jsonl

Rule:    rules/no-sensitive-info-in-grip.md
Origin:  2026-05-19 AGORA review of PR #2926 (concern 2); generalised financial→sensitive-info 2026-06-23.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

_CLAUDE_DIR = Path.home() / ".claude"
# Code is imported from the tree this hook lives in, never from ~/.claude:
# where ~/.claude is a stale copy rather than a link to the clone (a Windows
# teammate's layout, where Python reads the home folder from USERPROFILE),
# importing from it crashed this gate before it could check anything
# (fleet report 2026-10-02, B-2). Data paths are unchanged.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.hook_response import emit, pretool_decision  # noqa: E402

_AUDIT_LOG = _CLAUDE_DIR / "logs" / "sensitive-info-guard.jsonl"
_GATED_TOOLS = {"Write", "Edit"}

# Directories (relative to ~) whose content is subject to sensitive-pattern checks.
_GATED_DIRS = (
    str(Path.home() / ".claude" / "prep"),
    str(Path.home() / ".claude" / "exports"),
)

# Files that are always exempt — they must quote sensitive patterns to document them.
_EXEMPT_SUFFIXES = (
    "hooks/sensitive-info-guard.py",
    "rules/no-sensitive-info-in-grip.md",
    "tests/test_sensitive_info_guard.py",
)
_EXEMPT_PREFIXES = (
    str(Path.home() / ".claude" / "docs" / "migrations"),
    str(Path.home() / ".claude" / "projects"),  # memory files
)

# Financial content markers (case-insensitive).
_FINANCIAL = (
    r"invoice|billing|\bIBAN\b|SWIFT|bank account|account holder"
    r"|rate card|day rate|retainer|VAT number|remittance|payfast"
    r"|generate-invoice\.py|invoice-history\.json"
    r"|chlup-legal-ai-platform-proposal|leandro-perucchi-proposal"
    r"|perucchi-proposal-final"
)

# Personal / contact / identity PII markers.
_PII = (
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"   # email address
    r"|\+\d[\d\s().-]{8,}\d"                              # international phone (E.164-ish)
    r"|\b\d{13}\b"                                        # SA national ID (13 digits)
    r"|\b(?:\d[ -]?){13,19}\b"                            # card-shaped digit run
    r"|passport\s*(?:no|number)|national\s*id\b"
    r"|date\s*of\s*birth|\bDOB\b|\bSSN\b"
)

_SENSITIVE_PATTERN = re.compile(_FINANCIAL + _PII, re.IGNORECASE)

# ---------------------------------------------------------------------------
# Canonical client/person-name tokens — SINGLE SOURCE OF TRUTH for name leaks.
#
# These are the distinctive client/person-name fragments this runtime guard
# already knows: each appears inside _FINANCIAL above (as a full proposal-
# document slug). Exposing them here as bare, lowercase fragments lets the
# AGPL public-release leak-probe DERIVE its denylist from this guard instead
# of hand-maintaining a separate short list — so a name this guard blocks can
# never silently leak past the release gate. DRY: one denylist, two consumers
# (this runtime Write/Edit guard + the release-build probe).
#
# The probe scans built text for any word CONTAINING one of these fragments.
# NOT exhaustive: the full client/person roster is curated here by the repo
# owner, and everything added here flows to the release probe automatically.
#
# Invariant (enforced by tests/test_gatec_probe_dry_denylist.py): every token
# here is a substring of _FINANCIAL, so the token list and the runtime deny
# patterns can never drift apart.
# ---------------------------------------------------------------------------
SENSITIVE_NAME_TOKENS: tuple[str, ...] = ("chlup", "leandro", "perucchi")


def sensitive_name_tokens() -> tuple[str, ...]:
    """Return the client/person-name tokens this guard knows.

    The single source of truth the AGPL release leak-probe derives its
    client/person-name denylist from (see rules/no-sensitive-info-in-grip.md).
    """
    return SENSITIVE_NAME_TOKENS


# Obvious fakes / placeholders — a match consisting only of these must NOT deny,
# so example/demo docs and test fixtures keep working.
_FAKE_PATTERN = re.compile(
    r"example\.(?:com|org|net)|@example|\byou@|\bjane@|\bjohn@|test@|noreply@"
    r"|\bacme\b|\blocalhost\b|127\.0\.0\.1|0{6,}|1234567890|2345678901"
    r"|AKIAIOSFODNN7EXAMPLE|your-token-here|changeme|x{4,}",
    re.IGNORECASE,
)

# The repo owner's own identity domains — allowed per rules/no-sensitive-info-in-grip.md
# (operator/team identity is the owner's own data, not a third-party leak).
_OWNER_PATTERN = re.compile(r"@codetonight\.co\.za|@codetonight", re.IGNORECASE)

_RULE_REF = (
    "Sensitive financial/PII content is not permitted in the GRIP repo. "
    "Use scripts/sensitive-repo-bootstrap.sh to create a dedicated private repo, "
    "or write directly to CodeTonight-SA/exampleorg-financials. "
    "See rules/no-sensitive-info-in-grip.md"
)


def _read_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _extract(tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    if tool_name == "Write":
        return (tool_input.get("content", "") or "", tool_input.get("file_path", "") or "")
    if tool_name == "Edit":
        return (tool_input.get("new_string", "") or "", tool_input.get("file_path", "") or "")
    return None


def _is_exempt(file_path: str) -> bool:
    for suffix in _EXEMPT_SUFFIXES:
        if file_path.endswith(suffix):
            return True
    for prefix in _EXEMPT_PREFIXES:
        if file_path.startswith(prefix):
            return True
    return False


def _is_gated_path(file_path: str) -> bool:
    return any(file_path.startswith(d) for d in _GATED_DIRS)


def _has_real_sensitive(content: str) -> bool:
    """True iff content holds a sensitive marker that is not an obvious fake
    or the repo owner's own identity."""
    for match in _SENSITIVE_PATTERN.finditer(content):
        token = match.group(0)
        if _FAKE_PATTERN.search(token) or _OWNER_PATTERN.search(token):
            continue
        return True
    return False


def _audit(record: dict) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _AUDIT_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _emit_deny(reason: str) -> None:
    emit(pretool_decision("deny", reason))
    sys.exit(0)


def main() -> int:
    try:
        payload = _read_stdin()
        tool_name = payload.get("tool_name") or payload.get("toolName") or ""
        if tool_name not in _GATED_TOOLS:
            return 0

        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        extracted = _extract(tool_name, tool_input)
        if extracted is None:
            return 0

        content, file_path = extracted

        # Exempt paths always pass through.
        if _is_exempt(file_path):
            return 0

        # Only gate writes under prep/ or exports/.
        if not _is_gated_path(file_path):
            return 0

        # Check content for sensitive financial / PII markers.
        if _has_real_sensitive(content):
            _audit({"decision": "deny", "tool": tool_name, "file_path": file_path})
            _emit_deny(_RULE_REF)

    except Exception:
        # Fail-open: a guard bug must never brick Write/Edit.
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
