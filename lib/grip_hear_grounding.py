#!/usr/bin/env python3
"""grip-hear grounding — the checks that make a hearing falsifiable.

Origin: 2026-08-05. The `hear` MCP tool described a slow ballad about a man's
death as "a high-energy electronic dance track with a driving beat and uplifting
synth melodies, perfect for a workout or a club" — genre, mood, energy 0.85,
darkness 0.15, every field populated. Its own provenance block said
`floor_source: "empty"` and `tempo_key: "unavailable"`: nothing had been
measured. A second call on the same file returned, verbatim, "I am an AI and do
not have the ability to listen to audio". The audio had never reached the model
on either call; the first answer was invented from the prompt alone and returned
in the same structured shape a real measurement would have used.

Three independent things must hold before a hearing may be reported, and each
one is checked here:

1. the audio really is attached to the request  -> assert_audio_attached
2. the model did not say it cannot hear         -> assert_heard
3. something really was measured from signal    -> assert_floor_measured

Every check raises a HearGroundingError, which is a RuntimeError, so callers
that already handle RuntimeError keep working unchanged. No check ever degrades
to a default, a warning, or an empty result: a grounding check that fails open
is precisely the bug this module exists to remove. When grip-hear cannot prove
it heard the track, it says so instead of describing one.

Stdlib only, pure, no I/O. scripts/grip-hear.py is deliberately
self-contained, and being unable to check a hearing must never be the reason a
hearing goes unchecked.
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import Any

__all__ = [
    "HearGroundingError",
    "AudioNotAttachedError",
    "NoAudioAdmissionError",
    "FloorUnavailableError",
    "SilentAudioError",
    "no_audio_admission",
    "assert_heard",
    "assert_audio_attached",
    "measured_facts",
    "assert_floor_measured",
    "assert_audible",
    "SILENCE_DBFS",
    "hal_media_attested",
    "HAL_MEDIA_ATTESTATION_KEYS",
]


class HearGroundingError(RuntimeError):
    """A hearing could not be grounded, so no reading is reported.

    Subclasses RuntimeError deliberately: scripts/grip-hear.py, the MCP server
    and the CLI all already treat RuntimeError as "surface this plainly", so
    every existing handler reports these correctly without modification.
    """

    kind = "grounding"


class AudioNotAttachedError(HearGroundingError):
    """The request that was about to be sent carried no usable audio."""

    kind = "audio_not_attached"


class NoAudioAdmissionError(HearGroundingError):
    """The model answered by saying it cannot hear audio."""

    kind = "no_audio_admission"

    def __init__(self, message: str, admission: str = "", rung: str = ""):
        super().__init__(message)
        self.admission = admission
        self.rung = rung


class FloorUnavailableError(HearGroundingError):
    """Nothing was measured from the signal, so nothing may be asserted about it."""

    kind = "floor_unavailable"


class SilentAudioError(HearGroundingError):
    """The file decoded, but there is effectively nothing audible in it."""

    kind = "silent_audio"


# --- 1. did the model just tell us it cannot hear? ---------------------------

# Each pattern matches a FIRST-PERSON statement of incapacity, or an explicit
# statement that no audio arrived. They are deliberately narrow so that ordinary
# musical description does not trip them: "the vocals cannot be heard clearly"
# describes the track, "I cannot hear the audio" describes a broken pipeline.
# The distinguishing feature is the speaker being the one who cannot hear.
_ADMISSION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # "I do not have the ability to listen to audio" (the observed instance)
        r"\bdo(?:es)?\s+not\s+have\s+the\s+(?:ability|capability)\s+to\s+"
        r"(?:listen|hear|process\s+audio|analy[sz]e\s+audio)",
        r"\bdon'?t\s+have\s+the\s+(?:ability|capability)\s+to\s+"
        r"(?:listen|hear|process\s+audio|analy[sz]e\s+audio)",
        # "I cannot listen to the audio" / "I can't hear this recording"
        r"\bI\s+(?:can\s?no|can')?t\s+(?:actually\s+)?"
        r"(?:listen\s+to|hear|play|access)\b",
        r"\bI\s+cannot\s+(?:actually\s+)?(?:listen\s+to|hear|play|access)\b",
        # "I am unable to process audio files"
        r"\b(?:am|are|is)\s+(?:currently\s+)?unable\s+to\s+"
        r"(?:listen|hear|process|access|analy[sz]e)\s+"
        r"(?:the\s+|this\s+|any\s+)?(?:audio|sound|music|recording|file)",
        # "no audio was provided" / "I did not receive any audio"
        r"\bno\s+audio\s+(?:file\s+)?(?:was\s+|has\s+been\s+)?"
        r"(?:provided|attached|supplied|received|included|uploaded)",
        r"\bI\s+(?:did\s+not|didn'?t|do\s+not|don'?t)\s+(?:receive|get|have)\s+"
        r"(?:any\s+|an?\s+|the\s+)?(?:audio|sound|music|recording)",
        # "as a text-based model I have no access to audio"
        r"\b(?:text[-\s]based|language)\s+model[^.]{0,60}\b"
        r"(?:cannot|can'?t|no\s+access|unable)",
    )
)

# A model that cannot hear often opens with a refusal before explaining why.
# This is NOT an admission on its own (a refusal can be a safety decline about
# real audio), so it only counts when an admission pattern also matches — it is
# used purely to widen the quoted sentence, never to trigger the error.
_REFUSAL_PREFIX = re.compile(
    r"(?:I'?m\s+sorry[^.]*\.\s*)?(?:I\s+cannot\s+fulfill[^.]*\.\s*)?", re.IGNORECASE
)


def _sentence_around(text: str, start: int, end: int) -> str:
    """The full sentence containing text[start:end], so the quote reads naturally.

    Quoting only the regex match ("do not have the ability to listen") loses the
    speaker and reads like an accusation; quoting the whole response buries it.
    The sentence is the honest unit.
    """
    left = max(
        (text.rfind(mark, 0, start) for mark in (". ", "! ", "? ", "\n")),
        default=-1,
    )
    right_candidates = [
        pos for pos in (text.find(mark, end) for mark in (".", "!", "?", "\n")) if pos != -1
    ]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    return text[left + 1 : right].strip()


def no_audio_admission(text: Any) -> str | None:
    """Return the sentence in which the model admits it cannot hear, else None.

    Pure and side-effect free, so it is safe to call on every rung's output.
    Non-string input (a already-parsed dict, None) returns None: there is no
    sentence to quote and the caller's other checks still apply.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    for pattern in _ADMISSION_PATTERNS:
        match = pattern.search(text)
        if match:
            return _sentence_around(text, match.start(), match.end())
    return None


