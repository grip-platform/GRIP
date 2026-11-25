"""Is a parked `(yours)` step a DECISION for the operator, or a CHORE?

Every turn that ends `RECOMMENDED NEXT STEP (yours)` adds a question to the shared
pending store (`lib/pending_question.py`), chores included. Measured on
2026-09-29: the live store held 62 questions, all open, all parked, none ever
answered. A board where everything is waiting on the operator tells him nothing.

The presence Wave 3 brief (drafts/ULTRAPROMPT-2026-09-24-presence-wave2-handoff.md)
says a step should only earn a place if it matches one of the four HALT classes
in `rules/operational-context.md`:

  irreversible     an operation that cannot be undone
  fork             a choice between directions with materially different outcomes
  information-gap  something only the operator knows or has
  commercial       a commercial decision about a client

THE FAIL DIRECTION IS LOAD-BEARING.
  A decision misread as a chore silently disappears from the operator's board:
  expensive, and invisible by construction. A chore kept is merely today's status
  quo. So "unknown", and anything the model could not answer, counts as a
  DECISION. The only verdict that can ever remove anything is "chore", and only
  the floor or a real model answer can produce it. A default can never be a chore:
  `Materiality.__post_init__` refuses to construct one, so no code path can.

TWO LAYERS.
  `classify_floor` is deterministic and high precision, and abstains freely. It
  names a decision only on unmistakable wording, and calls a step a chore only
  when it is short, names session housekeeping (`/context`, `/clear`, a fresh
  session), and carries none of a broad set of words that could mean a choice,
  an external effect, money, a credential or another person.

  `classify` asks the floor first and, only when the floor abstains, asks a cheap
  model through `infer_classify.classify_detail`. That is the same shape as
  `operator_close_detector.classify_intent`: an actor-framed question, explicit
  labels, and a declared default for when no model is reachable.

WHERE EACH LAYER RUNS.
  The Stop hook uses the floor ONLY. Measured 2026-09-29: the unchanged hook runs
  in 31-75 ms; a model call takes 1.0-1.6 s (median 1.26 s over 8 cold calls) and
  its read timeout is 45 s (`lib/hal_llm_adapter.py`), against a 10 s hook
  budget. A hook killed mid-call before the question is written would lose the
  question, which is exactly the expensive error. The model runs offline, in
  `scripts/pending-materiality-report.py`, where latency costs nothing.

SHADOW MODE.
  Nothing is dropped unless `GRIP_PENDING_MATERIALITY_GATE=1` is set. Unset, every
  question is recorded exactly as before, with its verdict attached, so the verdict
  can be measured against real use before anyone trusts it to remove anything.

RESIDUAL, stated rather than left to be found.
  `infer_classify._parse` accepts a sentence that contains exactly one label, so a
  model replying "not a chore" in prose would read as "chore". The question asks
  for one word, and the hook never consults the model, so this can only skew the
  offline report, never the live gate.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

__all__ = [
    "Materiality",
    "UNKNOWN",
    "VERDICTS",
    "SOURCES",
    "HALT_CLASSES",
    "WHY_YOURS",
    "GATE_ENV",
    "classify_floor",
    "classify",
    "gate_armed",
    "should_drop",
]

VERDICTS = ("decision", "chore", "unknown")
SOURCES = ("floor", "model", "cache", "default")
HALT_CLASSES = ("irreversible", "fork", "information-gap", "commercial")

# The HALT class in plain words, for a person reading the board on a phone.
WHY_YOURS = {
    "irreversible": "it cannot be undone once it is done",
    "fork": "it is a choice between directions with different outcomes",
    "information-gap": "it needs something only you know or have",
    "commercial": "it is a commercial decision about a client",
}

GATE_ENV = "GRIP_PENDING_MATERIALITY_GATE"

# The only sources whose "chore" may remove a question. "default" is absent on
# purpose, and `Materiality` refuses to pair it with "chore" in the first place.
_DROPPING_SOURCES = ("floor", "model", "cache")


@dataclass(frozen=True)
class Materiality:
    """One verdict: what kind of step this is, why, and who decided."""

    verdict: str
    halt_class: Optional[str] = None
    source: str = "default"

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict {self.verdict!r} is not one of {VERDICTS}")
        if self.source not in SOURCES:
            raise ValueError(f"source {self.source!r} is not one of {SOURCES}")
        if self.halt_class is not None and (
            self.halt_class not in HALT_CLASSES or self.verdict != "decision"
        ):
            raise ValueError("a HALT class belongs only to a decision")
        if self.verdict == "chore" and self.source == "default":
            raise ValueError(
                "a default can never be a chore: an unanswered classification "
                "must keep the question, never remove it"
            )

    @property
    def keeps(self) -> bool:
        """True unless this is a chore. Unknown renders and counts as a decision."""
        return self.verdict != "chore"

    @property
    def droppable(self) -> bool:
        """True only for a chore named by the floor or a real model answer."""
        return self.verdict == "chore" and self.source in _DROPPING_SOURCES

    @property
    def why_yours(self) -> Optional[str]:
        """The HALT class in plain words, or None when there is no class."""
        return WHY_YOURS.get(self.halt_class) if self.halt_class else None

    def as_dict(self) -> dict:
        """The verdict for a log line. Carries no step text."""
        return {"verdict": self.verdict, "halt_class": self.halt_class,
                "source": self.source}


# What an unanswerable classification resolves to: keep the question.
UNKNOWN = Materiality("unknown", None, "default")


# ------------------------------------------------------------ the floor

def _normalise(text: str) -> str:
    """Lower case, single spaces, and no markdown code or emphasis marks."""
    return " ".join(str(text or "").replace("`", " ").replace("*", " ").split()).lower()


# Unmistakable wording for each HALT class, checked in this order: the most
# serious class wins when a step matches several.
_DECISION: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("irreversible", re.compile(
        r"\bforce[- ]?push\w*|\bpush\s+(?:-f\b|--force)|--force-with-lease"
        r"|\brm\s+-(?:rf|fr)\b"
        r"|\bdrop\s+(?:the\s+)?(?:\w+\s+)?(?:database|db|table|schema)s?\b"
        r"|\bdelet(?:e|es|ed|ing|ion)\b|\bpurg(?:e|es|ed|ing)\b"
        r"|\bwip(?:e|es|ed|ing)\b|\berase[sd]?\b|\berasing\b"
        r"|\brewrit\w*\s+(?:the\s+)?(?:git\s+)?history\b"
        r"|\bpublish\w*"
        r"|\bpost\w*\s+(?:it\s+|this\s+|that\s+)?"
        r"(?:publicly|on\s+linkedin|to\s+linkedin|to\s+the\s+public)\b"
        r"|\bsend(?:s|ing)?\b(?!\s+me\b)"
        r"|\b(?:pay|paying|purchase|purchasing|buy|buying)\b")),
    ("commercial", re.compile(
        r"\binvoic\w*|\bretainer\w*|\bday[- ]rates?\b|\brate\s+card\b"
        r"|\bpayment\s+terms\b|\bstatement\s+of\s+work\b|\bengagement\s+letter\b"
        r"|\bsign\w*\s+(?:the\s+|this\s+|that\s+)?(?:\w+\s+)?contract\b"
        r"|\b(?:client|customer|firm)\b.{0,80}\b(?:pric(?:e|es|ing)|quote|discount)\b"
        r"|\b(?:pric(?:e|es|ing)|quote|discount)\b.{0,80}\b(?:client|customer|firm)\b")),
    ("fork", re.compile(
        r"\bdecid(?:e|ing)\b|\bchoose\b|\bchoice\s+between\b"
        r"|\bwhether\b.{0,160}\bor\b"
        r"|\bwhich\s+(?:one|option|approach|path|direction|way)\b"
        r"|\boption\s+[ab12]\b|\byour\s+call\b|\bgo\s*/\s*no[- ]go\b")),
    # "Tell me to X" and "let me know if you want me to X" ask for a go-ahead on
    # the assistant's own step, never for a fact only the operator has. V>>'s
    # correction of 2026-10-10 quoted "tell me to watch PR #7836 and I'll take it
    # through review and merge", which this pattern used to certify as an
    # information gap. Such steps now fall through to "unknown", which still keeps
    # the question, so this can never remove one; and "me" is in `_VETO`, so they
    # can never read as a chore either. Only the go-ahead forms are excluded:
    # "tell me when you have the 2FA code" and "let me know if your account has
    # access" still ask for something only he has, and stay information gaps.
    ("information-gap", re.compile(
        r"\btell\s+me\b(?!\s+(?:to|(?:when|if|whether)\s+(?:to|i\s+should))\b)"
        r"|\blet\s+me\s+know\b(?!\s+(?:if|when|whether)\s+"
        r"(?:you\s+(?:want|would\s+like)\s+me\s+to|to|i\s+should)\b)"
        r"|\bsend\s+me\b|\bshare\s+your\b"
        r"|\bdo\s+you\s+(?:have|know|remember|recall)\b"
        r"|\bonly\s+you\s+(?:have|know)\b")),
)

# Session housekeeping: the class of step the brief names as a chore.
_CHORE = re.compile(
    r"(?<![\w/])/(?:context|clear|compact|cost|status|save|exit|resume|recall"
    r"|usage|doctor|ultraclear)\b"
    r"|\b(?:fresh|new)\s+(?:claude\s+(?:code\s+)?)?session\b"
    r"|\brestart\s+(?:the\s+|this\s+|your\s+)?(?:session|terminal|shell|claude(?:\s+code)?)\b"
    r"|\breload\s+(?:the\s+|your\s+)?(?:session|shell|terminal|window)\b"
    r"|\bopen\s+a\s+new\s+(?:terminal|tab|window)\b")

# Words that could mean a choice, an external effect, money, a credential or
# another person. Any one of them stops the floor calling a step a chore. It is
# broad on purpose: a false veto costs only an abstention, which the model or the
# default then keeps, while a missed veto could remove a real decision.
_VETO = re.compile(
    r"\?"
    r"|\b(?:or|either|vs|versus|whether|which|unless|me)\b"
    r"|\b(?:decid|decision|choos|choice|pick|prefer|option|approv|authori[sz]"
    r"|sign[- ]?off|go[- ]ahead|consent|agree)\w*"
    r"|\b(?:merg|push|deploy|releas|ship|publish|post|send|email|messag|repl(?:y|ie)"
    r"|respond|announc|share|tweet|invit|forward|paste|report|screenshot|show)\w*"
    r"|\b(?:delet|remov|drop|purg|wip|eras|destroy|reset|revert|rewrit|overwrit"
    r"|forc|kill|terminat|cancel|uninstall|unsubscrib|archiv|rotat|revok|rm)\w*"
    r"|\b(?:pay|paid|purchas|buy|bought|refund|charg|bill|invoic|pric|contract"
    r"|retainer|discount|proposal|budget|client|customer|firm|commercial|money"
    r"|spend)\w*|\bquot(?:e|es|ed|ing)\b|\bfees?\b"
    r"|\b(?:password|passphrase|credential|token|secret|key|keys|login|log\s+in"
    r"|sign\s+in|2fa|otp|oauth|auth\w*|permission\w*|grant\w*|access)\b"
    r"|\b(?:bind|arm|enabl|activat|install|configur|export|set|env|switch|upgrad"
    r"|downgrad|chang|edit|writ|commit)\w*"
    r"|\b(?:tell|ask|call|meet|phone|whatsapp|slack|discord|linkedin|court"
    r"|lawyer|legal|school)\w*")

# A chore is a short instruction. A long step has room for a second clause the
# veto does not know, so past this length the floor leaves it to the model.
_CHORE_MAX_WORDS = 24


def _is_chore(text: str) -> bool:
    return (
        bool(text)
        and len(text.split()) <= _CHORE_MAX_WORDS
        and _CHORE.search(text) is not None
        and _VETO.search(text) is None
    )


def classify_floor(step_text: str) -> Materiality:
    """The deterministic verdict, or an abstention ("unknown", source "floor")."""
    text = _normalise(step_text)
    for halt_class, pattern in _DECISION:
        if pattern.search(text):
            return Materiality("decision", halt_class, "floor")
    if _is_chore(text):
        return Materiality("chore", None, "floor")
    return Materiality("unknown", None, "floor")


# ------------------------------------------------------------ the model

# Actor-framed, following `operator_close_detector`: it asks what the step asks OF
# the person, never which words appear, and it names the safe answer for doubt.
_QUESTION = (
    "The TEXT below is the closing line an AI assistant wrote to the person it "
    "works for, handing that person the next step. Decide what the step asks of "
    "that person. Answer 'irreversible' if it asks them to authorise or carry out "
    "something that cannot be undone: deleting data, force-pushing, publishing or "
    "posting publicly, sending a message on their behalf, or spending money. "
    "Answer 'fork' if it asks them to choose between directions whose outcomes "
    "differ materially. Answer 'information-gap' if it needs a fact, an answer, an "
    "approval or an access that only that person has. Answer 'commercial' if it is "
    "a commercial decision about a client: pricing, terms, a contract or an "
    "invoice. Answer 'chore' ONLY if it is routine housekeeping that needs none of "
    "the person's judgement, knowledge or authority, such as restarting a session "
    "or running a status command, so that nothing would be lost if it never "
    "appeared on their list of decisions. Answer 'unsure' if you cannot tell. When "
    "torn between 'chore' and any other answer, do not answer 'chore'."
)
_LABELS = HALT_CLASSES + ("chore", "unsure")
_MAX_TEXT = 2000

InferFn = Callable[..., tuple]


def _default_infer() -> Optional[InferFn]:
    """`infer_classify.classify_detail`, or None when it cannot be imported."""
    try:
        from infer_classify import classify_detail
    except Exception:  # noqa: BLE001 - try the package form next
        try:
            from lib.infer_classify import classify_detail
        except Exception:  # noqa: BLE001 - no model layer; the default keeps it
            return None
    return classify_detail


def _from_label(label: str, source: str) -> Materiality:
    """Map a model answer to a verdict. Anything unexpected keeps the question."""
    if source == "default" or label not in _LABELS:
        return UNKNOWN
    if label == "chore":
        return Materiality("chore", None, source)
    if label in HALT_CLASSES:
        return Materiality("decision", label, source)
    return Materiality("unknown", None, source)


def classify(step_text: str, *, infer: Optional[InferFn] = None) -> Materiality:
    """Floor first; the model only when the floor abstains. Never raises.

    `infer` takes `classify_detail`'s signature and exists so tests can answer
    without a network. Left as None, the real `infer_classify` is used.
    """
    try:
        floor = classify_floor(step_text)
        if floor.verdict != "unknown":
            return floor
        fn = infer if infer is not None else _default_infer()
        if fn is None:
            return UNKNOWN
        label, source = fn(str(step_text or "")[:_MAX_TEXT], _QUESTION, _LABELS,
                           default="unsure")
        return _from_label(label, source)
    except Exception:  # noqa: BLE001 - a classifier fault must keep the question
        return UNKNOWN


# ------------------------------------------------------------ the gate

def gate_armed(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only when the gate variable is exactly "1". Absence is never permission."""
    raw = (env if env is not None else os.environ).get(GATE_ENV, "")
    return str(raw).strip() == "1"


def should_drop(materiality: Optional[Materiality],
                env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether a step must NOT be added: armed gate AND a real chore verdict."""
    return (materiality is not None and materiality.droppable
            and gate_armed(env))
