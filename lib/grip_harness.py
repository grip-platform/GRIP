"""grip_harness — wire any AI CLI harness at HAL :4010 + a GRIP MCP capability.

This is the backend for ``bin/grip-harness``. It is the syscall-doctrine shim
for "point a foreign harness at GRIP": a stable API
(``emit_codex_config`` / ``merge_managed_block`` / ``HARNESSES``) over the
per-harness config-file dialects.

Design (CBC, KISS, DRY):
- Pure functions only here — no argv parsing, no process spawning, no prompts.
  ``bin/grip-harness`` owns the CLI surface and side effects; this owns the
  text the harness must read. That keeps every function unit-testable with a
  tmp path and zero network (Rule 14 Goodhart-safe).
- GRIP-owned config is fenced between sentinel markers
  (``# >>> GRIP-MANAGED`` … ``# <<< GRIP-MANAGED``). ``merge_managed_block``
  rewrites ONLY that span, leaving user blocks (``[projects.*]``,
  ``[plugins.*]``, ``[features]``) byte-untouched → idempotent re-runs.

Canonical facts (verify-canonical, all re-read live this session):
- HAL gateway: ``http://127.0.0.1:4010/v1`` (lib/hal/gateway.py DEFAULT_GATEWAY_PORT).
- Codex 0.139 ``[model_providers.hal-gateway]`` needs exactly THREE keys:
  ``base_url`` (/v1 suffix; Codex appends /responses), ``env_key`` (sent as
  ``Authorization: Bearer``; gateway rewrites → ``x-api-key``), and
  ``wire_api = "responses"`` — a single ``wire_api = "chat"`` block makes
  codex 0.136+ FAIL TO LOAD config entirely (the regression canary).
- Non-Anthropic cross-wire requires the gateway running with
  ``HAL_GATEWAY_MULTIPROVIDER=1``; the client Bearer is then irrelevant —
  the gateway substitutes ``grip-<provider>`` from the keychain.
- grip-run MCP server: ``node <GRIP_HOME>/mcp-servers/grip-run/server.js``
  (zero-dep stdio; run/list_langs/log). Universal per mcp-org-isolation — org
  servers (postgres/atlassian/zendesk/a client's Slack) are NEVER emitted.

Harness emitter registry (generalised W2 — "any OpenAI-compat-base-url harness"):
- ``codex``    → ~/.codex-hal/config.toml (TOML fence; Responses wire + grip-run MCP).
- ``aider``    → ~/.aider.conf.yml (YAML ``#``-comment fence; openai-api-base
  :4010 + ``model: openai/<id>``; env AIDER_OPENAI_API_KEY=HAL_GATEWAY_KEY).
  Live-CLI harness — verified on PATH this session.
- ``opencode`` → ~/.config/opencode/opencode.json (JSON deep-merge of the
  ``provider.hal`` block — npm ``@ai-sdk/openai-compatible`` + options.baseURL
  :4010; user/other-provider keys preserved). Live-CLI harness — verified on
  PATH; the live config on this machine confirmed the exact schema.
- ``cursor``   → GUI Settings, NO config file. Cursor's OpenAI-base override is
  set in-app (Settings → Models → OpenAI Base URL); there is no file to write
  idempotently and it is NOT live-CLI-provable. So the "emitter" PRINTS the
  exact override instructions (a COMPLETE implementation of "how to wire
  Cursor" — never a hollow/fake config write; no-stubs + feature-complete).

Each supported harness names its emitter via ``HARNESSES[name]["emitter"]``;
``EMITTERS`` is the dispatch table ``bin/grip-harness`` calls generically (DRY:
the :4010 base + the managed-fence/merge helpers are shared across all of them).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

# --- Canonical constants (single source of truth; DRY) ----------------------

HAL_GATEWAY_BASE_URL = "http://127.0.0.1:4010/v1"
HAL_GATEWAY_ENV_KEY = "HAL_GATEWAY_KEY"
HAL_GATEWAY_KEYCHAIN = "grip-hal-gateway-token"  # client Bearer PLACEHOLDER, NOT a provider key: with multiprovider (now DEFAULT ON) the gateway injects the real per-model provider key (grip-openai/grip-deepseek/grip-gemini/local) internally. Never the Anthropic key — sovereign, non-Anthropic-by-default. (claude-* verbatim is now opt-in: set HAL_GATEWAY_KEY to a real Anthropic key for those ids.)
from grip_infinite import WIRE_HOLD_TIMEOUT_MS as HAL_HOLD_TIMEOUT_MS

HAL_WIRE_API = "responses"  # NON-NEGOTIABLE — "chat" breaks codex config load
HAL_PROVIDER_NAME = "hal-gateway"
HAL_DEFAULT_MODEL = "deepseek/deepseek-v4-pro"  # see HARNESS_DEFAULT_MODELS below

# Per-harness default model, ratified by an exogenous non-Anthropic sovereign
# council on 2026-08-19 (seal 227dbd00581bda7c, IDR precog-1787172261-efbcdc2c,
# judge gemini-3.1-pro-preview, zero Anthropic calls; re-read with
# `bin/grip-sovereign-council --show 227dbd00581bda7c`).
#
# WHY NOT ONE ID FOR EVERYTHING. Every id here was round-trip measured against
# the live gateway the same day, prompting "Reply with exactly: OK" — the cost
# is the completion_tokens spent to emit one word, i.e. pure per-turn reasoning
# overhead. It ranges from 1 (gemini-2.5-flash) to 73 (kimi-k3). In a harness
# doing many small tool-calling turns that difference compounds on every turn,
# which is why the many-turn agentic coders default to the 1-token model while
# the precision-critical ones pay for reasoning depth.
#
# WHY NOT deepseek-chat, which is CHEAPER (1 token) than the v4-pro default it
# lost to: it is a legacy ALIAS, not a named current model. DeepSeek's own API
# answers an unknown id with "The supported API model names are
# deepseek-flash or deepseek-v4-pro", and the alias self-reports a stale
# "DeepSeek-V3". The council rejected it for REPRODUCIBILITY — an id whose
# referent can move silently is not a defensible default, whatever it costs.
# (An earlier council, seal b9e1bfad3e7a0a29, DID pick deepseek-chat, but its
# ballot omitted the v4 ids entirely; that verdict is superseded rather than
# overruled — it answered a question posed with an incomplete menu.)
#
# A NOTE ON THE 2026-08-19 OUTAGE, because the history reads oddly otherwise.
# For roughly an hour this default was kimi-k3. That was NOT a disagreement
# with the council: all three DeepSeek ids were returning "Insufficient
# Balance" (the account reached -$0.07), so the ratified default could not
# answer a single request. The account was topped up, all three ids were
# re-verified serving, and the ratified default was restored. The council's
# verdict never changed; the provider was down.
#
# The durable lesson is in the tests rather than here: routable and servable
# are different properties, and only the first was being checked, so a default
# that could not answer anything shipped looking fully tested.
# test_every_default_model_actually_answers now closes that gap.
#
# Absence is meaningful. A harness NOT listed here falls to HAL_DEFAULT_MODEL by
# design; only a role the council actually argued for earns a line.
HARNESS_DEFAULT_MODELS = {
    # Surgical multi-file diff edits: fails loudly on diff-format
    # non-compliance, so it gets the code-specialised model. Unanimous across
    # both councils.
    "aider": "kimi-k2.7-code",
    # Many small tool-calling turns — per-turn overhead dominates, so the
    # 1-token model wins on total cost despite less reasoning depth.
    # Repointed from gemini-2.5-flash on 2026-11-24 (GRIP#9389): Google
    # auto-routes that id to gemini-3.5-flash-lite from 2026-12-01.
    "codex": "gemini/gemini-3.5-flash-lite",
    "opencode": "gemini/gemini-3.5-flash-lite",
    "kilo": "gemini/gemini-3.5-flash-lite",
    "cline": "gemini/gemini-3.5-flash-lite",
    "crush": "gemini/gemini-3.5-flash-lite",
    # Executes code locally: a wrong command is expensive to undo, so this one
    # buys reasoning depth rather than turn economy.
    "interpreter": HAL_DEFAULT_MODEL,
    # Was deepseek/deepseek-v4-flash; moved off DeepSeek with everything else
    # when the account ran dry.
    # Server harness, not a turn-by-turn terminal loop, so the per-turn
    # overhead argument does not apply with the same force.
    "trueforge": "deepseek/deepseek-flash",
}

# GRIP MCP capabilities safe to expose to a foreign harness (universal only).
# grip-run is the KISS choice: zero-dep stdio, no secrets, no network.
GRIP_RUN_MCP = "grip-run"
UNIVERSAL_MCP_RELPATHS = {
    GRIP_RUN_MCP: "mcp-servers/grip-run/server.js",
}

# Antepaste MCP server (CodeTonight-SA/antepaste packages/mcp — PLANNED, not
# yet live): a local clipboard-history MCP over streamable HTTP, loopback only.
# The bearer token lives in the OS keychain and reaches a harness ONLY through
# the env var below — no secret is ever written into a config file. Port 2547
# (keypad C-L-I-P) is registered in rules/port-registry.md. The emitters built
# on these constants are exposed but NOT wired into any default output; the
# live registration lands only once the server exists.
ANTEPASTE_MCP_NAME = "antepaste"
ANTEPASTE_MCP_PORT = 2547
ANTEPASTE_MCP_URL = f"http://127.0.0.1:{ANTEPASTE_MCP_PORT}/mcp"
ANTEPASTE_MCP_TOKEN_ENV = "ANTEPASTE_MCP_TOKEN"

# Idempotency sentinels — merge_managed_block rewrites ONLY this fenced span.
MANAGED_BEGIN = "# >>> GRIP-MANAGED (grip-harness) — do not edit inside this block"
MANAGED_END = "# <<< GRIP-MANAGED (grip-harness)"

# Non-Anthropic provider profiles emitted as ~/.codex/<name>.config.toml so
# `codex --profile <name>` offloads to that vendor THROUGH the gateway.
# Model ids are forwarded verbatim; verified live. deepseek-chat is RETIRED
# upstream (2026-08-30: the API serves ONLY the v4 lineup), so the deepseek
# profile carries v4-pro.
CROSS_WIRE_PROFILES = {
    "deepseek": "deepseek-v4-pro",
    "gemini3": "gemini-3.1-pro-preview",
    "xai": "grok-4",
}

# aider reads an OpenAI-compatible base via --openai-api-base / the YAML key
# ``openai-api-base`` (env AIDER_OPENAI_API_BASE) and routes a model through it
# when the model id is prefixed ``openai/``. The key value lives in env
# AIDER_OPENAI_API_KEY (= HAL_GATEWAY_KEY) — never written into the file.
AIDER_OPENAI_BASE_KEY = "openai-api-base"
AIDER_ENV_KEY = "AIDER_OPENAI_API_KEY"  # holds the value of HAL_GATEWAY_KEY
AIDER_MODEL_PREFIX = "openai/"           # tells aider to use the OpenAI provider

# opencode deep-merges a ``provider.<id>`` block; the openai-compatible adapter
# is npm ``@ai-sdk/openai-compatible`` with options.baseURL + options.apiKey.
# (Schema verified against the live ~/.config/opencode/opencode.json this session.)
OPENCODE_PROVIDER_ID = "hal"
OPENCODE_NPM = "@ai-sdk/openai-compatible"
OPENCODE_DEFAULT_MODELS = {
    # Proven live via HAL :4010 /v1/chat/completions on 2026-06-29 (200 OK):
    # gpt-5.5→gpt-5.5-2026-04-23, gpt-5→gpt-5-2025-08-07, gpt-4o→gpt-4o-2024-08-06,
    # gemini-3.1-pro-preview, gemini-2.5-flash. deepseek-chat/reasoner RETIRED
    # upstream 2026-08-30 (API serves ONLY the v4 lineup) — replaced by the
    # v4 ids, re-verified live the same day. Only PROVEN chat ids are listed —
    # never an unverified or Responses-API-only id (those fail on first message).
    "gpt-5.5": "GPT-5.5 (via HAL → OpenAI)",
    "gpt-5": "GPT-5 (via HAL → OpenAI)",
    "gpt-4o": "GPT-4o (via HAL → OpenAI)",
    "claude-opus-4-8": "Claude Opus 4.8 (via HAL → Anthropic)",
    "deepseek-flash": "DeepSeek Flash (via HAL)",
    "deepseek-v4-pro": "DeepSeek V4 Pro (via HAL)",
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro (via HAL)",
    # Repointed from gemini-2.5-flash on 2026-11-24 (GRIP#9389/dogfood-domain).
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite (via HAL)",
    # The VPS sovereign council model (ADR 0012, C'): opencode hal/council -> :4010
    # -> VPS council endpoint (sealed, Pool-B). Proven live via :4010 2026-07-13.
    "council": "Sovereign Council (VPS, Pool-B, sealed)",
    # Relay-roster models, added to config/opencode-sovereign.json in July but
    # never mirrored here — the drift #4264 caught. llama via #3836 (2026-07-04),
    # the Moonshot and Qwen ids via #3839 (2026-07-05). Names are copied verbatim
    # from the template so both sides read identically to a user.
    #
    # The gap was not cosmetic: this dict is the proven menu behind
    # is_unverified_opencode_chat_model(), so selecting a model that SHIPS in
    # the template was warned as unproven.
    #
    # Moonshot: moonshot-v1-* was retired upstream 2026-08-31 (HTTP 404 since).
    # The bare kimi-* ids route to Moonshot via HAL's ("kimi", "moonshot")
    # alias; both were probed 200 through :4010 /v1/chat/completions on
    # 2026-09-07.
    "openai/gpt-oss-120b": "gpt-oss-120b (via HAL → Groq)",
    "kimi-k3": "Kimi K3 (via HAL → Moonshot)",
    "kimi-k2.7-code": "Kimi K2.7 Code (via HAL → Moonshot)",
    "qwen-max": "Qwen Max (via HAL → DashScope)",
    "qwen3-coder-plus": "Qwen3 Coder Plus (via HAL → DashScope)",
}

# OpenAI "*-pro" ids (gpt-5-pro, gpt-5.5-pro) are Responses-API-ONLY: on the
# opencode @ai-sdk/openai-compatible /v1/chat/completions path they fail with
# "This is not a chat model … did you mean to use v1/completions?". A picker
# selection of a *-pro id was previously wired VERBATIM as the opencode default
# (opencode_provider_block's setdefault), so the harness launched on a default
# that failed on the user's FIRST message. Map *-pro → its chat sibling instead.
# Verified live 2026-06-29 via HAL :4010: gpt-5.5-pro → "not a chat model";
# gpt-5.5 → gpt-5.5-2026-04-23 (200 OK).
OPENCODE_RESPONSES_ONLY_TO_CHAT = {
    "gpt-5.5-pro": "gpt-5.5",
    "gpt-5-pro": "gpt-5",
}


def resolve_opencode_chat_model(model: str) -> str:
    """Map a Responses-API-only OpenAI id to its chat-completions sibling.

    Pure. Returns the BARE id (any ``provider/`` prefix stripped, whitespace
    trimmed) — an OpenCode ``provider.hal`` model key must be bare, so an
    ``openai/gpt-5.5`` picker input normalises to ``gpt-5.5`` consistently
    whether or not it is in the Responses-only map. Already-chat / unknown ids
    pass through as their bare form.
    """
    name = (model or "").strip()
    bare = name.split("/", 1)[1] if "/" in name else name
    return OPENCODE_RESPONSES_ONLY_TO_CHAT.get(bare, bare)


def is_unverified_opencode_chat_model(model: str) -> bool:
    """True if a (resolved) default id is NOT in the proven-live chat menu.

    Closes the gap a reviewer flagged: a hardcoded ``*-pro`` map cannot know a
    *future* Responses-only / non-existent id (``gpt-6-pro``, a typo), which the
    picker would otherwise wire verbatim. We deliberately do NOT use a blanket
    ``endswith('-pro')`` heuristic — it is UNSOUND across providers (Google's
    ``gemini-*-pro`` ARE chat models). Instead, any id outside the proven menu is
    WARNED by the caller (never silently remapped or blocked) — the user may still
    wire a new id they know is valid, but a typo / Responses-only id is surfaced
    before launch instead of failing on the first message.
    """
    return resolve_opencode_chat_model(model) not in OPENCODE_DEFAULT_MODELS

# Registry of supported harnesses. Adding a harness = one entry (with an
# ``emitter`` naming its function in EMITTERS) + one emitter function (DRY: the
# :4010 base + the managed-fence/merge helpers are shared). Unknown harness →
# loud rejection (never silent mis-wire) — enforced in bin/grip-harness.
HARNESSES = {
    "codex": {
        "config_path": "~/.codex-hal/config.toml",
        "wire_api": HAL_WIRE_API,
        "status": "supported",
        "emitter": "codex",
        "live_provable": True,
        "note": "codex-cli 0.136+ — Responses wire, HAL :4010, grip-run MCP.",
    },
    "aider": {
        "config_path": "~/.aider.conf.yml",
        "status": "supported",
        "emitter": "aider",
        "live_provable": True,
        "note": "aider — openai-api-base :4010, model openai/<id>, key via "
                f"${AIDER_ENV_KEY}.",
    },
    "opencode": {
        "config_path": "~/.config/opencode/opencode.json",
        "status": "supported",
        "emitter": "opencode",
        "live_provable": True,
        "note": "opencode — provider.hal (@ai-sdk/openai-compatible) baseURL :4010.",
    },
    "cursor": {
        "config_path": "(GUI Settings → Models → OpenAI Base URL — no file)",
        "status": "supported",
        "emitter": "cursor",
        "live_provable": False,  # GUI app — config-emittable but not CLI-provable
        "note": "Cursor — GUI base-URL override; emitter PRINTS the steps (no file).",
    },
    "agy": {
        "config_path": "~/.gemini/antigravity-cli/settings.json",
        "status": "supported",
        "emitter": "agy",
        "live_provable": True,
        "note": "Antigravity CLI (agy) — trusted workspaces, MCP wiring, context auto-load.",
    },
    # --- Wave 2 (2026-08-19): probed live, then adversarially verified. ------
    # `live_provable` means a wired config can be CHECKED by running the binary.
    # `confidence` records how the config KEYS themselves were established, and
    # is deliberately kept even where it is weak: a docs-only key that turns out
    # wrong fails silently, so a future debugger needs to know which of these
    # were read off a running tool and which off a page.
    "hermes": {
        "config_path": "~/.hermes/config.yaml",
        "status": "supported",
        "emitter": "hermes",
        "live_provable": True,
        "confidence": "verified-live",
        "note": "hermes — model.provider MUST be the literal 'custom'; that is "
                "what unlocks an arbitrary endpoint. OPENAI_BASE_URL is NOT the "
                "mechanism (hermes restricts it to its openai-api provider).",
    },
    "codewhale": {
        "config_path": "~/.codewhale/config.toml",
        "status": "supported",
        "emitter": "codewhale",
        "live_provable": True,
        "confidence": "verified-live",
        "note": "codewhale — [providers.<id>] table + top-level provider/"
                "default_text_model.",
    },
    "interpreter": {
        "config_path": "~/Library/Application Support/open-interpreter/profiles/default.yaml",
        "status": "supported",
        "emitter": "interpreter",
        "live_provable": True,
        "confidence": "verified-live",
        "note": "Open Interpreter — routes via LiteLLM, so the openai/ model "
                "prefix is required for api_base to be honoured.",
    },
    "goose": {
        "config_path": "~/.config/goose/config.yaml",
        "status": "supported",
        "emitter": "goose",
        "live_provable": True,
        "confidence": "verified-live",
        "note": "goose — flat SCREAMING_SNAKE keys. Splits the endpoint into "
                "OPENAI_HOST (authority) + OPENAI_BASE_PATH (route); a full "
                "/v1 URL in OPENAI_HOST doubles the path and 404s.",
    },
    "kilo": {
        "config_path": "~/.config/kilo/kilo.jsonc",
        "status": "supported",
        "emitter": "kilo",
        "live_provable": True,
        "confidence": "verified-live",
        "note": "kilo — @ai-sdk/openai-compatible, same provider shape as "
                "opencode. Verified END-TO-END: it issued a real request to "
                ":4010 and returned the upstream model's own error.",
    },
    "cline": {
        "config_path": "~/.cline/data/settings/providers.json",
        "status": "supported",
        "emitter": "cline",
        "live_provable": True,
        "confidence": "docs-only",
        "note": "cline — provider keys from the published OpenAI-compatible "
                "shape, not from a live wire.",
    },
    "continue": {
        "config_path": "~/.continue/config.yaml",
        "status": "supported",
        "emitter": "continue",
        "live_provable": True,
        "confidence": "docs-only",
        "note": "Continue CLI (binary `cn`) — models: list with apiBase/apiKey.",
    },
    "pi": {
        "config_path": "~/.pi/agent/models.json",
        "status": "supported",
        "emitter": "pi",
        "live_provable": True,
        "confidence": "docs-only",
        "note": "pi — config file is the ONLY route: no CLI flag and no env "
                "var sets its base URL, so a wrong key here is silent.",
    },
    # --- Wave 2, no writable global config: the emitter PRINTS steps. --------
    # Same pattern as cursor. These are complete implementations of "how to
    # wire this harness"; writing a file none of them reads would report
    # success and change nothing (rules/no-stubs.md).
    "qwen": {
        "config_path": "(environment variables — no file)",
        "status": "supported",
        "emitter": "qwen",
        "live_provable": True,
        "confidence": "verified-live",
        "note": "Qwen Code — OPENAI_BASE_URL (the /v1 suffix is REQUIRED; qwen "
                "appends /chat/completions) + OPENAI_API_KEY + OPENAI_MODEL.",
    },
    "crush": {
        "config_path": "(./crush.json — PROJECT-LOCAL, no global file)",
        "status": "supported",
        "emitter": "crush",
        "live_provable": False,
        "confidence": "docs-only",
        "note": "Crush — ~/.config/crush/crush.json does NOT work; it is on "
                "neither discovery list (refuted against the real binary). The "
                "global-tier file is ~/.config/crush/crushrc, bash not JSON.",
    },
    "trueforge": {
        "config_path": "(HTTP API on the running server — no config file)",
        "status": "supported",
        "emitter": "trueforge",
        "live_provable": False,
        "confidence": "docs-only",
        "note": "TrueForge — a server, not a terminal agent. Providers are "
                "POSTed to /api/v1/settings/model-providers after launch; "
                "config-file, env-var and CLI-flag were each refuted.",
    },
    "kiro": {
        # Kiro does NOT speak the OpenAI-compat chat/completions API — its
        # inference endpoint is AWS-backed (CodeWhisperer streaming), so the
        # HAL gateway wiring pattern (codex/opencode/aider) does NOT apply.
        # The integration surface is Kiro's own config-file mechanism:
        #   - ~/.kiro/steering/grip-context.md  — injected into every session
        #   - ~/.kiro/agents/grip.md            — global "GRIP mode" agent
        # Both files use the GRIP-MANAGED fence for idempotent re-runs.
        # Config paths shown as tuple because there are two files to write.
        "config_path": "~/.kiro/steering/grip-context.md + ~/.kiro/agents/grip.md",
        "status": "supported",
        "emitter": "kiro",
        "live_provable": False,  # No OpenAI-compat endpoint to probe
        "confidence": "verified-live",  # Agent/steering schema verified from docs + live ~/.kiro
        "note": "Kiro CLI — GRIP context via ~/.kiro/steering/ + ~/.kiro/agents/; "
                "grip-run MCP declared in the GRIP agent. No HAL gateway wire "
                "(Kiro's model endpoint is AWS-backed, not OpenAI-compat).",
    },
    # Documented-but-not-yet-emitted. NOT silently accepted: bin/grip-harness
    # dispatches only emitters that exist, so requesting this yields an explicit
    # "planned, not implemented" message (no stub config — no-stubs).
    "claude-code": {"config_path": "~/.claude/settings.json", "status": "env-wire",
                    "note": "ANTHROPIC_BASE_URL → :4010 via the GRIP_INFINITE=1 "
                            "launch seam (lib/grip_infinite.py + bin/grip "
                            "grip_handoff, ADR-0002 W0). Opt-in, fail-open, "
                            "caller-wins; no config-file emitter by design."},
}

SUPPORTED_HARNESSES = tuple(
    name for name, spec in HARNESSES.items() if spec["status"] == "supported"
)


# --- GRIP_HOME resolution ---------------------------------------------------

def resolve_grip_home(env: dict | None = None) -> Path:
    """Resolve the GRIP install root, honouring the ~/.grip neutral-home convention.

    Precedence:
      1. ``$GRIP_HOME`` if set, non-empty, and a real directory.
      2. ``~/.grip`` IF it looks like a populated GRIP home (has ``mcp-servers/``)
         — the blackbook neutral home, when realised.
      3. ``~/.claude`` — the live GRIP repo (the default on this machine, where
         ``~/.claude`` is NOT yet a symlink to ``~/.grip``).
    """
    env = os.environ if env is None else env
    explicit = (env.get("GRIP_HOME") or "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_dir():
            return p
    home = Path(env.get("HOME", str(Path.home()))).expanduser()
    neutral = home / ".grip"
    if (neutral / "mcp-servers").is_dir():
        return neutral
    return home / ".claude"


def mcp_server_path(grip_home: Path, name: str) -> Path:
    """Absolute path to a universal GRIP MCP server entry script."""
    relpath = UNIVERSAL_MCP_RELPATHS.get(name)
    if relpath is None:  # defensive — callers pass only known names
        raise KeyError(f"unknown/ineligible GRIP MCP server: {name!r}")
    return grip_home / relpath


# --- Codex fragment emitters (pure text) ------------------------------------

def _codex_provider_block() -> str:
    """The [model_providers.hal-gateway] table: the provider DEFINITION only.

    The THREE mandatory keys (base_url / env_key / wire_api) are emitted
    explicitly; ``wire_api`` is hard-pinned to "responses" because a "chat"
    value makes codex fail to load config entirely.

    It sets no root ``model`` or ``model_provider``. Those used to sit here, in
    the BASE config, so every plain ``codex`` run went through the gateway
    instead of the operator's ChatGPT subscription (V>> 2026-10-01: use a
    subscription by default wherever one exists). The pin now lives in the
    ``hal`` profile (``emit_hal_profile``), which ``gg++ codex`` selects.
    """
    return (
        f"[model_providers.{HAL_PROVIDER_NAME}]\n"
        'name = "HAL Gateway"\n'
        f'base_url = "{HAL_GATEWAY_BASE_URL}"   # /v1 suffix; Codex appends /responses\n'
        f'env_key = "{HAL_GATEWAY_ENV_KEY}"             # sent as Authorization: Bearer; gateway -> x-api-key\n'
        f'wire_api = "{HAL_WIRE_API}"                  # MANDATORY — "chat" breaks codex config load\n'
        f'stream_idle_timeout_ms = {HAL_HOLD_TIMEOUT_MS}\n'
        'request_max_retries = 4\n'
        'stream_max_retries = 5\n'
        'supports_websockets = false\n'
    )


def _codex_mcp_block(server_js: Path, name: str = GRIP_RUN_MCP) -> str:
    """A [mcp_servers.<name>] block spawning a GRIP MCP server over stdio."""
    return (
        f"[mcp_servers.{name}]\n"
        'command = "node"\n'
        f'args = ["{server_js}"]\n'
        "startup_timeout_sec = 120\n"
    )


def _is_unspecified_address(host: str | None) -> bool:
    """True for any textual spelling of the unspecified (wildcard) address.

    ``ipaddress`` normalises the spelling, so ``0.0.0.0``, ``::``, ``::0``,
    ``0:0:0:0:0:0:0:0`` and the fully expanded zero all read as unspecified —
    a fixed string set recognised only the first two (AGORA #5534, speaker A).
    A hostname that is not an IP literal is not unspecified; the bare
    shorthand ``0`` is not an IP literal to ``ipaddress`` either, and no
    vendor documents that form, so it is the one known residual.
    """
    try:
        return ipaddress.ip_address(host or "").is_unspecified
    except ValueError:
        return False


def _reject_wildcard_host(url: str) -> str:
    """Refuse a wildcard BIND address (0.0.0.0 / ::) used as a CONNECT url.

    Shared by the Codex HTTP-MCP and OpenCode remote-MCP emitters. A wildcard
    is what a server listens on, never what a client dials; a config carrying
    one is a bind/connect confusion that fails on some platforms and silently
    "works" on others. Scheme-agnostic (``urlsplit`` parses any scheme). Loud
    ValueError, never a silent rewrite. Returns ``url`` unchanged so callers
    can use it inline.
    """
    host = urlsplit(url).hostname
    if _is_unspecified_address(host):
        raise ValueError(
            f"MCP url {url!r} uses wildcard bind address {host!r}; "
            "dial a concrete host such as 127.0.0.1"
        )
    return url


def _codex_mcp_http_block(name: str, url: str, bearer_token_env_var: str) -> str:
    """A [mcp_servers.<name>] block for a streamable-HTTP MCP server.

    Codex's HTTP form is ``url`` + ``bearer_token_env_var``: the token is read
    from the environment at launch and never written into the file. Key
    spelling verified 2026-09-11 by a controlled test against codex-cli
    0.148.0 — ``codex mcp add <name> --url <url> --bearer-token-env-var <VAR>``
    into a scratch CODEX_HOME wrote exactly these two keys under that header.
    Same quoting as ``_codex_mcp_block`` (double-quoted values, no indent).
    """
    _reject_wildcard_host(url)
    return (
        f"[mcp_servers.{name}]\n"
        f'url = "{url}"\n'
        f'bearer_token_env_var = "{bearer_token_env_var}"\n'
    )


def antepaste_codex_block() -> str:
    """The Antepaste MCP block for Codex — exposed, NOT in emit_codex_config yet."""
    return _codex_mcp_http_block(ANTEPASTE_MCP_NAME, ANTEPASTE_MCP_URL, ANTEPASTE_MCP_TOKEN_ENV)


def emit_codex_config(grip_home: Path, mcp: str = GRIP_RUN_MCP) -> str:
    """The GRIP-MANAGED Codex base fragment (provider definition + MCP), fenced.

    Pure: returns text only. ``merge_managed_toml`` decides where it lands. The
    model and the gateway pin belong to the ``hal`` profile, never to the base.
    """
    server_js = mcp_server_path(grip_home, mcp)
    body = _codex_provider_block() + "\n" + _codex_mcp_block(server_js, mcp)
    return f"{MANAGED_BEGIN}\n{body}{MANAGED_END}\n"


def base_pins_gateway_outside_fence(text: str) -> bool:
    """True when a root ``model_provider`` naming the gateway sits OUTSIDE the fence.

    That line is the operator's own, or an older writer's, so grip-harness
    reports it and never edits it. While it stands, a plain ``codex`` still
    routes through HAL instead of the subscription.
    """
    outside = _excise_managed_block(text)
    limit = _first_table_header_offset(outside)
    head = outside if limit is None else outside[:limit]
    return any(_root_assignment_name(line) == "model_provider" and HAL_PROVIDER_NAME in line
               for line in head.splitlines())


def emit_cross_wire_profile(profile: str, model: str) -> str:
    """A sibling ~/.codex/<profile>.config.toml FENCED body.

    Fenced with the shared GRIP-MANAGED markers so merge_managed_toml rewrites
    ONLY GRIP's span. The profile is an OVERLAY onto the base config, and the
    operator's own additions to it — most importantly the [hooks.state]
    trust hashes codex writes when a hook is approved — are user data that
    live in the same file. Writing the whole file (the earlier behaviour)
    silently deleted every one of them on a re-run, which re-prompts for
    hook trust and looks like a fresh, unrelated failure. Same defect and
    same fix as lib/grip_harness_family.py::emit_direct_codex_profile.
    """
    body = (
        f'model = "{model}"\n'
        f'model_provider = "{HAL_PROVIDER_NAME}"\n'
    )
    return (
        f"{MANAGED_BEGIN}\n"
        f"# Codex profile '{profile}' — GENERATED by grip-harness. Re-run to refresh.\n"
        f"# Routes via HAL :4010 (responses wire). Gateway cross-wires\n"
        f"# Responses -> {profile} chat-completions -> Responses (HAL_GATEWAY_MULTIPROVIDER=1).\n"
        f"# Only this fenced span is GRIP-managed; anything below it is yours.\n"
        f"{body}{MANAGED_END}\n"
    )


HAL_CODEX_PROFILE = "hal"


def emit_hal_profile(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """The fenced ``hal`` profile overlay: the gateway pin and the chosen model.

    ``codex -p hal`` layers this over the base config, so only a launch that
    asks for HAL routes through it; a plain ``codex`` keeps its own default
    provider, which for a ChatGPT login is the subscription.
    """
    body = (
        f'model = "{default_model}"\n'
        f'model_provider = "{HAL_PROVIDER_NAME}"\n'
    )
    return (
        f"{MANAGED_BEGIN}\n"
        f"# Codex profile '{HAL_CODEX_PROFILE}' -- GENERATED by grip-harness. Re-run to refresh.\n"
        "# Routes via HAL :4010 (responses wire); select it with `codex -p hal`.\n"
        "# Only this fenced span is GRIP-managed; anything below it is yours.\n"
        f"{body}{MANAGED_END}\n"
    )


# --- aider fragment emitter (YAML; reuses the # managed fence) ---------------

def write_cross_wire_profile(
    profile: str, model: str, codex_home: "str | None" = None
) -> Path:
    """Write <CODEX_HOME>/<profile>.config.toml as a fenced overlay; idempotent.

    MERGES rather than overwrites. The profile is an OVERLAY onto the base config
    and carries operator data too — codex appends [hooks.state] trust hashes to
    the profile it actually loaded, and those hashes are what stops it
    re-prompting for hook approval on every launch. Writing the emitted body
    verbatim (the earlier behaviour) deleted every one of them on a re-run, so
    the operator saw an unrelated-looking "trust this hook?" prompt after what
    should have been a model-only change.

    Only the GRIP-MANAGED fence is rewritten; every line outside it survives
    verbatim. Mirrors lib/grip_harness_family.py::write_codex_direct_profile,
    which already had to solve this for the vendor-direct overlays.
    """
    path = _codex_home(codex_home) / f"{profile}.config.toml"
    return _write_fenced_profile(path, emit_cross_wire_profile(profile, model))


def write_hal_profile(
    default_model: str = HAL_DEFAULT_MODEL, codex_home: "str | None" = None
) -> Path:
    """Write <CODEX_HOME>/hal.config.toml, the profile ``gg++ codex`` selects.

    Same fenced merge as the cross-wire profiles, so codex's own trust records
    and anything else the operator adds below the fence survive a re-run.
    """
    home = Path(codex_home).expanduser() if codex_home else Path(HARNESSES["codex"]["config_path"]).expanduser().parent
    path = home / f"{HAL_CODEX_PROFILE}.config.toml"
    return _write_fenced_profile(path, emit_hal_profile(default_model))


def _codex_home(codex_home: "str | None") -> Path:
    return Path(codex_home or os.environ.get("CODEX_HOME") or "~/.codex").expanduser()


def _write_fenced_profile(path: Path, block: str) -> Path:
    """Merge ``block`` into the overlay at ``path``; lines outside the fence survive."""
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    # A fence with no end marker cannot be repaired by inspection, so the merge
    # below would drop whatever sat beneath it. Back it up instead.
    if has_truncated_fence(existing):
        backup = path.with_suffix(path.suffix + f".grip-backup-{int(time.time())}")
        atomic_write_text(backup, existing)
        print(
            f"[grip-harness] WARNING: {path} has a GRIP-MANAGED block with no end "
            f"marker. Your original is saved at {backup}",
            file=sys.stderr,
        )
    write_config_text(path, merge_managed_toml(existing, block))
    return path


def emit_aider_config(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """The GRIP-MANAGED ~/.aider.conf.yml fragment, fence-wrapped.

    aider reads ``openai-api-base`` (env AIDER_OPENAI_API_BASE) and routes a
    model through that base when the id is prefixed ``openai/``. The api KEY is
    NOT written to the file — it lives in env ``AIDER_OPENAI_API_KEY`` (set to
    HAL_GATEWAY_KEY), so the config carries no secret (syscall-doctrine).
    The ``#`` fence markers are valid YAML comments, so ``merge_managed_block``
    works unchanged (DRY) and the file stays valid YAML.
    """
    model = default_model if default_model.startswith(AIDER_MODEL_PREFIX) else AIDER_MODEL_PREFIX + default_model
    body = (
        f'{AIDER_OPENAI_BASE_KEY}: "{HAL_GATEWAY_BASE_URL}"   # HAL gateway; key via ${AIDER_ENV_KEY}\n'
        f'model: "{model}"                  # openai/ prefix routes via the base above\n'
    )
    return f"{MANAGED_BEGIN}\n{body}{MANAGED_END}\n"


# --- cursor "emitter" (GUI app — PRINTS steps, never writes a fake file) -----

def emit_cursor_instructions(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """Cursor base-URL override INSTRUCTIONS (a complete impl of 'how to wire Cursor').

    Cursor is a GUI app with no idempotently-writable config file and no live
    CLI to prove against, so the honest, COMPLETE implementation is to emit the
    exact override steps — never a hollow/fake config write (no-stubs /
    feature-complete). The operator pastes these into Cursor's Settings.
    """
    return (
        "Cursor — wire to GRIP's HAL gateway (GUI, no config file to write):\n"
        "  1. Cursor → Settings → Models → OpenAI API Key.\n"
        f"  2. Override OpenAI Base URL → {HAL_GATEWAY_BASE_URL}\n"
        f"  3. API Key → paste the value of ${HAL_GATEWAY_ENV_KEY} "
        f"(security find-generic-password -s {HAL_GATEWAY_KEYCHAIN} -w).\n"
        f"  4. Add a custom model id → {default_model} (HAL routes it: gpt-* → OpenAI, claude-* → Anthropic).\n"
        "  5. Toggle 'Override OpenAI Base URL' ON, then Verify.\n"
        "  Note: for non-Anthropic models the gateway must run with "
        "HAL_GATEWAY_MULTIPROVIDER=1.\n"
    )


# --- Idempotent merge -------------------------------------------------------

def merge_managed_block(existing: str, managed_block: str) -> str:
    """Insert/replace the single GRIP-MANAGED fenced span in ``existing``.

    - No prior block → append (one blank-line separator if file non-empty).
    - Prior block present → replace ONLY the fenced span; everything outside
      (user [projects.*]/[plugins.*]/[features] blocks) is preserved verbatim.
    - Idempotent: merge(merge(x)) == merge(x) for the same managed_block.

    ``managed_block`` must be a full fence (MANAGED_BEGIN … MANAGED_END).
    """
    begin = existing.find(MANAGED_BEGIN)
    if begin == -1:
        if not existing:
            return managed_block
        sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        return existing + sep + managed_block
    end = existing.find(MANAGED_END, begin)
    if end == -1:
        # Corrupt/truncated fence — replace from begin to EOF (fail-safe).
        return existing[:begin] + managed_block
    end_full = end + len(MANAGED_END)
    # Swallow a single trailing newline after the end marker to avoid drift.
    if existing[end_full:end_full + 1] == "\n":
        end_full += 1
    return existing[:begin] + managed_block + existing[end_full:]


def _excise_managed_block(existing: str) -> str:
    """Remove EVERY GRIP-MANAGED fenced span (inverse of the insert).

    All of them, not just the first: a file that somehow carries two fences
    (manual edit, botched merge, an older writer run twice) would otherwise keep
    the survivor, and the freshly inserted fence then redeclares
    ``[model_providers.hal-gateway]`` — the duplicate-key crash this whole path
    exists to remove (AGORA #4297, reproduced before fixing).
    """
    text = existing
    while True:
        begin = text.find(MANAGED_BEGIN)
        if begin == -1:
            return text
        end = text.find(MANAGED_END, begin)
        if end == -1:
            return text[:begin]      # corrupt/truncated fence — drop to EOF
        end_full = end + len(MANAGED_END)
        if text[end_full:end_full + 1] == "\n":
            end_full += 1
        text = text[:begin] + text[end_full:]


def has_truncated_fence(existing: str) -> bool:
    """True when a GRIP-MANAGED block opens but never closes.

    That file cannot be repaired by inspection: the managed block declares its
    OWN table headers (``[model_providers.hal-gateway]``), so "excise up to the
    next header" would cut the fence short, and excising to EOF deletes whatever
    the user had below it. Callers use this to take a backup BEFORE rewriting,
    so the ambiguity costs a copy rather than the user's ``[projects.*]`` trust
    settings (AGORA #4301, reproduced: a truncated fence dropped a real
    ``[projects."…"]`` section).
    """
    begin = existing.find(MANAGED_BEGIN)
    return begin != -1 and existing.find(MANAGED_END, begin) == -1


def write_config_text(path: Path, text: str) -> None:
    """No-op for identical content; retain the original before the first rewrite."""
    if path.exists():
        original = path.read_text(encoding="utf-8")
        if original == text:
            return
        backup = path.with_name(path.name + ".grip-backup")
        if not backup.exists():
            atomic_write_text(backup, original)
    atomic_write_text(path, text)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically — readers see old or new, never half.

    ``Path.write_text`` truncates first and then writes, so a crash, a kill, or a
    full disk mid-write leaves a TORN file. For a config carrying a fenced block
    that is exactly how a truncated fence is born, and the next run then reads it
    back (see :func:`has_truncated_fence`). Writing to a sibling temp file and
    renaming removes that state at the source: ``os.replace`` is atomic on POSIX
    and Windows, and a same-directory temp guarantees the same filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave a stray temp behind on failure; the original is untouched.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _opens_multiline_string(line: str) -> str | None:
    """Return the delimiter this line leaves OPEN, else None.

    Heuristic, not a full TOML parser: an odd count of a triple-delimiter on one
    line leaves it open (``x = \"\"\"`` opens; ``x = \"\"\"a\"\"\"`` does not).
    """
    for delim in ('"""', "'''"):
        if line.count(delim) % 2 == 1:
            return delim
    return None


def _first_table_header_offset(text: str) -> int | None:
    """Byte offset of the first TOML table header (``[t]`` / ``[[t]]``), else None.

    Skips anything inside a multi-line string OR a multi-line array, so neither a
    literal ``[x]`` in a prompt body nor a nested-array continuation line such as
    ``  ["a", "b"],`` is mistaken for a real header — splicing the fence there
    corrupts the file (AGORA #4297 concern 2, reproduced before fixing).

    Indentation is deliberately NOT used to disqualify a header: TOML accepts an
    indented ``  [table]`` (verified — ``tomllib`` parses it), so the column-0
    rule the council suggested would skip legitimate headers. Nesting state, not
    indentation, is what distinguishes a header from array content.
    """
    offset = 0
    open_delim: str | None = None
    depth = 0
    for line in text.splitlines(keepends=True):
        if open_delim is not None:               # inside a multi-line string
            if open_delim in line:
                open_delim = None
        elif depth > 0:                          # inside a multi-line array
            depth = max(0, depth + _bracket_delta(line))
        elif line.lstrip().startswith("["):      # at top level → a real header
            return offset
        else:
            open_delim = _opens_multiline_string(line)
            depth = max(0, _bracket_delta(line))
        offset += len(line)
    return None


_INLINE_STRING_RE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"' r"|'[^']*'")


def _bracket_delta(text: str) -> int:
    """Net ``[`` minus ``]``, ignoring brackets inside strings or comments.

    A raw count is wrong: ``x = "a[b"`` would read as an open array forever.
    Inline strings are removed first, so a surviving ``#`` really is a comment.
    """
    bare = _INLINE_STRING_RE.sub("", text).split("#", 1)[0]
    return bare.count("[") - bare.count("]")


def _value_is_incomplete(value: str) -> bool:
    """True if ``value`` leaves an array or string OPEN at end of line."""
    return _bracket_delta(value) != 0 or bool(_opens_multiline_string(value))


def _root_assignment_name(line: str) -> str | None:
    """The key a line assigns at root, or None if the line is not an assignment.

    Reports the key regardless of how complex the value is. Not truncating a
    multi-line value is ``_strip_root_keys``'s job (it skips the whole span), so
    bailing out here would leave a multi-line ``model = [...]`` in place and let
    the fence add a second one -- a duplicate-key crash (AGORA #4292 concern 1).
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    name = stripped.partition("=")[0].strip()
    # TOML treats `model`, `"model"` and `'model'` as the SAME key, so a quoted
    # user copy must normalise to the bare form or it is missed and duplicated
    # (tomllib: "Cannot overwrite a value"). Only a fully-quoted single segment
    # is unwrapped — a dotted key like `tool.model` stays distinct from `model`.
    if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'":
        name = name[1:-1]
    return name


def _managed_root_keys(managed_block: str) -> set[str]:
    """The root-level keys the fence itself defines (those before its first table)."""
    limit = _first_table_header_offset(managed_block)
    head = managed_block if limit is None else managed_block[:limit]
    names = (_root_assignment_name(ln) for ln in head.splitlines())
    return {n for n in names if n}


def _strip_root_keys(text: str, keys: set[str]) -> str:
    """Drop root-scope assignments of ``keys`` (only those before the first table).

    A matched multi-line value (array or triple-quoted string) is removed in
    FULL: dropping just its opening line would strand the continuation lines and
    corrupt the file, while leaving it entirely lets the fence add a second copy
    of the same key -- the duplicate-key crash this whole path exists to avoid.
    Keys NOT in ``keys`` are never touched, whatever their arity.
    """
    if not keys:
        return text
    limit = _first_table_header_offset(text)
    head = text if limit is None else text[:limit]
    tail = "" if limit is None else text[limit:]

    kept: list[str] = []
    pending = ""                      # non-empty while a matched value stays open
    for line in head.splitlines(keepends=True):
        if pending:                   # inside a matched multi-line value
            pending += line
            if not _value_is_incomplete(pending):
                pending = ""          # value closed — resume keeping lines
            continue
        if _root_assignment_name(line) in keys:
            value = line.partition("=")[2]
            pending = value if _value_is_incomplete(value) else ""
            continue
        kept.append(line)
    if pending:
        # The value never closed, so the input is malformed TOML (it would not
        # load anyway). Everything after the opening line was consumed as part
        # of that value — returning `kept` here would SILENTLY DELETE it. Change
        # nothing instead: fail safe on user content, never destroy it.
        return text
    return "".join(kept) + tail


def merge_managed_toml(existing: str, managed_block: str) -> str:
    """TOML-aware placement of the managed fence — root keys stay at ROOT.

    ``merge_managed_block`` appends at EOF, which is correct for YAML (aider) but
    WRONG for TOML: a bare key binds to the most recently opened table, so a fence
    appended behind ``[desktop]`` silently turns ``model`` into ``desktop.model``
    and codex never sees the selected model. In a valid TOML file every root key
    precedes the first table header, so inserting the fence immediately BEFORE
    that header is correct by construction — the fence's own root keys bind to
    root, its ``[table]`` blocks self-scope, and the user's very next line is
    their own header, which re-scopes before anything of theirs can be captured.

    Heals an existing misplaced fence (excise, then re-insert at the right spot),
    so a config written by the old EOF-append path is corrected on the next run.

    The fence is AUTHORITATIVE for the root keys it emits (``model`` /
    ``model_provider`` — the routing settings grip-harness exists to own), so a
    pre-existing root-level copy outside the fence is reclaimed rather than
    duplicated. Without that, a stock config carrying its own root ``model``
    makes codex die on "duplicate key" the moment the fence is added. Only
    simple, single-line, root-scope assignments of those exact keys are touched;
    every other user line survives verbatim.

    Idempotent: merge(merge(x)) == merge(x).
    """
    stripped = _strip_root_keys(
        _excise_managed_block(existing), _managed_root_keys(managed_block)
    )
    idx = _first_table_header_offset(stripped)
    if idx is None:
        # No table header anywhere → nothing can capture the keys; EOF is safe.
        return merge_managed_block(stripped, managed_block)
    # Normalise the seam rather than appending to it: excision leaves the blank
    # separator behind, so a grow-by-one-newline rule would drift on every run.
    head = stripped[:idx].rstrip("\n")
    if head:
        head += "\n\n"
    return head + managed_block + "\n" + stripped[idx:]


# --- opencode JSON emitter + structured deep-merge --------------------------
# opencode.json is JSON (not a fence-able text file), so idempotency is achieved
# by replacing ONLY the provider.<id> sub-object and preserving every sibling
# key ($schema, other providers, user top-level settings) verbatim.

def opencode_provider_block(default_model: str = HAL_DEFAULT_MODEL) -> dict:
    """The GRIP-managed ``provider.hal`` value (openai-compatible adapter → :4010).

    Pure: returns a dict. The api key is a ``{env:HAL_GATEWAY_KEY}`` reference —
    no secret is written to the file (syscall-doctrine). The default model is
    guaranteed present in the models map even if not in OPENCODE_DEFAULT_MODELS.
    A Responses-API-only ``*-pro`` default is remapped to its chat sibling so the
    harness never launches on a default that fails on the first message.
    """
    default_model = resolve_opencode_chat_model(default_model)
    models = {mid: {"name": label} for mid, label in OPENCODE_DEFAULT_MODELS.items()}
    models.setdefault(default_model, {"name": f"{default_model} (via HAL)"})
    return {
        "npm": OPENCODE_NPM,
        "name": "HAL Gateway",
        "options": {
            "headerTimeout": HAL_HOLD_TIMEOUT_MS,
            "chunkTimeout": HAL_HOLD_TIMEOUT_MS,
            "baseURL": HAL_GATEWAY_BASE_URL,
            "apiKey": "{env:" + HAL_GATEWAY_ENV_KEY + "}",
        },
        "models": models,
    }


def opencode_remote_mcp_block(name: str, url: str, token_env_var: str) -> dict:
    """A GRIP-managed ``mcp.<name>`` entry for a remote (streamable-HTTP) MCP.

    Pure: returns ``{"mcp": {name: {...}}}`` shaped for opencode.json's ``mcp``
    map — schema ``$defs/McpRemoteConfig`` (``type`` / ``url`` / ``headers`` /
    ``enabled``; opencode.ai/config.json, read 2026-09-11). The bearer token
    is an OpenCode ``{env:VAR}`` reference — the same substitution
    ``opencode_provider_block`` uses for ``apiKey`` and the form the MCP docs
    show (``"Authorization": "Bearer {env:MY_API_KEY}"``) — so no secret is
    written to the file (syscall-doctrine). NOT merged by
    ``merge_opencode_config``; that wiring is a later step.
    """
    _reject_wildcard_host(url)
    return {
        "mcp": {
            name: {
                "type": "remote",
                "url": url,
                "headers": {"Authorization": "Bearer {env:" + token_env_var + "}"},
                "enabled": True,
            }
        }
    }


def antepaste_opencode_block() -> dict:
    """The Antepaste MCP entry for OpenCode — exposed, NOT merged by default yet."""
    return opencode_remote_mcp_block(ANTEPASTE_MCP_NAME, ANTEPASTE_MCP_URL, ANTEPASTE_MCP_TOKEN_ENV)


def merge_opencode_config(existing_json: str, default_model: str = HAL_DEFAULT_MODEL) -> str:
    """Deep-merge the GRIP ``provider.hal`` block into an opencode.json string.

    - Empty/blank/whitespace input → a fresh minimal doc with the block.
    - Existing doc → preserve every key; set ``$schema`` if absent; replace
      only GRIP-owned leaves in ``provider.hal`` (other keys survive).
    - Invalid JSON → raise ValueError (loud; never silently clobber a real file).
    - Idempotent: merging the same default twice yields identical output.
    - The top-level ``model`` key is (re)pointed at ``hal/<resolved>``. That key
      is the field OpenCode actually reads to decide which model it opens on, so
      leaving it stale made a chosen model (e.g. DeepSeek) launch on whatever a
      previous session left behind (e.g. Kimi). It is the same bare id as the one
      ``opencode_provider_block`` guarantees exists in the models map, so the two
      can never disagree.
    """
    doc = _load_json_object(existing_json, "opencode.json")
    doc.setdefault("$schema", "https://opencode.ai/config.json")
    providers = doc.get("provider")
    if not isinstance(providers, dict):
        providers = {}
    managed = opencode_provider_block(default_model)
    current = providers.setdefault(OPENCODE_PROVIDER_ID, {})
    _merge_harness_keys(current, managed)
    # Remove only obsolete entries emitted by GRIP, not operator definitions.
    # These Responses-only models cannot use this chat-completions adapter.
    for model in OPENCODE_RESPONSES_ONLY_TO_CHAT:
        if current["models"].get(model) == {"name": f"{model} (via HAL)"}:
            del current["models"][model]
    doc["provider"] = providers
    doc["model"] = f"{OPENCODE_PROVIDER_ID}/{resolve_opencode_chat_model(default_model)}"
    doc["small_model"] = doc["model"]
    return json.dumps(doc, indent=2) + "\n"


def merge_agy_config(existing_json: str, grip_home: Path, default_model: str = HAL_DEFAULT_MODEL) -> str:
    """Deep-merge GRIP settings into agy settings.json.
    
    Injects trustedWorkspaces for ~/.grip and ~/.hal, context.fileName 
    auto-loads for AGENTS.md, and mcpServers for grip-run.
    """
    doc = _load_json_object(existing_json, "agy settings.json")

    # Add trusted workspaces
    trusted = doc.get("trustedWorkspaces", [])
    if isinstance(trusted, list):
        for p in [str(grip_home), str(grip_home.parent / ".hal")]:
            if p not in trusted:
                trusted.append(p)
        doc["trustedWorkspaces"] = trusted

    # Context auto-load
    context = doc.get("context", {})
    if isinstance(context, dict):
        filenames = context.get("fileName", [])
        if isinstance(filenames, list):
            if "AGENTS.md" not in filenames:
                filenames.append("AGENTS.md")
        context["fileName"] = filenames
        doc["context"] = context

    # Add grip-run MCP
    mcp = doc.get("mcpServers", {})
    if isinstance(mcp, dict):
        if "grip-run" not in mcp:
            server_js = mcp_server_path(grip_home, GRIP_RUN_MCP)
            mcp["grip-run"] = {
                "command": "node",
                "args": [str(server_js)]
            }
        doc["mcpServers"] = mcp

    return json.dumps(doc, indent=2) + "\n"




# ---------------------------------------------------------------------------
# Wave-2 harness emitters (2026-08-19)
#
# Eleven harnesses were probed live and adversarially verified before any of
# this was written; the per-harness notes record which claims are backed by a
# running binary and which by documentation only.
#
# There is deliberately NO single generic emitter. The probes came back
# genuinely heterogeneous: goose splits the endpoint across OPENAI_HOST and
# OPENAI_BASE_PATH rather than taking a /v1 base_url, hermes needs the literal
# magic value ``provider: custom`` before an arbitrary endpoint is honoured at
# all, and interpreter nests under ``llm:`` with an ``openai/`` model prefix. A
# uniform writer would emit files these tools parse happily and ignore — a
# config that reports success and changes nothing, which rules/no-stubs.md
# forbids precisely because a later reader believes it worked.
#
# What IS shared is the mechanism: _load_json_object, merge_managed_block,
# merge_managed_toml and atomic_write_text are common to all of them.
# ---------------------------------------------------------------------------

def _load_json_object(text: str, label: str) -> dict:
    """Parse a JSON object, or raise loudly. Blank input yields ``{}``.

    Extracted at the sixth consumer (opencode, agy + the four JSON harnesses
    below), which is well past the point YSH says to abstract. Raising rather
    than defaulting is the load-bearing part: silently replacing a file that
    failed to parse would discard whatever the operator had there.
    """
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        doc = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label} is not valid JSON ({e}); refusing to clobber it")
    if not isinstance(doc, dict):
        raise ValueError(f"{label} root must be a JSON object")
    return doc


# --- JSON harnesses ---------------------------------------------------------

def merge_cline_config(existing_json: str, default_model: str = HAL_DEFAULT_MODEL) -> str:
    """Deep-merge a GRIP ``hal`` provider into cline's providers.json.

    Confidence: DOCS-ONLY. The key names (baseUrl/apiKey/model) come from
    cline's published OpenAI-compatible provider shape, not from a live wire.
    """
    doc = _load_json_object(existing_json, "cline providers.json")
    providers = doc.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    providers[HAL_PROVIDER_NAME] = {
        "name": "HAL Gateway",
        "baseUrl": HAL_GATEWAY_BASE_URL,
        "apiKey": f"${{env:{HAL_GATEWAY_ENV_KEY}}}",
        "model": default_model,
    }
    doc["providers"] = providers
    return json.dumps(doc, indent=2) + "\n"


def merge_kilo_config(existing_json: str, default_model: str = HAL_DEFAULT_MODEL) -> str:
    """Deep-merge a GRIP ``hal`` provider into kilo.jsonc.

    Confidence: VERIFIED-LIVE end-to-end — kilo issued a real HTTP request to
    the gateway and returned the upstream model's own error, which is stronger
    evidence than any config-schema reading.

    Uses ``@ai-sdk/openai-compatible``, the same npm provider opencode already
    wires, so the two blocks are deliberately the same shape.
    """
    doc = _load_json_object(existing_json, "kilo.jsonc")
    doc.setdefault("$schema", "https://app.kilo.ai/config.json")
    provider = doc.get("provider")
    if not isinstance(provider, dict):
        provider = {}
    provider[HAL_PROVIDER_NAME] = {
        "npm": "@ai-sdk/openai-compatible",
        "name": "HAL Gateway",
        "options": {
            "baseURL": HAL_GATEWAY_BASE_URL,
            "apiKey": f"{{env:{HAL_GATEWAY_ENV_KEY}}}",
        },
        "models": {default_model: {"name": f"{default_model} (via HAL)"}},
    }
    doc["provider"] = provider
    doc["model"] = f"{HAL_PROVIDER_NAME}/{default_model}"
    return json.dumps(doc, indent=2) + "\n"


def merge_pi_config(existing_json: str, default_model: str = HAL_DEFAULT_MODEL) -> str:
    """Deep-merge a GRIP ``hal`` provider into pi's models.json.

    Confidence: DOCS-ONLY. pi has no CLI flag and no env var for the base URL —
    the config file is the only route, so a wrong key here is silent.

    pi's schema REQUIRES an ``api`` field (provider or model level) — a missing
    one is a hard load error, not a silent default. And ``apiKey`` resolution
    takes BARE env-var names; ``${VAR}`` is a literal string. The entry is
    merged, never replaced, so operator customisations (compat, reasoning,
    extra models) survive a re-run of the emitter.
    """
    doc = _load_json_object(existing_json, "pi models.json")
    providers = doc.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    entry = providers.get(HAL_PROVIDER_NAME)
    if not isinstance(entry, dict):
        entry = {}
    entry.update({
        "baseUrl": HAL_GATEWAY_BASE_URL,
        "api": "openai-completions",
        "apiKey": HAL_GATEWAY_ENV_KEY,
    })
    models = entry.get("models")
    if not isinstance(models, list):
        models = []
        entry["models"] = models
    if not any(isinstance(m, dict) and m.get("id") == default_model for m in models):
        models.append({"id": default_model, "name": f"{default_model} (via HAL)"})
    providers[HAL_PROVIDER_NAME] = entry
    doc["providers"] = providers
    return json.dumps(doc, indent=2) + "\n"


# --- TOML harness -----------------------------------------------------------

def emit_codewhale_config(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """The GRIP-MANAGED codewhale fragment (fenced TOML).

    Confidence: VERIFIED-LIVE. Shape follows codewhale's own documented
    example: a top-level ``provider``/``default_text_model`` pair plus a
    ``[providers.<id>]`` table.
    """
    return (
        f"{MANAGED_BEGIN}\n"
        f'provider = "{HAL_PROVIDER_NAME}"\n'
        f'default_text_model = "{default_model}"\n'
        "\n"
        f"[providers.{HAL_PROVIDER_NAME}]\n"
        f'base_url = "{HAL_GATEWAY_BASE_URL}"\n'
        f'api_key = "${HAL_GATEWAY_ENV_KEY}"\n'
        f"{MANAGED_END}\n"
    )


# --- YAML harnesses ---------------------------------------------------------

def _merge_harness_keys(existing: dict, managed: dict) -> None:
    """Replace owned leaves, preserving operator siblings at every level."""
    for key, value in managed.items():
        if isinstance(value, dict) and isinstance(existing.get(key), dict):
            _merge_harness_keys(existing[key], value)
        else:
            existing[key] = value


def merge_hermes_config(existing: str, block: str) -> str:
    """Consolidate duplicate owned YAML mappings, retaining all other keys."""
    import yaml

    # Fold every occurrence, including the old managed block, before replacing
    # owned leaves. Unlike safe_load alone, this retains siblings from duplicates.
    root = yaml.compose(existing)
    owned = {"model": {}, "display": {}, "streaming": {}}
    spans = []
    if root is not None:
        if not isinstance(root, yaml.MappingNode):
            raise ValueError("Hermes config must be a YAML mapping")
        for key, value in root.value:
            if key.value in owned:
                value_doc = yaml.safe_load(yaml.serialize(value))
                if isinstance(value_doc, dict):
                    _merge_harness_keys(owned[key.value], value_doc)
                spans.append((key.start_mark.index, value.end_mark.index))
    for start, end in reversed(spans):
        existing = existing[:start] + existing[end:]
    existing = existing.replace(MANAGED_BEGIN, "").replace(MANAGED_END, "")
    _merge_harness_keys(owned, yaml.safe_load(block))
    body = yaml.safe_dump(owned, sort_keys=False)
    return existing.rstrip() + ("\n\n" if existing.strip() else "") + MANAGED_BEGIN + "\n" + body + MANAGED_END + "\n"


def emit_hermes_config(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """The GRIP-MANAGED hermes fragment (fenced YAML).

    Confidence: VERIFIED-LIVE — the ``custom`` provider profile resolves inside
    hermes' installed venv.

    ``provider: custom`` is a MAGIC VALUE, not a label: it is the literal
    string that unlocks an arbitrary endpoint. Setting base_url without it is
    ignored. Note also that OPENAI_BASE_URL is NOT the mechanism here —
    hermes' own docs restrict that variable to its ``openai-api`` provider,
    so the obvious guess silently does nothing.
    """
    return (
        f"{MANAGED_BEGIN}\n"
        "model:\n"
        f"  default: {default_model}\n"
        "  provider: custom\n"
        f"  base_url: {HAL_GATEWAY_BASE_URL}\n"
        f"  api_key: ${HAL_GATEWAY_ENV_KEY}\n"
        "display:\n  streaming: false\n"
        "streaming:\n  enabled: false\n"
        f"{MANAGED_END}\n"
    )


def emit_continue_config(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """The GRIP-MANAGED Continue fragment (fenced YAML).

    Confidence: VERIFIED-LIVE as of 2026-08-19, after a round-trip caught the
    first version being rejected outright.

    ``name``, ``version`` and ``schema`` are REQUIRED top-level fields, not
    decoration. Without them Continue refuses the whole document with
    ``Failed to parse config: name: Required, version: Required`` — so the
    models block never loads and the harness silently keeps its own provider.
    Every config-shape test passed on the broken version, because the YAML was
    well-formed, fenced and pointed at the gateway; only running the harness
    surfaced it.

    The api key uses Continue's ``${{ env.VAR }}`` interpolation. A bare
    ``$VAR`` is taken as a literal string and sent as the credential, which
    fails upstream as an auth error rather than a config one.
    """
    return (
        f"{MANAGED_BEGIN}\n"
        "name: GRIP HAL\n"
        "version: 0.0.1\n"
        "schema: v1\n"
        "models:\n"
        "  - name: HAL Gateway\n"
        "    provider: openai\n"
        f"    model: {default_model}\n"
        f"    apiBase: {HAL_GATEWAY_BASE_URL}\n"
        f"    apiKey: ${{{{ env.{HAL_GATEWAY_ENV_KEY} }}}}\n"
        f"{MANAGED_END}\n"
    )


def emit_interpreter_config(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """The GRIP-MANAGED Open Interpreter profile fragment (fenced YAML).

    Confidence: VERIFIED-LIVE.

    The ``openai/`` prefix on the model is required, not cosmetic: Open
    Interpreter routes through LiteLLM, which selects its adapter from that
    prefix. Without it the id is not recognised as OpenAI-compatible and the
    api_base is not used.
    """
    return (
        f"{MANAGED_BEGIN}\n"
        "llm:\n"
        f'  model: "openai/{default_model}"\n'
        f'  api_base: "{HAL_GATEWAY_BASE_URL}"\n'
        f'  api_key: "${HAL_GATEWAY_ENV_KEY}"\n'
        f"{MANAGED_END}\n"
    )


def emit_goose_config(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """The GRIP-MANAGED goose fragment (fenced YAML, flat SCREAMING_SNAKE).

    Confidence: VERIFIED-LIVE — ``goose info`` names the config path itself and
    the key names appear in the shipped binary.

    goose does NOT take a /v1 base URL. It splits the endpoint into
    ``OPENAI_HOST`` (scheme and authority only) and ``OPENAI_BASE_PATH`` (the
    route), so writing the full gateway URL into OPENAI_HOST produces a
    doubled path and a 404. The split below is why this harness could not
    share a generic emitter.
    """
    host = HAL_GATEWAY_BASE_URL.rsplit("/v1", 1)[0]
    return (
        f"{MANAGED_BEGIN}\n"
        "GOOSE_PROVIDER: openai\n"
        f"GOOSE_MODEL: {default_model}\n"
        f"OPENAI_HOST: {host}\n"
        "OPENAI_BASE_PATH: v1/chat/completions\n"
        f"{MANAGED_END}\n"
    )


# --- Harnesses with NO writable global config -------------------------------
# Each of these PRINTS its exact wiring steps. That is the complete and honest
# implementation of "how to wire this harness" (the same choice already made
# for cursor): none of the three has a global file GRIP could write, so
# emitting one would produce a config the tool never reads while reporting
# success.

def emit_qwen_instructions(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """Qwen Code — environment variables. Confidence: VERIFIED-LIVE.

    The base URL MUST carry the /v1 suffix: qwen appends /chat/completions to
    whatever it is given.
    """
    return (
        "Qwen Code reads its OpenAI-compatible endpoint from the environment.\n"
        "Add to your shell profile, or let `bin/grip` export them at launch:\n"
        "\n"
        f'  export OPENAI_BASE_URL="{HAL_GATEWAY_BASE_URL}"   # /v1 is REQUIRED\n'
        f'  export OPENAI_API_KEY="${HAL_GATEWAY_ENV_KEY}"\n'
        f'  export OPENAI_MODEL="{default_model}"\n'
        "\n"
        "Equivalent per-invocation flags (present in build 0.21.13, absent from --help):\n"
        f'  qwen --openai-base-url "{HAL_GATEWAY_BASE_URL}" -m "{default_model}"\n'
    )


def emit_crush_instructions(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """Crush — PROJECT-LOCAL config only. Confidence: DOCS-ONLY (path refuted).

    The obvious global path ``~/.config/crush/crush.json`` does not exist and
    is in neither of crush's discovery lists; the adversarial verifier refuted
    it against the real Mach-O binary. Crush reads ``./crush.json`` from its
    launch directory, so there is no global file for GRIP to write — writing
    one would be a config crush never reads.
    """
    return (
        "Crush is configured PER PROJECT, not globally.\n"
        f"Write ./crush.json in the directory you launch crush from:\n"
        "\n"
        "  {\n"
        '    "providers": {\n'
        f'      "{HAL_PROVIDER_NAME}": {{\n'
        f'        "base_url": "{HAL_GATEWAY_BASE_URL}",\n'
        f'        "api_key": "${HAL_GATEWAY_ENV_KEY}",\n'
        '        "type": "openai"\n'
        "      }\n"
        "    },\n"
        '    "models": {\n'
        f'      "large": {{"model": "{default_model}", "provider": "{HAL_PROVIDER_NAME}"}}\n'
        "    }\n"
        "  }\n"
        "\n"
        "Note: ~/.config/crush/crush.json does NOT work — it is not on crush's\n"
        "discovery path. The global-tier file is ~/.config/crush/crushrc, which\n"
        "is bash format rather than JSON.\n"
    )


def _trueforge_manifest_json(default_model: str) -> str:
    """The provider manifest as JSON, built by json.dumps rather than by hand.

    Hand-assembled across six f-string fragments, this payload shipped one
    closing brace short — the outer object opened by `-d '{` was never closed,
    so an operator copy-pasting the "complete instructions" would send an
    unparseable body, the registration would be rejected, and agents would
    reference a provider that was never created. Exactly the reports-success-
    changes-nothing failure these emitters exist to avoid. Caught by AGORA on
    PR #4983.

    Serialising a dict cannot lose a brace, which is why the fragments are
    gone. Single quotes are the shell quoting around -d, so the JSON itself
    must contain none — json.dumps only emits double quotes, and the model ids
    in play are ASCII identifiers.
    """
    return json.dumps({
        "manifest": {
            "type": "custom",
            "name": "hal",
            "base_url": HAL_GATEWAY_BASE_URL,
            "auth": {"api_key": "hal-local"},
            "models": [{"name": "hal-default", "model_id": default_model}],
        }
    })


def emit_trueforge_instructions(default_model: str = HAL_DEFAULT_MODEL) -> str:
    """TrueForge — registered over HTTP after the server starts.

    Confidence: DOCS-ONLY. TrueForge is a server with a chat UI and HTTP API,
    not a terminal agent, and it has NO authorable config file: config-file,
    env-var and CLI-flag were each affirmatively refuted. Providers are
    registered by calling the running server, so this is a POST-LAUNCH step —
    the wire only holds while the server is up.
    """
    return (
        "TrueForge has no config file. Register the provider against the\n"
        "RUNNING server (start it first with `npx @truefoundry/trueforge`):\n"
        "\n"
        "  curl -sS -X POST http://localhost:8790/api/v1/settings/model-providers \\\n"
        "    -H 'Content-Type: application/json' \\\n"
        f"    -d '{_trueforge_manifest_json(default_model)}'\n"
        "\n"
        'Agents then reference the model as "hal/hal-default".\n'
    )


# --- Kiro emitters (2026-10-02) ---------------------------------------------
# Kiro's integration surface is NOT the model endpoint (AWS-backed, non-compat)
# but its own config-file mechanism: global steering files and global agent
# definitions. Both are Markdown with optional YAML frontmatter.
#
# Confidence: VERIFIED-LIVE — ~/.kiro/agents/ and ~/.kiro/steering/ confirmed
# present and their schemas read from vendor docs + live ~/.kiro tree this
# session. The MCP shape (command/args) matches Kiro's agent config reference
# (kiro.dev/docs/custom-agents/configuration-reference/).

KIRO_STEERING_PATH = "~/.kiro/steering/grip-context.md"
KIRO_AGENT_PATH = "~/.kiro/agents/grip.md"
KIRO_MANAGED_BEGIN = "<!-- >>> GRIP-MANAGED (grip-harness) — do not edit inside this block -->"
KIRO_MANAGED_END = "<!-- <<< GRIP-MANAGED (grip-harness) -->"
#: The Markdown fence in general. Kiro was its first user; lib/canonical_style_projection.py
#: is the second, with its own marker text so the two fences never collide in one file.
MARKDOWN_MANAGED_BEGIN = KIRO_MANAGED_BEGIN
MARKDOWN_MANAGED_END = KIRO_MANAGED_END


def merge_markdown_managed_block(existing: str, managed_block: str, *,
                                 begin: str = MARKDOWN_MANAGED_BEGIN,
                                 end: str = MARKDOWN_MANAGED_END) -> str:
    """Insert/replace one GRIP-MANAGED HTML-comment fence in a Markdown file.

    Identical semantics to merge_managed_block but with HTML comment markers
    (``<!-- >>> ... -->``), so the fence is invisible when the harness renders the
    Markdown and does not pollute the content shown to the model. ``begin`` and
    ``end`` let a second writer keep its own fence beside a Kiro one.
    """
    start = existing.find(begin)
    if start == -1:
        if not existing:
            return managed_block
        sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        return existing + sep + managed_block
    stop = existing.find(end, start)
    if stop == -1:
        return existing[:start] + managed_block
    end_full = stop + len(end)
    if existing[end_full:end_full + 1] == "\n":
        end_full += 1
    return existing[:start] + managed_block + existing[end_full:]


def has_truncated_markdown_fence(existing: str, *, begin: str = MARKDOWN_MANAGED_BEGIN,
                                 end: str = MARKDOWN_MANAGED_END) -> bool:
    """True when a Markdown fence opens but never closes: see has_truncated_fence."""
    start = existing.find(begin)
    return start != -1 and existing.find(end, start) == -1


def merge_kiro_managed_block(existing: str, managed_block: str) -> str:
    """The Kiro fence: merge_markdown_managed_block with the grip-harness markers."""
    return merge_markdown_managed_block(existing, managed_block,
                                        begin=KIRO_MANAGED_BEGIN, end=KIRO_MANAGED_END)


# The two Kiro files' fixed text. Kept as templates so the emitters stay short;
# the only placeholders are paths, filled in by str.format (no literal braces).
_KIRO_STEERING_TEMPLATE = (
    "# GRIP Context — always active\n"
    "\n"
    "You are operating inside GRIP (General Reasoning & Intelligence Platform).\n"
    "The operator is V>>, pronoun he/him.\n"
    "\n"
    "## Cross-harness contract\n"
    "\n"
    "Read `{agents_md}` for the canonical cross-harness contract:\n"
    "skills, agents, modes, safety gates, HAPPI protocol, and HAL.\n"
    "\n"
    "## Output style\n"
    "\n"
    "Think out loud, then answer short + plain + intuitive — never dropping\n"
    "a fact, never wasting a token. Plain prose first; bullets for parallel\n"
    "facts only. Em dash as ` -- ` (two hyphens, spaces either side).\n"
    "\n"
    "## Safety\n"
    "\n"
    "- Never commit secrets, tokens or credentials.\n"
    "- Destructive git operations (force-push, hard reset) require explicit approval.\n"
    "- Ask before any irreversible action with blast radius.\n"
    "\n"
    "## GRIP skills\n"
    "\n"
    "GRIP skills live at `{grip_home}/skills/*/SKILL.md`. "
    "Each is a harness-agnostic Markdown protocol. Invoke by name.\n"
    "\n"
    "## HAPPI dispatch\n"
    "\n"
    "Any deterministic AI operation: `bash ~/.hal/happi.md run` with a\n"
    "JSON envelope on stdin. See AGENTS.md for the full HAPPI contract.\n"
)

_KIRO_AGENT_TEMPLATE = (
    "---\n"
    "name: grip\n"
    "description: GRIP mode — full GRIP infrastructure, grip-run MCP, AGENTS.md context\n"
    "tools: [\"read\", \"write\", \"shell\", \"web\", \"todo_list\"]\n"
    "mcpServers:\n"
    "  grip-run:\n"
    "    command: node\n"
    "    args: [\"{server_js}\"]\n"
    "resources:\n"
    "  - file://{agents_md}\n"
    "  - file://{claude_md}\n"
    "welcomeMessage: \"GRIP mode active. HAL, skills, and grip-run MCP available.\"\n"
    "---\n"
    "\n"
    "You are operating as a GRIP agent. Apply GRIP canonical output style\n"
    "and safety rules from AGENTS.md. Skills are in\n"
    "`{grip_home}/skills/*/SKILL.md`.\n"
    "\n"
    "Key capabilities available via the grip-run MCP tool:\n"
    "- `run`: execute a shell command in the GRIP home directory\n"
    "- `list_langs`: list supported languages\n"
    "- HAPPI dispatch: `bash ~/.hal/happi.md run` with a JSON envelope\n"
    "- grip-anywhere: `grip-anywhere <repo>` for repo health analysis\n"
    "- knowledge absorption: `python3 lib/hal/knowledge_absorption.py`\n"
)


def emit_kiro_steering(grip_home: "Path | None" = None) -> str:
    """The GRIP-MANAGED ~/.kiro/steering/grip-context.md content.

    Kiro reads every file in ~/.kiro/steering/ and injects its contents into
    every session automatically (no explicit agent reference needed). This file
    tells Kiro who V>> is, points at AGENTS.md as the canonical cross-harness
    contract, and wires the canonical output style and safety rules.

    The fence uses HTML comment markers so it is invisible in Kiro's rendered
    Markdown and does not appear as visible text to the model.
    """
    if grip_home is None:
        grip_home = resolve_grip_home()
    body = _KIRO_STEERING_TEMPLATE.format(agents_md=grip_home / "AGENTS.md", grip_home=grip_home)
    return f"{KIRO_MANAGED_BEGIN}\n{body}{KIRO_MANAGED_END}\n"


def emit_kiro_agent(grip_home: "Path | None" = None) -> str:
    """The GRIP-MANAGED ~/.kiro/agents/grip.md content (Markdown agent format).

    This defines the global 'grip' agent in Kiro: full tool access, grip-run
    MCP server declared, AGENTS.md and CLAUDE.md loaded as resources. The
    agent is selected with `/agent grip` inside any Kiro session.

    Schema verified against kiro.dev/docs/custom-agents/configuration-reference/
    (read 2026-10-02): YAML frontmatter with name/description/model/tools/
    mcpServers/resources/welcomeMessage, then a Markdown system prompt body.
    The grip-run MCP server is the only server declared (mcp-org-isolation).
    """
    if grip_home is None:
        grip_home = resolve_grip_home()
    body = _KIRO_AGENT_TEMPLATE.format(
        server_js=mcp_server_path(grip_home, GRIP_RUN_MCP),
        agents_md=grip_home / "AGENTS.md",
        claude_md=grip_home / "CLAUDE.md",
        grip_home=grip_home,
    )
    return f"{KIRO_MANAGED_BEGIN}\n{body}{KIRO_MANAGED_END}\n"


def emit_kiro(grip_home: "Path | None" = None) -> str:
    """Both Kiro files as one printable text: the steering file, then the agent.

    The dispatch table keys exactly one emitter per supported harness
    (tests/test_grip_harness.py), so Kiro's two writers sit behind this one
    entry. ``cmd_kiro`` in bin/grip-harness still calls emit_kiro_steering and
    emit_kiro_agent separately, because it merges each file in place.
    """
    return (f"--- {KIRO_STEERING_PATH} ---\n{emit_kiro_steering(grip_home)}\n"
            f"--- {KIRO_AGENT_PATH} ---\n{emit_kiro_agent(grip_home)}")


# --- Emitter dispatch table (DRY: bin/grip-harness calls this generically) ---
# Each value is the pure emitter for that harness. The CLI looks up
# HARNESSES[name]["emitter"] and resolves it here — adding a harness never
# touches the CLI's dispatch logic, only this table + the registry entry.
EMITTERS = {
    "codex": emit_codex_config,           # (grip_home, mcp=) -> fenced TOML; model -> hal profile
    "aider": emit_aider_config,           # (default_model=) -> fenced YAML
    "opencode": merge_opencode_config,    # (existing_json, default_model=) -> JSON
    "cursor": emit_cursor_instructions,   # (default_model=) -> printable steps (no file)
    "agy": merge_agy_config,              # (existing_json, grip_home=, default_model=) -> JSON
    # Wave 2 (2026-08-19) — probed live, adversarially verified.
    "cline": merge_cline_config,          # (existing_json, default_model=) -> JSON
    "kilo": merge_kilo_config,            # (existing_json, default_model=) -> JSON
    "pi": merge_pi_config,                # (existing_json, default_model=) -> JSON
    "codewhale": emit_codewhale_config,   # (default_model=) -> fenced TOML
    "hermes": emit_hermes_config,         # (default_model=) -> fenced YAML
    "continue": emit_continue_config,     # (default_model=) -> fenced YAML
    "interpreter": emit_interpreter_config,  # (default_model=) -> fenced YAML
    "goose": emit_goose_config,           # (default_model=) -> fenced YAML
    # No writable global config — these PRINT their steps (as cursor does).
    "qwen": emit_qwen_instructions,       # (default_model=) -> printable steps (env vars)
    "crush": emit_crush_instructions,     # (default_model=) -> printable steps (project-local)
    "trueforge": emit_trueforge_instructions,  # (default_model=) -> printable steps (HTTP API)
    # Kiro writes TWO files (steering + agent) via cmd_kiro in bin/grip-harness,
    # which calls emit_kiro_steering and emit_kiro_agent directly; this one entry
    # keeps the table at one emitter per harness.
    "kiro": emit_kiro,                    # (grip_home=) -> both Markdown files, labelled
}
