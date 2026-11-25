#!/usr/bin/env python3
"""Install GRIP's HAL gateway as a DeepSeek Harness (DSH) provider route.

DSH's pi-ai adapter is mounted *dormant* by the base bundle: it registers zero
routes until a ``llm-pi-ai:`` section in ``$DSH_HOME/settings.yaml`` supplies
provider profiles (``packages/bundle/base/cordis.patch.yml``: "Which adapters
exist is composition; which providers run is the user's settings document").
This module writes that section, so DSH can reach every Pool-B provider HAL
already routes instead of billing the Anthropic weekly limit.

Two properties this module guarantees, both load-bearing:

**No secret is ever written.** ``apiKeyEnv`` carries the *name*
``HAL_GATEWAY_KEY``; DSH resolves that reference per request. The value is
supplied at run time from the macOS keychain by ``bin/grip-dsh``, never
serialised to disk. This is the same syscall-doctrine seam
``lib/grip_harness.py`` already uses for aider and Cursor.

**The merge cannot clobber.** ``settings.yaml`` is also written by DSH's own web
Models page, so this module replaces exactly two keys —
``llm-pi-ai.providers.hal`` and, when it is still dsh's onboarding default,
the keyless ``agent-default-model`` pointing at ``deepseek-official`` (see
:func:`merge_route`) — and returns *unchanged* when both already match, so a
re-run rewrites nothing at all (PyYAML cannot preserve comments, so not
writing is the only way to keep them).

Every model id below returned HTTP 200 through the gateway on 2026-08-19
(``moonshot/kimi-k3`` on 2026-09-07, when it replaced the retired ``kimi-k2.5``),
and each ``contextWindow`` is read from ``~/.hal/config/model_catalog.yaml``
rather than invented. The list is curated by hand on purpose: HAL's router sends any id
it does not recognise to the verbatim Anthropic relay
(``lib/hal/gateway_egress.py``), which is the exact spend this route exists to
avoid, so only explicit ``<provider>/<model>`` ids from HAL's routable cloud
providers may appear.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import yaml

#: The pi-ai plugin's settings namespace (``settingsNamespace('llm-pi-ai')``,
#: ``packages/llm/llm-pi-ai/src/index.ts:87``). Top-level key in settings.yaml.
NAMESPACE = "llm-pi-ai"

#: Route name. Unclaimed by the sibling adapter, whose provider id is
#: ``deepseek-official`` (``packages/llm/llm-deepseek/src/index.ts:47``).
ROUTE = "hal"

#: Credential *reference*, not a value. Matches GRIP's canonical
#: ``lib/grip_harness.py::HAL_GATEWAY_ENV_KEY``.
API_KEY_ENV = "HAL_GATEWAY_KEY"

#: HAL gateway, loopback only. Port per ``rules/port-registry.md``.
BASE_URL = "http://127.0.0.1:4010/v1"

#: One of exactly three protocols pi-ai accepts
#: (``packages/llm/llm-pi-ai/src/provider.ts:48``).
API_PROTOCOL = "openai-completions"

#: Output cap sent on every request. Not cosmetic, and not the value you get by
#: leaving it out: omitting ``maxTokens`` does NOT send "no cap", it sends the
#: route's ``defaultMaxTokens``, whose built-in default is ``DEFAULT_MAX_TOKENS
#: = 32_768`` (``packages/llm/llm-pi-ai/src/config.ts:41``). Observed
#: 2026-08-19: a live turn on ``openai/gpt-4o-mini`` was rejected with
#: "max_tokens is too large: 32768. This model supports at most 16384
#: completion tokens", so the built-in default breaks that model outright.
#: 16384 is the largest value every model on this route accepts, and is the
#: observed ceiling rather than a guess.
#:
#: Overridable with ``GRIP_DSH_MAX_TOKENS`` so a raised provider ceiling does not
#: need a code release to use (AGORA #4936 round 2, concern 2). The literal stays
#: the default rather than moving to a config file: it is a MEASURED ceiling for
#: this specific model set, so it belongs beside the measurement that produced
#: it. A non-numeric or non-positive value is ignored in favour of the measured
#: default — a cap silently set to garbage is worse than one that is merely out
#: of date, because every request then fails for a reason nothing explains.
def _resolve_max_tokens(env: "dict[str, str] | None" = None) -> int:
    """The output cap, honouring GRIP_DSH_MAX_TOKENS when it is a positive int."""
    import os
    raw = (os.environ if env is None else env).get("GRIP_DSH_MAX_TOKENS", "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 16384
    return value if value > 0 else 16384


DEFAULT_MAX_TOKENS = _resolve_max_tokens()

# (id, display name, contextWindow or None, accepts images)
# DeepSeek retired chat/reasoner upstream (2026-08-30: the API serves ONLY the
# v4 lineup — deepseek-flash / -pro), so the route lists v4 ids. deepseek-flash
# is now text+vision (the vendor renamed v4-flash and absorbed -vision-exp).
# contextWindow is None rather than invented for ids the model catalog does not
# yet carry — pi-ai's own default applies.
_MODELS: tuple[tuple[str, str, int | None, bool], ...] = (
    ("groq/openai/gpt-oss-120b", "GPT-OSS 120B (Groq)", 131072, False),
    ("deepseek/deepseek-flash", "DeepSeek Flash", None, True),
    ("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro", None, False),
    ("gemini/gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", 1048576, True),
    # Both windows from the vendor's model pages, read 2026-10-18: 3.1 Pro "Input
    # token limit" "1,048,576" (the 2M figure here belonged to a model that never
    # shipped); Argon "Context window (unified input and output token limit)"
    # "1,048,576", inputs text, image, video, audio and PDF.
    ("gemini/gemini-3.1-pro-preview", "Gemini 3.1 Pro", 1048576, True),
    ("gemini/gemini-4-argon", "Gemini 4 Argon", 1048576, True),
    ("qwen/qwen3.5-flash", "Qwen3.5 Flash", 128000, False),
    # kimi-k2.5 was retired upstream 2026-08-31; kimi-k3 replaced it here on
    # 2026-09-07 (HTTP 200 via :4010; ctx from ~/.hal/config/model_catalog.yaml;
    # image input per GET /v1/models supports_image_in).
    ("moonshot/kimi-k3", "Kimi K3", 1048576, True),
    ("xai/grok-4.20-0309-non-reasoning", "Grok 4.20 Non-Reasoning", None, False),
    ("openai/gpt-4o-mini", "GPT-4o mini", 128000, True),
)


def default_settings_path() -> Path:
    """``$DSH_HOME/settings.yaml``, defaulting to ``~/.dsh`` as DSH itself does."""
    import os

    home = os.environ.get("DSH_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".dsh") / "settings.yaml"


def hal_route() -> dict[str, Any]:
    """The ``hal`` provider profile. Pure — no I/O, no environment reads.

    ``models`` is mandatory here and not a stylistic choice: ``hal`` is not one
    of pi-ai's 37 builtin providers, so its installed catalog describes no
    models and ``resolveRouteModels`` raises "resolves no models"
    (``packages/llm/llm-pi-ai/src/catalog.ts:487``), which refuses the whole
    namespace section at boot.

    ``defaultMaxTokens`` is set rather than left to pi-ai's built-in 32768,
    which OpenAI rejects for ``gpt-4o-mini``; see :data:`DEFAULT_MAX_TOKENS`.
    ``modelOverrides`` is omitted because pi-ai refuses it beside a ``models``
    list.
    """
    models: list[dict[str, Any]] = []
    for model_id, name, ctx, vision in _MODELS:
        entry: dict[str, Any] = {"id": model_id, "name": name}
        if ctx is not None:
            entry["contextWindow"] = ctx
        if vision:
            # A hand-declared route is text-only by default, so an attached
            # image is refused locally before it is ever sent. This is a
            # capability *claim* — unverified against the endpoint.
            entry["input"] = ["text", "image"]
        models.append(entry)
    return {
        "displayName": "HAL Gateway",
        "apiKeyEnv": API_KEY_ENV,
        "api": API_PROTOCOL,
        "baseURL": BASE_URL,
        "defaultMaxTokens": DEFAULT_MAX_TOKENS,
        "models": models,
    }


def merge_route(existing: Any) -> tuple[dict[str, Any], bool]:
    """Fold the ``hal`` route into ``existing``, preserving every other key.

    :param existing: the parsed settings document, or ``None`` when absent. A
        non-mapping document is a corrupt settings file, not something to
        overwrite silently.
    :returns: ``(merged_document, changed)``. ``changed`` is ``False`` when the
        route is already byte-equal, so the caller can skip the write and keep
        the operator's comments and formatting intact.
    :raises TypeError: if the document, its namespace, or its ``providers`` key
        is present but not a mapping.
    """
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise TypeError(f"settings document must be a mapping, got {type(existing).__name__}")

    merged = copy.deepcopy(existing)
    section = merged.setdefault(NAMESPACE, {})
    if not isinstance(section, dict):
        raise TypeError(f"{NAMESPACE!r} must be a mapping, got {type(section).__name__}")
    providers = section.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise TypeError(f"{NAMESPACE}.providers must be a mapping, got {type(providers).__name__}")

    route = hal_route()
    changed = providers.get(ROUTE) != route
    providers[ROUTE] = route

    # The dsh onboarding default routes through ``deepseek-official`` — dsh's
    # NATIVE provider, which needs a DEEPSEEK_API_KEY GRIP never stores (all
    # Pool-B traffic runs through HAL). That default fails every turn with
    # MISSING_CREDENTIAL. Migrate it to the hal route — and ONLY the onboarding
    # default; an operator's deliberate choice is never overwritten.
    # ``reasoningEffort`` is dropped: the onboarding value (``max``) is a
    # deepseek-official capability the pi-ai ``hal`` route does not declare,
    # and dsh refuses the whole turn locally (UNSUPPORTED_REASONING_EFFORT)
    # rather than fall back. Omitting lets dsh apply its own default.
    default_model = merged.get("agent-default-model")
    needs_migration = (
        isinstance(default_model, dict)
        and (default_model.get("provider") == "deepseek-official"
             or (default_model.get("provider") == ROUTE
                 and "reasoningEffort" in default_model))
    )
    if needs_migration:
        migrated = {k: v for k, v in default_model.items() if k != "reasoningEffort"}
        merged["agent-default-model"] = {
            **migrated,
            "provider": ROUTE,
            "model": "deepseek/deepseek-v4-pro",
        }
        changed = True
    return merged, changed


def install(path: Path | None = None) -> tuple[Path, bool]:
    """Write the route into ``path``, only when it would actually change.

    :returns: ``(path, wrote)``.
    """
    target = path or default_settings_path()
    existing = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else None
    merged, changed = merge_route(existing)
    if not changed:
        return target, False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(merged, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    return target, True


def main(argv: list[str]) -> int:
    """CLI: ``python3 -m lib.dsh_hal_route [<settings.yaml>]``."""
    target = Path(argv[1]).expanduser() if len(argv) > 1 else None
    try:
        written, wrote = install(target)
    except (TypeError, yaml.YAMLError) as exc:
        print(f"[grip-dsh] refusing to write: {exc}", file=sys.stderr)
        return 1
    print(f"[grip-dsh] {'wrote' if wrote else 'already current'}: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
