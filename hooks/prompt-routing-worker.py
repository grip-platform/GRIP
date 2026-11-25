#!/usr/bin/env python3
"""Prompt Routing Worker — detached subprocess for assessment.

Reads a user prompt from stdin JSON and writes the assessment to
state/routing/last-assessment.json. Runs detached from the hook so
classifier latency (heuristic: <10ms, semantic: <100ms) never blocks
the UserPromptSubmit flow.

Input:  JSON on stdin with {"message": "..."}
Output: state/routing/last-assessment.json

Version: 1.0.0
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
LIB_DIR = CLAUDE_DIR / "lib"
STATE_DIR = CLAUDE_DIR / "state" / "routing"
ASSESSMENT_FILE = STATE_DIR / "last-assessment.json"


def _read_prompt() -> str:
    """Read the prompt from stdin JSON. Returns empty string on failure."""
    try:
        worker_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return ""
    return worker_input.get("message", "")


def _run_assessment(prompt: str) -> dict:
    """Call lib/prompt_routing.assess and serialise the result to a dict."""
    if str(LIB_DIR) not in sys.path:
        sys.path.insert(0, str(LIB_DIR))
    from prompt_routing import assess  # type: ignore
    return asdict(assess(prompt))


def _write_assessment(payload: dict) -> None:
    """Atomically write the assessment to ASSESSMENT_FILE."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ASSESSMENT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(ASSESSMENT_FILE)


def main() -> None:
    prompt = _read_prompt()
    if not prompt:
        sys.exit(0)
    try:
        payload = _run_assessment(prompt)
    except Exception as exc:
        # Fail-safe: log to hooks.log and exit cleanly
        log_file = CLAUDE_DIR / "logs" / "hooks.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a") as f:
            f.write(f"[PROMPT-ROUTING-WORKER] {exc}\n")
        sys.exit(0)

    try:
        _write_assessment(payload)
    except OSError:
        pass
    _shadow_score(prompt, payload)
    sys.exit(0)


def _shadow_score(prompt: str, payload: dict) -> None:
    """Jev's shadow reading of the same prompt (the Jev plan's Phase 2b.3), after the
    assessment is written so nothing waits on it. Logs to
    logs/prompt-routing-shadow.jsonl and changes nothing the router says; this
    process inherits the session's working directory from the hook, which the
    privacy floor judges (lib/prompt_routing_score.py)."""
    try:
        if str(CLAUDE_DIR) not in sys.path:
            sys.path.insert(0, str(CLAUDE_DIR))
        from lib.prompt_routing_score import shadow
        shadow(prompt, assessment=payload)
    except Exception:  # noqa: BLE001 -- shadow work never disturbs routing
        return


if __name__ == "__main__":
    main()
