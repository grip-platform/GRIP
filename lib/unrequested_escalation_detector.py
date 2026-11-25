"""Detect UNREQUESTED ADVERSARIAL ESCALATION on personal material.

Pure, stdlib-only, no I/O. The hook does the reading; this decides.

INVARIANT THAT MAKES THIS SAFE (rules/critique-the-artefact-not-the-person.md):
this module has NO input carrying operator affect. No distress lexicon, no
sentiment, no "the operator seems upset". Every predicate measures the AGENT'S
OWN behaviour. A claim of distress in a prompt therefore cannot change any
verdict -- which is why this cannot be used to switch off R0 falsification.
Pinned by test_distress_text_is_not_an_input and test_source_carries_no_affect_lexicon.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- P1: artefact evidence (GENEROUS BY DESIGN -- any hit => INERT) -----------
ARTEFACT_COMMANDS = frozenset({
    "code-review", "ultrareview", "reviewing-contributions", "secure-code-review",
    "agora", "broly", "ultrajudge", "ultraread", "research-council", "converge",
    "prove-it", "falsify-self", "verdict", "witness", "threat-modeling",
    "critiquing-physics-claims", "red-teaming-strategic-decisions", "security-mode",
    "evidence-evaluation", "pre-mortem-facilitation", "rsi", "evo", "ultrado",
})
ARTEFACT_TOKEN = re.compile(
    r"(?:\b(?:PR|pull request|issue)\s*#?\d+|#\d{2,}|\b[0-9a-f]{7,40}\b"
    r"|\bgit(?:hub)?\b|\bdiff\b|\bcommit\b|\bdoi:|\barxiv\b|\bpre-?registration\b"
    r"|\bmerge\b|\bbranch\b|\bCI\b|\btest suite\b)",
    re.IGNORECASE,
)

# --- P2: personal-provenance evidence ----------------------------------------
PERSONAL_MEDIA_EXT = frozenset({
    ".m4a", ".mp3", ".wav", ".aac", ".aiff", ".opus", ".amr",
    ".mov", ".mp4", ".heic", ".jpg", ".jpeg", ".png3",
})
PERSONAL_DIR = re.compile(
    r"(voice ?memos|/Messages/|whatsapp-history|/Notes/|/Photos|Photos Library"
    r"|/Recordings|/Downloads/.*(?:voice|memo|recording))",
    re.IGNORECASE,
)
FIRST_PERSON = re.compile(r"\b(?:I|my|me|we|our|us)\b")
LIFE_EVENT = re.compile(
    r"\b(?:said|told|asked|felt|argued|shouted|cried|apologis|forgave|divorc"
    r"|funeral|diagnos|hospital|fired|quit|broke up|moved out|my (?:mum|mother"
    r"|dad|father|wife|husband|partner|son|daughter|brother|sister|friend|boss))",
    re.IGNORECASE,
)

# --- P3: was a new task actually requested? -----------------------------------
IMPERATIVE = frozenset({
    "do", "make", "fix", "write", "run", "check", "add", "remove", "delete",
    "send", "copy", "move", "transcribe", "find", "tell", "show", "explain",
    "look", "try", "use", "build", "review", "analyse", "analyze", "read",
    "open", "close", "merge", "push", "pull", "commit", "test", "list", "give",
    "draft", "summarise", "summarize", "compare", "search", "install", "help",
    "dig", "expand", "continue", "keep", "go", "carry", "elaborate", "assess",
    "audit", "critique", "challenge", "investigate", "verify", "confirm", "look",
})
LOCATOR = re.compile(r"(?:/\w|https?://|\.\w{2,4}\b|#\d+)")
# Leading discourse markers hide the imperative that follows: "now dig deeper",
# "ok so fix it", "please check". Stripped before the imperative test.
MARKER = frozenset({
    "now", "then", "ok", "okay", "so", "please", "also", "and", "but", "actually",
    "right", "next", "just", "maybe", "perhaps", "well", "yeah", "yes", "hmm",
})
# Polite request forms are directives even with no clause-head imperative.
POLITE = re.compile(
    r"\b(?:can|could|would|will) (?:you|we)\b|\blet'?s\b|\bi(?:'d| would) like\b"
    r"|\bgo (?:on|ahead|deeper)\b|\bkeep going\b|\bcarry on\b|\bdig (?:in|deeper)\b"
    r"|\btell me more\b|\bwhat (?:about|else)\b",
    re.IGNORECASE,
)

# --- P4: adversarial framing lexicon (NECESSARY condition; evasion => miss) ---
ADVERSARIAL = re.compile(
    r"\b(?:attack(?:er|ed|ing)?|adversar(?:y|ial|ies)|hostile|exploit(?:ed|ing)?"
    r"|weaponis|weaponiz|leverage(?:d)? against|vulnerab|expose[sd]?|liabilit"
    r"|incriminat|used against (?:you|him|her|them)|discredit|undermine"
    r"|they could|could be used|opposing|opponent|cross-?examin|impeach"
    r"|damaging|compromis|threat(?:en)?)\b",
    re.IGNORECASE,
)

# --- tunables (every threshold named, none inline) ----------------------------
UCD_STRONG = 2          # unrequested depth needed when provenance is a FILE
UCD_WEAK = 3            # ...when provenance is only a pasted narrative
GROWTH_RATIO = 1.5      # last assistant turn vs first in the 3-turn window
ALD_PER_KCHAR = 2.0     # adversarial hits per 1000 chars in the newest turn
NARRATIVE_MIN_WORDS = 120
DIRECTIVE_MAX_WORDS = 25
RATE_LIMIT_TURNS = 10   # at most one advisory per N assistant turns


@dataclass(frozen=True)
class Turn:
    role: str
    text: str


@dataclass(frozen=True)
class Touch:
    path: str
    in_git_repo: bool


@dataclass(frozen=True)
class Window:
    turns: tuple[Turn, ...] = ()
    touches: tuple[Touch, ...] = ()
    commands: tuple[str, ...] = ()
    turns_since_last_advisory: int = 999


@dataclass(frozen=True)
class Verdict:
    fires: bool
    reason: str
    signals: dict = field(default_factory=dict)


def _has_artefact_evidence(w: Window) -> bool:
    if any(c.lstrip("/") in ARTEFACT_COMMANDS for c in w.commands):
        return True
    if any(t.in_git_repo for t in w.touches):
        return True
    return any(ARTEFACT_TOKEN.search(t.text) for t in w.turns)


def _personal_provenance(w: Window) -> str:
    """Return 'file', 'narrative' or '' -- strength of person-subject evidence."""
    for t in w.touches:
        if t.in_git_repo:
            continue
        low = t.path.lower()
        if any(low.endswith(e) for e in PERSONAL_MEDIA_EXT) or PERSONAL_DIR.search(t.path):
            return "file"
    for t in w.turns:
        if t.role != "user" or len(t.text.split()) < NARRATIVE_MIN_WORDS:
            continue
        if len(FIRST_PERSON.findall(t.text)) >= 5 and LIFE_EVENT.search(t.text):
            return "narrative"
    return ""


def _is_directive(text: str) -> bool:
    if "?" in text or LOCATOR.search(text) or len(text.split()) > DIRECTIVE_MAX_WORDS:
        return True
    if POLITE.search(text):
        return True
    for sentence in re.split(r"[.!\n]", text):
        words = [w.strip(",:;") for w in sentence.strip().lower().split() if w.strip(",:;")]
        # Skip leading discourse markers, then test the true clause head.
        i = 0
        while i < len(words) and words[i] in MARKER:
            i += 1
        if i < len(words) and words[i] in IMPERATIVE:
            return True
    return False


def _unrequested_depth(turns: tuple[Turn, ...]) -> int:
    """Consecutive trailing assistant turns whose preceding user turn asked nothing."""
    depth = 0
    for i in range(len(turns) - 1, 0, -1):
        if turns[i].role != "assistant":
            continue
        prev = turns[i - 1]
        if prev.role == "user" and not _is_directive(prev.text):
            depth += 1
            continue
        break
    return depth


def _escalation(turns: tuple[Turn, ...]) -> tuple[bool, float, float]:
    a = [t.text for t in turns if t.role == "assistant"][-3:]
    if len(a) < 3:
        return False, 0.0, 0.0
    first, last = max(len(a[0]), 1), len(a[-1])
    growth = last / first
    ald_now = 1000.0 * len(ADVERSARIAL.findall(a[-1])) / max(last, 1)
    ald_then = 1000.0 * len(ADVERSARIAL.findall(a[0])) / max(len(a[0]), 1)
    return (growth >= GROWTH_RATIO and ald_now >= ALD_PER_KCHAR
            and ald_now >= ald_then), growth, ald_now


def assess(window: Window) -> Verdict:
    """Fire iff the AGENT is escalating unrequested adversarial output on
    personal material. Never reads operator affect."""
    if window.turns_since_last_advisory < RATE_LIMIT_TURNS:
        return Verdict(False, "rate-limited")
    if _has_artefact_evidence(window):
        return Verdict(False, "artefact-critique: inert by construction")
    provenance = _personal_provenance(window)
    if not provenance:
        return Verdict(False, "no person-subject evidence")
    depth = _unrequested_depth(window.turns)
    needed = UCD_STRONG if provenance == "file" else UCD_WEAK
    escalating, growth, ald = _escalation(window.turns)
    sig = {"provenance": provenance, "unrequested_depth": depth,
           "depth_needed": needed, "growth": round(growth, 2), "ald": round(ald, 2)}
    if depth >= needed and escalating:
        return Verdict(True, "unrequested adversarial escalation on personal material", sig)
    return Verdict(False, "below threshold", sig)


# --- The advisory. A MODULE CONSTANT, never generated, never templated from -----
# --- prompt text. Pinned by test_advisory_mandates_truth. Requirement A lives ---
# --- in this string: it orders re-delivery, and forbids withholding.  -----------
ADVISORY = """[CALIBRATION] You are escalating unrequested adversarial analysis on \
personal material ({signals}).

This is NOT an instruction to soften, hedge, or withdraw anything. Same facts, \
different delivery:

1. SAFETY LEADS. If any finding bears on physical safety, money, legal exposure, \
   or another person's wellbeing, state it FIRST and plainly. Never defer it, \
   never soften it, never drop it. That clause overrides everything below.
2. Lead with the single most important finding. Stop volunteering further \
   analysis, and ask what would actually help before continuing.
3. Check every inference that ran past its evidence, and check which way it \
   leaned. Retract only what the evidence does not support.

Withholding a true finding to spare someone's feelings is a FAILURE of this \
check, not a pass."""

_MUST_CONTAIN = ("SAFETY LEADS", "NOT an instruction to soften", "FAILURE of this")


def render_advisory(verdict: Verdict) -> str:
    """Fill only the signal numbers. The instruction text is fixed."""
    sig = ", ".join(f"{k}={v}" for k, v in sorted(verdict.signals.items()))
    return ADVISORY.format(signals=sig)
