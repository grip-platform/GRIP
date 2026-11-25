#!/usr/bin/env python3
"""Active LLM tracker — runtime state file the statusline reads.

The statusline can render any model HAL routes to (Anthropic, xAI/Grok,
Google/Gemma, Ollama/local, Groq, etc.) by reading the JSON this module
writes. HAL's router can call into ``set_active()`` after every route, and
the operator can also manage state manually:

    python3 -m lib.active_llm set xai/grok-4.20-0309-non-reasoning
    python3 -m lib.active_llm set ollama/gemma4:26b
    python3 -m lib.active_llm clear
    python3 -m lib.active_llm show
    python3 -m lib.active_llm once xai/grok-4.20-0309-non-reasoning   # one-shot then revert
    python3 -m lib.active_llm undo               # pop previous off stack
    python3 -m lib.active_llm stack              # show marker history

State file: ~/.claude/state/session/active-llm.json (global — cold-start default only)
            ${TMPDIR}/grip-session-active-llm-${SESSION_KEY}.json (per-session; authoritative)
Catalogue:  ~/.hal/config/model_catalog.yaml (tier + context window)

The shell statusline never imports this module — it reads the JSON directly.
This module is the WRITER; the JSON is the contract.

Per-session isolation
---------------------
The CLI (``__main__``) ALWAYS resolves and passes the session key so every
interactive ``/llm`` write hits the per-session file — the same path the
statusline reads (``statusline-command.sh:303``).  ``GLOBAL_STATE_FILE`` is a
cold-start default only; interactive ``/llm`` NEVER writes to it.

Session-key resolution order (``_resolve_cc_pid``):
  1. ``CLAUDE_CODE_SESSION_ID`` env — stable, unique per CC session (preferred).
  2. ``GRIP_CC_PID`` env — explicit override (HAL server or scripts).
  3. Grandparent PID via ``ps`` — interactive fallback.
  4. None — fall back to GLOBAL_STATE_FILE (cold start, no session).

Pass ``cc_pid`` to ``set_active``, ``set_council``, ``clear_active``, or
``show_active`` to operate on a session-scoped file rather than the global
one.  When ``cc_pid`` is omitted, ``_resolve_cc_pid()`` auto-detects the
session key.

History stack
-------------
Every ``set_active`` / ``set_council`` call captures the prior marker as
``previous`` in the new payload (bounded depth ≤ 5).  ``undo_active``
pops one entry.  ``set_active_once`` / ``consume_once_marker`` provide a
use-once "temporary override then revert" pattern for HAL callers.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
HAL_HOME = Path(os.environ.get("HAL_HOME", Path.home() / ".hal"))

# Global state file — back-compat alias kept for callers that import STATE_FILE.
GLOBAL_STATE_FILE = CLAUDE_DIR / "state" / "session" / "active-llm.json"
STATE_FILE = GLOBAL_STATE_FILE  # back-compat alias — DO NOT REMOVE

CATALOGUE = HAL_HOME / "config" / "model_catalog.yaml"

DEFAULT_TTL_SECONDS = 1800  # 30 minutes
# `pin` writes this instead of DEFAULT_TTL_SECONDS: a session-lifetime
# marker that does not silently lapse mid-session. NOT a new "no-expiry"
# schema field — it reuses the existing ``ttl_seconds`` so every
# downstream staleness check (``ts + ttl_seconds < now``: the statusline,
# HAL ``_api_query``, the P3-W2 ``_active_marker`` gate) keeps working
# unchanged (Rule 21 — one store, no schema change). 100 years >> any
# real session, so a pinned marker stays until an explicit ``/llm clear``
# or ``/llm undo``, never a silent 30-minute revert.
SESSION_PIN_TTL_SECONDS = 100 * 365 * 24 * 3600
_HISTORY_MAX_DEPTH = 5

# Tier alias defaults — what `cheap` / `local` / `frontier` map to.
# frontier -> deepseek-v4-pro: in-catalogue (tier: frontier), Pool-B per
# rules/pool-a-offload-routing.md, judge-best speaker in both recall design
# councils (seals 8acc9c14, bf070637). The previous xai/grok-3 was dead on
# BOTH ends — absent from the catalogue AND retired at the provider
# (api.x.ai/v1/models live-probe 2026-07-17 lists grok-4.20/4.3/4.5 only).
TIER_DEFAULTS: dict[str, str] = {
    "cheap":    "groq/openai/gpt-oss-120b",
    "local":    "ollama/gemma4:26b",
    "frontier": "deepseek/deepseek-v4-pro",  # legacy neutral; /llm frontier now resolves via frontier_default()
}

# /frontier — harness-aware frontier seat (V>> 2026-08-26).
# Rule: the frontier seat must never share a vendor with the driving harness
# (producer != verifier — rules/exogenous-anchor-law.md).
#   - Claude Code harness (or an Anthropic active-LLM pin) -> moonshot/kimi-k3
#     (council verdict 2026-08-26: GPT-5.6-sol AND deepseek-chat independently
#     picked Kimi K3 over Gemini Pro — vendor-independence (Moonshot is the
#     5th distinct vendor), near-frontier reasoning, lower cost, no judge-
#     family collision, no DeepSeek double-count).
#   - any other harness (Pi / Codex / OpenCode / Aider / plain shell, "like
#     now") -> anthropic/claude-opus-5 via CCH (the proven Claude-Premium
#     subscription path; claude -p --model claude-opus-5 live-verified
#     2026-08-26).
FRONTIER_CLAUDE = "anthropic/claude-opus-5"
FRONTIER_NON_ANTHROPIC = "moonshot/kimi-k3"


def _is_anthropic_address(address: str) -> bool:
    return address.startswith(("anthropic", "claude"))


def frontier_default() -> str:
    """The harness-aware /frontier default address.

    Detection order: (1) this session's active-LLM state (authoritative),
    (2) CLAUDE_CODE_SESSION_ID (present in every Claude Code session),
    (3) anything else counts as a non-Claude harness -> frontier Claude.
    """
    state = _read_state(_state_file(_session_key()))
    address = (state or {}).get("address", "")
    if address:
        return FRONTIER_NON_ANTHROPIC if _is_anthropic_address(address) else FRONTIER_CLAUDE
    if os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return FRONTIER_NON_ANTHROPIC
    return FRONTIER_CLAUDE

# Display-name heuristics.  Match order matters — more specific patterns first.
DISPLAY_RULES: list[tuple[str, str]] = [
    # Anthropic — Claude 5 frontier generation first (fable/mythos ids carry
    # no opus/sonnet substring, so without these rows _resolve_display falls
    # through to the title-case fallback and the statusline renders
    # "Claude Fable 5" instead of "Fable"; found 2026-08-25).
    (r"fable",                         "Fable"),
    (r"mythos",                        "Mythos"),
    (r"opus",                          "Opus"),
    (r"sonnet",                        "Sonnet"),
    (r"haiku",                         "Haiku"),
    # xAI — grok-3 / grok-3-mini / grok-4-0709 / grok-4-fast-non-reasoning /
    # grok-code-fast-1 all retired at the provider (live-probe 2026-09-15);
    # only the 4.20 / 4.x lineup remains.
    # 2026-11-24 (dogfood-domain family 5): without these two rows,
    # grok-4.20-0309-reasoning and grok-4.20-0309-non-reasoning both fell
    # through to the generic "grok-4\.20" rule below and rendered as the
    # identical "Grok 4.20" label, losing the one distinction that actually
    # changes behaviour (always-on thinking vs none). Non-reasoning is listed
    # first only because it is checked first here too -- the TOKEN SET match
    # in grip_harness_family is unrelated to this list's own ordering.
    (r"grok-4\.20-0309-non-reasoning", "Grok 4.20 Non-Reasoning"),
    (r"grok-4\.20-0309-reasoning",     "Grok 4.20 Reasoning"),
    (r"grok-4\.7",                     "Grok 4.7"),
    (r"grok-4\.20",                    "Grok 4.20"),
    (r"grok-4",                        "Grok 4"),
    (r"grok",                          "Grok"),
    # OpenAI. Astra first, ahead of any future generic gpt- rule. The 5.x tiers
    # still fall through to title-casing and render as "Gpt 5.6 Sol"; that is a
    # separate pre-existing defect, filed rather than widened into this change.
    # GPT-6.2 Astra/Sol (2026-11-24) checked ahead of the bare gpt-6-astra rule:
    # re.search is unanchored, so without their own rows they would still match
    # "gpt-6-astra" only if that literal substring occurred in "gpt-6.2-astra",
    # which it does not (the dot breaks it) -- these rows exist so the switcher
    # does not title-case them to "Gpt 6.2 Astra" instead.
    (r"gpt-6\.2-astra",                 "GPT-6.2 Astra"),
    (r"gpt-6\.2-sol",                   "GPT-6.2 Sol"),
    (r"gpt-6-astra",                   "GPT-6 Astra"),
    (r"o3-mini",                       "o3-mini"),
    (r"o3",                            "o3"),
    (r"gpt-4o-mini",                   "GPT-4o Mini"),
    (r"gpt-4o",                        "GPT-4o"),
    (r"gpt-4",                         "GPT-4"),
    # Google (cloud + open)
    (r"gemini-4-carbon",                "Gemini 4 Carbon"),
    (r"gemini-4-hydrogen",              "Gemini 4 Hydrogen"),
    (r"gemini-4-argon",                "Gemini 4 Argon"),
    (r"gemini-3.*pro",                 "Gemini 3 Pro"),
    (r"gemini-2\.5-flash",             "Gemini Flash"),
    (r"gemini-2\.0-flash",             "Gemini Flash"),
    (r"gemma-?4.*e4b",                 "Gemma 4 e4b"),
    (r"gemma-?4.*26b",                 "Gemma 4"),
    (r"gemma-?4.*27b",                 "Gemma 4 27B"),
    (r"gemma-?4.*31b",                 "Gemma 4 31B"),
    (r"gemma-?4",                      "Gemma 4"),
    (r"gemma-?3",                      "Gemma 3"),
    # DeepSeek — deepseek-chat / deepseek-reasoner / deepseek-v3.2 retired
    # 2026-08-30; only the v4 lineup remains.
    (r"deepseek-v4",                   "DeepSeek V4"),
    # Qwen
    (r"qwen4-max",                     "Qwen4 Max"),
    (r"qwen3\.8-flash",                "Qwen 3.8 Flash"),
    (r"qwen3\.6",                      "Qwen 3.6"),
    (r"qwen3\.5-flash",                "Qwen Flash"),
    (r"qwen3\.5-plus",                 "Qwen Plus"),
    (r"gpt-oss-120b",                  "GPT-OSS 120B"),
    (r"qwen",                          "Qwen"),
    # Llama (Meta)
    (r"llama-4-maverick",              "Llama 4 Maverick"),
    (r"llama-4-scout",                 "Llama 4 Scout"),
    (r"llama-3\.3",                    "Llama 3.3"),
    (r"llama",                         "Llama"),
    # Moonshot / Zhipu / Mistral
    # (moonshot-v1-* retired 2026-08-31; every live Moonshot id carries "kimi")
    # K3.1 Pro first: the generic rule would label it plain "Kimi", hiding which
    # Kimi answered once it is trialled against K3 for a council seat.
    (r"kimi-k3\.1-pro-highspeed",      "Kimi K3.1 Pro Highspeed"),
    (r"kimi-k3\.1-pro",                "Kimi K3.1 Pro"),
    (r"kimi",                          "Kimi"),
    # GLM-5.4, GLM-5.3-Flash and GLM-5.3 first: the generic "glm-5" rule below would
    # otherwise match inside all three (re.search semantics), labelling each plain
    # "GLM 5". glm-5.3-flash is listed before glm-5.3 so the bare pattern does not
    # also match inside the flash variant's id.
    (r"glm-5\.4",                      "GLM 5.4"),
    (r"glm-5\.3-flash",                "GLM 5.3 Flash"),
    (r"glm-5\.3",                      "GLM 5.3"),
    (r"glm-5\.1",                      "GLM 5.1"),
    (r"glm-5",                         "GLM 5"),
    (r"glm-4\.7",                      "GLM 4.7"),
    (r"glm-4\.6",                      "GLM 4.6"),
    (r"glm-4\.5-air",                  "GLM Air"),
    (r"glm-4\.5",                      "GLM 4.5"),
    (r"glm",                           "GLM"),
    (r"mistral-large-4",               "Mistral Large 4"),
    (r"mistral-small",                 "Mistral Small"),
    (r"mistral",                       "Mistral"),
    # Local meta-providers (auto-discovery)
    (r"^auto$",                        "auto"),
]

PROVIDER_LABELS: dict[str, str] = {
    "anthropic":  "",
    "xai":        "",
    "openai":     "",
    "gemini":     "",
    "deepseek":   "",
    "qwen":       "",
    "groq":       "",
    "moonshot":   "",
    "zhipu":      "",
    "mistral":    "",
    "openrouter": "OR",
    "ollama":     "local",
    "lmstudio":   "local",
    "vllm":       "local",
    "mlx_vlm":    "local",
    "lemonade":   "local",
    "soruxgpt":   "OR",
    "vulavula":   "VV",
}


# ---------------------------------------------------------------------------
# Per-session file resolution
# ---------------------------------------------------------------------------

def _resolve_cc_pid() -> Optional[str]:
    """Return the session key for per-session file naming.

    Priority:
    1. ``CLAUDE_CODE_SESSION_ID`` env — the stable CC session identifier
       present in every CC session environment; unique, never reused across
       sessions.
    2. ``GRIP_CC_PID`` env var (explicit override, e.g. HAL server passing
       session_id).
    3. Grandparent PID via ``ps`` (interactive fallback when CLAUDE_CODE_SESSION_ID
       is not set, e.g. calling the module from a plain shell outside CC).
    4. None — fall back to GLOBAL_STATE_FILE.
    """
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if session_id:
        return session_id
    pid_env = os.environ.get("GRIP_CC_PID", "").strip()
    if pid_env:
        return pid_env
    try:
        ppid = os.getppid()
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(ppid)],
            capture_output=True, text=True, timeout=2,
        )
        grandparent = result.stdout.strip()
        return grandparent if grandparent and grandparent != "1" else None
    except Exception:
        return None


def _state_file(cc_pid: Optional[str] = None) -> Path:
    """Return the appropriate state file path for this session.

    If ``cc_pid`` is provided, or auto-detected via ``_resolve_cc_pid()``,
    returns a per-session temp file.  Falls back to ``GLOBAL_STATE_FILE``.
    """
    pid = cc_pid or _resolve_cc_pid()
    if pid:
        tmp = Path(os.environ.get("GRIP_TMP_DIR", os.environ.get("TMPDIR", "/tmp")))
        return tmp / f"grip-session-active-llm-{pid}.json"
    return GLOBAL_STATE_FILE


# ---------------------------------------------------------------------------
# History stack helpers
# ---------------------------------------------------------------------------

def _strip_previous(payload: dict) -> dict:
    """Return a copy of payload without its 'previous' field."""
    return {k: v for k, v in payload.items() if k != "previous"}


def _truncate_stack(payload: dict, max_depth: int = _HISTORY_MAX_DEPTH) -> dict:
    """Return a new payload with the 'previous' chain capped at max_depth.

    Does NOT mutate the input dict.  The top-level payload counts as depth 1,
    so the chain of 'previous' entries is capped at (max_depth - 1) nodes.
    """
    result = dict(payload)
    node = result
    depth = 1
    while "previous" in node and node["previous"] is not None:
        if depth >= max_depth:
            node["previous"] = None
            break
        # Recurse into a fresh copy of the child to avoid mutating input
        child = dict(node["previous"])
        node["previous"] = child
        node = child
        depth += 1
    return result


def _read_state(path: Path) -> Optional[dict]:
    """Read and parse a state file; return None on any error."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Catalogue helpers (unchanged from original)
