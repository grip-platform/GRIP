#!/usr/bin/env python3
"""Bash execution-error classifier — the capture half of the learning loop.

GRIP's learning pipeline (``lib/learning-detector.py`` → ``learning/insights.jsonl``)
captures git commits, GRIP-file edits, skill invocations, gate blocks, and
★ Insight blocks (see ``hooks/rsi-posttool-hook.py``). It has **no** branch for
the most common self-correctable signal: a Bash tool call that GRIP itself ran
and got wrong — a zsh word-split, a ``:r``-modifier misuse, a bad git pathspec,
a guardrail-denied substitution, a macOS ``timeout`` that does not exist. Those
error strings die in the transcript, so GRIP repeats the same shell mistakes
session after session.

This module is the pure, stdlib-only classifier that closes that gap. It maps a
``(tool_output, exit_code)`` pair to a single :class:`BashErrorEvent` (or
``None`` when the call succeeded). It performs **no** I/O — the PostToolUse hook
(``hooks/bash_error_learner_hook.py``) does the side effects (it Popens the
existing ``rsi_auto_collector.py learn-event`` bridge). Keeping classification
pure makes it Goodhart-testable: every pattern can be exercised against a real
in-tree error string with a deterministic, mutation-sensitive expected category.

Design (per ``rules/no-stubs.md`` — every symbol fully implemented):

* :func:`classify` is the only entry point. It returns a complete event or
  ``None``; there is no partial / placeholder path.
* Patterns are ordered **most-specific-first**. The first match wins, so a
  precise category (``zsh_word_split``) is never shadowed by the generic
  fallback (``nonzero_generic``). Ordering is load-bearing and is asserted by
  the Goodhart corpus test.
* A clean call (``exit_code == 0`` and no error signature in the output) maps to
  ``None`` — success is never "learned" as an error.

This file has zero third-party imports (its only non-stdlib import is the pure,
stdlib-only sibling ``lib/secret_redactor.py``) and zero hardcoded operator
paths (``rules/path-portability.md``): it never touches the filesystem at all.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Optional

# Sibling in-repo import (same pattern as hooks/bash_error_learner_hook.py).
# Deliberately NOT wrapped in try/except: a silent ImportError here would ship
# UNREDACTED evidence into a persisted, cross-machine-broadcast file. Failing
# loudly is the safe direction (rules/tests-must-be-able-to-fail.md).
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from secret_redactor import redact_secrets  # noqa: E402

__all__ = ["BashErrorEvent", "classify"]


@dataclass(frozen=True)
class BashErrorEvent:
    """A classified Bash execution error, ready to persist as a learning insight.

    Attributes
    ----------
    category:
        Stable machine key for the error class (e.g. ``"zsh_word_split"``).
        Used for dedup/aggregation downstream; never user-facing prose.
    summary:
        One-line plain-language description of what went wrong, suitable as the
        ``description`` argument to the ``learn-event`` bridge.
    evidence:
        The short snippet of tool output that triggered the match — the
        concrete proof a future reader can verify against. Credential-scrubbed
        by :func:`_safe_evidence` before trimming, because this string is
        PERSISTED and broadcast cross-machine. (Before 2026-08-17 this
        attribute was documented as "redaction-safe" while the code only
        trimmed its length — the claim now matches the mechanism.)
    exit_code:
        The process exit code as classified (``-1`` when the signal came from
        output content rather than a numeric code, e.g. a guardrail denial).
    """

    category: str
    summary: str
    evidence: str
    exit_code: int


class _Pattern(NamedTuple):
    """An ordered classification rule (most-specific-first)."""

    category: str
    summary: str
    regex: re.Pattern


# ---------------------------------------------------------------------------
# Ordered pattern table — MOST SPECIFIC FIRST. The first match wins.
#
# Each regex matches against the combined tool output (stdout+stderr). The
# categories below are the execution-mistake classes GRIP actually makes and
# that the learning loop was blind to. Sources for the literal strings:
#   - zsh word-split / :r-modifier         → rules/bash-safety.md (Syntax Safety)
#   - macOS no `timeout`                    → rules/bash-safety.md (PERMANENT)
#   - GRIP tool-substitution guardrail      → context-gate Gate 5 (observed this session)
#   - git pathspec / dubious ownership      → rules/operational-context.md (VPS git)
#   - rg IO error on missing path           → observed this session (cwd reset)
# ---------------------------------------------------------------------------
_PATTERNS: tuple[_Pattern, ...] = (
    # --- GRIP guardrail denials (no nonzero exit, but a clear correction) ---
    _Pattern(
        "guardrail_tool_substitution",
        "Used grep/find/cat instead of the mandated rg/fd tool — a GRIP "
        "tool-substitution guardrail blocked it (rules/bash-safety.md).",
        re.compile(r"GUARDRAIL: *TOOL SUBSTITUTION", re.IGNORECASE),
    ),
    _Pattern(
        "guardrail_generic",
        "A GRIP PreToolUse guardrail blocked the Bash call — fix the root "
        "cause, never evade the gate (rules/no-guardrail-evasion.md).",
        re.compile(r"\[GUARDRAIL:", re.IGNORECASE),
    ),
    # --- zsh syntax mistakes (rules/bash-safety.md Syntax Safety) ---
    _Pattern(
        "zsh_word_split",
        "zsh word-split / globbing error — quote the variable or expansion, or "
        "use a bash array (rules/bash-safety.md).",
        re.compile(
            r"zsh:[^\n]*\b(?:no matches found|bad pattern|bad substitution|"
            r"event not found|parse error near)",
            re.IGNORECASE,
        ),
    ),
    # --- macOS missing GNU coreutils (rules/bash-safety.md PERMANENT) ---
    # MUST precede the generic command_not_found: `timeout: command not found`
    # also matches that generic pattern, but the macOS-timeout class is more
    # specific and more actionable. Ordering is asserted by the corpus test.
    _Pattern(
        "macos_missing_timeout",
        "Used `timeout` on macOS, which has no GNU timeout — use a Python "
        "subprocess timeout or Popen instead (rules/bash-safety.md).",
        re.compile(
            r"timeout:[^\n]*(?:command not found|No such file)|"
            r"(?:^|\W)timeout: illegal|"
            r"\bg?timeout: command not found",
            re.IGNORECASE,
        ),
    ),
    _Pattern(
        "command_not_found",
        "Command not found — likely a missing tool or a PATH/shell-state "
        "assumption that does not hold (shell is non-interactive).",
        re.compile(
            r"(?:command not found|: not found$)|"
            r"zsh:[^\n]*command not found",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    # --- git mistakes (rules/operational-context.md, commit-standards.md) ---
    _Pattern(
        "git_dubious_ownership",
        "git refused with 'dubious ownership' — run the git op as the owning "
        "user (rules/operational-context.md VPS git).",
        re.compile(r"detected dubious ownership", re.IGNORECASE),
    ),
    _Pattern(
        "git_pathspec_error",
        "git pathspec did not match — a multi-path variable that failed to "
        "word-split, or a wrong/typo'd path (rules/sync-mirror-safety.md).",
        re.compile(
            r"(?:pathspec '[^']*' did not match|"
            r"fatal: pathspec|"
            r"did not match any files?)",
            re.IGNORECASE,
        ),
    ),
    _Pattern(
        "git_non_fast_forward",
        "git push rejected (non-fast-forward) — pull --rebase before retrying "
        "(CLAUDE.md Git Operations).",
        re.compile(
            r"(?:\! \[rejected\]|non-fast-forward|Updates were rejected because)",
            re.IGNORECASE,
        ),
    ),
    _Pattern(
        "git_lock_contention",
        "git index/ref lock held — a concurrent session owns HEAD; prefer "
        "temp-index plumbing (rules/concurrent-commit-plumbing.md).",
        re.compile(
            r"(?:Unable to create '[^']*index\.lock|"
            r"another git process seems to be running|"
            r"could not detach HEAD)",
            re.IGNORECASE,
        ),
    ),
    # --- ripgrep / fd path mistakes (observed: cwd resets between calls) ---
    _Pattern(
        "rg_io_missing_path",
        "ripgrep hit an IO error on a missing path — agent cwd resets between "
        "Bash calls; use absolute paths (rules/bash-safety.md).",
        re.compile(
            r"rg:[^\n]*(?:IO error|No such file or directory)",
            re.IGNORECASE,
        ),
    ),
    _Pattern(
        "no_such_file",
        "A referenced file or directory does not exist — verify the path "
        "(cwd resets between agent Bash calls; prefer absolute paths).",
        re.compile(r"No such file or directory", re.IGNORECASE),
    ),
    _Pattern(
        "permission_denied",
        "Permission denied executing the command — wrong user, missing +x, or "
        "a protected path.",
        re.compile(r"[Pp]ermission denied", re.IGNORECASE),
    ),
    # --- generic syntax (bash/zsh) ---
    _Pattern(
        "shell_syntax_error",
        "Shell syntax error — a malformed command (unbalanced quotes, stray "
        "token, bad redirection).",
        re.compile(
            r"(?:syntax error near unexpected token|"
            r"unexpected end of file|"
            r"parse error near)",
            re.IGNORECASE,
        ),
    ),
)


# Output substrings that, when present, indicate a real error even on a zero
# exit code (a guardrail denial returns the corrective text, not a failure
# code; the harness may surface the denial without a nonzero status).
_ZERO_EXIT_ERROR_SIGNATURES = re.compile(
    r"\[GUARDRAIL:|GUARDRAIL: *TOOL SUBSTITUTION",
    re.IGNORECASE,
)


def _safe_evidence(line: str) -> str:
    """Credential-scrub a raw output line, THEN trim it to ~160 chars.

    Order is load-bearing: scrubbing first means a secret can never be cut in
    half by the trim and leave a usable fragment behind.

    Evidence is not merely displayed — it is persisted to
    ``learning/insights.jsonl`` and broadcast cross-machine, and a shell
    routinely echoes the failing command (and any secret in it) back in stderr,
    e.g. ``/bin/sh: deploy --token <SECRET>: No such file or directory``.
    Before 2026-08-17 this function only trimmed length, so such a line was
    persisted verbatim.
    """
    return redact_secrets(line.strip())[:160]


def _first_line_evidence(text: str, match: re.Match) -> str:
    """Return the matched line, credential-scrubbed and trimmed.

    Uses the line containing the match so the evidence is the actual error,
    not an arbitrary head of the output.
    """
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    if end == -1:
        end = len(text)
    return _safe_evidence(text[start:end])


def _succeeded(text: str, exit_code: int) -> bool:
    """True when the call is a clean success — nothing to learn from.

    Exit 0 alone is not sufficient: a guardrail denial returns corrective text
    with a zero status, so an output signature can still mark a failure.
    """
    return exit_code == 0 and not _ZERO_EXIT_ERROR_SIGNATURES.search(text)


def _specific_event(text: str, exit_code: int) -> Optional[BashErrorEvent]:
    """First (most-specific) matching pattern, or ``None``.

    Ordering is load-bearing: ``_PATTERNS`` is most-specific-first, so a precise
    category is never shadowed by the generic fallback. Asserted by the corpus
    test.
    """
    for pat in _PATTERNS:
        m = pat.regex.search(text)
        if m:
            return BashErrorEvent(
                category=pat.category,
                summary=pat.summary,
                evidence=_first_line_evidence(text, m),
                exit_code=exit_code,
            )
    return None


def _generic_event(text: str, exit_code: int) -> BashErrorEvent:
    """Coarse fallback so a real failure is never silently dropped.

    The falsifier to watch is this category becoming the ONLY one that ever
    fires — that would mean the specific patterns have gone dead.
    """
    lines = text.strip().splitlines()
    return BashErrorEvent(
        category="nonzero_generic",
        summary=(
            f"A Bash command exited non-zero ({exit_code}) without a "
            "recognised error signature — review the command."
        ),
        evidence=_safe_evidence(lines[-1]) if lines else "",
        exit_code=exit_code,
    )


def classify(tool_output: str, exit_code: int) -> Optional[BashErrorEvent]:
    """Classify a Bash tool result into a single error event, or ``None``.

    ``tool_output`` is the combined stdout+stderr (``None`` treated as empty);
    ``exit_code`` is the process exit code. Returns a fully-populated event for
    the most-specific matching pattern, a ``nonzero_generic`` event for an
    unrecognised failure, or ``None`` when the call cleanly succeeded.

    Every returned ``evidence`` string is credential-scrubbed — see
    :func:`_safe_evidence`.
    """
    text = tool_output or ""
    if _succeeded(text, exit_code):
        return None
    event = _specific_event(text, exit_code)
    if event is not None:
        return event
    return _generic_event(text, exit_code) if exit_code != 0 else None
