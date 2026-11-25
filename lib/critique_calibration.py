"""Signals for `rules/proportionate-critique.md`.

WHAT THIS IS
------------
A calculator, not a gate. It reads text and turn lengths, computes a handful of
deterministic signals, and returns a *recommendation*. It writes nothing, blocks
nothing, and has no authority over any tool call. The only two recommendations it
can return are DELIVER_IN_FULL and STATE_AND_ASK -- both of which mandate saying
every finding you hold. There is deliberately no value meaning "withhold", and no
promotion path to one, because a suppression channel is the failure this rule
exists to prevent in the opposite direction.

WHY IT NEVER READS EMOTION
--------------------------
The obvious design would soften when the operator sounds distressed. That design
is broken twice: an agent cannot observe an inner state (sarcasm, stoicism and
misdirected frustration all defeat it), and it would be an R0 bypass by
construction -- anyone typing "this is upsetting" would switch off falsification,
security findings and adversarial review. So no predicate here takes affect as an
input. Every signal measures either the SUBJECT (what kind of thing is being
critiqued) or the AGENT'S OWN OUTPUT (is it ratcheting). A claim of distress
changes no verdict, which is precisely what makes this unusable as a shield.

WHY safety_critical IS AN INPUT AND NOT AN INFERENCE
---------------------------------------------------
`calibrate` accepts `safety_critical` from the caller and defaults it to False.
If it were inferred, a classifier miss could suppress a safety finding -- the one
error this design must be structurally incapable of. A forgotten flag costs
nothing instead, because the non-override path is STATE_AND_ASK, which still
requires stating every finding.

On top of the caller's flag sits `safety_lexicon_hit`, which can only ever turn
the override ON. It is monotone toward delivery: a false positive produces more
output, never less, so the lexicon can be as generous as it likes.

WHERE TO CALL IT
----------------
Called after a turn is delivered it is an audit signal and cannot unsay anything.
It earns its keep when called WHILE COMPOSING -- before deciding whether to open
a second self-assigned investigation. That is the placement it is written for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Sequence

__all__ = [
    "Subject",
    "Recommendation",
    "Calibration",
    "classify_subject",
    "was_critique_requested",
    "escalation_ratio",
    "adversarial_density",
    "safety_lexicon_hit",
    "calibrate",
    "render_advisory",
    "ADVISORY",
]


class Subject(Enum):
    """What kind of thing is under critique.

    ARTEFACT and AMBIGUOUS behave identically at the recommendation layer. They
    are kept apart so a caller can see *why* full strength was recommended, and
    so the ambiguous population can be measured separately when the rule's
    falsification data is collected.
    """

    ARTEFACT = "ARTEFACT"
    PERSONAL_ACCOUNT = "PERSONAL_ACCOUNT"
    AMBIGUOUS = "AMBIGUOUS"


class Recommendation(Enum):
    """Both values deliver. Neither withholds.

    DELIVER_IN_FULL -- say everything, at full adversarial strength, keep going.
    STATE_AND_ASK   -- say everything you already hold, plainly and once, then
                       ask what would help instead of commissioning yourself a
                       further investigation.
    """

    DELIVER_IN_FULL = "DELIVER_IN_FULL"
    STATE_AND_ASK = "STATE_AND_ASK"


# --------------------------------------------------------------------------
# Artefact evidence -- STRUCTURAL ONLY.
#
# An earlier detector treated bare words ("commit", "merge", "branch",
# "expose") as proof of an artefact, anywhere in the window, including in the
# agent's own output. "He wouldn't commit" is ordinary English in exactly the
# relationship narratives this rule is about, so one such word silently made
# the whole check inert. Everything below therefore requires STRUCTURE a person
# does not produce by accident in prose: a numbered reference, a file
# extension, a fence, a slash command. The one lexical exception is
# _ARTEFACT_NOUN, explained where it is defined.
# --------------------------------------------------------------------------
ARTEFACT_COMMANDS = frozenset({
    "code-review", "ultrareview", "reviewing-contributions", "secure-code-review",
    "agora", "broly", "ultrajudge", "ultraread", "research-council", "converge",
    "prove-it", "falsify-self", "verdict", "witness", "threat-modeling",
    "critiquing-physics-claims", "red-teaming-strategic-decisions", "security-mode",
    "evidence-evaluation", "pre-mortem-facilitation", "rsi", "evo", "ultrado",
    "recap", "aurora", "standup", "task-reconcile", "onboarding-interview",
    "simplify", "security-review",
})

_NUMBERED_REF = re.compile(
    r"\b(?:PR|pull request|issue|ticket|MR)\s*#?\s*\d+"
    r"|(?<![\w#])#\d{2,}\b"
    r"|\bdoi:\s*\S+|\barxiv[:/ ]|\bpre-?registration\b",
    re.IGNORECASE,
)
_SOURCE_EXT = re.compile(
    r"\.(?:py|pyi|ts|tsx|js|jsx|go|rs|rb|java|kt|swift|c|h|cc|cpp|cs|sh|zsh|bash"
    r"|sql|tf|yaml|yml|toml|ini|cfg|json|md|rst|ipynb|lock|gradle|proto)\b",
    re.IGNORECASE,
)
_CODE_STRUCTURE = re.compile(
    r"```|@@ -\d|^\+\+\+ b/|^--- a/|\bdiff --git\b", re.MULTILINE
)
# The one LEXICAL signal, and so the exception to "structural only" above: a
# possessive or demonstrative plus a work-product noun ("my plan", "this
# draft", "our PR"). It exists because the rule names the operator's own work
# product as an artefact. Worth +1, it can never make ARTEFACT alone; its one
# effect is to stop a PERSONAL_ACCOUNT reading. So "the" is not accepted, and
# nor are "class" or a bare "test": "the plan for the weekend", "the test
# results from the clinic" and "my class" name no work product, yet each one
# alone switched the origin case to AMBIGUOUS and silenced the rule (GRIP#5083).
_ARTEFACT_NOUN = re.compile(
    r"\b(?:this|my|our)\s+"
    r"(?:PR|pull request|diff|patch|commit|branch|repo|repository|codebase|module"
    r"|function|test suite|spec|schema|migration|draft|design|design doc"
    r"|proposal|plan|paper|manuscript|abstract|architecture|contract|policy"
    r"|deck|slide|readme|changelog|rule|skill)\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Personal-account evidence.
#
# ".png" is present deliberately: an earlier list carried the typo ".png3",
# which meant the single most common screenshot format escaped the check
# entirely. A screenshot reviewed for security still classifies ARTEFACT,
# because the artefact side of the ledger wins -- see classify_subject.
# --------------------------------------------------------------------------
PERSONAL_MEDIA_EXT = frozenset({
    ".m4a", ".mp3", ".wav", ".aac", ".aiff", ".opus", ".amr", ".ogg",
    ".mov", ".mp4", ".heic", ".jpg", ".jpeg", ".png",
})
_PERSONAL_DIR = re.compile(
    r"voice ?memos|/Messages/|whatsapp-history|/Notes/|/Photos|Photos Library"
    r"|/Recordings|/Camera Roll|/Voicemail",
    re.IGNORECASE,
)
_FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|my|me|myself)\b")
_LIFE_EVENT = re.compile(
    r"\b(?:argued|shouted|cried|apologis|apologiz|forgave|divorc|separat(?:ed|ing)"
    r"|funeral|grief|diagnos|hospital|therapy|counsell|custody|evicted|dumped"
    r"|broke up|moved out|walked out|stopped speaking|fell out)\b",
    re.IGNORECASE,
)
_RELATIONSHIP = re.compile(
    r"\bmy (?:mum|mom|mother|dad|father|wife|husband|partner|girlfriend|boyfriend"
    r"|son|daughter|brother|sister|ex|family|marriage|therapist|doctor)\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Did the operator ASK for critique? Deliberately narrow. "Transcribe this",
# "copy that", "summarise it" are NOT critique requests -- that is the whole
# shape of the origin case.
# --------------------------------------------------------------------------
_CRITIQUE_REQUEST = re.compile(
    r"\b(?:critique|review|red[- ]?team|pre[- ]?mortem|threat[- ]?model|falsify"
    r"|audit|adversarial|steelman|devil'?s advocate|poke holes|tear (?:it|this) apart"
    r"|stress[- ]?test|pen[- ]?test|be (?:brutal|harsh|critical|ruthless)"
    r"|find (?:the )?(?:flaws|holes|problems|bugs|issues|weaknesses)"
    r"|what(?:'s| is| am I| are the)? (?:wrong|missing|weak)"
    r"|what could go wrong|what would break|attack (?:this|it)"
    r"|how (?:would|could) (?:this|it) fail|worst[- ]case)\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Adversarial framing density, measured on the AGENT'S OWN output.
# --------------------------------------------------------------------------
_ADVERSARIAL = re.compile(
    r"\b(?:attack(?:s|ed|er|ers|ing)?|adversar(?:y|ial|ies)|hostile|exploit(?:ed|ing|s)?"
    r"|weaponis\w*|weaponiz\w*|vulnerab\w*|incriminat\w*|discredit\w*|undermin\w*"
    r"|cross[- ]?examin\w*|impeach\w*|damaging|liabilit\w*|opposing counsel"
    r"|used against|leveraged against|turned against|held against)\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Safety lexicon. MONOTONE TOWARD DELIVERY: a hit can only ever force
# DELIVER_IN_FULL, so a false positive costs nothing and the list is generous
# on purpose. Distress vocabulary is deliberately ABSENT -- "anxious",
# "upset", "paranoid" are affect, not safety, and putting them here would
# smuggle an emotion reading in through the back door.
# --------------------------------------------------------------------------
_SAFETY = re.compile(
    # First-person phrasings are the ones a person actually uses, and the first
    # draft of this list had none of them: it matched "kill myself" and "kill her"
    # but not "kill me" -- which is both the commonest self-referential phrasing
    # AND how a REPORTED THREAT is spoken ("he said he would kill me").
    # Erring wide is correct here and is not a trade-off: this lexicon can only
    # FORCE DELIVERY, never suppress, so a false positive delivers a finding that
    # might otherwise have been held. Monotone toward delivery, by design.
    r"\b(?:suicid\w*|self[- ]?harm|kill (?:me|her|him|them|you|myself|himself|herself)"
    r"|want(?:s|ed)? to die|end (?:my|his|her|their) life|take (?:my|his|her) own life"
    r"|no reason to live|better off without me|do(?:n't| not) want to be here"
    # Every inflection, not a hand-picked few: "he was abusing me", "she was
    # choking" and "overdosing" all missed the earlier forms (GRIP#5083).
    r"|overdos\w*|assault\w*|abus\w*|coercive control|stalk(?:er|ing|ed)"
    r"|restraining order|safeguarding|domestic violence|threat(?:en|ened|ening)? "
    r"(?:to|her|him|them|you)|weapon|strangl\w*|chok\w*"
    r"|fraud\w*|embezzl\w*|money launder\w*|criminal charge|prosecut\w*|arrest\w*"
    r"|statute of limitations|limitation period|court date|filing deadline"
    r"|evict\w*|repossess\w*|bankrupt\w*|insolvenc\w*|foreclos\w*"
    r"|irreversible|unrecoverable|data loss|breach of contract|liable for"
    r"|medical emergency|ambulance|A&E|emergency room)\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Tunables. Every threshold named, none inline. Values inherited from the
# earlier escalation detector, which is an anchored starting point rather than
# a measured optimum -- the rule's Falsification section names the data that
# would re-tune them.
# --------------------------------------------------------------------------
ESCALATION_RATIO_THRESHOLD = 1.5
"""Newest agent turn this many times longer than the window's first."""

