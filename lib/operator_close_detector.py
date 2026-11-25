"""Detect assistant text that tells the operator to stop, or closes on his behalf.

Pure, no I/O, no side effects. Consumed by hooks/operator-close-hook.py.

WHY THIS IS CODE AND NOT A RULE
  `rules/never-close-on-the-operators-behalf.md` and the memory behind it already
  said all of this, and were violated sixteen times in a single session on
  2026-09-02 without the author noticing once. That is a Class B failure in the
  sense of `feedback_grip_forgets_by_not_binding_not_by_not_learning.md`: the
  behaviour recurred DESPITE a written record, and writing a better record is the
  fix that has already been tried and has already failed.

WHY A PHRASING BAN IS NOT ENOUGH
  The impulse does not present as the forbidden thing. After a hard session it
  presents as kindness — landing the conversation gently. So the detector does not
  look for a fixed list of pleasantries. It looks for the three shapes the
  behaviour actually takes, each keyed on the OPERATOR being the subject:

    rest_directive   telling him to sleep, rest or stop
    clock_inference  inferring his state or availability from the hour
    self_close       declaring the session over on his behalf

  Statements about other people are not hits. "Max is asleep" and "let him sleep"
  concern a child; "Deon replied tonight" is a fact about an event. The
  discriminator is whether the operator is the subject, not whether a word appears
  — the substring-matching defect class this codebase has hit seven times.

TWO LAYERS, AND WHICH ONE IS PRIMARY
  `scan()` is a LEXICAL FLOOR, not the detector. It knows only the phrasings that
  have actually been written to the operator, it needed three tuning rounds to
  stop firing on "let him sleep", and the seventeenth phrasing will walk straight
  past it. That non-convergence is the whole thesis of
  `lib/infer_classify.py`: each round of regex tuning buys exactly one corpus.

  `classify_intent()` is the PRIMARY classifier — a cheap model reads the text and
  answers whether the writer is closing on the reader's behalf. It generalises to
  wording nobody has written down. `scan_full()` is the union the hook consumes.

  The floor is kept rather than replaced, for one reason: `classify_intent`
  returns its declared default when no model is reachable, so an inference-only
  detector goes silent offline — the shipped-inert shape this shim exists to
  avoid. The floor is what still fires with no network. It is a net under the
  classifier, not the classifier.

  This is NOT the `llm-augmentation-pattern.md` safety-gate shape, and the
  distinction matters: that rule governs ALLOW/DENY gates where one verdict is
  strictly safer and a model outage must not open the gate. Nothing here blocks
  anything, so being wrong is inefficient rather than unsafe — which is exactly
  the class `infer_classify` documents itself as being for.
"""

from __future__ import annotations

import re
from typing import List, NamedTuple


class Finding(NamedTuple):
    kind: str
    span: str