def assert_heard(text: str, *, rung: str) -> str:
    """Return `text` unchanged, or raise if it is the model saying it cannot hear.

    This is the check that would have turned the 2026-08-05 incident from a
    confident fabrication into a one-line error on the very first call: the
    model's own words are the strongest available evidence that the audio never
    arrived, and they were being discarded.
    """
    admission = no_audio_admission(text)
    if admission is None:
        return text
    raise NoAudioAdmissionError(
        f"The model answered without hearing the audio (rung: {rung}). "
        f"It said: {admission!r}. No reading is reported — a description "
        f"produced without the audio is invented, not heard.",
        admission=admission,
        rung=rung,
    )


# --- 2. is the audio actually in the request we are about to send? -----------

# Keys HAL's /api/infer may use to attest that it really attached the media it
# was handed. HAL's current response shape carries none of them, so the HAL rung
# counts as UNVERIFIED and the ladder falls through to a rung whose attachment
# can be checked. Any one of these appearing (truthy, or a non-empty list) is
# accepted, so HAL can adopt whichever name fits its schema.
HAL_MEDIA_ATTESTATION_KEYS = ("media_attached", "media", "content_parts", "media_parts")


def hal_media_attested(payload: Any) -> bool:
    """True only if a HAL /api/infer response proves it attached the media.

    Absence of proof is not proof of attachment. HAL answers `ok: true` with
    text whether or not the audio reached an audio-capable provider, so a bare
    `ok` is exactly the signal that let a prompt-only answer through as a
    hearing.
    """
    if not isinstance(payload, dict):
        return False
    for key in HAL_MEDIA_ATTESTATION_KEYS:
        value = payload.get(key)
        if isinstance(value, bool):
            if value:
                return True
        elif isinstance(value, (list, tuple)):
            if len(value) > 0:
                return True
        elif isinstance(value, int) and value > 0:
            return True
    return False