ADVERSARIAL_DENSITY_THRESHOLD = 2.0
"""Adversarial-framing hits per 1000 characters of the newest agent turn."""

NARRATIVE_MIN_WORDS = 120
"""Below this a first-person passage is an aside, not an account of a life."""

FIRST_PERSON_MIN_HITS = 5
"""First-person markers needed before a long passage counts as an account."""

PERSONAL_EVIDENCE_MIN = 2
"""Personal-side score needed for PERSONAL_ACCOUNT. Two independent signals,
so one relationship noun in passing is AMBIGUOUS rather than personal."""

ARTEFACT_EVIDENCE_MIN = 2
"""Artefact-side score needed for ARTEFACT. One weak noun phrase is not enough."""


@dataclass(frozen=True)
class Calibration:
    """The result. Advisory throughout -- nothing here can stop anything.

    `signals` is the audit trail: every number that produced the
    recommendation, so a reader can disagree with the verdict on the evidence
    rather than on trust.
    """

    recommendation: Recommendation
    subject: Subject
    safety_critical_override: bool
    rationale: str
    signals: Mapping[str, object] = field(default_factory=dict)

    @property
    def delivers_everything(self) -> bool:
        """True for every reachable value. Kept as an explicit statement of the
        design invariant: no recommendation withholds a finding."""
        return self.recommendation in (
            Recommendation.DELIVER_IN_FULL,
            Recommendation.STATE_AND_ASK,
        )


