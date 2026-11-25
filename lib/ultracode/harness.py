"""ultracode P3 — the HAPPI harness seam (hypothesis H-UC-HARNESS).

One JSON envelope in, one NDJSON event stream out — the HAPPI.md contract
(started / delta / completed; exit 0 on completed, non-zero on error). Every
harness drives the kernel through the SAME executor:

    in-process (e.g. Claude Code):   execute_envelope(envelope)
    headless / OpenCode / any CLI:   echo '<envelope-json>' | python3 -m ultracode.harness

There is deliberately no branch anywhere on which harness is calling — that is
H-UC-HARNESS's falsifier: if a harness-specific code path were required, the
hypothesis dies. Results are content-addressed (``result_hash``), so cross-harness
equality is a hash comparison, not an opinion.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from .adapters import DeterministicAdapter, HALAdapter
from .kernel import content_hash
from .topology import solve
from .workflows import WORKFLOWS

HAPPI_V = "happi/1.1"
CMD = "ultracode.solve"
MAX_ENVELOPE_BYTES = 1_000_000  # fail-closed size floor: refuse absurd payloads early

_ADAPTERS = {
    "deterministic": lambda wf: DeterministicAdapter(wf["reason"]),
    "hal": lambda wf: HALAdapter(),
}


def make_envelope(workflow: str, task: Any, adapter: str = "deterministic") -> dict:
    """A deterministic HAPPI envelope for an ultracode solve. Same inputs → same id."""
    return {
        "v": HAPPI_V,
        "id": "uc-" + content_hash([CMD, workflow, task, adapter])[:16],
        "cmd": CMD,
        "args": [task],
        "flags": {"workflow": workflow, "adapter": adapter},
    }


def parse_envelope(raw: Any) -> dict:
    """Validate an envelope; fail closed with plain-language errors."""
    if isinstance(raw, (bytes, str)):
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise ValueError(
                f"envelope too large (over {MAX_ENVELOPE_BYTES} bytes) — refusing it")
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"envelope is not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise ValueError("envelope must be a JSON object")
    if not str(raw.get("v", "")).startswith("happi/"):
        raise ValueError("envelope 'v' must be a happi/<x.y> version string")
    if raw.get("cmd") != CMD:
        raise ValueError(
            f"unknown cmd {raw.get('cmd')!r} — this runtime serves only {CMD!r}")
    flags = raw.get("flags") or {}
    if flags.get("workflow") not in WORKFLOWS:
        raise ValueError(
            f"unknown workflow {flags.get('workflow')!r} — known: {sorted(WORKFLOWS)}")
    if flags.get("adapter", "deterministic") not in _ADAPTERS:
        raise ValueError(
            f"unknown adapter {flags.get('adapter')!r} — known: {sorted(_ADAPTERS)}")
    if not raw.get("args"):
        raise ValueError("envelope needs args[0] — the task to solve")
    return raw


def execute_envelope(envelope: Any, cache: "dict | None" = None, adapter=None) -> dict:
    """THE single executor every harness shares (the H-UC-HARNESS construction).

    ``adapter`` may be injected (tests, live overrides); by default it resolves from
    the envelope's flags via the workflow registry. ``cache`` may be shared across
    calls to carry the kernel's idempotency through the seam.
    """
    env = parse_envelope(envelope)
    flags = env["flags"]
    workflow = WORKFLOWS[flags["workflow"]]
    if adapter is None:
        adapter = _ADAPTERS[flags.get("adapter", "deterministic")](workflow)
    result = solve(env["args"][0], adapter, {} if cache is None else cache,
                   workflow["cfg"]())
    return {
        "id": env["id"],
        "workflow": flags["workflow"],
        "topology": result.get("topology"),
        "output": result.get("output"),
        "result_hash": content_hash(result.get("output")),
        "calls": getattr(adapter, "calls", None),
    }


def _emit(stream, **event) -> None:
    stream.write(json.dumps(event, sort_keys=True, default=str) + "\n")


def main() -> int:
    """Headless harness: stdin envelope → NDJSON events → exit code (HAPPI contract).

    Timestamps are deterministic ordinals, not wall clock, so the full event stream
    is replay-stable — two identical envelopes yield byte-identical streams.
    """
    raw = sys.stdin.read()
    envelope_id = "unknown"
    try:
        env = parse_envelope(raw)
        envelope_id = env["id"]
        _emit(sys.stdout, v=HAPPI_V, id=envelope_id, type="started", ts=0)
        result = execute_envelope(env)
        _emit(sys.stdout, v=HAPPI_V, id=envelope_id, type="delta", ts=1, result=result)
        _emit(sys.stdout, v=HAPPI_V, id=envelope_id, type="completed", ts=2)
        return 0
    except Exception as exc:  # fail closed: any failure → error event + non-zero exit
        _emit(sys.stdout, v=HAPPI_V, id=envelope_id, type="error", ts=1,
              message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
