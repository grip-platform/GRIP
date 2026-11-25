"""council_seat.py — shared seat-resolution helpers for GRIP's multi-model
councils.

Two councils need the identical logic (YSH — abstract it at the second
instance): extracted 2026-11 from lib/ultrajudge.py, which carried both
helpers below verbatim before this extraction (GRIP#9187). Used by:

- lib/ultrajudge.py — resolves each (role, model) seat's address before
  dispatch.
- lib/ultraread.py — resolves each ``--models`` label before dispatch. Before
  this fix every label was accepted and then discarded: ``_dispatch_real``
  called ``adapter.generate(prompt, ...)``, a method with no model parameter
  at all, so every seat answered down HAL's shared default route regardless
  of the label it was given (rules/declared-input-must-reach-the-actor.md).

``model_address`` maps a short label to a HAL ``provider/model`` address.
``json_object`` tolerantly extracts the JSON object a council prompt asks
each model to return.
"""
from __future__ import annotations

import json
import re

# Short council labels -> HAL provider/model addresses. The catalogue-resolved
# families keep the address current as models rotate; the fallbacks are the
# adapter's own known-good SKUs. "gpt-oss-120b" stays a literal groq address
# by design — the catalogue's rows are openrouter-routed, while groq is the
# deliberate fail-safe SKU (see hal_llm_adapter._DIRECT_PROVIDERS). It replaced
# "llama-3.3-70b" when Groq retired llama-3.3-70b-versatile on 2026-06-17; the
# label moved with the model so a council record never names a model it did not
# call.
FALLBACK_ADDRESSES: dict[str, str] = {
    "opus": "anthropic/claude-opus-5",
    "sonnet": "anthropic/claude-sonnet-5",
    "grok": "xai/grok-4.20-0309-non-reasoning",
    "gpt-oss-120b": "groq/openai/gpt-oss-120b",
    # Astra — OpenAI's frontier reasoning model, reached through the HAL gateway
    # on :4010 (the ladder has no openai rung; see hal_llm_adapter._gateway_call).
    "astra": "openai/gpt-6-astra",
    # Repointed from gemini-2.5-flash on 2026-11-24 (GRIP#9389): Google
    # auto-routes that id to gemini-3.5-flash-lite from 2026-12-01.
    "gemini": "gemini/gemini-3.5-flash-lite",
}
ADDRESS_FAMILIES: dict[str, str] = {
    "opus": "anthropic/claude-opus-",
    "sonnet": "anthropic/claude-sonnet-",
    "grok": "xai/grok-",
}

# Retired labels still accepted, mapped to the label that now names the seat, so
# an old invocation keeps working while every record prints the current label.
# "grok-3" called a newer Grok after Grok 3 retired (GRIP#9373), the same move
# "llama-3.3-70b" made when Groq retired its model.
LABEL_ALIASES: dict[str, str] = {"grok-3": "grok"}


def canonical_label(label: str) -> str:
    """The current name of a council label; any other label passes through."""
    return LABEL_ALIASES.get(label, label)


def _resolve_address_fn():
    """Import ``hal_model_resolver.resolve_address`` via a three-tier
    fallback — relative, then ``lib.``-absolute, then bare module name — so
    this module works both as a package import (``lib.council_seat``) and
    when a CLI that uses it is run as a file (``bin/grip-py
    lib/ultraread.py``, which puts ``lib/`` itself, not the repo root, on
    ``sys.path``, so neither the relative nor the ``lib.``-absolute form
    resolves and only the bare module name does). Returns ``None`` when none
    of the three succeed, so the caller can fall back to its literal.
    """
    try:
        from .hal_model_resolver import resolve_address
        return resolve_address
    except ImportError:
        pass
    try:
        from lib.hal_model_resolver import resolve_address
        return resolve_address
    except ImportError:
        pass
    try:
        from hal_model_resolver import resolve_address
        return resolve_address
    except ImportError:
        return None


def model_address(label: str) -> str:
    """Resolve a short council label to a HAL ``provider/model`` address.

    Catalogue-first via hal_model_resolver (never raises, falls back to the
    known-good literal); unknown labels pass through unchanged so an explicit
    ``--models provider/model`` address is honoured verbatim.
    """
    label = canonical_label(label)
    fallback = FALLBACK_ADDRESSES.get(label, label)
    family = ADDRESS_FAMILIES.get(label)
    if family is None:
        return fallback
    resolve_address = _resolve_address_fn()
    if resolve_address is None:
        return fallback
    return resolve_address(family, fallback)


def _strip_fence(text: str) -> str:
    """The reply with one leading/trailing ``` fence removed (with or without
    a language tag)."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s


def json_object(text: str) -> dict:
    """Tolerant JSON-object extraction from a model's raw reply.

    Strips one ``` fence, then decodes the first ``{`` that opens a complete
    JSON object, ignoring prose before and after it -- the shape every
    council prompt asks for but not every model honours. Decoding from each
    ``{`` in turn (``json.JSONDecoder.raw_decode``) also survives prose that
    itself contains braces, which slicing from the first ``{`` to the last
    ``}`` did not. A reply that is valid JSON but not an object (a bare list,
    say) gets the precise "not an object" message.

    Raises ``ValueError`` when nothing here parses as JSON at all, and
    ``ValueError`` when it parses but the value is not a dict.
    """
    s = _strip_fence(text)
    decoder = json.JSONDecoder()
    for brace in re.finditer(r"\{", s):
        try:
            value, _end = decoder.raw_decode(s, brace.start())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    try:
        parsed = json.loads(s)
    except ValueError:
        raise ValueError("no JSON object in model response") from None
    raise ValueError(f"parsed JSON is not an object: {type(parsed).__name__}")