def assert_audio_attached(part: Any, *, path: Any, expected_mime: str,
                          expected_bytes: int | None = None) -> dict:
    """Return the request part unchanged, or raise if it carries no real audio.

    `part` is the exact dict that becomes the audio element of the outgoing
    request body, so this asserts on what will actually be SENT rather than on
    what the code intended to send. Two shapes are valid:

      {"inlineData": {"mimeType": ..., "data": <base64>}}   small tracks
      {"fileData":   {"fileUri": ...,  "mimeType": ...}}    Files API uploads

    For the inline shape the base64 is decoded and its length checked, which is
    what catches an empty or unreadable file encoding to `""` and being posted
    as if it were music.
    """
    if not isinstance(part, dict):
        raise AudioNotAttachedError(
            f"Refusing to listen to {path}: the request carried no audio part "
            f"(got {type(part).__name__})."
        )

    inline = part.get("inlineData")
    if isinstance(inline, dict):
        return _assert_inline_ok(inline, part, path=path, expected_mime=expected_mime,
                                 expected_bytes=expected_bytes)

    file_data = part.get("fileData")
    if isinstance(file_data, dict):
        return _assert_file_data_ok(file_data, part, path=path, expected_mime=expected_mime)

    raise AudioNotAttachedError(
        f"Refusing to listen to {path}: the request has neither inlineData nor "
        f"fileData, so the model would answer from the prompt text alone. "
        f"Keys present: {sorted(part)}."
    )


def _assert_inline_ok(inline: dict, part: dict, *, path: Any, expected_mime: str,
                      expected_bytes: int | None) -> dict:
    """Validate the inline shape: real bytes, right mime. Raise or return `part`."""
    mime = inline.get("mimeType")
    if mime != expected_mime:
        raise AudioNotAttachedError(
            f"Refusing to listen to {path}: the attached audio is declared as "
            f"{mime!r} but the file is {expected_mime!r}. A wrong mime type is "
            f"silently ignored by the model, which then answers from the prompt."
        )
    data = inline.get("data")
    if not isinstance(data, str) or not data:
        raise AudioNotAttachedError(
            f"Refusing to listen to {path}: the request's inline audio field is "
            f"empty, so no audio would reach the model."
        )
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AudioNotAttachedError(
            f"Refusing to listen to {path}: the attached audio is not valid "
            f"base64 ({exc}), so the model would receive nothing usable."
        ) from exc
    if len(decoded) == 0:
        raise AudioNotAttachedError(
            f"Refusing to listen to {path}: the attached audio decodes to 0 "
            f"bytes. An empty file cannot be heard."
        )
    if expected_bytes is not None and len(decoded) != expected_bytes:
        raise AudioNotAttachedError(
            f"Refusing to listen to {path}: {len(decoded)} bytes were attached "
            f"but the file is {expected_bytes} bytes. The model would hear a "
            f"truncated or wrong track."
        )
    return part


def _assert_file_data_ok(file_data: dict, part: dict, *, path: Any,
                         expected_mime: str) -> dict:
    """Validate the Files API shape: real URI, right mime. Raise or return `part`."""
    uri = file_data.get("fileUri")
    if not isinstance(uri, str) or not uri.strip():
        raise AudioNotAttachedError(
            f"Refusing to listen to {path}: the upload returned no file URI, so "
            f"the request would reference no audio."
        )
    mime = file_data.get("mimeType")
    if mime != expected_mime:
        raise AudioNotAttachedError(
            f"Refusing to listen to {path}: the uploaded audio is declared as "
            f"{mime!r} but the file is {expected_mime!r}."
        )
    return part