def _artefact_score(text: str, touched_paths: Sequence[str], commands: Sequence[str]) -> int:
    """Weight of evidence that the subject is a submitted artefact.

    Structural evidence only. See the ARTEFACT block above for why bare words
    are excluded.
    """
    score = 0
    if any(c.lstrip("/").strip() in ARTEFACT_COMMANDS for c in commands):
        score += 2
    if any(_SOURCE_EXT.search(p) for p in touched_paths):
        score += 2
    if _NUMBERED_REF.search(text):
        score += 2
    if _SOURCE_EXT.search(text):
        score += 2
    if _CODE_STRUCTURE.search(text):
        score += 2
    if _ARTEFACT_NOUN.search(text):
        score += 1
    return score


def _personal_score(text: str, touched_paths: Sequence[str]) -> int:
    """Weight of evidence that the subject is a person's own account."""
    score = 0
    for path in touched_paths:
        low = path.lower()
        if any(low.endswith(ext) for ext in PERSONAL_MEDIA_EXT):
            score += 1
            break
    haystack = " ".join([text, *touched_paths])
    if _PERSONAL_DIR.search(haystack):
        score += 1
    if _RELATIONSHIP.search(text):
        score += 1
    words = len(text.split())
    if (
        words >= NARRATIVE_MIN_WORDS
        and len(_FIRST_PERSON.findall(text)) >= FIRST_PERSON_MIN_HITS
        and _LIFE_EVENT.search(text)
    ):
        score += 2
    return score


