"""DFI-FIB convergence loop for low-resource African-language translation.

Built on ``facebook/nllb-200-distilled-600M`` (verified present locally,
2026-09-24 -- ``~/.cache/huggingface/hub/models--facebook--nllb-200-distilled-600M``).
Used tonight to translate a message into Tumbuka for a real recipient, whose
verified reaction was "GRIP writes better Tumbuka than Tumbuka speakers."

This module turns that one-off manual translate/back-translate/eyeball check
into a repeatable capability, using GRIP's own recursive-improvement kernel
(``lib.improve_kernel.InnerspeakerGo``) rather than a bespoke loop: the
generic convergence machinery (fixed-point registry, depth cap, telemetry)
already exists and this is exactly the kind of state->ImproveResult step it
was designed for.

The refinement strategy encodes an empirical finding from tonight, not a
guess: a standalone "Welcome!" back-translated as "What a joy!" (no clean
single-word equivalent), while short, simple, single-clause sentences
round-tripped cleanly. So when fidelity is low, the converge step SPLITS the
source on clause boundaries and retranslates piecewise, rather than retrying
the same call (which would be a no-op against a deterministic model — NLLB's
default decoding is greedy, so translating unchanged input twice always
reaches the SAME fingerprint on attempt 2, correctly registering as a fixed
point rather than looping pointlessly).

Fidelity metric is a plain word-overlap (Jaccard) score between the original
source and the back-translation, with two hard invariant gates layered on
top (negation and digits must survive the round trip -- see fidelity_score).
This is deliberately crude and inexpensive rather than pretending precision
it does not have (no embedding model is wired here) -- it is closer to an
automated eyeball check than a real semantic metric. A Fable-led review
(2026-09-24) demonstrated two concrete failure modes this shipped version
did NOT catch -- a flipped negation scoring 0.75, a wrong number scoring
1.0 -- both now hard-gated to 0.0; a genuinely better metric (chrF++ over
the round trip) remains real future work, named so it is not forgotten
rather than quietly assumed done.

LICENSING (found by the same review, missed when this module was first
written): facebook/nllb-200-distilled-600M is CC-BY-NC-4.0 -- NON-COMMERCIAL.
This module is a research/internal capability under that licence, not a
sellable backbone, until either Meta grants a commercial licence or the
pipeline is re-based onto a permissively-licensed model (Apache-2.0
google/madlad400-3b-mt confirms Chichewa and Shona; Tumbuka coverage there
is UNVERIFIED). Do not productise or sell output from this module as-is.

KNOWN LIMITATION (same review, not yet fixed): every step after the first
sets ``attempted_split=True``, so depth 3 onward re-runs the identical
split-and-translate call against a deterministic model and immediately
hits a fixed point -- ``_MAX_DEPTH = 5`` therefore explores at most 2
genuinely distinct strategies (unsplit, then split-once), not 5. A third
refinement strategy for cases where one split isn't enough is unbuilt.

Usage::

    from lib.low_resource_translate import translate_converging

    result = translate_converging("Welcome! I am truly happy about this new chapter.", "tum_Latn")
    print(result.state.translation, result.metrics["fidelity"])
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lib.improve_kernel import ImproveResult, InnerspeakerGo, fingerprint

MODEL_ID = "facebook/nllb-200-distilled-600M"
DEFAULT_SOURCE_LANG = "eng_Latn"

# Languages this module has actually verified against the tokenizer's own
# vocab (never asserted from memory -- see verify_language_supported()).
SUPPORTED_LANGUAGES = {
    "tum_Latn": "Tumbuka (Chitumbuka) -- Malawi, Zambia",
    "nya_Latn": "Chichewa/Nyanja -- Malawi",
    "sna_Latn": "Shona (chiShona) -- Zimbabwe",
    # South Africa's 11 official languages -- 8 of 11 verified present in
    # this model's vocab (2026-09-24); Zulu smoke-tested with a real
    # translate/back-translate round trip, 1.0 fidelity, same as Chichewa
    # and Shona. The other 7 are verified present in the vocab but not yet
    # individually smoke-tested -- vocab presence and translation quality
    # are different claims (never-system-python-for-grip.md's own
    # "implemented vs. observed serving" distinction, applied here).
    "zul_Latn": "isiZulu -- South Africa (smoke-tested, 1.0 fidelity)",
    "xho_Latn": "isiXhosa -- South Africa",
    "afr_Latn": "Afrikaans -- South Africa",
    "sot_Latn": "Sesotho (Southern Sotho) -- South Africa, Lesotho",
    "nso_Latn": "Sepedi (Northern Sotho) -- South Africa",
    "tsn_Latn": "Setswana -- South Africa, Botswana",
    "tso_Latn": "Xitsonga -- South Africa",
    "ssw_Latn": "siSwati -- South Africa, Eswatini",
}

# Checked and NOT present in facebook/nllb-200-distilled-600M's vocab
# (2026-09-24, both plausible FLORES-200 codes tried directly against the
# tokenizer): Ndebele (neither `nde_Latn` nor `nbl_Latn` resolves). Named
# here, not silently omitted, so a future reader does not re-derive the
# same negative and does not assume it was simply forgotten -- Zimbabwe's
# second-most-spoken language is a genuine gap in this model, not yet
# solved by any language in this table.
UNSUPPORTED_ZIMBABWE_LANGUAGES = {
    "Ndebele (isiNdebele) -- checked nde_Latn and nbl_Latn, neither in "
    "the NLLB-200 vocab; would need a different model (MMS, a Lelapa "
    "product, or another vendor) to cover."
}

# Same discipline, South Africa's remaining 2 of 11 official languages
# (checked 2026-09-24 directly against the tokenizer, alongside the 8 that
# ARE present -- see SUPPORTED_LANGUAGES above).
UNSUPPORTED_SOUTH_AFRICA_LANGUAGES = {
    "Tshivenda -- checked ven_Latn, not in the NLLB-200 vocab.",
    "isiNdebele -- same gap as Zimbabwean Ndebele above; South African "
    "and Zimbabwean Ndebele are distinct languages but neither has a "
    "working FLORES-200 code in this model.",
}

_FIDELITY_THRESHOLD = {"fidelity": ("ge", 0.45)}
_MAX_DEPTH = 5  # deliberately shallow: each depth is a real model call, not free


# Split ONLY on boundaries that carry no meaning of their own (sentence-
# ending punctuation, commas). Do NOT split on " and "/" but " -- an
# earlier version did, and because re.split() consumes its separator, the
# conjunction was silently dropped from the rejoined translation (found by
# a Fable-led review 2026-09-24, reading this exact regex: "We work hard
# and we play hard" would translate as two clauses and rejoin as if the
# "and" had never been there). Losing "but" is worse than losing "and" --
# it can invert a contrast -- so both are excluded, not just the one found.
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|,\s+")

# Digits included (a Fable-led review 2026-09-24 demonstrated the fidelity
# gate was blind to them: "pay 500 kwacha" vs a mistranslated "pay 50
# kwacha" scored a perfect 1.0, since neither number is a letter sequence).
_WORD_RE = re.compile(r"[a-zA-Z0-9']+")

# A hard, deterministic invariant: negation must survive the round trip.
# Word-overlap alone cannot catch this -- "I am not coming" back-translated
# as "I am coming" shares 3 of 4 words (0.75 Jaccard, comfortably above the
# 0.45 threshold) despite meaning the opposite thing. Demonstrated by the
# same review; this is not a proxy metric like Jaccard, it is a fact either
# holds or does not, so it fails the candidate outright rather than nudging
# a score.
_NEGATION_MARKERS = {"not", "no", "never", "none", "nobody", "nothing", "n't", "cannot"}


class ModelUnavailableError(RuntimeError):
    """Raised when the NLLB model/tokenizer cannot be loaded or verified."""


@dataclass
class _ModelBundle:
    tokenizer: object
    model: object


_bundle: Optional[_ModelBundle] = None


def _load_bundle() -> _ModelBundle:
    """Lazy-load the tokenizer + model once per process. Loud on failure --
    a silently-absent translator is worse than an explicit refusal (no-stubs.md)."""
    global _bundle
    if _bundle is not None:
        return _bundle
    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ModelUnavailableError(
            "transformers is not importable in this interpreter. Use "
            "~/.claude/venv/bin/python3 (per never-system-python-for-grip.md) "
            "-- that venv has transformers + torch verified working."
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_ID)
    _bundle = _ModelBundle(tokenizer=tokenizer, model=model)
    return _bundle


def verify_language_supported(lang_code: str) -> bool:
    """True iff lang_code is a real token in the loaded tokenizer's vocab --
    checked against the artefact, never asserted from memory or a table
    someone typed (anchor-on-the-artefact-not-the-session.md)."""
    bundle = _load_bundle()
    vocab = bundle.tokenizer.get_vocab()
    return lang_code in vocab


def _translate_one(text: str, *, src: str, tgt: str) -> str:
    bundle = _load_bundle()
    bundle.tokenizer.src_lang = src
    tgt_id = bundle.tokenizer.convert_tokens_to_ids(tgt)
    inputs = bundle.tokenizer(text, return_tensors="pt")
    out = bundle.model.generate(**inputs, forced_bos_token_id=tgt_id, max_length=128)
    return bundle.tokenizer.decode(out[0], skip_special_tokens=True)


def _word_set(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)}


def _has_negation(words: set[str]) -> bool:
    """True iff any word IS a negation marker, or CONTAINS one as a
    contraction ("don't", "can't", "won't"). A plain set-membership check
    would miss every contraction, since "'" is a word character in
    _WORD_RE and "don't" never appears as the standalone token "n't"."""
    return any(
        w in _NEGATION_MARKERS or any(marker in w for marker in ("n't",))
        for w in words
    )


def fidelity_score(original: str, back_translation: str) -> float:
    """Jaccard word-overlap between the original source and its
    back-translation, HARD-GATED on negation surviving the round trip.

    Word-overlap alone cannot catch a flipped negation: "I am not coming"
    back-translated as "I am coming" shares 3 of 4 words (0.75) despite
    meaning the opposite thing. Negation is a fact, not a matter of degree,
    so a mismatch returns 0.0 outright rather than discounting the overlap
    score -- this is a deliberately blunt invariant, not a replacement for
    a real semantic metric (chrF++ or similar remains future work; see
    lib/low_resource_translate.py module docstring).

    0.0 on two empty strings would be undefined (0/0) -- treated as 1.0
    (nothing was said, nothing was lost) rather than raising, since an empty
    source is a legitimate degenerate input, not an error.
    """
    a, b = _word_set(original), _word_set(back_translation)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if _has_negation(a) != _has_negation(b):
        return 0.0
    return len(a & b) / len(a | b)


def _split_clauses(text: str) -> list[str]:
    parts = [p.strip() for p in _CLAUSE_SPLIT_RE.split(text) if p.strip()]
    return parts if len(parts) > 1 else [text]


@dataclass
class TranslationState:
    source: str
    target_lang: str
    translation: str = ""
    back_translation: str = ""
    attempted_split: bool = field(default=False)


def _converge_step(state: TranslationState, depth: int) -> ImproveResult[TranslationState]:
    """One DFI-FIB step: translate, back-translate, score. If fidelity is
    low and the source hasn't been split yet, split on clause boundaries and
    translate piecewise on the NEXT call -- otherwise this is the best
    achievable candidate and further recursion would just repeat it (the
    kernel's fixed-point check catches that via fingerprint() on the whole
    state, the SAME function the kernel itself uses on its incoming state --
    a bespoke fingerprint scheme here would never compare equal to that and
    fixed points would never be detected)."""
    tgt_lang = state.target_lang
    if state.attempted_split:
        clauses = _split_clauses(state.source)
        translated_clauses = [
            _translate_one(c, src=DEFAULT_SOURCE_LANG, tgt=tgt_lang) for c in clauses
        ]
        translation = " ".join(translated_clauses)
    else:
        translation = _translate_one(state.source, src=DEFAULT_SOURCE_LANG, tgt=tgt_lang)

    back = _translate_one(translation, src=tgt_lang, tgt=DEFAULT_SOURCE_LANG)
    score = fidelity_score(state.source, back)

    new_state = TranslationState(
        source=state.source,
        target_lang=tgt_lang,
        translation=translation,
        back_translation=back,
        attempted_split=True,
    )

    return ImproveResult(
        state=new_state,
        metrics={"fidelity": score},
        depth=depth,
        fingerprint=fingerprint(new_state),
    )


def translate_converging(
    text: str,
    target_lang: str,
    *,
    fixed_points_path: Path | None = None,
    telemetry_path: Path | None = None,
) -> ImproveResult[TranslationState]:
    """Translate ``text`` into ``target_lang`` via the DFI-FIB convergence
    loop: translate, back-translate, score fidelity; if below threshold,
    retry once with the source split into clauses (the refinement strategy
    tonight's manual pass found actually helps); stop at a fixed point,
    threshold pass, or depth cap -- whichever comes first.

    Raises ModelUnavailableError if the model/tokenizer can't load, or
    ValueError if target_lang isn't a verified FLORES-200 code -- both loud,
    never a silent wrong-language fallback.

    SHARP EDGE (found via a real test failure, 2026-09-24, not yet patched
    upstream): if the DEFAULT fixed_points_path is reused across separate
    calls whose STATE fingerprint happens to already be registered as a
    fixed point from a prior process, InnerspeakerGo's short-circuit path
    (lib/improve_kernel.py::_try_short_circuit) returns
    ``ImproveResult(state, {}, depth, fp)`` -- an EMPTY metrics dict. This
    function refuses to pass that on: it raises ModelUnavailableError (the
    check after the kernel call below), so a result it returns always carries
    ``metrics["fidelity"]``. Pass an isolated fixed_points_path/telemetry_path
    (a temp directory, or one scoped per caller) to avoid the error. The
    collision itself is a property of the shared kernel, not something this
    module can fix alone.
    """
    if target_lang not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"{target_lang!r} is not in SUPPORTED_LANGUAGES {list(SUPPORTED_LANGUAGES)}. "
            "Add it only after verifying it against the tokenizer vocab "
            "(verify_language_supported), not from memory."
        )
    if not verify_language_supported(target_lang):
        raise ModelUnavailableError(
            f"{target_lang!r} is not a real token in {MODEL_ID}'s vocab -- "
            "the SUPPORTED_LANGUAGES table has drifted from the artefact."
        )

    initial = TranslationState(source=text, target_lang=target_lang)

    kernel: InnerspeakerGo[TranslationState] = InnerspeakerGo(
        converge=_converge_step,
        threshold=_FIDELITY_THRESHOLD,
        max_depth=_MAX_DEPTH,
        fixed_points_path=fixed_points_path,
        telemetry_path=telemetry_path,
    )
    result = kernel(initial)
    if "fidelity" not in result.metrics:
        # The documented fixed-point-collision sharp edge (see this
        # function's own docstring): _try_short_circuit's fallback is
        # ImproveResult(state, {}, depth, fp) -- an EMPTY metrics dict.
        # An earlier version of this check tested `not
        # result.state.translation.strip()`, reasoning the state would be
        # the untranslated INITIAL state -- true only when the collision
        # hits on the very first call. Running the real calibration
        # harness against the shared default registry (2026-09-24)
        # surfaced the case that check missed: the collision can also hit
        # on a RECURSIVE call, where `state` is an intermediate step that
        # already has a real, non-empty .translation -- so .translation
        # being non-empty proves nothing; checking metrics directly is the
        # actual failure signature, not a proxy for it.
        raise ModelUnavailableError(
            f"translate_converging({target_lang!r}) returned no fidelity "
            "metric -- almost certainly the fixed-point registry "
            "collision documented above. Use an isolated fixed_points_path."
        )
    return result


# ---------------------------------------------------------------------------
# Inbound direction (source-language -> English) -- relay integration
# ---------------------------------------------------------------------------
#
# Deliberately NOT run through the DFI-FIB convergence loop above: that loop's
# refinement strategy (split on English clause boundaries, re-translate) is
# tuned for producing a good OUTBOUND translation, where fidelity matters
# because a real person reads the polished result. For UNDERSTANDING an
# inbound message well enough to act on it, one direct translation is the
# right cost/quality trade -- if it is unclear, that is itself useful
# information (ask a clarifying question) rather than something to spend a
# second model call trying to fix blind.

RELAY_LANGUAGE_BY_SENDER: dict[str, str] = {
    # {daemon-resolved sender name: NLLB language code, e.g. "tum_Latn"}.
    # Empty: no sender is translated today.
    # Extend this table, never infer a sender's language from message
    # content -- a wrong guess here silently mistranslates, which is worse
    # than not translating at all.
}


def translate_inbound_to_english(text: str, source_lang: str) -> str:
    """One direct translation from source_lang to English -- no convergence
    loop, no fidelity gate. Raises the same loud errors as
    translate_converging for an unsupported/unverified language."""
    if source_lang not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"{source_lang!r} is not in SUPPORTED_LANGUAGES {list(SUPPORTED_LANGUAGES)}."
        )
    if not verify_language_supported(source_lang):
        raise ModelUnavailableError(
            f"{source_lang!r} is not a real token in {MODEL_ID}'s vocab."
        )
    return _translate_one(text, src=source_lang, tgt=DEFAULT_SOURCE_LANG)


def translate_relay_event_inbound(event: dict) -> dict:
    """Relay integration point: given a grip-channel inbound event dict
    (the exact shape relay_queue_drain.py::_assemble_event builds -- carries
    "text" and "sender_name", the daemon-resolved name the general channel
    allowlist already authenticated). Returns a COPY of the event with
    "text" replaced by its English translation when the sender is in
    RELAY_LANGUAGE_BY_SENDER, and the ORIGINAL untranslated text preserved
    under "text_original" so nothing is silently lost.

    Unknown senders, or senders not in the table, pass through unchanged --
    this function is additive, never a filter.
    """
    sender_name = event.get("sender_name")
    lang = RELAY_LANGUAGE_BY_SENDER.get(sender_name or "")
    if lang is None or "text" not in event:
        return event
    translated = dict(event)
    translated["text_original"] = event["text"]
    translated["text"] = translate_inbound_to_english(event["text"], lang)
    return translated


def translate_relay_reply_outbound(reply: str, sender_name: str | None) -> str | None:
    """Relay integration point for the OUTBOUND direction: translate GRIP's
    generated reply into sender_name's language before it is delivered,
    when sender_name is in RELAY_LANGUAGE_BY_SENDER.

    Unlike the inbound direction, this DOES run the full DFI-FIB convergence
    loop (translate_converging) -- a real person reads this polished result,
    so the fidelity gate and split-refinement strategy earn their cost here
    in a way they would not for a one-shot inbound gloss.

    Returns reply UNCHANGED (None passes through as None) when sender_name
    isn't in the table, or when reply is None/empty -- this function raises
    on a genuine translation failure (ModelUnavailableError, ValueError) so
    the CALLER decides the fail-open policy explicitly, matching
    relay_queue_drain.py's _translate_inbound_if_configured pattern rather
    than silently swallowing errors two layers away from where they're
    handled.
    """
    lang = RELAY_LANGUAGE_BY_SENDER.get(sender_name or "")
    if lang is None or not reply:
        return reply
    # A per-call ephemeral fixed_points_path, deliberately NOT the shared
    # default -- found necessary (2026-09-24) after real repeated-sentence
    # traffic (this module's own test suite, run many times over one
    # session) triggered the fixed-point-collision sharp edge in
    # translate_converging's own docstring. Relay replies gain nothing from
    # cross-call convergence memoization (each message is cheap to
    # translate fresh; the registry exists for RSI-style convergence loops
    # where re-solving the same target is the expensive case), so isolating
    # it entirely removes the collision class for production relay traffic
    # rather than just documenting the risk.
    with tempfile.TemporaryDirectory(prefix="grip-relay-translate-") as tmp:
        result = translate_converging(
            reply, lang,
            fixed_points_path=Path(tmp) / "fixed-points.jsonl",
            telemetry_path=Path(tmp) / "telemetry.jsonl",
        )
    return result.state.translation