# --- 3. was anything actually measured from the signal? ----------------------

def _positive_number(value: Any) -> bool:
    """A real, positive measurement — 0.0 is the estimators' failure value."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _any_number(value: Any) -> bool:
    """A real number of any sign — loudness in dBFS is always negative."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


# Facts the deterministic floor can establish from the waveform, each with the
# test for "is this an actual measurement". Duration proves the file decoded to
# real audio of real length; loudness proves there is something in it to hear.
# Tempo may honestly be 0 for a free-time drone and key may be absent for a
# tonally flat signal, so neither alone is required.
_MEASURABLE_FACTS = (
    ("duration_s", _positive_number),
    ("tempo_bpm", _positive_number),
    ("key", _non_empty_text),
    ("rms_dbfs", _any_number),
)


def measured_facts(floor: Any) -> tuple[str, ...]:
    """Which floor values are real measurements, in declaration order.

    A key present but empty, or a tempo of 0.0 (the estimator's failure value),
    is not a measurement. Returning the names rather than a bare bool lets the
    provenance block state exactly what was measured instead of claiming it.
    """
    if not isinstance(floor, dict):
        return ()
    return tuple(name for name, is_measurement in _MEASURABLE_FACTS
                 if is_measurement(floor.get(name)))


def assert_floor_measured(floor: Any, *, audio_path: Any, diagnosis: str) -> tuple[str, ...]:
    """Return the measured fact names, or raise if the signal was never analysed.

    `diagnosis` says WHY the floor is empty in the caller's own terms (module
    not importable, ran but found nothing), because "no tempo" and "librosa is
    not installed" call for completely different fixes and the operator should
    not have to guess which one happened.
    """
    facts = measured_facts(floor)
    if facts:
        return facts
    raise FloorUnavailableError(
        f"Refusing to describe {audio_path}: nothing was measured from the "
        f"audio ({diagnosis}). Without the deterministic floor every field "
        f"would be the model's guess presented as a reading, which is how "
        f"grip-hear once described a funeral ballad as a workout track. "
        f"Install the floor's dependency (`pip install librosa`) to restore "
        f"measurement, or measure loudness directly with "
        f"`ffmpeg -i {audio_path} -ac 1 -ar 8000 -f s16le -`."
    )


# Median frame loudness, in dBFS, below which a recording has nothing audible in
# it. Music typically sits near -20 dBFS and quiet ambient near -35; a silent
# room tone falls below -55. The threshold sits under anything anyone would
# deliberately record and above the -61.5 dBFS control clip that was described
# as "a baby giggling" on 2026-08-06.
#
# Falsifier: if a genuinely audible track is ever refused here, RAISE the
# threshold — do not remove the check. If a silent clip ever passes it, lower it.
# Either way the fix is the number, not the principle.
SILENCE_DBFS = -55.0


def assert_audible(floor: Any, *, audio_path: Any) -> None:
    """Raise if the floor measured a recording with nothing audible in it.

    Duration and tempo cannot tell a real recording from a silent one — a
    near-silent clip decodes to a perfectly real duration, which is why "the
    floor measured something" is necessary but not sufficient. On 2026-08-06 a
    deliberately near-silent control clip (median -61.5 dBFS, zero acoustic
    events) came back described as "a baby giggling", in the same shape as every
    genuine reading. Loudness is what makes that refusable.

    Silent when the floor reports no loudness at all: an older floor, or an
    injected one in a test, should not be treated as evidence of silence.
    """
    if not isinstance(floor, dict):
        return
    level = floor.get("rms_dbfs")
    if not _any_number(level) or level >= SILENCE_DBFS:
        return
    raise SilentAudioError(
        f"Refusing to describe {audio_path}: the recording is effectively "
        f"silent ({level} dBFS median, below the {SILENCE_DBFS} dBFS floor). "
        f"There is nothing audible to describe, and any description of it "
        f"would be invented — a silent control clip was once reported as "
        f"'a baby giggling'. Check the file is the one you meant, and that it "
        f"is not an empty or failed recording."
    )
