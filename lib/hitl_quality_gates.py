"""HITL quality-gate validators for AskUserQuestion payloads.

Enforces the universal quality gates documented in skills/hitl/SKILL.md:

- Header length <= 12 chars
- 2-4 options per CHOICE question; no options at all on a typed question
- 1-4 questions per batched AskUserQuestion call
- Every question text ends with `?`
- No "Other" mention in question text or option labels
  (AskUserQuestion UI injects the Other option automatically)
- A question's `kind` is one of choice / text / number, or absent

Claude Code carries two AskUserQuestion schemas, selected at runtime. The
older "dialog" schema has choice questions only. The newer "form" schema
adds `kind: "text"` (a text box, NO options, takes a `placeholder`) and
`kind: "number"` (a numeric control, requires `min` and `max`). See
skills/creating-form-input/SKILL.md.

`kind` defaults to "choice" everywhere, so every caller written against the
dialog schema keeps its existing behaviour byte for byte.

Usage:

    from lib.hitl_quality_gates import validate_questions, QualityGateViolation

    try:
        validate_questions(questions)
    except QualityGateViolation as e:
        print(f"Gate violation: {e}")

Designed to be called:

- Pre-tool-use hook, blocking Claude Code's AskUserQuestion calls that
  violate the gates
- Pre-commit / CI lint, scanning example AskUserQuestion JSON payloads
  in skill docs
- Unit tests validating any skill's wizard shape programmatically
"""

from __future__ import annotations

import re
from typing import Any, Iterable

MAX_HEADER_LEN = 12
MIN_OPTIONS_PER_QUESTION = 2
MAX_OPTIONS_PER_QUESTION = 4
MIN_QUESTIONS_PER_CALL = 1
MAX_QUESTIONS_PER_CALL = 4

# Form-schema question kinds. The binary's own guidance: "Omit kind for an
# ordinary choice question. Use "kind": "text" for an open-ended question (a
# text box, no options) and "kind": "number" with "min"/"max" (optionally
# "step", "defaultValue", "unit") for a quantity."
VALID_KINDS = ("choice", "text", "number")
DEFAULT_KIND = "choice"

# Kinds that render their own input control and therefore carry no options.
_TYPED_KINDS = ("text", "number")

# Capital-O word boundary match targets the UI's "Other" button reference
# specifically. Lowercase "other" in words like "another", "mother" is
# legitimate and must not trigger. Legitimate capital-O "Other" at sentence
# start ("Other considerations...") is also poor option wording per the
# anti-pattern table in skills/creating-wizards/SKILL.md.
# Both the dialog and the form forbid these as authored OPTION NAMES. The
# dialog UI appends its own "Other"; the form lets the operator type into any
# question and leave any blank, and its schema is explicit -- "There should be
# no 'Other' or 'Skip' option".
#
# Scope differs between the two words, deliberately:
#
#   Other -- question text, option labels AND descriptions. This is
#            pre-existing behaviour and is left exactly as it was.
#   Skip  -- question text and option labels ONLY.
#
# Skip is NOT checked in descriptions because the prohibition is on offering an
# option *named* Skip, and a description is prose about an option, not its name.
# "Skip the slow ones and run the rest" is a legitimate explanation, and an
# external reviewer caught the first version of this rejecting it. The tell that
# it was over-scoped: a test in this suite had its description reworded to get
# around the gate rather than the gate being questioned.
#
# Capital-letter word-boundary matching throughout: lowercase "skip" and the
# "other" inside "another" are ordinary English and must never trip the gate.
_OTHER_MENTION_PATTERN = re.compile(r"\bOther\b")
_OPTION_NAME_PATTERN = re.compile(r"\b(?:Other|Skip)\b")


class QualityGateViolation(ValueError):
    """Raised when a HITL call violates a universal quality gate."""


def validate_header(header: Any) -> None:
    """Header must be a non-empty string <= MAX_HEADER_LEN characters."""
    if not isinstance(header, str):
        raise QualityGateViolation(
            f"header must be a string, got {type(header).__name__}"
        )
    if not header:
        raise QualityGateViolation("header must not be empty")
    if len(header) > MAX_HEADER_LEN:
        raise QualityGateViolation(
            f"header length {len(header)} exceeds "
            f"MAX_HEADER_LEN={MAX_HEADER_LEN}: {header!r}"
        )


