"""Background runner for external review — launched by knowledge-work-hook.py.

Runs asynchronously in a detached subprocess. Logs results to external-reviews.jsonl.
Does NOT block the main Claude Code session.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".claude" / "lib"))

LOG_FILE = Path.home() / ".claude" / "logs" / "external-reviews.jsonl"


async def run_review(payload: dict):
    from external_reviewer import review_output, ReviewType

    type_map = {
        "KNOWLEDGE": ReviewType.KNOWLEDGE,
        "DECISION": ReviewType.DECISION,
        "CLAIM": ReviewType.CLAIM,
        "CODE": ReviewType.CODE,
    }
    rt = type_map.get(payload["type"], ReviewType.KNOWLEDGE)
    result = await review_output(payload["content"], payload["context"], rt)

    entry = {
        "ts": time.time(),
        "provider": result.provider,
        "model": result.model,
        "score": result.score,
        "risk": result.hallucination_risk,
        "issues": result.issues[:5],
        "type": payload["type"],
    }

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def validate_payload(payload: dict) -> bool:
    """Validate external review payload structure."""
    required = {"content", "context", "type"}
    if not isinstance(payload, dict):
        return False
    if not required.issubset(payload.keys()):
        return False
    if payload["type"] not in {"KNOWLEDGE", "DECISION", "CLAIM", "CODE"}:
        return False
    if not isinstance(payload["content"], str) or not payload["content"]:
        return False
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    try:
        payload = json.loads(sys.argv[1])
    except (json.JSONDecodeError, TypeError) as e:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps({"ts": time.time(), "error": f"Invalid JSON: {e}"}) + "\n")
        sys.exit(1)
    if not validate_payload(payload):
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps({"ts": time.time(), "error": "Invalid payload structure"}) + "\n")
        sys.exit(1)
    asyncio.run(run_review(payload))