# Second person, so the subject is the operator rather than a child or third party.
_YOU = r"(?:you|your|yourself)"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # --- telling him to rest or stop -------------------------------------------
    ("rest_directive", re.compile(
        r"\b(?:go\s+to\s+(?:sleep|bed)|go(?:\s+and)?\s+(?:get\s+some\s+)?sleep|get\s+some\s+(?:sleep|rest)"
        r"|sleep\s+well|rest\s+up|call\s+it\s+a\s+night|turn\s+in\s+for\s+the\s+night"
        r"|put\s+the\s+phone\s+down|step\s+away\s+(?:for\s+)?(?:the\s+)?(?:night|now)"
        r"|both\s+of\s+you\s+sleep)",
        re.I)),
    ("rest_directive", re.compile(
        r"\b(?:stop|rest|sleep)\s+now\b|\bnow\s+(?:stop|rest)\b", re.I)),
    # Bare "sleep" ONLY as a sentence-final imperative. A loose \bsleep\b matched
    # "let him sleep", "she will sleep at the hospital" and "sleep where you can hear
    # him" — four false positives in calibration, all about someone other than the
    # operator. Sentence-final position is what makes it an instruction to HIM.
    ("rest_directive", re.compile(
        r"(?:^|[.;!?]\s+|,\s+)(?:and\s+)?(?:then\s+)?(?:go\s+)?sleep\s*[.!]?\s*$",
        re.I | re.M)),
    ("rest_directive", re.compile(
        rf"\b{_YOU}\s+(?:should|need\s+to|ought\s+to|must)\s+(?:sleep|rest|stop|go\s+to\s+bed)\b",
        re.I)),

    # --- inferring his state or availability from the clock ---------------------
    ("clock_inference", re.compile(
        r"\bit(?:'s| is)\s+(?:very\s+|quite\s+|getting\s+)?late\b", re.I)),
    ("clock_inference", re.compile(
        r"\bat\s+this\s+hour\b|\bthis\s+late\b|\bthe\s+hour\s+it\s+is\b", re.I)),
    ("clock_inference", re.compile(
        rf"\b{_YOU}(?:'ve| have)\s+(?:had\s+)?(?:a\s+)?(?:long|hard|brutal|rough|exhausting)\s+"
        r"(?:day|night|evening|week)\b", re.I)),
    ("clock_inference", re.compile(
        rf"\b{_YOU}\s+(?:must\s+be|are|look|sound)\s+(?:exhausted|shattered|worn\s+out|spent)\b",
        re.I)),
    ("clock_inference", re.compile(
        r"\bit(?:'s| is)\s+\d{1,2}[:.]\d{2}\b.{0,60}?\b(?:and|so)\b.{0,40}?"
        rf"\b(?:{_YOU}|stop|enough|sleep|rest)\b", re.I)),

    # --- closing the session on his behalf --------------------------------------
    ("self_close", re.compile(
        r"\bnothing\s+(?:more|else|further)\s+from\s+me\b", re.I)),
    ("self_close", re.compile(
        r"\bthat(?:'s| is)\s+(?:genuinely\s+|really\s+|truly\s+)?(?:everything|all|it|the\s+last\s+of\s+it)"
        r"\s+for\s+(?:tonight|today|this\s+evening)\b", re.I)),
    # A sign-off, not a report of one. "Alice said goodnight to Bob" is somebody
    # else's evening; requiring sentence-initial position drops every reporting
    # verb ("said/says/told/wished ... goodnight") without a verb blocklist.
    ("self_close", re.compile(
        r"(?:^|[.;!?]\s+|\n)\s*(?:good\s?night|goodnight)\b", re.I | re.M)),
    ("self_close", re.compile(
        r"\bwe(?:'re| are)\s+done\s+for\s+(?:tonight|today|the\s+(?:night|day|evening))\b", re.I)),
    ("self_close", re.compile(
        r"\bnothing\s+(?:more|else|further)\s+(?:needs?|requires?)\s+you\s+(?:tonight|today|this\s+evening)\b",
        re.I)),
    ("self_close", re.compile(
        r"\bthat(?:'s| is)\s+the\s+last\s+(?:decision|thing|of\s+it)\b.{0,40}\b(?:tonight|today)\b", re.I)),
]

_MAX_SPAN = 90