def validate_question_text(question: Any) -> None:
    """Question text must be a non-empty string ending with `?`."""
    if not isinstance(question, str):
        raise QualityGateViolation(
            f"question must be a string, got {type(question).__name__}"
        )
    if not question:
        raise QualityGateViolation("question must not be empty")
    if not question.rstrip().endswith("?"):
        raise QualityGateViolation(
            f"question must end with '?', got: {question!r}"
        )


def validate_kind(kind: Any) -> None:
    """Question `kind` must be absent, or one of VALID_KINDS.

    Absent means an ordinary choice question -- the form schema documents
    `kind` as omitted for the default case, so `None` is valid rather than
    merely tolerated.
    """
    if kind is None:
        return
    if not isinstance(kind, str):
        raise QualityGateViolation(
            f"kind must be a string or absent, got {type(kind).__name__}"
        )
    if kind not in VALID_KINDS:
        raise QualityGateViolation(
            f"kind {kind!r} is not one of {list(VALID_KINDS)}"
        )


def validate_option_count(options: Any, kind: str = DEFAULT_KIND) -> None:
    """Option count rules, which depend on the question's kind.

    A `choice` question (the default) needs MIN..MAX options. A `text` or
    `number` question renders its own control and must carry NO options --
    absent, not merely an empty list. An empty list is a caller who believes
    the key is mandatory and is about to render a chooser with nothing in it,
    which is a different defect from a correctly typed question.
    """
    if kind in _TYPED_KINDS:
        if options is None:
            return
        raise QualityGateViolation(
            f"a {kind!r} question must not carry options "
            f"(got {type(options).__name__}); it renders its own input control"
        )

    if not isinstance(options, list):
        raise QualityGateViolation(
            f"options must be a list, got {type(options).__name__}"
        )
    n = len(options)
    if n < MIN_OPTIONS_PER_QUESTION or n > MAX_OPTIONS_PER_QUESTION:
        raise QualityGateViolation(
            f"option count {n} outside allowed range "
            f"[{MIN_OPTIONS_PER_QUESTION}, {MAX_OPTIONS_PER_QUESTION}]"
        )


def validate_number_range(q: Any) -> None:
    """A `number` question must carry a usable min/max range.

    Silent for every other kind. The binary documents `defaultValue` as
    constrained to the min/max range, so a default outside it is a defect the
    UI cannot render honestly.
    """
    if not isinstance(q, dict) or q.get("kind") != "number":
        return

    missing = [k for k in ("min", "max") if q.get(k) is None]
    if missing:
        raise QualityGateViolation(
            f"a 'number' question requires {' and '.join(missing)}"
        )

    lo, hi = q.get("min"), q.get("max")
    for name, val in (("min", lo), ("max", hi)):
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise QualityGateViolation(
                f"{name} must be a number, got {type(val).__name__}"
            )
    if lo >= hi:
        raise QualityGateViolation(
            f"min {lo} must be less than max {hi}"
        )

    default = q.get("defaultValue")
    if default is not None:
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            raise QualityGateViolation(
                f"defaultValue must be a number, got {type(default).__name__}"
            )
        if not (lo <= default <= hi):
            raise QualityGateViolation(
                f"defaultValue {default} outside range [{lo}, {hi}]"
            )


def validate_option_shape(option: Any, index: int) -> None:
    """Each option must be a dict with non-empty `label` and `description`."""
    if not isinstance(option, dict):
        raise QualityGateViolation(
            f"option[{index}] must be a dict, got {type(option).__name__}"
        )
    label = option.get("label")
    description = option.get("description")
    if not isinstance(label, str) or not label:
        raise QualityGateViolation(
            f"option[{index}].label must be a non-empty string"
        )
    if not isinstance(description, str) or not description:
        raise QualityGateViolation(
            f"option[{index}].description must be a non-empty string"
        )


def validate_no_other_mention(question: str, options: list[dict]) -> None:
    """Neither question text nor option labels may offer 'Other' or 'Skip'.

    The AskUserQuestion UI injects the Other option automatically; mentioning
    it in text is redundant and distracting. See anti-patterns in
    skills/creating-wizards/SKILL.md and skills/hitl/SKILL.md.
    """
    hit = _OPTION_NAME_PATTERN.search(question)
    if hit:
        raise QualityGateViolation(
            f"question must not mention {hit.group(0)!r} "
            f"(the UI provides it, or the operator may simply leave it blank): "
            f"{question!r}"
        )
    for i, opt in enumerate(options):
        if not isinstance(opt, dict):
            # Shape was already reported by validate_option_shape; skip.
            continue
        label = opt.get("label", "")
        description = opt.get("description", "")
        lab_hit = _OPTION_NAME_PATTERN.search(label) if isinstance(label, str) else None
        if lab_hit:
            raise QualityGateViolation(
                f"option[{i}].label must not mention {lab_hit.group(0)!r}: {label!r}"
            )
        desc_hit = (_OTHER_MENTION_PATTERN.search(description)
                    if isinstance(description, str) else None)
        if desc_hit:
            raise QualityGateViolation(
                f"option[{i}].description must not mention {desc_hit.group(0)!r}: "
                f"{description!r}"
            )


