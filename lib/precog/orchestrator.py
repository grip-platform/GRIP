"""PRECOG orchestrator — pure-deterministic decision layer, zero LLM calls.

Coordinates fingerprint → cache-check → effort-selection → IDR-build → cache-write.
All LLM reasoning happens in the Opus turn itself, NOT here.

Wave B W2. Hypotheses bound: H706 (chain-integrity + replay-determinism).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from lib.precog.effort_selector import EffortTier, adaptive_effort_selector, score_components
from lib.precog.fingerprint import compute_fingerprint
from lib.precog.idr import PrecogIDR, build_idr, append_idr
from dataclasses import asdict
from lib.precog.replay import (
    _validate_decision_dict,
    cache_decision,
    emit_replay_idr,
    replay_lookup,
)
from lib.precog.state import write_current_idr  # W4 consumer pointer

# Sovereign default: route through the HAL gateway's non-Anthropic default.
# Explicit Anthropic callers can still pass provider_addresses themselves.
_DEFAULT_PROVIDER = "openai/gpt-5.5"

# AGORA-3 fix: surface holistic_synthesiser fallback events to stderr, but ONLY
# on the FIRST fallback per process (never flood — one log line is enough to
# alert; subsequent calls stay silent).
_HOLISTIC_FALLBACK_LOGGED = False

# W3 token-budget probe constant — char/token ratio for the input-cost estimator.
# 4 chars/token is the canonical English approximation used by tiktoken-style
# heuristics across the major providers. Used only by measure_input_tokens for
# the H707 probe; the orchestrator's actual fingerprint/decision pipeline is
# unaffected.
_CHARS_PER_TOKEN = 4


def measure_input_tokens(
    prompt: str,
    kernel_text: str = "",
    routing_hint: dict | None = None,
    auto_mode_hint: dict | None = None,
    budget: dict | None = None,
) -> int:
    """Estimate the input-token cost of an orchestrate() call's inputs.

    Pure deterministic — sum of character lengths across the inputs that
    cross the orchestrator boundary, divided by _CHARS_PER_TOKEN (=4) for
    a tiktoken-style approximation. Zero LLM calls.

    Used by the W3 token-budget smoke (H707 probe): median(input_tokens)
    across the 5 representative smoke prompts MUST stay <= 8000 to
    confirm H707 within budget.

    H707 falsifier: if median > 8000, the orchestrator step is itself
    consuming budget that defeats the "cheap-by-context-discipline"
    invariant — switch to kernel-digest-only inputs per the plan's
    uncertainty flag #1.
    """
    chars = len(prompt) + len(kernel_text)
    for hint in (routing_hint, auto_mode_hint, budget):
        if hint is not None:
            chars += len(json.dumps(hint, sort_keys=True, separators=(",", ":")))
    return chars // _CHARS_PER_TOKEN


def _kernel_digest(kernel_text: str) -> str:
    """sha256 of kernel_text. Tries holistic_synthesiser.payload_digest first; fails open.

    AGORA-3 fix: a one-shot stderr warning announces when the cross-module
    import fails (typically because holistic_synthesiser is unavailable in
    a stripped checkout). Subsequent calls stay silent — no flood.
    """
    global _HOLISTIC_FALLBACK_LOGGED
    if not kernel_text:
        return ""
    try:
        from lib.holistic_synthesiser import payload_digest  # type: ignore
        return payload_digest(kernel_text)
    except Exception as exc:
        if not _HOLISTIC_FALLBACK_LOGGED:
            print(
                f"[precog] holistic_synthesiser.payload_digest unavailable "
                f"({type(exc).__name__}); falling back to sha256 for kernel_digest",
                file=sys.stderr,
            )
            _HOLISTIC_FALLBACK_LOGGED = True
        return hashlib.sha256(kernel_text.encode()).hexdigest()


def _routing_hint_repr(routing_hint: dict | None) -> str | None:
    """Canonical JSON repr for fingerprinting; None stays None (distinct from empty dict)."""
    if routing_hint is None:
        return None
    return json.dumps(routing_hint, sort_keys=True, separators=(",", ":"))


def _build_rationale(tier: EffortTier, pattern: str, kernel_text: str, prompt: str) -> str:
    """Short (<= 200 char) templated rationale string — no LLM.

    AGORA-4 fix: uses public `score_components` helper instead of importing
    private `_novelty_score` / `_CLAIM_KEYWORDS` from effort_selector.
    """
    kernel_keys = set(kernel_text.lower().split()) if kernel_text else set()
    components = score_components(prompt, kernel_keys)
    raw = (
        f"tier={tier.value} · pattern={pattern} · "
        f"novel={components['novelty']:.2f} · "
        f"claim_density={components['claim_density']}"
    )
    return raw[:200]


def _extract_section_addresses(section: object) -> set[str]:
    """Return every provider-address string found in one catalogue section.

    Canonical catalogue shape is ``section: list[dict[{"address": "...", ...}]]``
    (verified against ``~/.hal/config/model_catalog.yaml`` on 2026-05-24).
    The pre-fix parser only handled ``list[str]`` and ``dict[str, _]`` — for
    ``list[dict]`` it stringified each dict, so every canonical address was
    reported missing. This helper reads the ``address`` field when entries are
    dicts; the ``list[str]`` and ``dict[str, _]`` branches are kept for
    forward-compat with future catalogue shapes.
    """
    if isinstance(section, list):
        return {_entry_address(e) for e in section}
    if isinstance(section, dict):
        return {str(k) for k in section}
    return set()


def _entry_address(entry: object) -> str:
    """Pull the canonical ``address`` field from a list entry, or fall back to ``str``."""
    if isinstance(entry, dict) and "address" in entry:
        return str(entry["address"])
    return str(entry)


def _load_catalogue_addresses() -> set[str] | None:
    """Read ``~/.hal/config/model_catalog.yaml`` and return its address set.

    Returns ``None`` when the catalogue is missing, the YAML library is
    unavailable, the file is malformed, or parsing raises. Never raises —
    the catalogue check is advisory only.
    """
    catalogue_path = Path.home() / ".hal" / "config" / "model_catalog.yaml"
    if not catalogue_path.exists():
        return None
    try:
        import yaml  # type: ignore
    except ImportError:
        print(
            "[precog] WARNING: 'yaml' library unavailable; "
            "skipping provider catalogue check",
            file=sys.stderr,
        )
        return None
    try:
        with open(catalogue_path) as fh:
            catalogue = yaml.safe_load(fh)
    except Exception:
        return None
    if not isinstance(catalogue, dict):
        return None  # malformed / partially-written YAML
    known: set[str] = set()
    for section in catalogue.values():
        known.update(_extract_section_addresses(section))
    return known


def _validate_addresses_against_catalogue(provider_addresses: list[str]) -> None:
    """Advisory catalogue check — warns to stderr, NEVER raises.

    Delegates the YAML I/O + parsing to ``_load_catalogue_addresses`` and the
    per-section address extraction to ``_extract_section_addresses``; this
    function just decides which addresses to warn about. Decomposed 2026-05-24
    to keep cyclomatic complexity within the trust-DEFAULT warn band (<15).
    """
    known = _load_catalogue_addresses()
    if known is None:
        return
    for addr in provider_addresses:
        if addr.startswith(("harness/", "hal/")):
            continue
        if addr not in known:
            print(
                f"[precog] WARNING: provider '{addr}' not found in "
                "model_catalog.yaml",
                file=sys.stderr,
            )


def orchestrate(
    prompt: str,
    *,
    predecessor_idr: str | None = None,
    depth: int = 0,
    kernel_text: str = "",
    routing_hint: dict | None = None,
    auto_mode_hint: dict | None = None,
    budget: dict | None = None,
    hyp_backlog: int = 0,
    provider_addresses: list[str] | None = None,
    chain_path: Path | None = None,
    cache_dir: Path | None = None,
    cache_ttl_seconds: int = 24 * 3600,
) -> PrecogIDR:
    """Pure-deterministic precog orchestration pass.

    Returns a PrecogIDR (either fresh decision or replay). Zero LLM calls.
    Fail-open on every optional input: missing kernel, missing routing_hint,
    missing budget all produce a valid IDR with safe defaults.
    """
    # 1. Kernel digest
    digest = _kernel_digest(kernel_text)

    # 2. Routing-hint repr for fingerprint (None != "" by design)
    hint_repr = _routing_hint_repr(routing_hint)

    # 3. Fingerprint
    fingerprint = compute_fingerprint(prompt, digest, hint_repr)

    # 4. Cache check — AGORA-2 fix: gate replay emission on decision schema
    # validity (rejects shape-drift across TTL windows; falls through to a
    # fresh build if the cached payload doesn't match the current schema).
    cached = replay_lookup(fingerprint, ttl_seconds=cache_ttl_seconds, cache_dir=cache_dir)
    if cached is not None and _validate_decision_dict(cached.get("decision")):
        replay = emit_replay_idr(
            prompt=prompt,
            fingerprint=fingerprint,
            cached_payload=cached,
            predecessor_idr=predecessor_idr,
            depth=depth,
        )
        append_idr(replay, path=chain_path)
        # W4: pointer file so consumers (router, adapter) can read the latest
        # emitted IDR without traversing the chain. Fail-open: write errors
        # never break dispatch (state.py swallows OSError internally).
        write_current_idr(asdict(replay))
        return replay

    # 5. Effort tier
    kernel_keys = set(kernel_text.lower().split()) if kernel_text else set()
    tier: EffortTier = adaptive_effort_selector(
        prompt, kernel_keys, routing_hint or {}, budget or {}, hyp_backlog
    )

    # 6. Decision dict
    resolved_providers = provider_addresses if provider_addresses is not None else [_DEFAULT_PROVIDER]
    if resolved_providers:
        _validate_addresses_against_catalogue(resolved_providers)

    pattern = (routing_hint or {}).get("recommended_pattern", "Focused")
    decision: dict[str, Any] = {
        "provider_addresses": resolved_providers,
        "fallback_chain": [],          # deferred W3
        "effort_tier": tier.value,
        "downstream_shape": pattern,
        "dispatch_depth": depth,
        "memory_keys_loaded": [],       # deferred KONO integration
        "skill_overlay": [],            # deferred
        "rationale_digest": _build_rationale(tier, pattern, kernel_text, prompt),
    }

    # 7. Build inputs provenance
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
    # C1 sentinel: "<no-hint>" when routing_hint is None, so downstream
    # consumers never receive an un-typed None. The SHA-256 of "<no-hint>"
    # is deterministic and distinguishable from the SHA of any real hint.
    if hint_repr is not None:
        routing_sha = hashlib.sha256(hint_repr.encode()).hexdigest()
    else:
        routing_sha = "<no-hint>"
    inputs: dict[str, Any] = {
        "prompt_sha256": prompt_sha,
        "kernel_digest_sha256": digest,
        "routing_hint_sha256": routing_sha,
        "budget_state": budget,
        "hyp_backlog": hyp_backlog,
    }

    # 8. Build + append IDR
    idr = build_idr(
        prompt, fingerprint, decision,
        predecessor_idr=predecessor_idr,
        depth=depth,
        kind="precog-decision",
        inputs=inputs,
    )

    # 9. Cache + persist + W4 pointer
    cache_decision(fingerprint, decision, original_idr_id=idr.id, cache_dir=cache_dir)
    append_idr(idr, path=chain_path)
    write_current_idr(asdict(idr))  # W4 consumer pointer (fail-open inside)
    return idr