def classify_subject(
    request_text: str,
    *,
    touched_paths: Sequence[str] = (),
    commands: Sequence[str] = (),
) -> Subject:
    """Decide what kind of thing is being critiqued.

    Biased toward ARTEFACT on purpose. `rules/proportionate-critique.md` puts it
    plainly: false silence on a person's real problem is bad, false softening of
    an artefact review is worse, because it corrupts the machinery everything
    else depends on. So any real artefact evidence outranks the personal side,
    and a tie goes to ARTEFACT.
    """
    artefact = _artefact_score(request_text, touched_paths, commands)
    personal = _personal_score(request_text, touched_paths)
    if artefact >= ARTEFACT_EVIDENCE_MIN and artefact >= personal:
        return Subject.ARTEFACT
    if personal >= PERSONAL_EVIDENCE_MIN and artefact == 0:
        return Subject.PERSONAL_ACCOUNT
    return Subject.AMBIGUOUS


def was_critique_requested(request_text: str) -> bool:
    """True when the operator asked for critique, review, or adversarial work.

    Narrow by design. "Transcribe this", "copy that file", "summarise it" must
    all be False: a mechanical request answered with a forensic threat analysis
    is the entire origin case.
    """
    return bool(_CRITIQUE_REQUEST.search(request_text))


def escalation_ratio(turn_output_lengths: Sequence[int]) -> float:
    """How much the agent's output has grown across the supplied window.

    Newest turn relative to the window's first. The caller chooses the window.
    Fewer than two turns has no baseline, so it returns 1.0 -- no growth
    claimed rather than growth assumed, because the honest answer to "is this
    escalating?" with one data point is "cannot tell".
    """
    lengths = [int(n) for n in turn_output_lengths]
    if len(lengths) < 2:
        return 1.0
    baseline = max(lengths[0], 1)
    return lengths[-1] / baseline


def adversarial_density(text: str) -> float:
    """Adversarial-framing hits per 1000 characters.

    Density rather than a raw count, so a long careful piece is not penalised
    for containing the word "attack" twice while a short savage one escapes.
    """
    if not text:
        return 0.0
    return 1000.0 * len(_ADVERSARIAL.findall(text)) / len(text)


def safety_lexicon_hit(text: str) -> bool:
    """True when the text touches physical, legal, or financial safety.

    Monotone toward delivery: this can only turn the safety override ON, so
    over-matching produces more output and never less.
    """
    return bool(_SAFETY.search(text))