def _collect_question_violations(q: Any) -> list[str]:
    """Collect ALL quality-gate violations from a single question dict.

    Internal helper shared by `validate_question` (raises on first) and
    `collect_violations` (accumulates across the whole batch).
    """
    if not isinstance(q, dict):
        return [f"question must be a dict, got {type(q).__name__}"]

    violations: list[str] = []

    kind = q.get("kind")
    try:
        validate_kind(kind)
        effective_kind = kind if isinstance(kind, str) and kind else DEFAULT_KIND
    except QualityGateViolation as e:
        violations.append(str(e))
        # Carry on under choice semantics so the caller still learns about
        # every other fault in the same pass.
        effective_kind = DEFAULT_KIND

    for validator, arg in (
        (validate_header, q.get("header")),
        (validate_question_text, q.get("question")),
    ):
        try:
            validator(arg)
        except QualityGateViolation as e:
            violations.append(str(e))

    options = q.get("options")
    options_ok = True
    try:
        validate_option_count(options, kind=effective_kind)
    except QualityGateViolation as e:
        violations.append(str(e))
        options_ok = False  # Cannot run option-shape checks on invalid options

    try:
        validate_number_range(q)
    except QualityGateViolation as e:
        violations.append(str(e))

    if options_ok:
        if isinstance(options, list):
            for i, opt in enumerate(options):
                try:
                    validate_option_shape(opt, i)
                except QualityGateViolation as e:
                    violations.append(str(e))

        # A typed question has no options, so the "Other" gate can only be
        # checked against the question text -- but it still must be, because
        # the form schema is stricter here, not laxer: "There should be no
        # 'Other' or 'Skip' option".
        question_text = q.get("question")
        if isinstance(question_text, str):
            try:
                validate_no_other_mention(
                    question_text, options if isinstance(options, list) else []
                )
            except QualityGateViolation as e:
                violations.append(str(e))

    return violations


def validate_question(q: Any) -> None:
    """Validate a single question dict against all universal quality gates.

    Raises `QualityGateViolation` on the first violation encountered.
    For the non-raising variant that reports all violations at once, use
    `collect_violations`.
    """
    violations = _collect_question_violations(q)
    if violations:
        raise QualityGateViolation(violations[0])


def validate_questions(questions: Any) -> None:
    """Validate a full AskUserQuestion payload (list of 1-4 questions)."""
    if not isinstance(questions, list):
        raise QualityGateViolation(
            f"questions must be a list, got {type(questions).__name__}"
        )
    n = len(questions)
    if n < MIN_QUESTIONS_PER_CALL or n > MAX_QUESTIONS_PER_CALL:
        raise QualityGateViolation(
            f"question count {n} outside allowed range "
            f"[{MIN_QUESTIONS_PER_CALL}, {MAX_QUESTIONS_PER_CALL}]"
        )
    for i, q in enumerate(questions):
        try:
            validate_question(q)
        except QualityGateViolation as e:
            raise QualityGateViolation(f"questions[{i}]: {e}") from e


def collect_violations(questions: Iterable[Any]) -> list[str]:
    """Non-raising variant: return a list of violation messages.

    Useful for CI lints that want to report all violations in one pass
    rather than failing on the first.
    """
    violations: list[str] = []
    try:
        if not isinstance(questions, list):
            violations.append(
                f"questions must be a list, got {type(questions).__name__}"
            )
            return violations
    except TypeError:
        violations.append("questions must be iterable")
        return violations

    n = len(questions)
    if n < MIN_QUESTIONS_PER_CALL or n > MAX_QUESTIONS_PER_CALL:
        violations.append(
            f"question count {n} outside allowed range "
            f"[{MIN_QUESTIONS_PER_CALL}, {MAX_QUESTIONS_PER_CALL}]"
        )

    for i, q in enumerate(questions):
        for v in _collect_question_violations(q):
            violations.append(f"questions[{i}]: {v}")

    return violations
