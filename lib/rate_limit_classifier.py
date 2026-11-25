"""rate_limit_classifier — deterministic transient-vs-usage-cap 429 discriminator.

The L1 mechanical floor for ``rules/rate-limit-never-blocks.md``. A 429 is not one
thing:

  * a TRANSIENT server limit ("Server is temporarily limiting requests", "not your
    usage limit", "overloaded") is a load-shed the provider can recover from — the
    remedy (retry / failover) is ALWAYS available, so it can never terminally block;
  * a USAGE_CAP ("you have reached your usage limit", "you've hit your session
    limit", "quota exceeded", "credit balance is too low", a reset timestamp) is a
    genuine ceiling — a bounded wait-to-reset or an escalation, not a retry.
    ``cap_reset`` reads the reset time such a message names.
  * a MODEL_CAP ("You're out of usage credits. Run /usage-credits to keep using
    <model>", often followed by "or /model to switch models") is ONE model's
    allowance running out while every other model still runs on the subscription
    -- the remedy is switching model, never a retry and never the paid API-key
    bucket (GRIP#7274).

The verdict is read off the harness/provider WORDING, never the bare status code —
exactly as the rule prescribes. Until now that discrimination lived only as prose
("distinguish transient vs usage-cap at the CALL SITE by the harness wording" —
``bin/grip-auth-failover``); this module makes it a single, tested function so no
call site has to eyeball the message. When the wording carries no decisive signal
the verdict is UNKNOWN with ``retryable=True`` — the safer-to-retry default the rule
mandates (the cost of a needless retry is seconds; the cost of a false "blocked" is
a stalled operation).

Pure stdlib, zero GRIP imports — portable, mockable, importable from both ``bin/``
and ``lib/``.

Falsification (inherited from the rule): wrong if real provider messages routinely
carry BOTH a cap phrase and a transient phrase such that the cap-before-transient
precedence mis-routes more than it helps → re-tune the signal lists (never drop the
default-to-transient). Track: false-"blocked" reports off a transient 429 (target 0).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["TRANSIENT", "USAGE_CAP", "MODEL_CAP", "UNKNOWN", "Verdict", "classify",
           "is_retryable", "model_cap_model", "cap_reset"]

TRANSIENT = "transient"
USAGE_CAP = "usage_cap"
MODEL_CAP = "model_cap"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Verdict:
    """Immutable classification result.

    ``retryable`` is the single actionable bit — True means retry/failover
    (never a terminal "blocked"), False means a bounded wait or an escalation.
    ``kind`` carries the nuance a caller may want (a *confident* transient vs an
    ambiguous message that merely *defaulted* to retry). ``reason`` names the
    exact signal that fired, for audit and plain-language surfacing.
    """

    kind: str
    retryable: bool
    reason: str

    @property
    def is_cap(self) -> bool:
        return self.kind == USAGE_CAP

    @property
    def is_model_cap(self) -> bool:
        """One model's allowance is used up. ``is_cap`` stays False: the account is
        not capped, and every other model still runs on the subscription."""
        return self.kind == MODEL_CAP


# An explicit negation of a cap phrase ("This is not your usage limit") is the
# canonical Anthropic overloaded wording — it MUST win over the bare "usage limit"
# substring, so it is checked first and short-circuits to TRANSIENT.
_NEGATED_CAP = (
    "not your usage limit",
    "not a usage limit",
    "not the usage limit",
    "isn't your usage limit",
    "is not your usage limit",
)

# ONE model's allowance used up, not the account's (GRIP#7274): running out of credits
# AND evidence that one model is meant -- a model named after "keep using" (see
# _MODEL_NAMED) or an offer to switch models. Running out alone could be the whole
# account, and an offer to switch alone is no limit at all. Checked before _CAP_SIGNALS,
# so a cap phrase in the same message cannot turn one model's limit into a cap on every
# model.
_MODEL_CAP_EXHAUSTED = ("out of usage credits",)
# Only a positive offer counts: the /model command spelled as an instruction. The bare
# words "switch models" also occur in "cannot switch models", which an account-wide
# message could say (GPT-6 Astra's review of GRIP#7495).
_MODEL_CAP_SWITCH = ("/model to switch", "run /model", "or /model")
# The spent model, as the harness names it: "... keep using Fable 5.1 or /model ..." or
# "... keep using Fable 5.1." The name ends at " or ", at a full stop that ends a sentence
# (not the one inside "5.1"), or at the end of the message.
_MODEL_NAMED = re.compile(r"keep using (.+?)(?: or |\.(?:\s|$)|$)", re.IGNORECASE)
# A model name as the harness spells one: words, then a version number at the end
# ("Fable 5.1", "Opus 5.5"). A digit anywhere is not enough: "your account for 30 days"
# has one (the same review).
_VERSIONED_MODEL = re.compile(r"^[A-Za-z][\w.-]*(?: [A-Za-z][\w.-]*)* \d+(?:\.\d+)*$")

# A genuine ceiling — bounded wait / escalate, NOT retry.
_CAP_SIGNALS = (
    "usage limit",
    "quota exceeded",
    "exceeded your quota",
    "plan limit",
    # The subscription's 5-hour limit: "You've hit your session limit · resets 7:20am
    # (UTC)". No other phrase here matched it, so at status 429 it counted as a
    # transient throttle and engaged the paid key (GRIP#7274, 13-day replay).
    "session limit",
    "weekly limit",
    "monthly limit",
    "daily limit",
    "limit reached",
    "reached your limit",
    "credit balance is too low",
    "insufficient credit",
    "insufficient quota",
    "upgrade your plan",
    "upgrade to continue",
    "limit resets",
    "resets at",
    "will reset",
)

# The reset a cap message names, after "resets " (and an optional "at "): up to the
# closing parenthesis of the time zone the harness appends, or to the end of the message.
_RESET_NAMED = re.compile(r"\bresets (?:at )?([^)]*\)|.*)", re.IGNORECASE)

# A load-shed the provider recovers from — retry / failover.
_TRANSIENT_SIGNALS = (
    "temporarily limiting",
    "temporarily unavailable",
    "temporarily",
    "overloaded",
    "server is busy",
    "servers are busy",
    "service unavailable",
    "high demand",
    "at capacity",
    "try again",
    "please retry",
    "503",
)


def classify(
    message: str,
    *,
    retry_after: bool = False,
    reset_at: str | None = None,
) -> Verdict:
    """Classify a 429-ish provider/harness message as transient vs usage-cap.

    Precedence (each rung short-circuits):
      1. explicit cap-negation ("not your usage limit") -> TRANSIENT
      2. out of usage credits AND one model meant (a
         versioned name after "keep using", or an
         offer to switch models)                        -> MODEL_CAP
      3. explicit usage-cap wording                     -> USAGE_CAP
      4. explicit transient wording                     -> TRANSIENT
      5. a usage reset timestamp (``reset_at``)          -> USAGE_CAP
      6. a ``Retry-After`` header (``retry_after``)      -> TRANSIENT
      7. nothing decisive                                -> UNKNOWN (retryable)

    Args:
      message:     the human-readable error text from the provider/harness.
      retry_after: True if the response carried a ``Retry-After`` header.
      reset_at:    a usage-reset timestamp string, if the response supplied one.
    """
    text = " ".join((message or "").lower().split())

    for phrase in _NEGATED_CAP:
        if phrase in text:
            return Verdict(TRANSIENT, True, f"negated-cap phrase: {phrase!r}")
    model_cap = _model_cap_reason(text)
    if model_cap:
        return Verdict(MODEL_CAP, False, model_cap)
    for phrase in _CAP_SIGNALS:
        if phrase in text:
            return Verdict(USAGE_CAP, False, f"usage-cap phrase: {phrase!r}")
    for phrase in _TRANSIENT_SIGNALS:
        if phrase in text:
            return Verdict(TRANSIENT, True, f"transient phrase: {phrase!r}")

    if reset_at:
        return Verdict(USAGE_CAP, False, f"reset timestamp present: {reset_at}")
    if retry_after:
        return Verdict(TRANSIENT, True, "Retry-After header present")

    return Verdict(UNKNOWN, True, "no decisive signal; defaulting to retry (rule)")


def _model_cap_reason(text: str) -> str:
    """The audit reason when the normalised ``text`` says one model's allowance is used
    up -- out of usage credits, AND a model named or an offer to switch -- else ""."""
    exhausted = next((p for p in _MODEL_CAP_EXHAUSTED if p in text), "")
    model = model_cap_model(text)
    switch = next((p for p in _MODEL_CAP_SWITCH if p in text), "")
    if exhausted and (model or switch):
        evidence = f"model named {model!r}" if model else repr(switch)
        return f"model-cap phrases: {exhausted!r} + {evidence}"
    return ""


def model_cap_model(message: str) -> str:
    """The model a model-cap message names after "keep using " ("Fable 5.1"), or "" when
    it names none. The name must end in a version number: "Claude" is not one particular
    model, and "your account for 30 days" is not a model at all."""
    match = _MODEL_NAMED.search(" ".join((message or "").split()))
    name = match.group(1) if match else ""
    return name if _VERSIONED_MODEL.match(name) else ""


def cap_reset(message: str) -> str:
    """The reset time a usage-cap message names, or "" when it names none.

    "You've hit your session limit · resets 7:20am (UTC)" gives "7:20am (UTC)": the text
    after "resets " up to the closing parenthesis, or to the end of the message without a
    final full stop. The text keeps the message's own spelling; nothing is parsed into a
    time, because the harness prints a clock time and a time zone, not a date."""
    match = _RESET_NAMED.search(" ".join((message or "").split()))
    return match.group(1).strip().rstrip(".") if match else ""


def is_retryable(
    message: str,
    *,
    retry_after: bool = False,
    reset_at: str | None = None,
) -> bool:
    """Convenience gate: True => retry/failover, False => bounded wait/escalate."""
    return classify(message, retry_after=retry_after, reset_at=reset_at).retryable