def scan(text: str) -> List[Finding]:
    """Return every hit in `text`, deduplicated by (kind, lowered span)."""
    if not text or not isinstance(text, str):
        return []
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            # Some patterns anchor on the preceding sentence boundary, so the
            # match starts with the delimiter. Report the phrase, not the seam.
            span = m.group(0).strip().lstrip(".;!?,").strip()[:_MAX_SPAN]
            key = (kind, span.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(Finding(kind, span))
    return out


def advisory(findings: List[Finding]) -> str:
    """One line naming what was said and what to do instead. Empty when clean."""
    if not findings:
        return ""
    kinds = sorted({f.kind for f in findings})
    spans = "; ".join(f'"{f.span}"' for f in findings[:4])
    return (
        f"[CLOSING ON THE OPERATOR'S BEHALF — {', '.join(kinds)}] the response says: {spans}. "
        "He decides when a session ends, and a clock says nothing about his hours — he works "
        "with North Americans. Cut it and end on the work: what is running, what is ready, "
        "what is next. See rules/never-close-on-the-operators-behalf.md."
    )

# --- layer 2: what the text MEANS --------------------------------------------

# Actor-framed, not topic-framed. The measured lesson from `next_move.is_delegable`
# is that the framing is the lever and not the model: "could an automated agent do
# this alone?" got 0/7 wrong where "does this involve payment?" got 2/8, same model,
# same titles. So this asks what the WRITER is doing TO THE READER, never which
# words are present — and it names the false-positive class explicitly, because
# every one of this detector's tuning rounds was a third party's sleep.
_QUESTION = (
    "The TEXT below was written by an AI assistant to the person it works for. "
    "Is the assistant telling that person to stop working, sleep or rest; or "
    "making a claim about how tired they are, how late it is for them, or when "
    "their day should end; or announcing that the session is finished on their "
    "behalf? Statements about anyone ELSE — a child sleeping, a third party's "
    "night, a scheduled event — are not this."
)

_MAX_TEXT = 4000

# The classifier is an outside model (HAL routes it to Groq), so a turn reaches
# it only through the redaction shield, and only when the caller says it may.
# "not-sent" records that the model was never asked.
NOT_SENT = "not-sent"


def _shielded(text: str) -> str | None:
    """``text`` through the redaction shield, or None when the shield is unavailable."""
    try:
        from review_redaction import WITHHELD, redact
    except Exception:  # noqa: BLE001
        try:
            from lib.review_redaction import WITHHELD, redact
        except Exception:  # noqa: BLE001 -- no shield, so nothing is sent
            return None
    scrubbed = redact(text)
    return None if scrubbed == WITHHELD else scrubbed


def classify_intent(text: str) -> tuple[str, str]:
    """Ask a cheap model whether the text closes on the operator's behalf.

    Returns `(label, source)` where label is "yes"/"no" and source is "cache",
    "model" or "default", mirroring `infer_classify.classify_detail`, or
    "not-sent" when the shield was unavailable and nothing left.

    The default is "no", deliberately. An unreachable model must not manufacture
    an accusation; it means this layer is silent and the lexical floor speaks
    alone. The caller can tell the two apart by reading `source`.
    """
    if not text or not isinstance(text, str):
        return "no", "default"
    try:
        from infer_classify import classify_detail
    except Exception:  # noqa: BLE001 — the floor still works without it
        try:
            from lib.infer_classify import classify_detail
        except Exception:  # noqa: BLE001
            return "no", "default"
    shielded = _shielded(text)
    if shielded is None:
        return "no", NOT_SENT
    try:
        # Scrubbed whole, then cut: a cut first could split a name in two, and
        # the shield recognises neither half.
        return classify_detail(shielded[:_MAX_TEXT], _QUESTION, ("yes", "no"), default="no")
    except Exception:  # noqa: BLE001 — never let a classifier fault reach a Stop hook
        return "no", "default"


def scan_full(text: str, *, ask_model: bool = True) -> tuple[List[Finding], str]:
    """Floor plus classifier, deduplicated. Returns `(findings, source)`.

    `source` describes the SEMANTIC layer only — "model", "cache", "default", or
    "not-sent" when ``ask_model`` is False or the shield was unavailable — so a
    caller can say whether the classifier actually ran. A finding of kind
    `semantic_close` means the model saw something the patterns did not.
    """
    findings = list(scan(text))
    label, source = classify_intent(text) if ask_model else ("no", NOT_SENT)
    if label == "yes" and not findings:
        findings.append(Finding("semantic_close", _summarise(text)))
    return findings, source


def _summarise(text: str, limit: int = 90) -> str:
    """The last non-empty line, which is where a sign-off lands."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return (lines[-1] if lines else (text or "").strip())[:limit]