def calibrate(
    request_text: str,
    *,
    assistant_turn_lengths: Sequence[int] = (),
    latest_output: str = "",
    touched_paths: Sequence[str] = (),
    commands: Sequence[str] = (),
    safety_critical: bool = False,
    critique_requested: Optional[bool] = None,
) -> Calibration:
    """Compute the signals and recommend a posture.

    Five separate conditions each return DELIVER_IN_FULL, checked in this order:
    a safety override, an explicit request for critique, a subject that is not a
    person's own account, output that is not growing, and output that is not
    adversarially framed. Only when all five fail does STATE_AND_ASK apply -- and
    that still requires stating every finding already held.

    Args:
        request_text: what the operator asked for, plus any account they gave.
        assistant_turn_lengths: character counts of recent agent turns, oldest
            first. The caller windows it.
        latest_output: the newest agent turn, or the draft about to be sent.
            Measured for adversarial framing and scanned for safety vocabulary.
        touched_paths: files read or written while forming the critique.
        commands: slash commands invoked, with or without the leading slash.
        safety_critical: the caller's own judgement. True forces delivery.
            Defaults False because a forgotten flag must cost nothing.
        critique_requested: override the inference from `request_text`. None
            means infer.
    """
    subject = classify_subject(
        request_text, touched_paths=touched_paths, commands=commands
    )
    requested = (
        was_critique_requested(request_text)
        if critique_requested is None
        else bool(critique_requested)
    )
    ratio = escalation_ratio(assistant_turn_lengths)
    density = adversarial_density(latest_output)
    override = bool(
        safety_critical
        or safety_lexicon_hit(latest_output)
        or safety_lexicon_hit(request_text)
    )

    signals: Mapping[str, object] = {
        "subject": subject.value,
        "critique_requested": requested,
        "escalation_ratio": round(ratio, 2),
        "escalation_threshold": ESCALATION_RATIO_THRESHOLD,
        "adversarial_density": round(density, 2),
        "density_threshold": ADVERSARIAL_DENSITY_THRESHOLD,
        "turns_measured": len(list(assistant_turn_lengths)),
        "safety_critical_override": override,
    }

    def _full(reason: str) -> Calibration:
        return Calibration(
            recommendation=Recommendation.DELIVER_IN_FULL,
            subject=subject,
            safety_critical_override=override,
            rationale=reason,
            signals=signals,
        )

    if override:
        return _full(
            "safety-critical: deliver first and plainly, regardless of every "
            "other signal"
        )
    if requested:
        return _full("the operator asked for critique")
    if subject is not Subject.PERSONAL_ACCOUNT:
        return _full(f"subject is {subject.value}, so this rule is silent")
    if ratio < ESCALATION_RATIO_THRESHOLD:
        return _full("output is not ratcheting")
    if density < ADVERSARIAL_DENSITY_THRESHOLD:
        return _full("critique is not adversarially framed")

    return Calibration(
        recommendation=Recommendation.STATE_AND_ASK,
        subject=subject,
        safety_critical_override=override,
        rationale=(
            "unrequested adversarial escalation on a person's own account: "
            "state every finding you hold, then ask what would help"
        ),
        signals=signals,
    )


# --------------------------------------------------------------------------
# The advisory text is a MODULE CONSTANT. It is never generated, never
# templated from prompt text, and never softened. Only the signal numbers are
# interpolated. Its first clause is the safety floor and its last sentence
# forbids the failure mode this whole module could otherwise become.
# --------------------------------------------------------------------------
ADVISORY = """[CALIBRATION] Unrequested adversarial escalation on a person's own \
account ({signals}).

This is NOT an instruction to soften, hedge, delay, or withdraw anything.

1. SAFETY FIRST. Anything bearing on physical safety, abuse, legal exposure, \
money, or irreversible harm is stated first and plainly. Never deferred, never \
softened, never dropped. This clause overrides everything below it.
2. STATE EVERY FINDING you already hold, once, in ordinary words. A finding that \
arrives later is stated later; there is no cut-off.
3. THEN ASK what would actually help, instead of opening another investigation \
nobody commissioned.
4. CHECK EVERY INFERENCE that ran past its evidence, and check which way it \
leaned. Retract only what the evidence does not support.

Withholding a true finding to spare someone's feelings is a FAILURE of this \
check, not a pass."""

_ADVISORY_REQUIRED = (
    "SAFETY FIRST",
    "NOT an instruction to soften",
    "STATE EVERY FINDING",
    "FAILURE of this",
)


def render_advisory(calibration: Calibration) -> str:
    """Fill in the signal numbers. The instruction text never varies."""
    rendered = ", ".join(f"{k}={v}" for k, v in sorted(calibration.signals.items()))
    return ADVISORY.format(signals=rendered)