# ---------------------------------------------------------------------------

def _resolve_display(provider: str, model: str) -> str:
    """Compute a plain-English display name from provider + model."""
    bare = model.lower()
    label = bare
    for pattern, name in DISPLAY_RULES:
        if re.search(pattern, bare):
            label = name
            break
    else:
        label = bare.split("/")[-1].split(":")[0]
        label = re.sub(r"[-_]", " ", label).title()

    suffix = PROVIDER_LABELS.get(provider, "")
    if suffix:
        return f"{label} ({suffix})"
    return label


def _load_catalogue() -> dict[str, dict]:
    """Parse model_catalog.yaml without requiring PyYAML."""
    out: dict[str, dict] = {}
    if not CATALOGUE.exists():
        return out

    current: dict = {}
    in_list = False
    for line in CATALOGUE.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "models:":
            in_list = True
            continue
        if not in_list:
            continue
        if stripped.startswith("- address:"):
            if current.get("address"):
                out[current["address"]] = current
            current = {"address": stripped.split(":", 1)[1].strip()}
            continue
        if ":" in stripped and not stripped.startswith("-"):
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key in ("tier", "context_window", "modality", "paid"):
                if key == "context_window":
                    try:
                        val = int(val)
                    except ValueError:
                        continue
                if key == "paid":
                    val = val.lower() == "true"
                current[key] = val
    if current.get("address"):
        out[current["address"]] = current
    return out


