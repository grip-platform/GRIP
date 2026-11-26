"""True parallel multi-LLM PR council dispatch via HAL HAPPI.

Dispatches the same PR review prompt to N providers simultaneously via
ThreadPoolExecutor. Each provider gives an independent binary verdict:
PASS or BLOCK, with specific file:line issues only.

Anti-Goodhart guarantees:
    - Continuous quality scores are ignored; only file:line issues count
    - BLOCKER requires >= QUORUM providers to agree (except security issues)
    - Security issues (credentials, eval, SQL injection) block on ANY single report
    - The producing model NEVER scores its own output

Broly MESH deliberation roles:
    Analyst:          collects all unique issues from all providers
    Devil's Advocate: challenges issues found by only one provider
    Pragmatist:       filters out issues without valid file:line references
    Integrator:       applies quorum rule, produces final PASS/BLOCK

Usage:
    from lib.hal_council_dispatch import run_council_review, dispatch_parallel, mesh_deliberate

    verdict = run_council_review(diff_text="...", pr_title="fix: auth bug")
    if not verdict.is_pass:
        for issue in verdict.confirmed_blockers:
            print(f"BLOCKER: {issue.file_line} — {issue.description}")
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import random
import re
import shutil
import time
import uuid
import subprocess
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed, Future, TimeoutError as FutureTimeoutError
from threading import Lock
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


HAL_HOME = Path(os.environ.get("HAL_HOME", str(Path.home() / ".hal")))
_HAL_BIN_DEFAULT = str(HAL_HOME / "bin" / "hal")
_DEFAULT_TIMEOUT_S = 90

# Per-rung timeout overrides. A rung listed here FAILS FAST at its override
# rather than the global 90 s, and — uniquely — a TIMEOUT on it is treated as
# SUBSTITUTABLE (fall through to the next rung) instead of the #2725 default of
# non-substitutable. The distinction is real: a *code* timeout fails identically
# on every rung, but a *provider-latency* timeout on Alibaba's qwen says nothing
# about xAI's grok or OpenAI's gpt. qwen3.5-flash answers in ~15-22 s normally
# (6/6 live probes 2026-08-18, 15.9-21.6 s) but intermittently spikes past 90 s
# under DashScope load; at the full 90 s the timeout consumes the whole future
# deadline (timeout + slack) and starves the role's fall-through, so the council
# lands 2/3 speakers and cannot render a verdict. A 45 s cap (2x the normal
# latency) fails a spiking qwen fast enough that grok/gpt still fit the remaining
# budget, turning a lost speaker into a recovered one. Scoped to qwen; every
# other rung keeps the #2725 non-substitutable-timeout contract unchanged.
_QWEN_RUNG_TIMEOUT_S = 45
_RUNG_TIMEOUT_OVERRIDES: dict[str, int] = {
    "qwen/qwen3.5-flash": _QWEN_RUNG_TIMEOUT_S,
}
_MAX_DIFF_CHARS = 32_000

# Minimum providers that must agree on a BLOCKER before it blocks the PR.
QUORUM = 2


def _env_int(name: str, default: int) -> int:
    """Parse a non-negative int env override; fall back to default on garbage."""
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v >= 0 else default
    except (ValueError, TypeError):
        return default


# #2725 graceful-degradation policy.
#
# Minimum number of providers that must return a non-ERROR vote for the
# council verdict to be a VALID PASS/BLOCK. Below this floor the council
# cannot decide the PR's code quality — the surviving providers' blockers
# are low-confidence (issue #2725 evidence: under 3/5 quorum #2723 B4
# emitted a disproven "all IDR hash corruption" false blocker). Rather than
# let a degraded reviewer false-BLOCK on noise, the council emits a DISTINCT
# non-code verdict (CouncilVerdict.quorum_failure=True) so loops/operators
# can tell reviewer-infrastructure failure from a real code block.
#
# Default 3 (a 3/5 majority of the 5-provider roster). Configurable via
# HAL_COUNCIL_MIN_QUORUM. With >= this many responders the verdict is valid
# and decided ONLY by the responders — never forced BLOCK by max-iterations.
MIN_QUORUM_RESPONDERS = _env_int("HAL_COUNCIL_MIN_QUORUM", 3)

# 429 backoff+retry (issue #2725 option 3). A provider is only counted as
# non-responding AFTER these retries are exhausted, so a transient
# rate-limit does not silently drop a provider from quorum.
_RATELIMIT_MAX_RETRIES = _env_int("HAL_COUNCIL_RATELIMIT_RETRIES", 2)
_RATELIMIT_BASE_BACKOFF_S = 2.0  # exponential: 2s, 4s, 8s … plus jitter
_RATELIMIT_MAX_BACKOFF_S = 30.0

_RATELIMIT_MARKERS = ("rate_limit", "429", "too many requests", "ratelimit")


def _is_rate_limit_error(reason: Optional[str]) -> bool:
    """True when a happi error reason indicates provider rate-limiting (HTTP 429)."""
    if not reason:
        return False
    low = reason.lower()
    return any(m in low for m in _RATELIMIT_MARKERS)


# An empty-but-successful completion (exit=0, no visible text). A reasoning
# model can spend its whole `max_tokens` budget on hidden reasoning and emit
# nothing visible — the documented starvation class (#4178, and the same one
# `_JUDGE_MAX_TOKENS` was raised for). It exits 0, so nothing treats it as a
# failure and the speaker dies silently.
_EMPTY_COMPLETION_REASON = "empty_completion"

# The two halves of the old "timeout_or_missing_binary". Both remain
# NON-substitutable, exactly as the conflated string was: `_is_substitutable_error`
# matches only rate-limit and empty-completion markers, and neither new string
# contains one. Whether a timeout *should* become substitutable is a real
# question (#2725 says no, on the grounds that a code timeout fails identically
# on every rung) — but that is a retry-policy decision, not a rename, and it is
# deliberately NOT bundled into this change.
_TIMEOUT_REASON = "timeout"
_UNUSABLE_BINARY_REASON = "hal_binary_unusable"


def _is_empty_completion_error(reason: Optional[str]) -> bool:
    """True when the provider exited cleanly but produced no visible text."""
    if not reason:
        return False
    return _EMPTY_COMPLETION_REASON in reason.lower()


def _is_substitutable_error(reason: Optional[str]) -> bool:
    """True when ANOTHER provider could plausibly succeed where this one failed.

    Substituting a provider fixes a fault that belongs to the provider — a
    rate-limit, or a model whose reasoning tokens starve its visible output.
    It cannot fix a code timeout or a malformed envelope, which would fail
    identically on every rung, so those deliberately stay non-substitutable
    (#2725) and are surfaced rather than retried down the whole ladder.
    """
    return _is_rate_limit_error(reason) or _is_empty_completion_error(reason)


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with full jitter, capped. attempt is 0-indexed."""
    ceiling = min(_RATELIMIT_BASE_BACKOFF_S * (2 ** attempt), _RATELIMIT_MAX_BACKOFF_S)
    return random.uniform(0.0, ceiling)


def _token(env_var: str, keychain_service: str) -> str:
    """Return `env:VAR` if the env var is already set, else `keychain:SERVICE`.

    HAL's _resolve_apikey_source handles both prefix formats natively.
    This makes the roster work in CI (env vars from GitHub Secrets) and
    locally (macOS Keychain) without any code change between environments.
    Evaluated at import time — set env vars before import when needed.
    """
    return f"env:{env_var}" if os.environ.get(env_var) else f"keychain:{keychain_service}"


# AGORA speaker-ladder auth scheme for the Claude Code OAuth rung. Dispatched
# by lib/agora_pr_review.py::_dispatch_speaker as a direct `claude -p` spawn —
# NEVER as a hal --happi-api {"scheme": "subscription"} envelope (that path
# raised `CCH error: xxhash package required` headless and emptied the roster,
# GRIP #71). The scheme string is defined once here; agora_pr_review imports it.
CCH_SCHEME = "cch"

# Env var NAME for the Claude Code OAuth rung. Presence is checked by
# `_env_present` — set AND non-blank — and only that emptiness is inspected;
# the VALUE is never read into the roster anywhere in this module. The spawned
# `claude -p` reads it from the inherited environment.
_CCH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"


def _in_ci() -> bool:
    """True inside a GitHub Actions runner (GITHUB_ACTIONS=true, set by GitHub)."""
    return os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"


def _env_present(env_var: str) -> bool:
    """True iff ``env_var`` is set to a NON-BLANK value.

    GitHub Actions sets an environment variable to the EMPTY STRING when the
    named secret does not exist. So ``NAME in os.environ`` — a membership test
    on the NAME — cannot tell "set" from "declared but empty", and a workflow
    that exports a secret nobody has created marks the rung PRESENT carrying an
    empty token. That is strictly WORSE than absent: instead of falling through
    to the next rung on the ladder, the role burns on a runtime auth failure,
    and the council sits a thinner bench with no signal that it has.

    Observed 2026-09-09. GRIP holds DASHSCOPE_API_KEY and seats qwen at rung 0
    (run 34222651596, GRIP#5372); HAL did not hold it, so its quality role fell
    down the ladder onto a DeepSeek reasoning model and returned empty (run
    34406599311, HAL#565). HAL pull requests were reviewed by a thinner council
    than GRIP's, silently.

    Only the EMPTINESS of the value is inspected here. It is never returned,
    logged, or bound to a name, so the module's standing convention — the
    secret VALUE never enters the roster — is preserved. Both auth functions
    below share this one predicate so the two cannot drift apart.
    """
    return bool(os.environ.get(env_var, "").strip())


def _apikey_auth_or_absent(env_var: str, keychain_service: str) -> dict:
    """Presence-gated apikey auth for an AGORA ladder rung.

    env var set to a non-blank value → ``env:VAR`` (CI and local). Env var
    unset, empty, or whitespace-only — GitHub Actions sets a missing secret to
    the empty string, so these are one state, see ``_env_present``:
      - in CI there is no macOS keychain, so return ``{}`` — the empty dict
        marks the rung ABSENT and _build_role_chains' existing ``if e[2]``
        filter drops it. An unset secret must never burn a ~90 s runtime
        failure per role per depth discovering the keychain does not exist.
      - locally, fall back to ``keychain:SERVICE`` exactly like _token().

    Presence is decided by ``_env_present``: the NAME must be set AND its
    value non-blank. Only that emptiness is inspected — the secret VALUE is
    never read into the roster.
    """
    if _env_present(env_var):
        return {"scheme": "apikey", "token": f"env:{env_var}"}
    if _in_ci():
        return {}
    return {"scheme": "apikey", "token": f"keychain:{keychain_service}"}


_CCH_KEYCHAIN_SERVICE = "grip-claude-oauth"


def _keychain_has(service: str) -> bool:
    """True iff a keychain entry exists. Presence only — the value is never returned."""
    try:
        return subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, timeout=10,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _cch_auth_or_absent() -> dict:
    """CCH rung auth: present iff the Claude Code OAuth token env var is set.

    Presence is decided by ``_env_present`` — the name must be set AND its
    value non-blank — and only that emptiness is inspected; the token VALUE is
    never read here. An unset, empty, or whitespace-only token takes one path:
    the keychain fallback below, and failing that ``{}`` → rung absent (dropped
    by the ``if e[2]`` chain filter), so the rung switches off instantly by
    unsetting the secret.

    CI GATE (``_in_ci``), added to close the apikey/CCH asymmetry. ``_apikey_auth_or_absent``
    has always skipped the keychain fallback under ``_in_ci()`` — an unset secret
    must never burn a ~90 s runtime failure per role per depth discovering the
    keychain does not exist. This function instead relied on the assumption that
    CI has no ``security`` binary, which is true of the Linux runner but NOT of a
    macOS box that sets ``GITHUB_ACTIONS=true`` (the hermetic tests do exactly
    that, and so did the live Mac while its ``grip-claude-oauth`` keychain entry
    was present). The result was a CCH rung offered in CI with no token env var,
    reddening the roster's forward anchors. The gate is now explicit and matches
    the sibling: in CI an unset token ends at absent, keychain fallback is local.

    KEYCHAIN FALLBACK, added 2026-08-31. The sibling ``_apikey_auth_or_absent`` has always
    fallen back to ``keychain:SERVICE`` when the env var is unset; this one did not, so the
    CCH rung was dropped on every ordinary shell even though ``grip-claude-oauth`` holds a
    live subscription token. HAL's own resolver already looks there
    (``lib/hal/subscription.py`` from_env: env var, then env var, then keychain
    ``grip-claude-oauth``), which made CCH reachable from HAL while inert from GRIP — the
    two halves of one system disagreeing about where a credential lives. The reference is
    emitted as ``keychain:`` for the resolver to dereference; the value is still never read
    here, so the presence-only property this docstring promises is preserved.
    """
    if _env_present(_CCH_TOKEN_ENV):
        return {"scheme": CCH_SCHEME}
    if _in_ci():
        return {}
    if _keychain_has(_CCH_KEYCHAIN_SERVICE):
        return {"scheme": CCH_SCHEME, "token": f"keychain:{_CCH_KEYCHAIN_SERVICE}"}
    return {}


_SECURITY_KEYWORDS = frozenset({
    "credential", "secret", "api key", "apikey", "token", "password",
    "eval(", "sql injection", "path traversal", "ssrf", "hardcoded",
    "subprocess.call", "os.system", "pickle.load",
})

_BLOCKER_RE = re.compile(r"^BLOCKER:\s*(\S+)\s*[—\-–]+\s*(.+)$", re.MULTILINE)
_WARNING_RE = re.compile(r"^WARNING:\s*(\S+)\s*[—\-–]+\s*(.+)$", re.MULTILINE)
_VERDICT_RE = re.compile(r"^VERDICT:\s*(PASS|BLOCK)", re.MULTILINE)

_REVIEW_PROMPT_TEMPLATE = """\
You are an independent code reviewer on a multi-model council. Your job is
to find REAL bugs, security issues, and breaking changes in this PR diff.

RULES (follow exactly):
1. Only report issues you are >90% confident will cause production failures
   or security vulnerabilities.
2. Every issue MUST cite a specific file and line: "lib/foo.py:42"
3. Do NOT give quality scores, ratings, or subjective feedback.
4. Do NOT praise or summarise the code.
5. Security issues (hardcoded secrets, eval(), SQL injection, path traversal,
   SSRF, unsafe deserialization) are always BLOCKER.
6. Logic bugs that will cause production failures are BLOCKER.
7. All other issues are WARNING.

RESPONSE FORMAT (strict — one issue per line):
  BLOCKER: lib/foo.py:42 — why this will break production
  WARNING: lib/bar.py:17 — what should be improved

Finish with exactly one of these on its own line:
  VERDICT: PASS
  VERDICT: BLOCK

If you find no issues, write only:
  VERDICT: PASS

PR TITLE: {pr_title}

PR DIFF:
{diff_text}
"""

# Provider roster: (id, happi_provider_string, auth_dict, human_description)
# "subscription" reads CLAUDE_CODE_OAUTH_TOKEN (Claude Code OAuth token).
# "keychain:X" reads macOS Keychain service X at dispatch time.
_PROVIDER_ROSTER: list[tuple[str, str, dict, str]] = [
    (
        "claude-opus-4-7",
        "claude-opus-4-7",
        {"scheme": "subscription"},
        "Anthropic Claude Opus 4.7",
    ),
    (
        "gemini-4-carbon",
        "gemini/gemini-4-carbon",
        {"scheme": "apikey", "token": _token("GEMINI_API_KEY", "grip-gemini")},
        "Google Gemini 4 Carbon",
    ),
    (
        "gpt-oss-120b",
        "groq/openai/gpt-oss-120b",
        {"scheme": "apikey", "token": _token("GROQ_API_KEY", "grip-groq")},
        "OpenAI gpt-oss-120b via Groq",
    ),
    (
        "deepseek-flash",
        "deepseek/deepseek-flash",
        {"scheme": "apikey", "token": _token("DEEPSEEK_API_KEY", "grip-deepseek")},
        "DeepSeek Flash",
    ),
    (
        "grok-4.20-non-reasoning",
        "xai/grok-4.20-0309-non-reasoning",
        {"scheme": "apikey", "token": _token("XAI_API_KEY", "grip-xai")},
        "xAI Grok 4.20 Non-Reasoning",
    ),
]


# AGORA-specific speaker roster (3 non-Google frontier-tier providers).
# V>>-ratified 2026-05-21 per drafts/agora-enhancement-2026-05-21/03-speaker-selection.md.
# DISTINCT from _PROVIDER_ROSTER above so HAL Council (binary PASS/BLOCK) keeps
# its 5-voter quorum semantics while AGORA uses the slimmer 3-speaker panel.
#
# Exogenous-anchor-law (rules/exogenous-anchor-law.md): the 3 speakers MUST be
# non-Google because the judge is Google. The judge synthesises the 3 speaker
# outputs into a verdict — if any speaker were Google, the judge could
# predict-to-please its own family, violating the producer ≠ verifier
# invariant (anchor #3). Test T1 in tests/test_agora_judge.py asserts this
# family-disjoint property mechanically.
#
# DE-SUBSCRIPTION (GRIP #71, 2026-06-26, V>>--<<V bootstrap-ratified). Every
# speaker now authenticates via an API KEY, never Claude-subscription OAuth.
# Verify-canonical root cause (PR #3710/#3708 CI logs): Speaker A's old
# {"scheme": "subscription"} routed through the Claude Code Harness signing
# path, which raised `CCH error: xxhash package required for CCH signing` in
# the headless GitHub Actions runner → empty Speaker → AGORA rendered a FAILED
# (empty-roster) review on every PR. Subscription OAuth is also unavailable
# under OpenCode. Two unreliable providers were swapped at the same time:
#   - xAI Grok 3 → out of credits (live HTTP 403 "used all available credits")
#   - DeepSeek Reasoner → a thinking model whose reasoning tokens consumed the
#     2048 max_tokens budget, leaving an empty visible completion (the same
#     budget-starvation the Gemini judge hit; see _JUDGE_MAX_TOKENS).
# Replacements are proven NON-EMPTY via a live `hal` smoke test (2026-06-26):
# claude-sonnet-4-6 (x-api-key path), groq/openai/gpt-oss-120b, and
# deepseek-chat (non-reasoning). The round-robin fallback chain
# (_build_role_chains) keeps the roster non-empty even if one provider is down
# in CI. Families stay {anthropic, meta, deepseek} — disjoint from the Google
# judge (T1/T9 still green). API keys resolve from GitHub Actions secrets in
# CI (ANTHROPIC_API_KEY/GROQ_API_KEY/DEEPSEEK_API_KEY) and the macOS keychain
# locally — _token() picks the right source per environment.
#
# FALLBACK LADDER (2026-07-05, 0/3-speaker outage). The roster is now a
# 6-rung LADDER, not a fixed 3-speaker binding: _build_role_chains assigns
# each of the 3 AGORA roles a round-robin fallback chain over the whole
# ladder, so any live rung can speak for any role. Context: Groq returns
# HTTP 403 to GitHub Actions runner IPs and the Anthropic API key has empty
# credit (400), which left 0/3 speakers live. New rungs:
#   - CCH rung: claude -p billing the Max subscription via
#     CLAUDE_CODE_OAUTH_TOKEN (the proven VPS-relay headless pattern) —
#     dispatched by agora_pr_review._dispatch_speaker_cch as a direct
#     capability-locked spawn, NEVER via hal {"scheme": "subscription"}
#     (the xxhash-broken path GRIP #71 retired).
#   - qwen + moonshot rungs: direct provider HTTPS via the HAL CLI's
#     OpenAI-compat adapter (prefixes already supported). Model ids come
#     from the proven provider menus (qwen3.5-flash = HAL adapter default;
#     kimi-k2.6 = GRIP config/providers.yaml mid tier — moonshot-v1-* was
#     retired upstream 2026-05-25), never invented.
# Presence gating: a rung whose secret env var is unset carries auth={} and
# is dropped by _build_role_chains' `if e[2]` filter — in CI an unset secret
# no longer burns a ~90 s runtime failure per role per depth. Locally the
# keychain fallback keeps every apikey rung present, preserving prior
# behaviour. Dead-but-configured rungs (403/400 with the secret set) still
# fall through via chain[depth+1] — those are fast error events.
# Families stay {anthropic, meta, deepseek, alibaba, moonshot} — ALL
# non-Google, so the Gemini judge remains exogenous (T1/T9 green). Gemini
# is deliberately EXCLUDED from the speaker ladder: a Google speaker under
# a Google judge violates producer ≠ verifier (exogenous-anchor-law).


def _funded_speaker_rungs(_api) -> list[tuple[str, str, dict, str]]:
    """The top of the AGORA speaker ladder: the speakers that answered a live probe.

    LADDER RE-ORDERED 2026-07-25 after a live probe of every rung. The top
    three rungs are now the three that ACTUALLY answered, in three distinct
    non-Google families (deepseek / alibaba / xai), preserving both judge
    exogeneity and 3-role depth-0 coverage.
    """
    return [
        ("deepseek-flash", "deepseek/deepseek-flash", _api("DEEPSEEK_API_KEY", "grip-deepseek"),
         "DeepSeek Flash (primary funded speaker)"),
        # The quality seat. Qwen4-Max replaced Qwen3.5-Flash on V>>'s decision of
        # 2026-11-24 (GRIP#9495): in ten trial sittings it found two real defects
        # that the seated model and the quote gate missed
        # (docs/measurements/2026-11-24-agora-seat-trials.md). Its token budget and
        # its 150 s limit are in agora_pr_review._SPEAKER_SEAT_LIMITS.
        ("qwen4-max", "qwen/qwen4-max", _api("DASHSCOPE_API_KEY", "grip-dashscope"),
         "Alibaba Qwen4-Max via DashScope (quality speaker)"),
        # grok-3 -> grok-4.3 (2026-07-25). grok-3 ANSWERS at the xAI provider
        # (probed LIVE, 1.89s) but is ABSENT from config/model_catalog.yaml,
        # and this ladder routes through the HAL CLI, which hard-errors on an
        # address outside the catalogue ("'xai/grok-3' is not in the model
        # catalogue" — the pre-existing failure in test_hal_active_llm_marker).
        # Provider-liveness is NOT sufficient for a rung here: it must be live
        # AND catalogued. grok-4.3 is both (probed LIVE, 1.66s).
        ("grok-4.3", "xai/grok-4.3", _api("XAI_API_KEY", "grip-xai"),
         "xAI Grok 4.3 (funded speaker; CI secret present)"),
    ]


try:
    from openai_billing import paid_openai_auth
except ImportError:
    from lib.openai_billing import paid_openai_auth


#: The operator's opt-in for the paid Anthropic key (rules/claude-cap-moves-to-the-next-subscription.md).
_PAID_CLAUDE_OPT_IN = "GRIP_CLAUDE_ALLOW_PAID"


def _paid_claude_auth(_api) -> dict:
    """The paid Anthropic key's rung auth, present only when ``GRIP_CLAUDE_ALLOW_PAID`` is exactly "1".

    The rule: never engage the paid Anthropic API key unless the operator set that
    variable. Before GRIP#9985 a sitting rarely walked this far down the ladder;
    once a failed fallback no longer ends a sitting, an ungated paid rung would be
    reached whenever every vendor above it failed. Absent auth drops the rung from
    every chain (_build_role_chains filters on it).
    """
    if os.environ.get(_PAID_CLAUDE_OPT_IN) != "1":
        return {}
    return _api("ANTHROPIC_API_KEY", "grip-anthropic-external")


def _build_agora_speaker_roster() -> list[tuple[str, str, dict, str]]:
    """Construct the AGORA speaker fallback ladder (presence-gated auth).

    Separated into a function so tests can rebuild the ladder under a
    controlled environment; the module-level _AGORA_SPEAKER_ROSTER below is
    the import-time snapshot (env is fixed per CI run).
    """
    _api = _apikey_auth_or_absent
    # Funded + CI-reachable + exogenous speakers FIRST (judge = Gemini shares
    # none of these families), so each of the 3 roles maps to a funded speaker
    # at depth 0. Ordering IS the exogeneity mechanism — Anthropic (below) is
    # never reached while these are present (rules/pool-a-offload-routing.md).
    return _funded_speaker_rungs(_api) + [
        # DEMOTED 2026-07-25: quota-dead, HTTP 429 "exceeded your current
        # quota" on a live probe. Kept as an auto-revive rung (it returns the
        # moment billing is restored) but no longer occupies a depth-0 slot,
        # where it burned one guaranteed-failed call per role per run.
        #
        # RE-POINTED 2026-08-17 (V>> directive: "an expert frontier model
        # should be used. Not GPT-4o"). The rung was pinned to gpt-4o-mini,
        # which config/providers.yaml had ITSELF removed ("superseded by
        # gpt-5.4-mini/nano") and the catalogue listed only as superseded - so
        # this rung was doubly dead: no credit AND, per the grok-3 lesson
        # above, it would hard-error on the catalogue validator even with
        # credit. It now points at gpt-5.6-sol, OpenAI's GA flagship, whose id
        # was live-verified against api.openai.com/v1/models on 2026-08-17 and
        # which is now a real catalogue entry (config/model_catalog.yaml).
        # Still demoted, not promoted: the account probed
        # "credit_balance_exhausted" the same day, so it stays an auto-revive
        # rung. Restore OpenAI billing and it speaks at frontier quality.
        ("gpt-5.6-sol", "openai/gpt-5.6-sol", paid_openai_auth(),
         "OpenAI GPT-5.6 Sol frontier (fallback; auto-revives on credit)"),
        # Auto-revive fallbacks (dormant on CI; reached only if a funded speaker
        # above is absent). Groq is CI-blocked.
        ("gpt-oss-120b", "groq/openai/gpt-oss-120b", _api("GROQ_API_KEY", "grip-groq"),
         "OpenAI gpt-oss-120b via Groq (fallback; Cloudflare-blocks CI runner IPs)"),
        # Two reasoning fallbacks, measured 2026-11-26 on a 23,659-character technical
        # prompt: kimi-k2.6 (this slot until then) returned an empty completion at the
        # default budget and timed out at 240 s with the reasoning budget; these two
        # answered in 97.7 s and 126.7 s with the budget their seat limits give them
        # (agora_pr_review._SPEAKER_SEAT_LIMITS). Mistral streams its deltas as lists of
        # content blocks, which _delta_text reads (folded in from GRIP#10024).
        ("kimi-k3.1-pro-highspeed", "moonshot/kimi-k3.1-pro-highspeed", _api("MOONSHOT_API_KEY", "grip-moonshot"),
         "Moonshot Kimi K3.1 Pro high-speed (fallback)"),
        ("mistral-large-4", "mistral/mistral-large-4", _api("MISTRAL_API_KEY", "grip-mistral"),
         "Mistral Large 4 (fallback)"),
        # The two Claude rungs come LAST (GRIP#9985): a funded vendor's credit
        # outage falls to another vendor before any Claude spawn. On 2026-11-26
        # grok-4.3's 403 fell straight to the CCH spawn, which timed out at 120 s,
        # so the sitting ended at 2/3. Anthropic is reached last, for exogeneity and
        # credit, and the paid-key rung only with the operator's opt-in.
        ("claude-sonnet-4-6-cch", "claude-sonnet-4-6", _cch_auth_or_absent(),
         "Anthropic Claude Sonnet 4.6 via CCH OAuth (fallback; capability-locked claude -p)"),
        ("claude-sonnet-4-6", "claude-sonnet-4-6", _paid_claude_auth(_api),
         "Anthropic Claude Sonnet 4.6 x-api-key (fallback; auto-revives on credit)"),
    ]


_AGORA_SPEAKER_ROSTER: list[tuple[str, str, dict, str]] = _build_agora_speaker_roster()


# LLM-AS-JUDGE provider (Google Gemini 4 Carbon — exogenous to all speakers
# above). V>>-ratified 2026-05-21 with Gemini 3.1 Pro Preview; moved to Carbon,
# Google's "strongest reasoning model", on V>>'s instruction of 2026-11-26 to
# replace 3.1 Pro with the new Gemini 4 models. Tuple shape matches roster:
# (id, happi_provider_string, auth_dict, human_description).
#
# Fail-closed contract: if the judge timeouts, errors, returns invalid
# verdict format, or returns auth failure, AGORA renders a "Judge
# unavailable" banner and DOES NOT fabricate a verdict. Per
# rules/exogenous-anchor-law.md: a self-fulfilling judge from a speaker
# provider is worse than no judge.
_JUDGE_PROVIDER: tuple[str, str, dict, str] = (
    "gemini-4-carbon-judge",
    "gemini/gemini-4-carbon",
    {"scheme": "apikey", "token": _token("GEMINI_API_KEY", "grip-gemini")},
    "Google Gemini 4 Carbon (judge — exogenous to speakers)",
)

# Judges tried in order when the one above returns nothing at all (GRIP#6808).
# From 2026-09-29 15:40Z to 2026-09-30 ~19:45Z Google refused the Gemini judge on
# billing, and with no second judge nothing in the repo could merge for 28 hours.
# A fallback may sit only if its family is shared by no sitting speaker and is not
# the PR author's: agora_pr_review._dispatch_judge checks that on every call, so
# a speaker that itself fell back to one of these models is never judged by it.
def _build_judge_fallbacks() -> tuple[tuple[str, str, dict, str], ...]:
    """The fallback judges, presence-gated on judge-only key names.

    Read from AGORA_JUDGE_* rather than the providers' usual names because the
    speaker roster keys its own Moonshot and OpenAI rungs on MOONSHOT_API_KEY and
    OPENAI_API_KEY, and in CI those rungs stay absent only because the workflow
    does not pass those names. Passing them for the judge made Kimi a live CI
    speaker as well (measured 2026-10-01, on PR #6811). Locally both read the
    same keychain entries as the speakers. In CI an unset secret makes its rung
    absent (empty auth), and the ladder skips it without a call.
    """
    return (
        # Kimi K3.1 Pro replaced Kimi K2.6 on V>>'s decision of 2026-11-24
        # (GRIP#9495): at a 300 s limit it gave a valid verdict in 10 of 10 trial
        # sittings against K2.6's 7, and Moonshot retires the K2 series by the end
        # of December 2026. Its 180 s limit is in agora_pr_review._JUDGE_RUNG_TIMEOUTS.
        ("kimi-k3.1-pro-judge", "moonshot/kimi-k3.1-pro",
         _apikey_auth_or_absent("AGORA_JUDGE_MOONSHOT_API_KEY", "grip-moonshot"),
         "Moonshot Kimi K3.1 Pro (fallback judge)"),
        ("gpt-5.6-sol-judge", "openai/gpt-5.6-sol",
         _apikey_auth_or_absent("AGORA_JUDGE_OPENAI_API_KEY", "grip-openai"),
         "OpenAI GPT-5.6 Sol (fallback judge)"),
    )


_JUDGE_FALLBACKS: tuple[tuple[str, str, dict, str], ...] = _build_judge_fallbacks()


@dataclass
class Issue:
    """A specific code issue with a file:line reference."""
    file_line: str  # e.g. "lib/foo.py:42"
    description: str
    is_security: bool = False


@dataclass
class ProviderVote:
    """One provider's independent verdict on a PR diff."""
    provider: str
    verdict: str  # "PASS", "BLOCK", or "ERROR"
    blockers: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    raw_text: str = ""
    error: str = ""

    @property
    def adversarial_no_error(self) -> bool:
        """True if no blockers found — consumed by pipeline step_adversarial."""
        return self.verdict == "PASS"

    @property
    def score(self) -> float:
        """Numeric proxy for pipeline step_final_score outsider-multiplier."""
        return 0.0 if self.verdict == "BLOCK" else (50.0 if self.verdict == "ERROR" else 100.0)

    @property
    def is_outsider(self) -> bool:
        return False


@dataclass
class CouncilVerdict:
    """Final MESH-deliberated verdict from N independent providers.

    #2725: ``quorum_failure`` is True when fewer than MIN_QUORUM_RESPONDERS
    providers returned a non-ERROR vote. It is a DISTINCT verdict from a
    real code block — ``is_pass`` is False (the PR is not waved through on a
    broken reviewer) but the cause is reviewer-infrastructure (provider 429),
    NOT confirmed code defects. ``confirmed_blockers`` is empty in this state
    because the surviving providers are below the confidence floor. Defaults
    False so existing constructors/tests remain valid.
    """
    is_pass: bool
    confirmed_blockers: list[Issue]
    all_warnings: list[Issue]
    votes: list[ProviderVote]
    providers_responded: int
    providers_total: int
    quorum_failure: bool = False

    @property
    def adversarial_no_error(self) -> bool:
        return self.is_pass

    @property
    def score(self) -> float:
        return 100.0 if self.is_pass else 0.0

    @property
    def is_outsider(self) -> bool:
        return False


def _is_security_issue(description: str) -> bool:
    desc_lower = description.lower()
    return any(kw in desc_lower for kw in _SECURITY_KEYWORDS)


def _has_valid_file_line(file_line: str) -> bool:
    """Pragmatist gate: reject references without a colon or with placeholder text."""
    if ":" not in file_line:
        return False
    low = file_line.lower()
    return not any(low.startswith(p) for p in ("n/a", "none", "—", "-", "unknown"))


def _normalise_key(file_line: str) -> str:
    """Return the file portion only for quorum grouping.

    Different providers may flag the same bug at neighbouring lines. Grouping
    by file (not file:line) prevents quorum fragmentation across line offsets.
    """
    return file_line.split(":")[0] if ":" in file_line else file_line


def _parse_vote(provider: str, raw_text: str) -> ProviderVote:
    """Parse a provider's raw text response into a ProviderVote."""
    if not raw_text.strip():
        return ProviderVote(provider=provider, verdict="ERROR", error="empty_response")

    blockers: list[Issue] = []
    for m in _BLOCKER_RE.finditer(raw_text):
        file_line = m.group(1).strip()
        desc = m.group(2).strip()
        if _has_valid_file_line(file_line):
            blockers.append(Issue(
                file_line=file_line,
                description=desc,
                is_security=_is_security_issue(desc),
            ))

    warnings: list[Issue] = []
    for m in _WARNING_RE.finditer(raw_text):
        file_line = m.group(1).strip()
        desc = m.group(2).strip()
        if _has_valid_file_line(file_line):
            warnings.append(Issue(file_line=file_line, description=desc))

    verdict_m = _VERDICT_RE.search(raw_text)
    verdict = verdict_m.group(1) if verdict_m else ("BLOCK" if blockers else "PASS")

    return ProviderVote(
        provider=provider,
        verdict=verdict,
        blockers=blockers,
        warnings=warnings,
        raw_text=raw_text,
    )


_DEFAULT_SYSTEM_PROMPT = (
    "You are a code reviewer. Be precise. "
    "Cite file:line for every issue. "
    "Follow the exact response format in the user message."
)


#: Council rungs that think before they answer and need more output room than
#: the shared 2048 default. Gemini 4 Carbon thinks on every call, and Google's
#: Carbon migration guide asks for at least 32,768 output tokens; on a
#: 1,059-character ideation prompt its answer at 2048 came back shorter than at
#: 32,768 (2,051 against 2,274 characters, 2026-11-26), and a longer prompt
#: leaves its answer even less room.
_COUNCIL_MAX_TOKENS_BY_PROVIDER: dict[str, int] = {
    "gemini/gemini-4-carbon": 32768,
}


def _build_envelope(
    provider_id: str,
    happi_provider: str,
    auth: dict,
    prompt: str,
    system_override: Optional[str] = None,
) -> dict:
    """Build a HAPPI envelope for a single-shot council/AGORA dispatch.

    ``system_override`` lets callers replace the default HAL Council system
    prompt with a role-specific one. AGORA Speakers (plain-language, quality,
    technical) need their own system prompt because the default
    "Cite file:line for every issue" instruction conflicts with AGORA's
    plain-language Speaker A role (which is narrative, not citation-heavy).

    Origin: 2026-05-13. PRs #2670, #2671, #2674 all produced filler-output
    AGORA reviews ("Please provide the code for review") because the hardcoded
    HAL Council system prompt overrode AGORA's user-prompt role assignment.
    Models resolved the conflict by treating the diff as inadequate and
    asking for code, or by hallucinating generic reviews ("main.py:44 is_prime")
    on stock code. Falsified if AGORA filler-rate stays >=5% after the fix.
    """
    # HAL envelope schema (#2862 — the 2026-05-13 transitional dual-write is
    # now resolved). Verify-canonical against ~/.hal origin/main:
    #   - lib/hal/happi/envelope.py::user_message() is the PARAMOUNT canonical
    #     resolver: content > flags.message > args, and it SHORT-CIRCUITS on
    #     content (`if self.content: return self.content`).
    #   - All three chat adapters resolve via envelope.user_message()
    #     (anthropic.py:99, gemini.py:111, openai_compat.py:242) — none
    #     raw-read flags.message. The GRIP#2699 migration ("until HAL is
    #     patched to make all adapters read content") is COMPLETE.
    # Therefore, since `content` is always set below, no HAL adapter ever
    # consults `flags.message` — the prior dual-write was dead/redundant and
    # smuggled the prompt into a slot the #2697 contract forbids. Set
    # `content` only (the documented primary field); never `flags.message`.
    system_prompt = system_override if system_override is not None else _DEFAULT_SYSTEM_PROMPT
    return {
        "v": "happi/1.0",
        "id": f"council-{provider_id[:8]}-{uuid.uuid4().hex[:8]}",
        "cmd": "chat",
        "content": prompt,
        "flags": {
            "provider": happi_provider,
            "max_tokens": _COUNCIL_MAX_TOKENS_BY_PROVIDER.get(happi_provider, 2048),
            "system": system_prompt,
        },
        "auth": auth,
    }


def _shield(value, to=None):
    """``value`` scrubbed by lib/review_redaction.py before it leaves, in every
    invocation shape; with no shield to load it raises OSError and sends nothing."""
    try:
        from review_redaction import shield_payload
    except ImportError:
        try:
            from lib.review_redaction import shield_payload
        except ImportError as exc:  # no shield, no send: callers treat OSError as a failed send
            raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_payload(value, to=to)


def _shield_happi(payload: str, hal_bin: str = "hal") -> str:
    """The HAPPI payload with its prose scrubbed (``content`` and ``flags.system``),
    leaving routing and auth untouched. A payload that is not JSON has no
    structure to keep, so it is scrubbed whole. Raises OSError when it cannot be."""
    try:
        envelope = json.loads(payload)
    except ValueError:
        return _shield(payload, to=[hal_bin, "--happi-api"])
    return json.dumps(_shield(envelope, to=[hal_bin, "--happi-api"]))


def _run_hal_subprocess(
    payload: str, hal_bin: str, timeout: int,
) -> Optional[subprocess.CompletedProcess]:
    """Run `hal --happi-api` with the payload; return the process, or None if it could not run.

    None collapses three distinct failures — too slow, no such binary, and
    present-but-not-executable. Callers that need to tell them apart ask
    ``_subprocess_failure_reason`` rather than reading a widened return type
    here, for the reason given in that function's docstring.

    The payload passes the redaction shield first. When the shield cannot scrub
    it, nothing is sent and this also returns None; that case is logged here,
    because ``_subprocess_failure_reason`` only knows about the binary.
    """
    try:
        payload = _shield_happi(payload, hal_bin)
    except OSError as exc:  # a failed scrub, or no shield to load: nothing is sent
        logger.warning("HAL call not sent: %s", exc)
        return None
    try:
        return subprocess.run(
            [hal_bin, "--happi-api"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        return None
    except PermissionError:
        # The binary exists but is not executable — a botched install, or a
        # copy that lost its mode bit. Measured 2026-08-14: this raises
        # PermissionError, NOT FileNotFoundError, so before this line it
        # escaped uncaught and took the whole council dispatch down instead of
        # degrading to a non-responding provider like every other failure here.
        return None


def _subprocess_failure_reason(hal_bin: str) -> str:
    """Name why ``_run_hal_subprocess`` returned None: too slow, or unusable.

    The two demand opposite responses. A timeout means the binary ran, so the
    call is worth reconsidering; an unusable binary is a configuration fault
    that will fail identically forever. Reporting both as one string
    ("timeout_or_missing_binary") gave a caller nothing it could act on, and
    left an operator to correlate log lines by hand.

    Classified here rather than plumbed out of ``_run_hal_subprocess`` on
    purpose. That function has three production call sites and is patched with
    ``return_value=None`` at seven test sites; widening its return type would
    make every one of those patches silently stop intercepting — a green suite
    testing nothing, which is the failure this change exists to reduce.

    ``shutil.which`` is exact for the question being asked. Measured against
    ``subprocess.run`` on 2026-08-14, it returns a path exactly when the binary
    is runnable, and None both when it is absent and when it exists without the
    executable bit — which are the two non-retryable cases, correctly grouped.
    """
    return _TIMEOUT_REASON if shutil.which(hal_bin) else _UNUSABLE_BINARY_REASON


def _delta_text(text) -> str:
    """A delta's visible answer text.

    Some HAL adapters pass a provider's content blocks through as a list instead
    of a string: Mistral Large 4 streams ``[{"type": "thinking", ...}]`` (measured
    2026-11-26), and joining that raised "sequence item 0: expected str instance,
    list found", which ended the rung. Plain strings and ``text`` blocks are the
    answer; thinking blocks and anything else are not.
    """
    if isinstance(text, str):
        return text
    if not isinstance(text, list):
        return ""
    return "".join(item if isinstance(item, str) else item.get("text", "")
                   for item in text
                   if isinstance(item, str)
                   or (isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)))


def _parse_happi_events(stdout: str) -> tuple[list[str], Optional[str]]:
    """Parse happi JSONL stdout into (delta_parts, error_reason).

    error_reason is non-None iff at least one ``type:error`` event was seen.
    Malformed JSON lines are silently skipped (matches happi's tolerant shape).
    """
    parts: list[str] = []
    error_reason: Optional[str] = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = event.get("type", "")
        if t == "delta":
            parts.append(_delta_text(event.get("text", "")))
        elif t == "error":
            error_reason = event.get("error") or event.get("message") or json.dumps(event)
    return parts, error_reason


# Token usage per HAPPI call, read off the runtime's `completed` event (HAL's
# adapters put {"in", "out"} there, and Gemini adds "thinking"). It lives beside
# the transport rather than in its return value: _call_happi_ex is patched at
# many test sites, and widening its (text, reason) shape would make those patches
# stop intercepting. A caller that wants one sitting's usage takes usage_mark()
# before it starts and usage_since(mark) after. Bounded, so a long-lived process
# cannot grow it without limit. Only the auth SCHEME is kept -- never a token.
_USAGE_LOG: deque = deque(maxlen=10_000)
_USAGE_SEQ = itertools.count(1)
_USAGE_LOCK = Lock()


def _count(value) -> int:
    """A non-negative token count, or 0 for anything that is not one."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _parse_happi_usage(stdout) -> dict:
    """The usage block of the last ``completed`` event, or {} when none carried one."""
    usage: dict = {}
    for line in (stdout if isinstance(stdout, str) else "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (isinstance(event, dict) and event.get("type") == "completed"
                and isinstance(event.get("usage"), dict)):
            usage = event["usage"]
    return usage


def record_usage(provider: str, scheme: str, usage: dict) -> None:
    """Append one call's token usage.

    ``reported`` is False when the call gave no counts -- a timeout, an older
    HAL, a CLI seat -- so a total can say what it excludes instead of reading
    the gap as zero.
    """
    with _USAGE_LOCK:
        _USAGE_LOG.append({
            "seq": next(_USAGE_SEQ), "provider": provider, "scheme": scheme,
            "in": _count(usage.get("in")), "out": _count(usage.get("out")),
            "thinking": _count(usage.get("thinking")), "reported": bool(usage),
        })


def usage_mark() -> int:
    """The sequence number of the newest record; pass it to usage_since()."""
    with _USAGE_LOCK:
        return _USAGE_LOG[-1]["seq"] if _USAGE_LOG else 0


def usage_since(mark: int) -> list[dict]:
    """Every call recorded after ``mark``, oldest first."""
    with _USAGE_LOCK:
        return [dict(r) for r in _USAGE_LOG if r["seq"] > mark]


def _run_and_meter(envelope: dict, hal_bin: str, timeout: int):
    """Run one HAPPI call and record its token usage, whatever the outcome.

    A call that timed out, or whose ``completed`` event carried no usage, is
    recorded as unreported rather than dropped. ``_run_hal_subprocess`` is
    looked up by name, so the tests that patch it still intercept.
    """
    proc = _run_hal_subprocess(json.dumps(envelope), hal_bin, timeout)
    record_usage(str((envelope.get("flags") or {}).get("provider") or ""),
                 str((envelope.get("auth") or {}).get("scheme") or ""),
                 _parse_happi_usage(proc.stdout) if proc is not None else {})
    return proc


@dataclass(frozen=True)
class _DispatchReq:
    """One provider dispatch request — the params threaded through the
    attempt → retry → fallback chain. A single param object replaces the
    repeated 6-arg signature (DRY)."""
    pid: str
    happi_provider: str
    auth: dict
    prompt: str
    hal_bin: str
    timeout: int

    def with_provider(self, pid: str, happi_provider: str, auth: dict) -> "_DispatchReq":
        """Same prompt/bin/timeout, different provider — for fallback substitution."""
        return _DispatchReq(pid, happi_provider, auth, self.prompt, self.hal_bin, self.timeout)


def _log_nonzero_exit(proc, envelope_id: str, provider: str) -> Optional[str]:
    """Log a non-zero happi exit; return the error-reason string.

    The caller decides whether to surface it as a hard error (no parts) or
    keep partial output (#2725: return-but-warn — a SIGKILL'd provider that
    emitted partial deltas must never be counted as a clean complete vote
    silently).
    """
    logger.warning(
        "happi non-zero exit=%d (envelope=%s, provider=%s); stderr: %s",
        proc.returncode, envelope_id, provider, (proc.stderr or "")[:500],
    )
    return f"nonzero_exit_{proc.returncode}"


# HAL's HAPPI providers keep their own HTTP timeout (120 s in
# lib/hal/happi/providers/gemini.py, openai_compat.py and anthropic.py)
# unless the envelope's flags.timeout_s says otherwise, so a subprocess-level
# timeout alone never reaches the model call: a speaker whose seat limit is
# raised past 120 s (e.g. 734 s for a slow reasoning rung) still fails at
# 120 s underneath it. GRIP#10013 fixed this for the AGORA judge only, by
# setting flags.timeout_s in agora_pr_review.py's _judge_envelope. GRIP#10086
# makes it the one shared rule, here where every HAPPI envelope meets its
# subprocess timeout, so every caller of _call_happi_ex / _call_happi gets it
# for free instead of needing its own per-call copy.
_PROVIDER_TIMEOUT_MARGIN_S = 5


def _apply_default_provider_timeout(envelope: dict, timeout: Optional[int]) -> None:
    """MUTATES envelope["flags"]["timeout_s"] in place from timeout, unless
    already set.

    setdefault at both levels: a caller's explicit flags.timeout_s (the
    judge's, via _judge_envelope) always stands, and an envelope with no
    "flags" dict degrades to one being created rather than crashing. A
    falsy timeout (None or 0) means "no deadline" and is left alone.

    AGORA review (GRIP#10086) asked what happens if the SAME envelope dict
    is later reused for a retry or a fallback with a SHORTER timeout --
    setdefault is sticky, so the first, longer timeout_s would survive.
    Checked against every call site that reaches this function
    (_dispatch_attempt, _dispatch_speaker, _ask_judge): each builds a FRESH
    envelope via _build_envelope / _judge_envelope for every attempt,
    including every retry and every fallback rung, so no caller today
    reuses one envelope object across two calls with different timeouts.
    test_a_second_call_with_a_shorter_timeout_does_not_shrink_it pins the
    behaviour this relies on, so a future caller that DOES reuse an
    envelope gets the sticky-first-value contract documented here, not a
    surprise.
    """
    if not timeout:
        return
    envelope.setdefault("flags", {}).setdefault(
        "timeout_s", max(1, int(timeout) - _PROVIDER_TIMEOUT_MARGIN_S))


def _call_happi_ex(envelope: dict, hal_bin: str, timeout: int) -> tuple[str, Optional[str]]:
    """Call `hal --happi-api`; return (delta_text, error_reason).

    ``error_reason`` is non-None when the call failed (timeout, missing
    binary, happi error event, or non-zero exit with no parts). #2725:
    callers inspect it to detect HTTP 429 rate-limiting and retry/fall back
    BEFORE counting a provider as non-responding. Origin: 2026-05-06.
    """
    _apply_default_provider_timeout(envelope, timeout)
    envelope_id = envelope.get("id", "<no-id>")
    provider = envelope.get("flags", {}).get("provider", "<unknown>")
    proc = _run_and_meter(envelope, hal_bin, timeout)
    if proc is None:
        reason = _subprocess_failure_reason(hal_bin)
        if reason == _TIMEOUT_REASON:
            logger.warning(
                "happi subprocess timed out after %ds (envelope=%s, provider=%s)",
                timeout, envelope_id, provider,
            )
        else:
            # Do not quote the timeout here: it played no part, and printing it
            # is what made the old single message unreadable.
            logger.warning(
                "happi binary %r is missing or not executable (envelope=%s, provider=%s)",
                hal_bin, envelope_id, provider,
            )
        return "", reason

    parts, error_reason = _parse_happi_events(proc.stdout)
    if error_reason:
        logger.warning("happi error event (envelope=%s, provider=%s): %s",
                        envelope_id, provider, error_reason)
        return "", error_reason

    if proc.returncode != 0:
        reason = _log_nonzero_exit(proc, envelope_id, provider)
        if not parts:
            return "", reason
        # #2725: partial deltas arrived before a non-zero exit (e.g. the
        # provider was SIGKILL'd mid-stream, rc=137). Return the partial
        # text — but the warning above is the audit trail; never silent.

    text = "".join(parts)
    if not text.strip():
        logger.warning("happi empty completion (envelope=%s, provider=%s, exit=%d)",
                        envelope_id, provider, proc.returncode)
        return text, "empty_completion"
    return text, None


def _call_happi(envelope: dict, hal_bin: str, timeout: int) -> str:
    """Backward-compatible thin wrapper: return only the delta text.

    Retained so existing callers (e.g. AGORA adapters) are unaffected by the
    #2725 (text, reason) tuple split.
    """
    text, _reason = _call_happi_ex(envelope, hal_bin, timeout)
    return text


def _dispatch_attempt(req: _DispatchReq) -> tuple[ProviderVote, Optional[str]]:
    """One single HAPPI call. Returns (vote, error_reason).

    error_reason is non-None on any failure (so the caller can detect 429
    and decide whether to retry / fall back). On success error_reason is
    None and the vote is the parsed verdict.
    """
    try:
        envelope = _build_envelope(req.pid, req.happi_provider, req.auth, req.prompt)
        raw, reason = _call_happi_ex(envelope, req.hal_bin, req.timeout)
        if reason is not None or not raw.strip():
            err = reason or "no_response"
            return ProviderVote(provider=req.pid, verdict="ERROR", error=err), err
        return _parse_vote(req.pid, raw), None
    except Exception as exc:  # noqa: BLE001
        return ProviderVote(provider=req.pid, verdict="ERROR", error=str(exc)), str(exc)


def _dispatch_with_retry(req: _DispatchReq) -> tuple[ProviderVote, Optional[str]]:
    """#2725 option 3: dispatch with jittered exponential backoff on 429.

    Retries ONLY on rate-limit errors (not on timeouts/other failures —
    those are not transient and retrying wastes the council's wall-clock
    budget). Returns the final (vote, error_reason) after retries exhausted.
    """
    vote, reason = _dispatch_attempt(req)
    attempt = 0
    while _is_rate_limit_error(reason) and attempt < _RATELIMIT_MAX_RETRIES:
        sleep_s = _backoff_seconds(attempt)
        logger.warning(
            "provider %s rate-limited (429); backoff %.1fs then retry %d/%d",
            req.pid, sleep_s, attempt + 1, _RATELIMIT_MAX_RETRIES,
        )
        time.sleep(sleep_s)
        vote, reason = _dispatch_attempt(req)
        attempt += 1
    return vote, reason


def _dispatch_one(
    pid: str,
    happi_provider: str,
    auth: dict,
    prompt: str,
    hal_bin: str,
    timeout: int,
    *,
    fallbacks: Optional[list[tuple[str, str, dict, str]]] = None,
    _inflight: Optional[tuple[set, Lock]] = None,
) -> ProviderVote:
    """Dispatch one provider; always returns a ProviderVote.

    #2725 option 2+3: backoff+retry on 429, then substitute the next provider
    down the HAL ladder (``fallbacks``, in cloud→cheap→local order) so a full
    set of N independent responses is still gathered. The vote is tagged with
    its actual responding provider (``provider=fallback_pid``) so the verdict
    is honest about which model voted.

    Falls back on any SUBSTITUTABLE error — one another provider could
    plausibly survive: a rate-limit, or an empty-but-clean completion from a
    model whose reasoning tokens starved its visible output (#4178). It does
    NOT fall back on a code timeout or a malformed envelope, which would fail
    identically on every rung.

    Retry (same provider) still fires only on rate-limit: a starving model
    starves again, so retrying it just burns the council's wall-clock budget.

    Signature kept positional for ``council_converge._request_fixes``
    backward-compat; internally adapted to ``_DispatchReq``.
    """
    # A rung with a timeout override fails fast, and a timeout on it is
    # substitutable (a slow provider yields to a different one) — see
    # _RUNG_TIMEOUT_OVERRIDES. Fallbacks inherit the override timeout so the
    # whole fall-through still fits the future deadline (timeout + slack).
    rung_timeout = _RUNG_TIMEOUT_OVERRIDES.get(happi_provider, timeout)
    fast_fail_rung = rung_timeout < timeout
    req = _DispatchReq(pid, happi_provider, auth, prompt, hal_bin, rung_timeout)
    vote, reason = _dispatch_with_retry(req)
    if not (_is_substitutable_error(reason)
            or (fast_fail_rung and reason == _TIMEOUT_REASON)):
        return vote

    for fb_pid, fb_provider, fb_auth, _desc in (fallbacks or []):
        # #2866 thundering-herd guard: if another concurrent future in this
        # council round is ALREADY substituting into fb_pid, skip to the
        # next fallback rather than pile a redundant concurrent call onto
        # one provider. Correctness is unaffected — _dedupe_active still
        # dedupes by responding-provider id and an all-exhausted chain
        # still honestly degrades to quorum_failure (#2725 contract). The
        # reservation is per-round and released in `finally`, so a later
        # NON-concurrent reuse of the same fallback is still allowed.
        if _inflight is not None:
            _seen, _lk = _inflight
            with _lk:
                if fb_pid in _seen:
                    continue
                _seen.add(fb_pid)
        try:
            # Name the ACTUAL reason. Reporting "still 429" for an empty
            # completion sends the next debugger hunting a rate-limit that
            # never happened.
            logger.warning(
                "provider %s unusable (%s) after %d retries; falling back to %s",
                pid, reason or "unknown", _RATELIMIT_MAX_RETRIES, fb_pid,
            )
            fb_vote, fb_reason = _dispatch_with_retry(
                req.with_provider(fb_pid, fb_provider, fb_auth),
            )
            if not _is_substitutable_error(fb_reason) and fb_vote.verdict != "ERROR":
                return fb_vote
            if not _is_substitutable_error(fb_reason):
                # A non-substitutable failure on a RUNG is the same signal it
                # is on the primary: this fault would recur identically on
                # every remaining rung (a code timeout, a missing hal binary,
                # a malformed envelope). Walking the rest of the ladder would
                # burn ~90s per rung to arrive at the same answer. Stop, and
                # surface the primary's original error.
                logger.warning(
                    "fallback %s failed non-substitutably (%s); remaining rungs "
                    "would fail identically — abandoning the ladder",
                    fb_pid, fb_reason or "unknown",
                )
                break
        finally:
            if _inflight is not None:
                with _inflight[1]:
                    _inflight[0].discard(fb_pid)
    return vote  # all fallbacks exhausted/also-429 — surface the original ERROR


def _dedupe_active(votes: list[ProviderVote]) -> list[ProviderVote]:
    """Non-ERROR votes, deduped by responding-provider id (keep first).

    #2725: provider fallback can substitute a 429'd primary with another
    model. Deduping by responding-provider id guarantees a substitute can
    never double-count one model toward quorum — quorum independence is
    preserved even under degradation.
    """
    seen: set[str] = set()
    active: list[ProviderVote] = []
    for v in votes:
        if v.verdict == "ERROR" or v.provider in seen:
            continue
        seen.add(v.provider)
        active.append(v)
    return active


def _quorum_failure_verdict(
    votes: list[ProviderVote], responded: int,
) -> CouncilVerdict:
    """#2725 option 1+4: the distinct non-code verdict for a degraded reviewer.

    Fewer than MIN_QUORUM_RESPONDERS providers returned a usable vote (after
    backoff+retry and provider fallback). The council cannot judge the PR's
    code: ``is_pass=False`` (a broken reviewer must not wave a PR through —
    this is the council false-NEGATIVE guard, issue #2725 falsification
    clause), ``quorum_failure=True`` and ``confirmed_blockers=[]`` so the
    verdict is unambiguously "reviewer-infrastructure failure, NOT a
    confirmed code block". This is NOT a forced-BLOCK-by-max-iterations:
    it is a first-class verdict the loop/operator can act on differently
    (escalate the reviewer, do not loop it).
    """
    return CouncilVerdict(
        is_pass=False,
        confirmed_blockers=[],
        all_warnings=[],
        votes=votes,
        providers_responded=responded,
        providers_total=len(votes),
        quorum_failure=True,
    )


def mesh_deliberate(
    votes: list[ProviderVote],
    quorum: int = QUORUM,
    min_responders: int = MIN_QUORUM_RESPONDERS,
) -> CouncilVerdict:
    """Apply Broly MESH deliberation to produce a final binary verdict.

    Analyst:          collect all unique file-level issues from all providers
    Devil's Advocate: challenge issues found by only one provider
    Pragmatist:       filter issues lacking valid file:line references
    Integrator:       apply quorum rule → final PASS/BLOCK

    #2725: an explicit quorum POLICY gates the Integrator. With at least
    ``min_responders`` non-ERROR votes the verdict is VALID and decided
    only by the responders (never forced BLOCK by a max-iterations
    timeout). Below the floor the council emits the distinct
    quorum-failure verdict instead of letting low-confidence surviving
    providers false-BLOCK on noise (the #2723 B4 failure mode).
    """
    active = _dedupe_active(votes)

    if len(active) < max(min_responders, 1):
        # Degraded reviewer (provider 429s, all-fail, etc.) — distinct,
        # honest verdict. Subsumes the old all-fail branch.
        return _quorum_failure_verdict(votes, len(active))

    # Analyst: flatten all blocker/warning pairs first, then single-pass group.
    # Flatten is O(V*I) but avoids double-loop iteration in the grouping step.
    flat_blockers = [
        (issue, vote.provider)
        for vote in active
        for issue in vote.blockers
    ]
    flat_warnings = [
        (issue, vote.provider)
        for vote in active
        for issue in vote.warnings
    ]

    blocker_groups: dict[str, list[tuple[Issue, str]]] = {}
    for issue, provider in flat_blockers:
        blocker_groups.setdefault(_normalise_key(issue.file_line), []).append((issue, provider))

    # Devil's Advocate + Integrator: quorum check in one pass over groups.
    # Security issues: ANY single provider can confirm.
    # Other issues: require >= quorum providers to agree.
    confirmed_blockers: list[Issue] = []
    for reporter_pairs in blocker_groups.values():
        is_security = any(i.is_security for i, _ in reporter_pairs)
        if is_security or len(reporter_pairs) >= quorum:
            best = max(reporter_pairs, key=lambda p: len(p[0].description))[0]
            confirmed_blockers.append(best)

    # Collect unique warnings (union, deduplicated by file) — one pass, O(W).
    seen_warning_keys: set[str] = set()
    all_warnings: list[Issue] = []
    for w, _ in flat_warnings:
        key = _normalise_key(w.file_line)
        if key not in seen_warning_keys:
            seen_warning_keys.add(key)
            all_warnings.append(w)

    return CouncilVerdict(
        is_pass=len(confirmed_blockers) == 0,
        confirmed_blockers=confirmed_blockers,
        all_warnings=all_warnings[:10],
        votes=votes,
        providers_responded=len(active),
        providers_total=len(votes),
    )


def _build_fallback_chains(
    roster: list[tuple],
) -> dict[str, list[tuple[str, str, dict, str]]]:
    """Map each provider id → ordered fallback list (all OTHER roster entries).

    #2725 option 2. HAL-ladder order is preserved (roster is declared
    cloud→cheap→local), so a 429'd cloud primary substitutes the next
    cheaper model first. _dedupe_active prevents a model that also responds
    as a primary from being double-counted when consumed as a fallback.
    """
    return {
        pid: [entry for entry in roster if entry[0] != pid]
        for pid, *_rest in roster
    }


_FUTURE_DEADLINE_SLACK_S = 10  # Extra seconds beyond per-call timeout for future.result().


def dispatch_parallel(
    diff_text: str,
    pr_title: str = "",
    providers: Optional[list[tuple]] = None,
    hal_bin: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT_S,
    max_workers: int = 4,
    future_deadline: Optional[int] = None,
) -> list[ProviderVote]:
    """Dispatch the PR review prompt to N providers simultaneously.

    Returns one ProviderVote per provider. Failed providers return
    ProviderVote(verdict="ERROR") — never raises.

    Args:
        future_deadline: Hard wall-clock deadline (seconds) for each
            future.result() call. Defaults to timeout + _FUTURE_DEADLINE_SLACK_S.
            When a future exceeds this deadline the provider is cancelled and
            a tombstone ProviderVote(verdict="ERROR", error="deadline_exceeded")
            is appended — the polling loop always sees a terminal state.
    """
    roster = providers or _PROVIDER_ROSTER
    bin_path = hal_bin or _HAL_BIN_DEFAULT
    deadline = future_deadline if future_deadline is not None else timeout + _FUTURE_DEADLINE_SLACK_S

    diff_truncated = diff_text[:_MAX_DIFF_CHARS]
    if len(diff_text) > _MAX_DIFF_CHARS:
        diff_truncated += "\n\n[diff truncated — showing first 32k chars]"

    prompt = _REVIEW_PROMPT_TEMPLATE.format(
        pr_title=pr_title or "(untitled)",
        diff_text=diff_truncated,
    )

    # #2725 option 2: each primary provider gets the OTHER roster providers
    # as an ordered fallback chain (HAL ladder order). If the primary 429s
    # after backoff+retry, _dispatch_one substitutes the first non-429
    # fallback so a full set of N independent responses is still gathered.
    fb_chains = _build_fallback_chains(roster)
    # #2866: one round-scoped reservation set so concurrent 429'd primaries
    # do not all hammer the same shared fallback provider in this round.
    _round_inflight: tuple[set, Lock] = (set(), Lock())

    votes: list[ProviderVote] = []
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures: dict[Future, str] = {
        executor.submit(
            _dispatch_one, pid, hprov, auth, prompt, bin_path, timeout,
            fallbacks=fb_chains.get(pid),
            _inflight=_round_inflight,
        ): pid
        for pid, hprov, auth, _ in roster
    }
    try:
        for future in as_completed(futures, timeout=deadline):
            pid = futures[future]
            try:
                votes.append(future.result())
            except Exception as exc:  # noqa: BLE001
                votes.append(ProviderVote(provider=pid, verdict="ERROR", error=str(exc)))
    except FutureTimeoutError:
        # as_completed timed out — tombstone any providers not yet collected.
        collected_pids = {v.provider for v in votes}
        for future, pid in futures.items():
            if pid not in collected_pids:
                future.cancel()
                logger.warning(
                    "broly dispatch deadline exceeded for provider %s after %ds",
                    pid, deadline,
                )
                votes.append(ProviderVote(
                    provider=pid,
                    verdict="ERROR",
                    error="deadline_exceeded",
                ))
    finally:
        # Do not wait for hanging threads — they hold no shared state.
        executor.shutdown(wait=False)

    return votes


def run_council_review(
    diff_text: str,
    pr_title: str = "",
    hal_bin: Optional[str] = None,
    quorum: int = QUORUM,
    min_responders: int = MIN_QUORUM_RESPONDERS,
) -> CouncilVerdict:
    """Convenience entry: dispatch + MESH deliberate → final verdict."""
    votes = dispatch_parallel(diff_text, pr_title=pr_title, hal_bin=hal_bin)
    return mesh_deliberate(votes, quorum=quorum, min_responders=min_responders)


__all__ = [
    "Issue",
    "ProviderVote",
    "CouncilVerdict",
    "dispatch_parallel",
    "mesh_deliberate",
    "run_council_review",
    "_parse_vote",
    "_normalise_key",
    "_has_valid_file_line",
    "QUORUM",
    "MIN_QUORUM_RESPONDERS",
    "_PROVIDER_ROSTER",
    "_AGORA_SPEAKER_ROSTER",
    "_JUDGE_PROVIDER",
    "_JUDGE_FALLBACKS",
    "CCH_SCHEME",
    "_build_agora_speaker_roster",
    "_apikey_auth_or_absent",
    "_cch_auth_or_absent",
    "_is_rate_limit_error",
    "_build_fallback_chains",
    "_dedupe_active",
]
