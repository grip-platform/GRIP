"""Exogenous {gemini-4-carbon + gpt-5.5} council callable.

Two genuinely distinct provider families (Google + OpenAI) — exogenous to
Claude/Anthropic. Satisfies the exogenous-anchor-law requirement that the
verifier cannot be controlled or predicted-to-pleased by the generating model.

Usage::

    from lib.exogenous_council import run_exogenous_council, EXOGENOUS_ROSTER

    verdict = run_exogenous_council(diff_text="...", pr_title="fix: foo")
    print(verdict.is_pass, verdict.providers_responded)

    # Inspect roster families (Goodhart check):
    from lib.exogenous_council import roster_provider_families
    assert roster_provider_families() == {"gemini", "openai"}

Design notes
~~~~~~~~~~~~
* ``EXOGENOUS_ROSTER`` is the canonical two-entry list — one gemini entry,
  one openai entry.  Its format mirrors ``_PROVIDER_ROSTER`` in
  ``hal_council_dispatch`` so it can be passed directly to ``dispatch_parallel``.
* ``roster_provider_families()`` extracts the provider family from the happi
  provider string (``"gemini/…"`` → ``"gemini"``).  The test asserts
  ``len(families) == 2``, not just ``len(roster) == 2``, so two gemini entries
  would fail — Goodhart-proof.
* ``_EXOGENOUS_MIN_RESPONDERS = 1`` — one of two responding still produces a
  valid verdict; zero responders triggers ``quorum_failure`` (not a silent PASS).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from hal_council_dispatch import (  # noqa: E402
    CouncilVerdict,
    dispatch_parallel,
    mesh_deliberate,
    QUORUM,
)


try:
    from openai_billing import paid_openai_auth
except ImportError:
    from lib.openai_billing import paid_openai_auth


def _token(env_var: str, keychain_service: str) -> str:
    """Return ``env:VAR`` if set, else ``keychain:SERVICE``.

    Mirrors the private ``_token`` helper in ``hal_council_dispatch`` so this
    module works in CI (env vars) and locally (macOS Keychain) without change.
    """
    return f"env:{env_var}" if os.environ.get(env_var) else f"keychain:{keychain_service}"


# ── Exogenous roster ──────────────────────────────────────────────────────────
# Format: (id, happi_provider_string, auth_dict, human_description)
# gemini-4-carbon       = Google family (replaced gemini-3.1-pro-preview,
#                         V>> 2026-11-26)
# gpt-5.5               = OpenAI family
# Both are non-Anthropic — exogenous to Opus/Claude by construction.

EXOGENOUS_ROSTER: list[tuple[str, str, dict, str]] = [
    (
        "gemini-4-carbon",
        "gemini/gemini-4-carbon",
        {"scheme": "apikey", "token": _token("GEMINI_API_KEY", "grip-gemini")},
        "Google Gemini 4 Carbon",
    ),
    (
        "gpt-5.5",
        "openai/gpt-5.5",
        paid_openai_auth(),
        "OpenAI GPT-5.5",
    ),
]

# Quorum: require both to agree on a BLOCKER before it blocks.
# With only 2 providers, quorum=2 means unanimous agreement is needed.
_EXOGENOUS_QUORUM = 2

# Min responders: 1-of-2 still yields a valid verdict.
# 0-of-2 → quorum_failure (never a silent PASS).
_EXOGENOUS_MIN_RESPONDERS = 1


def roster_provider_families() -> set[str]:
    """Return the set of distinct provider families in EXOGENOUS_ROSTER.

    Extracts the family prefix from the happi provider string
    (``"gemini/gemini-4-carbon"`` → ``"gemini"``).

    Goodhart-proof: the test asserts ``len() == 2``, not ``len(roster) == 2``.
    Two gemini entries would return ``{"gemini"}`` and fail the assertion.
    """
    return {entry[1].split("/")[0] for entry in EXOGENOUS_ROSTER}


def run_exogenous_council(
    diff_text: str,
    pr_title: str = "",
    hal_bin: Optional[str] = None,
    timeout: int = 90,
) -> CouncilVerdict:
    """Run the exogenous {gemini-4-carbon + gpt-5.5} council.

    Dispatches the same prompt to both providers in parallel (via
    ``dispatch_parallel``) then applies MESH deliberation (``mesh_deliberate``).

    Args:
        diff_text: The diff or content to review.
        pr_title:  Optional PR title for context in the review prompt.
        hal_bin:   Path to the ``hal`` binary; defaults to ``~/.hal/bin/hal``.
        timeout:   Per-provider call timeout in seconds.

    Returns:
        A ``CouncilVerdict``.  ``verdict.quorum_failure`` is True when zero
        providers responded — the caller must not treat that as a PASS.
    """
    votes = dispatch_parallel(
        diff_text=diff_text,
        pr_title=pr_title,
        providers=EXOGENOUS_ROSTER,
        hal_bin=hal_bin,
        timeout=timeout,
    )
    return mesh_deliberate(
        votes,
        quorum=_EXOGENOUS_QUORUM,
        min_responders=_EXOGENOUS_MIN_RESPONDERS,
    )


__all__ = [
    "EXOGENOUS_ROSTER",
    "roster_provider_families",
    "run_exogenous_council",
]
