#!/usr/bin/env python3
"""Argv-driven Gentner structural-claim gate for the grip-channel bridge.

The Discord/WhatsApp bridge (``mcp-servers/grip-channel/src/python-gate.ts``)
needs to check a claimed structural analogy without ever building a shell
command from the (untrusted) claim text. This backend reads the claim from
``sys.argv[1]`` — never string-interpolated into a shell — and emits the same
JSON contract the TypeScript caller already parses::

    {"decision": "allow"|"reject", "reason": "...", "score": <float>}

It is the argv-passthrough replacement for the previous inline ``python3 -c``
program, closing the shell -> python command-injection path (CodeQL
js/command-line-injection alerts #77 and #78).

Fail-open contract: any internal error prints an ``allow`` verdict so the
bridge never blocks a reply on a gate failure (mirrors the TS ``fail open``
behaviour). Exit code is always 0 on this path so the caller's ``execFileSync``
receives parseable stdout rather than a thrown non-zero exit.
"""

import json
import sys
from pathlib import Path

# Self-sufficient import: add this file's own dir (lib/) to sys.path so
# `gentner_enforcer` resolves no matter how the CLI is invoked — direct file,
# `python -m`, symlink, or with a different sys.path[0]. Do NOT rely on the
# caller's PYTHONPATH or on Python's implicit script-dir insertion (the
# canonical GRIP shim, same pattern as lib/rsi_witness_publish.py).
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from gentner_enforcer import MappingProposal, MappingRelation, enforce  # noqa: E402


def _verdict(claim: str) -> dict:
    """Run the Gentner enforcer on ``claim`` and return the JSON-able verdict."""
    proposal = MappingProposal(
        source_domain="claim_source",
        target_domain="claim_target",
        source_elements=["source"],
        target_elements=["target"],
        relations=[MappingRelation("source", "target", "causal", claim)],
    )
    result = enforce(proposal)
    return {
        "decision": result.decision,
        "reason": result.reason,
        "score": result.score.total_score,
    }


# Upper bound on the claim length. The claim is untrusted; the enforcer runs
# regexes over it, so an unbounded input is a DoS vector. A structural analogy
# sentence is short — 10k chars is far above any legitimate claim.
_MAX_CLAIM_CHARS = 10_000


def main(argv: list[str]) -> int:
    """CLI entry: ``gentner_gate_cli.py <claim>`` -> JSON verdict on stdout."""
    claim = (argv[1] if len(argv) > 1 else "")[:_MAX_CLAIM_CHARS]
    try:
        verdict = _verdict(claim)
    except Exception as err:  # fail-open — never block the bridge on a gate error
        # The reason reaches a user-facing channel (autonomous-responder.ts
        # appends it to the reply), so it must NOT leak the exception type or
        # any environment detail. Log the type to stderr for server-side debug.
        print(f"[gentner_gate_cli] fail-open: {type(err).__name__}: {err}", file=sys.stderr)
        verdict = {
            "decision": "allow",
            "reason": "gate error — fail open",
            "score": 0.0,
        }
    print(json.dumps(verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