def _resolve_entry(address: str, catalogue: dict[str, dict]) -> dict:
    """Resolve display/tier/context_window for a single provider/model.

    Raises ``SystemExit`` (non-zero) if:
    - ``address`` does not contain a '/' separator.
    - ``address`` is not present in the catalogue (tier would be "unknown"),
      i.e. the address is unresolved / a typo.  This is a hard-error so the
      CLI never writes a success marker for an address the catalogue does not
      recognise (honest-model-labels, #2932).
    """
    if "/" not in address:
        raise SystemExit(
            f"error: address must be 'provider/model', got: {address!r}\n"
            "Example: xai/grok-4.20-0309-non-reasoning, ollama/gemma4:26b, anthropic/claude-opus-4-7"
        )
    provider, model = address.split("/", 1)
    cat = catalogue.get(address)
    if cat is None and catalogue:
        # Catalogue is present but the address is absent → typo / wrong name.
        # Hard-error before any write so the echo cannot lie.
        raise SystemExit(
            f"error: '{address}' is not in the model catalogue.\n"
            f"Run '/llm list' to see available addresses.\n"
            f"(Catalogue: {CATALOGUE})"
        )
    cat = cat or {}
    return {
        "provider": provider,
        "model": model,
        "address": address,
        "display": _resolve_display(provider, model),
        "tier": cat.get("tier", "unknown"),
        "context_window": int(cat.get("context_window", 0) or 0),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _attach_prior(payload: dict, prior: Optional[dict]) -> dict:
    """Attach previous marker (preserving its chain) and truncate to max depth."""
    if prior is not None:
        payload["previous"] = prior
    return _truncate_stack(payload)


def _commit(sf: Path, payload: dict) -> dict:
    """Read prior, attach as previous, cap stack, write — single canonical write path."""
    result = _attach_prior(payload, _read_state(sf))
    _write_state(sf, result)
    return result


def _solo_payload(
    address: str,
    mode: str,
    display: Optional[str],
    ttl_seconds: int,
    **extra,
) -> dict:
    """Build a solo-shape payload (used by set_active and set_active_once).

    Resolves the entry from the catalogue, applies an optional display override,
    and merges any extra fields (e.g. revert_after_consumes for solo-once,
    ``reason`` for the *why* this model was selected). None-valued extras are
    dropped so an absent ``reason`` never writes a literal ``null`` into the
    marker (keeps the payload the statusline reads clean).
    """
    entry = _resolve_entry(address, _load_catalogue())
    if display:
        entry["display"] = display
    return {
        "mode": mode,
        **entry,
        "ts": int(time.time()),
        "ttl_seconds": ttl_seconds,
        **{k: v for k, v in extra.items() if v is not None},
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_active(
    address: str,
    *,
    display: Optional[str] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    cc_pid: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    """Mark a single provider/model as active (solo mode).

    Captures the prior marker as ``previous`` (stack depth capped at 5).
    Pass ``cc_pid`` to write to a per-session file rather than the global one.
    ``reason`` is a short human string for *why* this model is active (e.g.
    ``"pin"``, ``"fallback: groq→gemini"``) surfaced by the status line.
    """
    return _commit(
        _state_file(cc_pid),
        _solo_payload(address, "solo", display, ttl_seconds, reason=reason),
    )


def set_council(
    addresses: list[str],
    *,
    mode: str = "council",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    cc_pid: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    """Mark several models as concurrently active (council / MoE / fallback chain).

    Captures the prior marker as ``previous`` (stack depth capped at 5).
    Pass ``cc_pid`` for per-session isolation. ``reason`` is a short human
    string for *why* this set is active (e.g. ``"council (3 seats)"``).
    """
    if not addresses:
        raise SystemExit("provide at least one provider/model address")
    if mode not in ("council", "moe", "fallback"):
        raise SystemExit(f"mode must be council/moe/fallback, got: {mode!r}")

    members = [_resolve_entry(a, _load_catalogue()) for a in addresses]
    payload: dict = {
        "mode": mode, "members": members, "count": len(members),
        "ts": int(time.time()), "ttl_seconds": ttl_seconds,
    }
    if reason:
        payload["reason"] = reason
    return _commit(_state_file(cc_pid), payload)


def _unlink_marker(path: Path) -> bool:
    """Delete ``path`` if it is there. Returns True when a file was removed."""
    if path.exists():
        path.unlink()
        return True
    return False


def clear_active(cc_pid: Optional[str] = None) -> bool:
    """Remove the active LLM marker. Returns True if a file was deleted.

    Single-file semantics, deliberately: this is the API other callers import,
    and ``tests/test_active_llm_session_scoped.py`` pins that a session-scoped
    clear never touches ``GLOBAL_STATE_FILE``.  The CLI's "remove every
    override" behaviour lives in ``clear_all_active``.
    """
    return _unlink_marker(_state_file(cc_pid))


def clear_all_active(cc_pid: Optional[str] = None) -> list[Path]:
    """Remove every marker that overrides the model — per-session AND global.

    From inside a session ``_state_file`` always resolves to the per-session
    temp file, so ``GLOBAL_STATE_FILE`` is unreachable through it and a stale
    global pin sits there lying on the statusline with no in-session way to
    remove it.  ``GLOBAL_STATE_FILE`` is therefore named directly rather than
    reached via ``_state_file(None)``, which would auto-detect the session key
    and hand back the per-session file again.

    Returns the paths actually deleted, so the caller reports what happened
    instead of guessing.  Outside a session both candidates are the same path:
    the second pass finds nothing and adds nothing.
    """
    removed: list[Path] = []
    for path in (_state_file(cc_pid), GLOBAL_STATE_FILE):
        if _unlink_marker(path):
            removed.append(path)
    return removed


def show_active(cc_pid: Optional[str] = None) -> Optional[dict]:
    """Return the current active LLM marker payload, or None if absent/unreadable."""
    return _read_state(_state_file(cc_pid))


def set_active_once(
    address: str,
    *,
    display: Optional[str] = None,
    cc_pid: Optional[str] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    reason: Optional[str] = None,
) -> dict:
    """Write a one-shot marker that reverts to the previous model after one HAL call.

    The marker has ``mode="solo-once"`` and ``revert_after_consumes=1``.
    HAL callers should invoke ``consume_once_marker()`` after using this model
    to trigger the automatic revert.

    Returns the written payload.
    """
    return _commit(
        _state_file(cc_pid),
        _solo_payload(
            address, "solo-once", display, ttl_seconds,
            revert_after_consumes=1,
            reason=reason,
        ),
    )


def consume_once_marker(cc_pid: Optional[str] = None) -> Optional[dict]:
    """Consume one use of a solo-once marker and revert to the previous model.

    If the current marker is ``mode="solo-once"``:
    - Decrements ``revert_after_consumes``.
    - When it reaches 0: replaces marker with ``previous`` (or clears if None).
    - Returns the marker that was consumed (the model that was just used).

    If the marker is NOT a solo-once marker, returns it unchanged (no-op).
    If no marker exists, returns None.

    Intended for HAL router/moe to call after dispatching a once-model.
    Wiring HAL to call this is deferred to a follow-up PR; this API is the
    contract.
    """
    sf = _state_file(cc_pid)
    current = _read_state(sf)
    if current is None:
        return None
    if current.get("mode") != "solo-once":
        return current

    consumed = dict(current)
    remaining = current.get("revert_after_consumes", 1) - 1
    if remaining <= 0:
        previous = current.get("previous")
        if previous is not None:
            _write_state(sf, previous)
        else:
            if sf.exists():
                sf.unlink()
    else:
        current["revert_after_consumes"] = remaining
        _write_state(sf, current)
    return consumed


def undo_active(cc_pid: Optional[str] = None) -> Optional[dict]:
    """Pop the previous marker off the history stack and make it active.

    If a ``previous`` entry exists in the current marker, writes it as the
    new active marker and returns it.  If ``previous`` is absent or None,
    clears the marker file and returns None.
    """
    sf = _state_file(cc_pid)
    current = _read_state(sf)
    if current is None:
        return None

    previous = current.get("previous")
    if previous:
        _write_state(sf, previous)
        return previous
    else:
        if sf.exists():
            sf.unlink()
        return None


# ---------------------------------------------------------------------------
# Freshness helper
# ---------------------------------------------------------------------------

def _is_fresh(payload: dict) -> bool:
    ts = payload.get("ts", 0)
    ttl = payload.get("ttl_seconds", DEFAULT_TTL_SECONDS)
    return (time.time() - ts) < ttl


# ---------------------------------------------------------------------------
# CLI command handlers
# ---------------------------------------------------------------------------

def _session_key() -> Optional[str]:
    """Resolve the current session key for CLI use.

    Always called by interactive CLI commands so writes target the per-session
    file — never GLOBAL_STATE_FILE.  Returns None only when no session key is
    detectable (cold start outside CC), in which case callers fall back to
    GLOBAL_STATE_FILE via ``_state_file(None)``.
    """
    return _resolve_cc_pid()


def _readback_check(sf: Path, requested_address: str) -> int:
    """Read back the per-session file and verify the persisted address matches.

    Prints the persisted address.  Returns 0 on match, 1 on mismatch (honest
    echo: the success message must never lie — #2932).
    """
    persisted = _read_state(sf)
    persisted_addr = persisted.get("address") if persisted else None
    if persisted_addr != requested_address:
        print(
            f"error: read-back mismatch — requested '{requested_address}' "
            f"but persisted file contains '{persisted_addr}'. Write may have failed.",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_set(args) -> int:
    key = _session_key()
    payload = set_active(args.address, display=args.display,
                         ttl_seconds=args.ttl, cc_pid=key, reason=args.reason)
    sf = _state_file(key)
    rc = _readback_check(sf, payload["address"])
    if rc != 0:
        return rc
    print(f"Active LLM set: {payload['display']} ({payload['address']}, tier={payload['tier']})")
    return 0


def _cmd_pin(args) -> int:
    """Like ``set`` but session-lifetime: no silent 30-min revert. Same
    store, same record shape — only the ``ttl_seconds`` differs (reuses
    ``set_active``; no parallel store, no schema change — Rule 21)."""
    key = _session_key()
    payload = set_active(args.address, display=args.display,
                         ttl_seconds=SESSION_PIN_TTL_SECONDS, cc_pid=key,
                         reason=args.reason)
    sf = _state_file(key)
    rc = _readback_check(sf, payload["address"])
    if rc != 0:
        return rc
    print(f"Active LLM pinned: {payload['display']} ({payload['address']}, "
          f"tier={payload['tier']}) — stays until /llm clear or /llm undo")
    return 0


def _cmd_council(args) -> int:
    key = _session_key()
    payload = set_council(args.addresses, mode=args.mode,
                          ttl_seconds=args.ttl, cc_pid=key, reason=args.reason)
    names = " · ".join(m["display"] for m in payload["members"])
    print(f"{args.mode.title()} active ({payload['count']} models): {names}")
    return 0


def _cmd_clear(_args) -> int:
    """``/llm clear`` means "stop overriding" — so it clears BOTH markers.

    An operator has one mental model of "the marker".  Clearing only the
    per-session one left a stale global pin in place and unreachable, so the
    statusline kept showing a model nothing was routing to.
    """
    removed = clear_all_active(_session_key())
    if not removed:
        print("Nothing to clear.")
        return 0
    for path in removed:
        print(f"Cleared: {path}")
    return 0


def _cmd_show(_args) -> int:
    key = _session_key()
    state = show_active(key)
    if not state:
        print("No active LLM marker. Statusline will show CC default.")
        return 0
    age = int(time.time() - state.get("ts", 0))
    fresh = "fresh" if _is_fresh(state) else "STALE"
    print(json.dumps(state, indent=2))
    print(f"\nFreshness: {fresh} (age={age}s, ttl={state.get('ttl_seconds')}s)")
    return 0


def _cmd_tier(args) -> int:
    """Set the active LLM to the alias default for this tier (cheap/local/frontier)."""
    key = _session_key()
    address = frontier_default() if args.cmd == "frontier" else TIER_DEFAULTS[args.cmd]
    payload = set_active(address, cc_pid=key)
    sf = _state_file(key)
    rc = _readback_check(sf, payload["address"])
    if rc != 0:
        return rc
    print(f"Active LLM set: {payload['display']} ({args.cmd} tier alias -> {address})")
    return 0


_TIER_ORDER = ["subscription", "frontier", "premium", "mid", "cheap", "local", "free", "unknown"]
_TIER_RANK = {tier: rank for rank, tier in enumerate(_TIER_ORDER)}


def _cmd_list(_args) -> int:
    """Print the catalogue grouped by tier — what's available to /llm set."""
    catalogue = _load_catalogue()
    if not catalogue:
        print("No catalogue found at ~/.hal/config/model_catalog.yaml")
        return 0
    items = sorted(
        ((entry.get("tier", "unknown"), addr) for addr, entry in catalogue.items()),
        key=lambda item: (_TIER_RANK.get(item[0], len(_TIER_ORDER)), item[1]),
    )
    current_tier = None
    for tier, addr in items:
        if tier != current_tier:
            print(f"\n{tier.upper()}")
            current_tier = tier
        print(f"  {addr}")
    return 0


def _cmd_once(args) -> int:
    """Set a one-shot marker that reverts after the next HAL call."""
    key = _session_key()
    payload = set_active_once(args.address, display=args.display,
                              ttl_seconds=args.ttl, cc_pid=key, reason=args.reason)
    prior = payload.get("previous")
    revert_to = prior.get("display", "CC default") if prior else "CC default"
    print(f"One-shot: {payload['display']} ({payload['address']}) — reverts to {revert_to} after next call")
    return 0


def _cmd_undo(_args) -> int:
    """Pop the previous marker off the history stack."""
    key = _session_key()
    result = undo_active(key)
    if result is None:
        print("Stack empty — cleared marker")
    else:
        addr = result.get("address", "unknown")
        display = result.get("display", addr)
        print(f"Reverted to {display} ({addr})")
    return 0


def _cmd_stack(_args) -> int:
    """Print the marker history stack (top to bottom, depth <= 5)."""
    key = _session_key()
    state = show_active(key)
    if not state:
        print("Stack empty — no active LLM marker.")
        return 0
    node: Optional[dict] = state
    depth = 0
    while node is not None:
        depth += 1
        mode = node.get("mode", "?")
        if mode in ("council", "moe", "fallback"):
            members = node.get("members", [])
            label = " · ".join(m.get("display", "?") for m in members)
            addr = f"[{mode}] {label}"
        else:
            addr = f"{node.get('display', '?')} ({node.get('address', '?')})"
        marker = " <- active" if depth == 1 else ""
        print(f"  {depth}. {addr}{marker}")
        node = node.get("previous")
    return 0


_DISPATCH = {
    "set":      _cmd_set,
    "pin":      _cmd_pin,
    "council":  _cmd_council,
    "clear":    _cmd_clear,
    "show":     _cmd_show,
    "cheap":    _cmd_tier,
    "local":    _cmd_tier,
    "frontier": _cmd_tier,
    "list":     _cmd_list,
    "once":     _cmd_once,
    "undo":     _cmd_undo,
    "stack":    _cmd_stack,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="active_llm", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_set = sub.add_parser("set", help="Mark a single provider/model as active")
    p_set.add_argument("address", help="provider/model (e.g. xai/grok-4.20-0309-non-reasoning)")
    p_set.add_argument("--display", help="Override the auto-resolved display name")
    p_set.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS,
                       help="Seconds until state is considered stale (default 1800)")
    p_set.add_argument("--reason", help="Short 'why' surfaced by the statusline (e.g. 'pin', 'trying X')")

    p_pin = sub.add_parser(
        "pin",
        help="Like set, but session-lifetime (no silent 30-min revert)")
    p_pin.add_argument("address", help="provider/model (e.g. moonshot/kimi-k2)")
    p_pin.add_argument("--display", help="Override the auto-resolved display name")
    p_pin.add_argument("--reason", help="Short 'why' surfaced by the statusline (e.g. 'pin')")
    # Deliberately NO --ttl: a pin is session-lifetime by definition;
    # use `set --ttl N` if you want a bounded marker.

    p_council = sub.add_parser(
        "council",
        help="Mark several models as concurrently active (council / MoE / fallback)",
    )
    p_council.add_argument("addresses", nargs="+",
                           help="provider/model list, e.g. xai/grok-4.20-0309-non-reasoning ollama/gemma4:26b")
    p_council.add_argument("--mode", default="council",
                           choices=["council", "moe", "fallback"])
    p_council.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS)
    p_council.add_argument("--reason", help="Short 'why' surfaced by the statusline (e.g. 'council (3 seats)')")

    sub.add_parser("clear",    help="Remove the active LLM marker")
    sub.add_parser("show",     help="Print current active LLM state")
    sub.add_parser("cheap",    help=f"Tier alias -> {TIER_DEFAULTS['cheap']}")
    sub.add_parser("local",    help=f"Tier alias -> {TIER_DEFAULTS['local']}")
    sub.add_parser("frontier", help=f"Harness-aware frontier: Claude outside Claude Code ({FRONTIER_CLAUDE}), Kimi K3 inside ({FRONTIER_NON_ANTHROPIC})")
    sub.add_parser("list",     help="Print catalogue grouped by tier")

    p_once = sub.add_parser("once", help="Use this model for the next HAL call only, then revert")
    p_once.add_argument("address", help="provider/model (e.g. xai/grok-4.20-0309-non-reasoning)")
    p_once.add_argument("--display", help="Override the auto-resolved display name")
    p_once.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS)
    p_once.add_argument("--reason", help="Short 'why' surfaced by the statusline (e.g. 'once')")

    sub.add_parser("undo",  help="Pop the previous marker off the history stack")
    sub.add_parser("stack", help="Show the marker history stack (depth <= 5)")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    handler = _DISPATCH.get(args.cmd)
    return handler(args) if handler else 1


if __name__ == "__main__":
    sys.exit(main())
