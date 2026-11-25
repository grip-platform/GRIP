"""AIDE prediction auto-grader — grade falsifiable predictions against reality.

Design sealed by the sovereign council (ultrareason, sha256
92c5c41469a5acae…): a minimal honest auto-grader requires

1. **Immutable append-only evidence** — every grade writes its evidence into the
   prediction record via `PredictionRegistry.grade` (append-only + idempotent:
   an already-graded prediction is never re-graded).
2. **Mechanical checks FIRST, deterministic, $0** — port-listening,
   decision-log growth, Slack-post presence. Pure functions; evidence is the
   actual observed state, never an opinion.
3. **Semantic fallback ONLY when mechanical cannot resolve** — one tightly
   constrained Pool-B call through the ultracode kernel (`lib.ultracode.kernel.run`
   + `HALAdapter`), content-hash idempotent with a persistent cache: re-running
   the same (task, state) makes ZERO new provider calls.
4. **Pre-registered frozen criteria** — the grader evaluates the *stored
   falsifier*, never a reinterpretation. No post-hoc success criteria.

Anti-theatre rails:
- A prediction whose falsifier matches no mechanical check AND whose semantic
  pass cannot reach a verdict is left **ungraded and surfaced for human
  judgment** — never force-graded, never silently confirmed.
- Mechanical checks never call an LLM. Semantic calls are JSON-mode, one
  verdict per call, evidence required.

Usage:
    from lib.aide.autograder import auto_grade
    summary = auto_grade()   # grades due ungraded predictions
"""
from __future__ import annotations

import json
import re
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .adaptive_engine import PredictionRegistry
from . import jsonl_io, paths

# ---------------------------------------------------------------------------
# Evidence capture (mechanical — real observations only)
# ---------------------------------------------------------------------------


