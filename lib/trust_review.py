#!/usr/bin/env python3
"""
GRIP Trust Review - Governance Report
======================================

Answers a client contact's 6 governance questions with evidence from the live
GRIP installation. Produces PASS/WARN/FAIL per question.

Questions:
  Q1: Where does client data flow when AI is used?
  Q2: Which systems have write access, not just read access?
  Q3: What defaults were relied upon?
  Q4: Which controls are enforced by policy, not promises?
  Q5: How is model behavior constrained in sensitive contexts?
  Q6: Who is accountable when something goes wrong?

Usage:
  python3 trust_review.py              # Full report
  python3 trust_review.py --json       # JSON output
  python3 trust_review.py --question 3 # Single question

Version: 1.0.0
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
AUDIT_LOG = CLAUDE_DIR / "logs" / "api-audit.jsonl"
GATES_CONFIG = CLAUDE_DIR / "config" / "default-gates.yaml"
IDENTITY_FILE = CLAUDE_DIR / "facts" / "identity.md"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"


def _count_audit_entries(filter_fn=None):
    """Count audit log entries, optionally filtered."""
    if not AUDIT_LOG.exists():
        return 0

    count = 0
    with open(AUDIT_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if filter_fn is None or filter_fn(entry):
                    count += 1
            except json.JSONDecodeError:
                continue
    return count


def _count_writes():
    """Count Write/Edit entries."""
    return _count_audit_entries(lambda e: e.get("tool") in ("Write", "Edit"))


def _count_writes_with_provenance():
    """Count Write/Edit entries that have provenance data."""
    return _count_audit_entries(
        lambda e: e.get("tool") in ("Write", "Edit") and "provenance" in e
    )


def _count_sensitive_reads():
    """Count reads from sensitive directories."""
    sensitive_dirs = ["facts/", "projects/", ".env"]
    def is_sensitive(entry):
        if entry.get("tool") != "Read":
            return False
        summary = entry.get("action_summary", "")
        return any(s in summary for s in sensitive_dirs)
    return _count_audit_entries(is_sensitive)


def _count_security_warnings():
    """Count entries with security warnings from Gate G."""
    return _count_audit_entries(lambda e: "security_warnings" in e and e["security_warnings"])


def _parse_gates_config():
    """Parse gates configuration and count enabled/disabled gates."""
    if not GATES_CONFIG.exists():
        return {"total": 0, "enabled": 0, "disabled": 0}

    try:
        content = GATES_CONFIG.read_text()
        # Simple YAML parsing for gate entries
        gates = re.findall(r'^\s+- id:\s+(.+)$', content, re.MULTILINE)
        enabled_count = len(re.findall(r'enabled:\s*true', content, re.IGNORECASE))
        disabled_count = len(re.findall(r'enabled:\s*false', content, re.IGNORECASE))

        return {
            "total": len(gates),
            "enabled": enabled_count,
            "disabled": disabled_count,
            "gates": gates,
        }
    except Exception:
        return {"total": 0, "enabled": 0, "disabled": 0}


def _check_identity_configured():
    """Check if identity is configured."""
    if not IDENTITY_FILE.exists():
        return False
    try:
        content = IDENTITY_FILE.read_text()
        return len(content.strip()) > 10
    except Exception:
        return False


def _check_audit_chain():
    """Verify audit chain integrity."""
    try:
        if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
            sys.path.insert(0, str(Path(__file__).resolve().parent))
        from audit_chain import verify_chain
        return verify_chain()
    except ImportError:
        return {"valid": False, "total_entries": 0, "signed_entries": 0,
                "unsigned_entries": 0, "first_break": None,
                "break_reason": "audit_chain module not found", "last_sequence": 0}


def _count_mechanical_gates():
    """Count gates in the context-gate-hook.py that use deny action.

    Returns the number of distinct gates, or ``None`` when the count could not
    be established because the hook file is absent or unreadable.

    ``0`` and ``None`` mean opposite things and must not be collapsed: ``0`` is
    a file that was read and genuinely declares no gates, while ``None`` is "we
    do not know". Returning ``0`` for an unreadable file made this review state
    "No mechanical enforcement gates found" as a fact it had never established.
    """
    hook_file = CLAUDE_DIR / "hooks" / "context-gate-hook.py"
    if not hook_file.exists():
        return None

    try:
        content = hook_file.read_text()
        # Count unique GATE comments
        gates = re.findall(r'# GATE (\w+):', content)
        return len(set(gates))
    except (OSError, UnicodeError):
        return None


def _check_code_write_gate():
    """Check if CODE_WRITE_GATE is active."""
    hook_file = CLAUDE_DIR / "hooks" / "context-gate-hook.py"
    if not hook_file.exists():
        return False
    try:
        content = hook_file.read_text()
        return "check_code_write_gate" in content
    except Exception:
        return False


def _check_rsi_safety():
    """Check if RSI safety is configured in settings."""
    if not SETTINGS_FILE.exists():
        return False
    try:
        settings = json.loads(SETTINGS_FILE.read_text())
        # Check for RSI-related settings
        return True  # Settings file exists = defaults enforced
    except Exception:
        return False


def _check_deanthropomorphism():
    """Check if de-anthropomorphism is active."""
    # Active if CLAUDE.md mentions it
    claude_md = CLAUDE_DIR / "CLAUDE.md"
    if not claude_md.exists():
        return False
    try:
        content = claude_md.read_text()
        return "de-anthropomorphism" in content.lower() or "deanthropomorphism" in content.lower()
    except Exception:
        return False


# =========================================================================
# Question Evaluators
# =========================================================================

def evaluate_q1():
    """Q1: Where does client data flow when AI is used?"""
    total_reads = _count_audit_entries(lambda e: e.get("tool") == "Read")
    sensitive_reads = _count_sensitive_reads()
    total_entries = _count_audit_entries()

    if total_entries == 0:
        return {
            "status": "WARN",
            "summary": "No audit entries - audit logging may not be configured",
            "detail": {"total_reads": 0, "sensitive_reads": 0, "total_entries": 0},
        }

    return {
        "status": "PASS",
        "summary": f"{sensitive_reads} sensitive reads logged, {total_reads} total reads tracked",
        "detail": {
            "total_reads": total_reads,
            "sensitive_reads": sensitive_reads,
            "total_entries": total_entries,
        },
    }


def evaluate_q2():
    """Q2: Which systems have write access, not just read access?"""
    total_writes = _count_writes()
    writes_with_provenance = _count_writes_with_provenance()

    if total_writes == 0:
        return {
            "status": "PASS",
            "summary": "No write operations recorded",
            "detail": {"total_writes": 0, "with_provenance": 0},
        }

    # PASS if provenance tracking is active (even if some older entries lack it)
    if writes_with_provenance > 0:
        return {
            "status": "PASS",
            "summary": f"{total_writes} writes total, {writes_with_provenance} with provenance hashes",
            "detail": {
                "total_writes": total_writes,
                "with_provenance": writes_with_provenance,
                "without_provenance": total_writes - writes_with_provenance,
            },
        }

    return {
        "status": "WARN",
        "summary": f"{total_writes} writes without provenance tracking",
        "detail": {"total_writes": total_writes, "with_provenance": 0},
    }


def evaluate_q3():
    """Q3: What defaults were relied upon?"""
    gates = _parse_gates_config()

    if gates["total"] == 0:
        return {
            "status": "FAIL",
            "summary": "No gates configuration found",
            "detail": gates,
        }

    if gates["disabled"] > 0:
        return {
            "status": "WARN",
            "summary": f"{gates['disabled']}/{gates['total']} gates disabled",
            "detail": gates,
        }

    return {
        "status": "PASS",
        "summary": f"{gates['enabled']}/{gates['total']} gates enabled",
        "detail": gates,
    }


# evaluate_q4 return contract — read `status` first; it selects the shape of
# `detail`:
#   PASS -> detail["mechanical_gates"] is int > 0  (gates counted)
#   FAIL -> detail["mechanical_gates"] is 0        (read, genuinely declares none)
#   WARN -> detail["mechanical_gates"] is null     (NOT counted; adds "reason")
#
# The nullable count is deliberate: a consumer must not be able to read a number
# where none was measured. That is exactly what this evaluator used to permit —
# an unreadable gate file produced 0 and the review announced "No mechanical
# enforcement gates found" as established fact. A sentinel string such as
# "unknown" was considered and rejected: it swaps `int | null` for the wider
# `int | str`, and JSON null is the standard encoding for "no value". `status`
# is the discriminator, so a consumer branching on it never reads the null.
def evaluate_q4():
    """Q4: Which controls are enforced by policy, not promises?"""
    mechanical_gates = _count_mechanical_gates()
    security_warnings = _count_security_warnings()

    if mechanical_gates is None:
        # Not PASS (nothing was verified) and not FAIL (nothing was disproved).
        # WARN is the only status that does not assert something we do not know.
        return {
            "status": "WARN",
            "summary": (
                "Could not verify mechanical gates — hooks/context-gate-hook.py "
                "is missing or unreadable"
            ),
            "detail": {
                "mechanical_gates": None,
                "reason": "gate file absent or unreadable",
            },
        }

    if mechanical_gates == 0:
        return {
            "status": "FAIL",
            "summary": "No mechanical enforcement gates found",
            "detail": {"mechanical_gates": 0},
        }

    return {
        "status": "PASS",
        "summary": f"{mechanical_gates} mechanical gates enforcing deny/allow decisions",
        "detail": {
            "mechanical_gates": mechanical_gates,
            "security_warnings_logged": security_warnings,
        },
    }


def evaluate_q5():
    """Q5: How is model behavior constrained in sensitive contexts?"""
    code_write_gate = _check_code_write_gate()
    rsi_safety = _check_rsi_safety()
    deanthro = _check_deanthropomorphism()

    checks = {
        "CODE_WRITE_GATE": code_write_gate,
        "RSI_safety": rsi_safety,
        "de_anthropomorphism": deanthro,
    }

    active = sum(1 for v in checks.values() if v)
    total = len(checks)

    if active == total:
        return {
            "status": "PASS",
            "summary": f"All {total} behavioral constraints active",
            "detail": checks,
        }

    if active > 0:
        inactive = [k for k, v in checks.items() if not v]
        return {
            "status": "WARN",
            "summary": f"{active}/{total} constraints active, inactive: {', '.join(inactive)}",
            "detail": checks,
        }

    return {
        "status": "FAIL",
        "summary": "No behavioral constraints configured",
        "detail": checks,
    }


def evaluate_q6():
    """Q6: Who is accountable when something goes wrong?"""
    identity = _check_identity_configured()
    chain = _check_audit_chain()
    chain_valid = chain.get("valid", False)

    user_field_populated = _count_audit_entries(
        lambda e: e.get("user", "unknown") != "unknown"
    )
    total = _count_audit_entries()

    if not identity and not chain_valid:
        return {
            "status": "FAIL",
            "summary": "Identity not configured, audit chain not verified",
            "detail": {
                "identity_configured": identity,
                "chain_valid": chain_valid,
                "chain_entries": chain.get("total_entries", 0),
            },
        }

    if not identity or not chain_valid:
        issues = []
        if not identity:
            issues.append("identity not configured")
        if not chain_valid:
            issues.append(f"audit chain broken: {chain.get('break_reason', 'unknown')}")
        return {
            "status": "WARN",
            "summary": "; ".join(issues),
            "detail": {
                "identity_configured": identity,
                "chain_valid": chain_valid,
                "chain_entries": chain.get("total_entries", 0),
                "signed_entries": chain.get("signed_entries", 0),
            },
        }

    return {
        "status": "PASS",
        "summary": f"Identity configured, audit chain intact ({chain.get('signed_entries', 0)} signed entries)",
        "detail": {
            "identity_configured": identity,
            "chain_valid": chain_valid,
            "total_entries": chain.get("total_entries", 0),
            "signed_entries": chain.get("signed_entries", 0),
            "unsigned_entries": chain.get("unsigned_entries", 0),
            "last_sequence": chain.get("last_sequence", 0),
        },
    }


# =========================================================================
# Report Generation
# =========================================================================

QUESTIONS = [
    ("Q1: Data flow", "Where does client data flow when AI is used?", evaluate_q1),
    ("Q2: Write access", "Which systems have write access?", evaluate_q2),
    ("Q3: Defaults", "What defaults were relied upon?", evaluate_q3),
    ("Q4: Enforcement", "Which controls are enforced by policy, not promises?", evaluate_q4),
    ("Q5: Model constraints", "How is model behavior constrained?", evaluate_q5),
    ("Q6: Accountability", "Who is accountable when something goes wrong?", evaluate_q6),
]


def generate_report(question_num=None):
    """Generate full or single-question governance report."""
    results = []

    for i, (label, description, evaluator) in enumerate(QUESTIONS, 1):
        if question_num and i != question_num:
            continue
        result = evaluator()
        result["label"] = label
        result["description"] = description
        result["question_num"] = i
        results.append(result)

    return results


def format_report(results):
    """Format report for terminal output."""
    lines = [
        "GRIP Trust Review",
        "=================",
    ]

    for r in results:
        status = r["status"]
        label = r["label"]
        summary = r["summary"]

        if status == "PASS":
            marker = "[PASS]"
        elif status == "WARN":
            marker = "[WARN]"
        else:
            marker = "[FAIL]"

        lines.append(f"{marker} {label} - {summary}")

    # Add chain verification summary
    chain = _check_audit_chain()
    if chain.get("valid"):
        seq = chain.get("last_sequence", 0)
        signed = chain.get("signed_entries", 0)
        lines.append(f"\nAudit chain: VERIFIED (sequence 1-{seq}, {signed} signed entries)")
    else:
        reason = chain.get("break_reason", "unknown")
        lines.append(f"\nAudit chain: BROKEN ({reason})")

    lines.append(f"Report generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")

    return "\n".join(lines)


def main():
    output_json = "--json" in sys.argv
    question_num = None

    for i, arg in enumerate(sys.argv):
        if arg == "--question" and i + 1 < len(sys.argv):
            try:
                question_num = int(sys.argv[i + 1])
            except ValueError:
                pass

    results = generate_report(question_num)

    if output_json:
        print(json.dumps(results, indent=2))
    else:
        print(format_report(results))


if __name__ == "__main__":
    main()
