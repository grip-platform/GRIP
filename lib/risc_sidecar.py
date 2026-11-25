"""RISC Sidecar — persistent scorer replacing per-call subprocess spawn.

Modes:
  --serve       Read JSON lines from stdin, write JSON lines to stdout.
                Stays alive until EOF. Per-call cost: ~5-10ms (no fork).
  --score-once  Score a single JSON payload from argv, print result, exit.

Wire format (request):
  {"id": "uuid", "text": "...", "context": {"user": "...", "channel": "..."}}

Wire format (response):
  {"id": "uuid", "score": float, "tier": "deny|warn|allow", "reasons": [str]}

On malformed input:
  {"id": "<id or null>", "error": "<description>"}

The scoring logic delegates entirely to the existing RISC library stack:
  risc_encoder.score_message -> risc_pattern_validator.validate_patterns
  -> risc_challenge_gate.generate_challenge

Usage (persistent):
  python3 lib/risc_sidecar.py --serve

Usage (one-shot, for tests and CLI):
  python3 lib/risc_sidecar.py --score-once '{"id":"x","text":"hello","context":{}}'
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_GRIP_ROOT = Path(__file__).resolve().parent.parent
if str(_GRIP_ROOT) not in sys.path:
    sys.path.insert(0, str(_GRIP_ROOT))


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

_DENY_AVG = 50.0   # avg RISC score below this -> deny
_WARN_AVG = 65.0   # avg below this -> warn; above -> allow


def _classify_tier(avg: float) -> str:
    if avg < _DENY_AVG:
        return "deny"
    if avg < _WARN_AVG:
        return "warn"
    return "allow"


# ---------------------------------------------------------------------------
# Core scoring (imports existing RISC stack; never reimplements logic)
# ---------------------------------------------------------------------------

def _run_score(text: str, user: str, channel: str) -> dict[str, Any]:
    """Invoke the three-layer RISC stack and return a normalised result dict."""
    from lib.risc_encoder import score_message
    from lib.risc_pattern_validator import validate_patterns
    from lib.risc_challenge_gate import generate_challenge

    score = score_message(text)
    score_dict = score.to_dict()

    validation = validate_patterns(user or "anonymous", score_dict, text, channel or "")
    val_dict = validation.to_dict()

    challenge = generate_challenge(score_dict, val_dict)

    all_flags = score_dict.get("flags", []) + val_dict.get("flags", [])
    reasons: list[str] = list(all_flags)
    if challenge:
        reasons.append(challenge)

    return {
        "avg": score_dict["avg"],
        "tier": _classify_tier(score_dict["avg"]),
        "reasons": reasons,
        "raw": {
            "R": score_dict["R"],
            "I": score_dict["I"],
            "S": score_dict["S"],
            "C": score_dict["C"],
        },
    }


# ---------------------------------------------------------------------------
# Request / response helpers
# ---------------------------------------------------------------------------

def _parse_request(raw: str) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Parse a JSON request line. Returns (id, payload, error_msg)."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, None, f"JSON parse error: {exc}"

    if not isinstance(payload, dict):
        return None, None, "request must be a JSON object"

    req_id = payload.get("id")
    text = payload.get("text")
    if not isinstance(text, str):
        return req_id, None, "missing or non-string 'text' field"

    return req_id, payload, None


def _score_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Score one validated request payload. Returns response dict."""
    req_id = payload.get("id")
    text = payload["text"]
    ctx = payload.get("context") or {}
    user = str(ctx.get("user", "anonymous"))
    channel = str(ctx.get("channel", ""))

    try:
        result = _run_score(text, user, channel)
    except Exception as exc:  # noqa: BLE001
        return {"id": req_id, "error": f"scoring failed: {exc}"}

    return {
        "id": req_id,
        "score": result["avg"],
        "tier": result["tier"],
        "reasons": result["reasons"],
    }


def _build_error(req_id: str | None, message: str) -> dict[str, Any]:
    return {"id": req_id, "error": message}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def serve(stdin=None, stdout=None) -> None:
    """Persistent mode: process JSON lines until EOF."""
    infile = stdin or sys.stdin
    outfile = stdout or sys.stdout

    for raw_line in infile:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        req_id, payload, err = _parse_request(raw_line)
        if err:
            response = _build_error(req_id, err)
        else:
            response = _score_request(payload)  # type: ignore[arg-type]

        outfile.write(json.dumps(response) + "\n")
        outfile.flush()


def score_once(json_str: str) -> dict[str, Any]:
    """One-shot mode: score a single JSON string, return response dict."""
    req_id, payload, err = _parse_request(json_str)
    if err:
        return _build_error(req_id, err)
    return _score_request(payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: risc_sidecar.py --serve | --score-once <json>", file=sys.stderr)
        sys.exit(1)

    if args[0] == "--serve":
        serve()
    elif args[0] == "--score-once":
        if len(args) < 2:
            print("--score-once requires a JSON argument", file=sys.stderr)
            sys.exit(1)
        result = score_once(args[1])
        print(json.dumps(result))
    else:
        print(f"Unknown flag: {args[0]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()