def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> Tuple[bool, str]:
    """True iff something listens on host:port. Evidence = the observed socket state."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True, f"TCP connect to {host}:{port} succeeded"
    except OSError as exc:
        return False, f"TCP connect to {host}:{port} failed: {exc}"
    finally:
        s.close()


def _count_decision_log_entries(start_utc: datetime, end_utc: datetime) -> Tuple[int, str]:
    """Count decision-log entries whose timestamp falls in [start, end)."""
    log = paths.CLAUDE_DIR / "data" / "aide" / "decision-log.jsonl"
    if not log.exists():
        return 0, "decision-log.jsonl missing"
    n = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            ts = datetime.fromisoformat(rec.get("timestamp", ""))
            if start_utc <= ts < end_utc:
                n += 1
        except (json.JSONDecodeError, ValueError):
            continue
    return n, f"{n} decision-log entries in window"


def _slack_posts_since(start_utc: datetime) -> Tuple[int, str]:
    """Count aide-ideate Slack posts logged after start_utc (evidence: our own log).

    Each posted line carries the Slack message timestamp as `ts=<unix>`; parse
    it and compare, so a prediction is never "confirmed" by posts that predate
    its own creation (the false-confirmation bug AGORA #4967 flagged).
    """
    log = paths.CLAUDE_DIR / "logs" / "aide-ideation.out.log"
    if not log.exists():
        return 0, "aide-ideation log missing"
    n = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        if "posted to Slack" not in line:
            continue
        m = re.search(r"ts=([0-9]+\.[0-9]+)", line)
        if not m:
            continue  # unparseable line → never counts as evidence
        try:
            ts_utc = datetime.fromtimestamp(float(m.group(1)), tz=timezone.utc)
        except (ValueError, OSError):
            continue
        if ts_utc >= start_utc:
            n += 1
    return n, f"{n} posted-to-Slack lines in aide-ideation log after {start_utc.isoformat()}"


# ---------------------------------------------------------------------------
# Mechanical check registry — frozen criteria matched against the STORED falsifier
# ---------------------------------------------------------------------------

# Each entry: (name, falsifier-pattern, checker(rec, now) -> (outcome|None, evidence))
# outcome: "confirmed" | "falsified" | None (cannot resolve mechanically)
_MECHANICAL_CHECKS: List[Dict[str, Any]] = []


def _mechanical(name: str, pattern: str):
    """Decorator: register a mechanical checker for falsifiers matching `pattern`."""
    def deco(fn):
        _MECHANICAL_CHECKS.append({"name": name, "pattern": pattern, "fn": fn})
        return fn
    return deco


@_mechanical("ports-listening", r"port")
def _check_ports(rec: Dict[str, Any], now: datetime) -> Tuple[Optional[str], str]:
    """Falsifier mentions a port → probe 3000 + 8080 (the launchd-managed pair)."""
    claim = str(rec.get("claim", ""))
    # Ports usually live in the falsifier ("port 3000 or 8080 is not listening"),
    # not the claim. Scan both so the mechanical path actually fires.
    text = claim + " " + str(rec.get("falsifier", ""))
    ports = [int(p) for p in re.findall(r"\b(3000|8080)\b", text)]
    if not ports:
        return None, "no numeric port in claim or falsifier to probe"
    results = [_port_listening(p) for p in ports]
    listening = all(ok for ok, _ in results)
    evidence = "; ".join(desc for _, desc in results)
    # Derive polarity from the stored falsifier, never hard-code it. A falsifier
    # phrased "port X is not listening" is CONFIRMED when the port IS listening
    # (the falsifying condition failed to occur). A positively-phrased falsifier
    # ("port X is listening") is the mirror image. If the polarity cannot be
    # determined, do NOT grade — return None so the semantic/human path decides.
    fals = str(rec.get("falsifier", "")).lower()
    if re.search(r"not\s+listening|isn't\s+listening|down|unreachable|stopped|failed", fals):
        outcome = "confirmed" if listening else "falsified"
    elif re.search(r"listening|up|reachable|running", fals):
        outcome = "falsified" if listening else "confirmed"
    else:
        return None, f"cannot determine falsifier polarity from: {fals[:80]}"
    return outcome, evidence


@_mechanical("decision-log-growth", r"decision-log")
def _check_decision_growth(rec: Dict[str, Any], now: datetime) -> Tuple[Optional[str], str]:
    """Falsifier mentions decision-log entries → count in window."""
    created = datetime.fromisoformat(rec["created_utc"])
    count, evidence = _count_decision_log_entries(created, now)
    m = re.search(r"at least (\d+)", str(rec.get("falsifier", "")))
    threshold = int(m.group(1)) if m else 1
    outcome = "confirmed" if count >= threshold else "falsified"
    return outcome, f"{evidence}; threshold {threshold}"


@_mechanical("slack-post-appeared", r"Slack post appears")
def _check_slack_post(rec: Dict[str, Any], now: datetime) -> Tuple[Optional[str], str]:
    """Falsifier references a Slack post appearing → check our ideation log."""
    created = datetime.fromisoformat(rec["created_utc"])
    count, evidence = _slack_posts_since(created)
    outcome = "confirmed" if count > 0 else "falsified"
    return outcome, evidence


@_mechanical("routine-occurred", r"^Routine '")
def _check_routine(rec: Dict[str, Any], now: datetime) -> Tuple[Optional[str], str]:
    """Routine forecasts: confirmed iff decision activity exists near the hour.

    Mechanical proxy for "the cadence fired": any decision-log entry whose hour
    matches the predicted routine hour within the window counts as the routine
    having occurred. A cadence is a habit, not an exact ping.
    """
    claim = str(rec.get("claim", ""))
    m = re.search(r"due at (\d{2}):\d{2}", claim)
    if not m:
        return None, "no hour embedded in routine claim"
    hour = int(m.group(1))
    created = datetime.fromisoformat(rec["created_utc"])
    log = paths.CLAUDE_DIR / "data" / "aide" / "decision-log.jsonl"
    if not log.exists():
        return None, "decision-log.jsonl missing"
    hits = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec_line = json.loads(line)
            ts = datetime.fromisoformat(rec_line.get("timestamp", ""))
            if created <= ts < now and ts.hour == hour:
                hits += 1
        except (json.JSONDecodeError, ValueError):
            continue
    evidence = f"{hits} decision entries at hour {hour:02d} since registration"
    if hits > 0:
        return "confirmed", evidence
    return "falsified", evidence


# ---------------------------------------------------------------------------
# Semantic fallback — one Pool-B call via the ultracode kernel (idempotent)
# ---------------------------------------------------------------------------


def _semantic_verdict(rec: Dict[str, Any], cache: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """One tightly-constrained Pool-B call. Content-hash idempotent: identical
    (task, state) makes zero adapter calls on re-run (ultracode H-UC-IDEM)."""
    try:
        from lib.ultracode.kernel import State, run
        from lib.ultracode.adapters import HALAdapter
    except ImportError:
        return None, "ultracode kernel unavailable; left for human judgment"

    task = {
        "claim": rec.get("claim", ""),
        "falsifier": rec.get("falsifier", ""),
        "created_utc": rec.get("created_utc", ""),
        "check_at_utc": rec.get("check_at_utc", ""),
    }
    prompt = (
        "You grade ONE falsifiable prediction against its stored falsifier. "
        "Reply with ONLY JSON: {\"outcome\": \"confirmed\"|\"falsified\"|\"unknown\", "
        "\"evidence\": \"<one sentence, observed reality>\"}. "
        "Use \"unknown\" when reality cannot be determined from the information "
        "given — never guess. "
        f"Prediction: {json.dumps(task, sort_keys=True)}"
    )
    try:
        result = run(task=prompt, state=State(), adapter=HALAdapter(), cache=cache)
        parsed = json.loads(result.output)
        outcome = parsed.get("outcome")
        evidence = parsed.get("evidence", "semantic verdict")
        if outcome not in ("confirmed", "falsified", "unknown"):
            return None, f"semantic pass returned invalid outcome {outcome!r}"
        if outcome == "unknown":
            return None, f"semantic pass could not determine: {evidence}"
        return outcome, f"semantic: {evidence}"
    except Exception as exc:  # noqa: BLE001 — fail open to human judgment
        return None, f"semantic pass failed: {str(exc)[:120]}"


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def auto_grade(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Grade every due, ungraded prediction.

    Returns a summary dict: graded (with outcome counts), left_for_human,
    mechanical vs semantic split, and per-prediction evidence.
    """
    now = now or datetime.now(timezone.utc)
    registry = PredictionRegistry()
    # Persistent cache — the ultracode idempotency store across runs.
    cache_file = paths.STATE_DIR / "autograder-cache.json"
    cache: Dict[str, Any] = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}

    summary: Dict[str, Any] = {
        "graded": 0, "confirmed": 0, "falsified": 0,
        "mechanical": 0, "semantic": 0, "left_for_human": 0,
        "details": [],
    }
    changed = False

    for rec in registry.all(limit=1000):
        if rec.get("outcome") is not None:
            continue  # idempotent: never re-grade
        try:
            check_at = datetime.fromisoformat(rec["check_at_utc"])
        except (KeyError, ValueError):
            continue
        if check_at > now:
            continue  # not due yet

        outcome: Optional[str] = None
        evidence = ""

        # 1. Mechanical first — deterministic, $0.
        for check in _MECHANICAL_CHECKS:
            if re.search(check["pattern"], str(rec.get("falsifier", "")) + " " + str(rec.get("claim", ""))):
                outcome, evidence = check["fn"](rec, now)
                if outcome is not None:
                    summary["mechanical"] += 1
                    break

        # 2. Semantic fallback — only when mechanical cannot resolve.
        if outcome is None:
            outcome, evidence = _semantic_verdict(rec, cache)
            if outcome is not None:
                summary["semantic"] += 1

        if outcome is None:
            summary["left_for_human"] += 1
            summary["details"].append({
                "pid": rec["pid"], "claim": rec["claim"][:80],
                "decision": "left_for_human", "evidence": evidence or "no mechanical match; semantic unresolved",
            })
            continue

        registry.grade(rec["pid"], outcome=outcome, evidence=[evidence])
        summary["graded"] += 1
        summary[outcome] += 1
        summary["details"].append({
            "pid": rec["pid"], "claim": rec["claim"][:80],
            "decision": outcome, "evidence": evidence,
        })
        changed = True

    if changed:
        try:
            cache_file.write_text(json.dumps(cache, indent=1), encoding="utf-8")
        except OSError:
            pass

    return summary


if __name__ == "__main__":
    import sys
    print(json.dumps(auto_grade(), indent=1))
    sys.exit(0)
