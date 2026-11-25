"""GRIP <-> prompt-cache-keepalive shim, bound to HAL's Anthropic cache path.

Wires the *installed* ``prompt-cache-keepalive`` package (pip-installed into the
GRIP venv from CodeTonight-SA/prompt-cache-keepalive) to HAL's real
``apply_cache_control`` so a GRIP autonomous loop can keep a HAL Anthropic
prompt cache warm across an idle gap — e.g. while awaiting a background agent.
Without it, an idle > ~5 min expires the cached system+tools prefix and the
resume turn pays ``cache_creation`` (1.25x) instead of ``cache_read`` (0.10x).

Measured on HAL's anthropic_native path (H792): a single touch over a ~5k-token
cached prefix turns a ~6.3k-token-equiv resume re-write into a ~1.0k-equiv
warm read — net positive, scaling linearly with prefix size.

Syscall-doctrine: the transport (``post_body``) and ``apply_cache_control`` are
dependency-injected — this shim owns only the body build and the usage ->
TouchResult mapping. No secret handling here; the keepalive timing + spend bound
live in the published package. Provider-agnostic by construction.
"""
from __future__ import annotations

from typing import Callable, Optional

from prompt_cache_keepalive import (
    KeepaliveConfig,
    KeepaliveStats,
    PromptCacheKeepalive,
    TouchResult,
)

# Usage dict shape returned by post_body (mirrors Anthropic's usage fields).
Usage = dict
PostBody = Callable[[dict], Usage]


def _load_hal_apply_cache_control() -> Callable[[dict], dict]:
    """Lazily load HAL's real apply_cache_control from the sibling ~/.hal repo.

    Loaded standalone (registered in sys.modules so its frozen dataclass
    resolves) — no HAL package-import chain, no heavy transitive deps.
    """
    import importlib.util
    import os
    import sys

    path = os.path.expanduser("~/.hal/lib/hal/prompt_cache.py")
    spec = importlib.util.spec_from_file_location("hal_prompt_cache", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.apply_cache_control


def build_cache_touch(
    post_body: PostBody,
    *,
    system: str,
    model: str,
    messages: Optional[list] = None,
    apply_cache_control: Optional[Callable[[dict], dict]] = None,
) -> Callable[[], TouchResult]:
    """Return a touch_fn that re-POSTs the cache_control-marked prefix.

    ``post_body(body) -> usage`` is the injected transport (bind it to HAL's
    AnthropicProvider, a raw urllib POST, or a mock). ``apply_cache_control``
    defaults to HAL's real marker logic, loaded on first use.
    """
    mark = apply_cache_control or _load_hal_apply_cache_control()
    tail = messages or [{"role": "user", "content": "."}]

    def touch() -> TouchResult:
        body = {"model": model, "max_tokens": 1, "system": system, "messages": tail}
        mark(body)  # HAL's real logic: marks system/tools blocks ephemeral
        try:
            usage = post_body(body)
        except Exception as exc:  # noqa: BLE001 — fail soft; the loop stops on ok=False
            return TouchResult(ok=False, error=str(exc))
        return TouchResult(
            ok=True,
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    return touch


def keep_hal_cache_warm(
    post_body: PostBody,
    *,
    system: str,
    model: str,
    is_active: Callable[[], bool],
    config: Optional[KeepaliveConfig] = None,
    messages: Optional[list] = None,
    apply_cache_control: Optional[Callable[[dict], dict]] = None,
) -> KeepaliveStats:
    """Keep a HAL Anthropic prompt cache warm while ``is_active()`` holds.

    Thin composition: build the HAL-bound touch, hand it to the published
    PromptCacheKeepalive (which owns the timing + the spend cap), run it.
    """
    touch = build_cache_touch(
        post_body, system=system, model=model, messages=messages,
        apply_cache_control=apply_cache_control,
    )
    return PromptCacheKeepalive(touch, config=config or KeepaliveConfig()).run(is_active)


__all__ = ["build_cache_touch", "keep_hal_cache_warm"]
