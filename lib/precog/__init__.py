"""PRECOG-IDR — precognitive orchestration head, depth-0 seed of the IDR chain.

Pilot W0-W3 (see plans/prompt-harness-opus-zesty-lightning.md).
Hypotheses H700-H705 registered 2026-05-23.
"""
from lib.precog.effort_selector import EffortTier, adaptive_effort_selector
from lib.precog.fingerprint import compute_fingerprint, normalise_prompt
from lib.precog.idr import PrecogIDR, build_idr, append_idr, read_idr_chain
from lib.precog.orchestrator import measure_input_tokens, orchestrate
from lib.precog.replay import (
    cache_decision,
    emit_replay_idr,
    replay_lookup,
)

__all__ = [
    "EffortTier", "adaptive_effort_selector",
    "compute_fingerprint", "normalise_prompt",
    "PrecogIDR", "build_idr", "append_idr", "read_idr_chain",
    "cache_decision", "emit_replay_idr", "replay_lookup",
    "orchestrate", "measure_input_tokens",
]
