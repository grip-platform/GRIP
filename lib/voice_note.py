"""Voice-note syscall shim — GRIP speaks to WhatsApp.

Single entry point ``speak_to_whatsapp(text, recipient, profile)`` that turns
text into a real WhatsApp voice-note bubble. The pipeline is a syscall ladder
(syscall-doctrine): one stable API, multiple backend rungs, automatic failover,
never a silent nothing.

Pipeline
--------
1. **TTS** — synthesise speech via grip-speak (Gemini 3.8 Flash TTS, direct —
   not through HAL). On any failure, ladder down to macOS ``say`` so the
   reply is never lost.
2. **Convert** — ffmpeg transcodes the synthesised WAV/AIFF to Opus-in-Ogg
   (``audio/ogg; codecs=opus``) — the codec WhatsApp renders as a true PTT
   voice note.
3. **Send** — ladder: (a) ``channel_send_voice`` MCP tool for the real
   voice-note bubble; (b) ``channel_send_file`` (audio attachment — a playable
   MVP rendering); (c) on total failure, return ``ok: False`` with a
   plain-language error.

Outbound brake (PARAMOUNT)
--------------------------
Honours the global STOP marker (``state/session/outbound-stop.json``) shared
with ``lib.whatsapp_transport`` — while it exists, every voice send is refused
fail-closed. Same contract as the text path; tested.

Gemini style annotation (GRIP#9447)
-----------------------------------
Moved off ``gemini-2.5-flash-preview-tts`` onto ``gemini-3.8-flash-tts``
(Google shuts the old model down 18 December 2026). The old model refused
short inputs ("ready", "ok") with HTTP 400 unless framed by a directive
concatenated into the text; that refusal does not reproduce on the new model,
and style now travels as a structured ``speech_metadata.style`` annotation
beside the text, never inside it. ``grip-speak``'s profile system resolves
the style + voice; this shim passes ``profile`` through unchanged.

Testability (DIP)
-----------------
``tts``, ``convert`` and ``send`` are injected callables defaulting to the
production backends, so tests exercise the ladder logic with zero network,
zero subprocess, zero Gemini key.

Public API::

    from lib.voice_note import speak_to_whatsapp

    result = speak_to_whatsapp("Heard you. On it.", "27000000000@s.whatsapp.net")
    # result: {ok, path, sent_as, error}
    #   sent_as in {"voice", "file"}; error is None on success.
"""

from __future__ import annotations

import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Callable, Optional

# Reuse the outbound brake + MCP tool-caller from the text transport (DRY —
# one marker, one handshake). Both are stdlib-only; this keeps the STOP path
# and the grip-channel MCP handshake identical across text and voice.
_LIB = Path(__file__).resolve().parent
if str(_LIB.parent) not in sys.path:
    sys.path.insert(0, str(_LIB.parent))
from lib.whatsapp_transport import STOP_MARKER, call_tool  # noqa: E402

__all__ = ["speak_to_whatsapp", "STOP_MARKER"]

# grip-speak is a script (hyphenated filename by convention), so it is loaded by
# file path rather than imported as a package module.
_GRIP_SPEAK_PATH = _LIB.parent / "scripts" / "grip-speak.py"

_VOICE_DIR = Path.home() / ".claude" / "state" / "voice"
# An input at or below this length used to trigger the retired model's
# transcript-vs-instruction refusal without a directive frame (GRIP#9447);
# gemini-3.8-flash-tts does not reproduce that failure (measured 2026-11-24:
# a bare "ready" gave 0.96s of audio). Kept as documentation of what counts
# as a "short input" for this shim; no longer gates a framing decision.
SHORT_INPUT_CHARS = 12


def _default_tts(text: str, profile: Optional[str]) -> Path:
    """Synthesise ``text`` to an audio file. grip-speak first, ``say`` fallback.

    Returns the path to a synthesised WAV (grip-speak) or AIFF (``say``).
    Raises ``RuntimeError`` only if BOTH backends fail — the caller treats
    that as a hard error (never a silent nothing).
    """
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return _grip_speak_tts(text, profile)
    except Exception as exc:  # noqa: BLE001 — ladder down on ANY grip-speak failure
        print(f"[voice_note] grip-speak failed ({exc}); falling back to say",
              file=sys.stderr)
        return _say_tts(text)


def _load_grip_speak():
    """Load the hyphenated grip-speak.py script as an importable module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("grip_speak", _GRIP_SPEAK_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load grip-speak from {_GRIP_SPEAK_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _grip_speak_tts(text: str, profile: Optional[str]) -> Path:
    """Synthesise via grip-speak's Gemini path. Text travels verbatim; style
    and voice come from grip-speak's profile resolution (GRIP#9447 — the
    retired model's directive concatenated into the text, the new model
    treats text strictly as a transcript and sends style as a separate
    annotation instead)."""
    grip_speak = _load_grip_speak()

    style, voice = grip_speak.resolve_profile(profile)
    api_key = grip_speak.get_gemini_key()
    audio, mime = grip_speak.tts_request(text, voice, api_key, style=style)
    wav = grip_speak.as_wav(audio, mime)
    out = _VOICE_DIR / f"voice-{uuid.uuid4().hex[:10]}.wav"
    out.write_bytes(wav)
    return out


def _say_tts(text: str) -> Path:
    """Fallback synthesis via macOS ``say -o`` (AIFF). The reply never fails silent."""
    out = _VOICE_DIR / f"voice-say-{uuid.uuid4().hex[:10]}.aiff"
    subprocess.run(["say", "-o", str(out), text], check=True,
                   capture_output=True, timeout=60)
    return out


# Q1 — one-time libopus capability cache. ``None`` until first probed, then
# the boolean answer. Probed once per process (the "assert once" pre-check),
# never re-run per voice note.
_OPUS_PROBE: Optional[bool] = None


def _probe_opus() -> bool:
    """Ask ffmpeg whether the libopus encoder is built in (the real probe)."""
    res = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True, timeout=20)
    return "libopus" in (res.stdout or "")


def _opus_available(*, probe: Callable[[], bool] = _probe_opus) -> bool:
    """Memoised libopus availability — probed once, cached for the process.

    DIP: ``probe`` is injectable so tests assert both the present and absent
    branches with zero ffmpeg. The cache means a missing libopus costs ONE
    probe, not a failed transcode on every send.
    """
    global _OPUS_PROBE
    if _OPUS_PROBE is None:
        try:
            _OPUS_PROBE = bool(probe())
        except Exception:  # noqa: BLE001 — an unprobeable ffmpeg ⇒ assume no opus
            _OPUS_PROBE = False
    return _OPUS_PROBE


def _encode(src: Path, codec: str, suffix: str) -> Path:
    """Shared ffmpeg encode to ``codec`` (mono, 32 kbps). DRY across rungs."""
    out = src.with_suffix(suffix)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-c:a", codec,
         "-b:a", "32k", "-ac", "1", str(out)],
        check=True, capture_output=True, timeout=60,
    )
    return out


def _default_convert(src: Path) -> Path:
    """Transcode to the WhatsApp PTT codec, laddering down when opus is absent.

    Q1 pre-check ladder: if libopus is available → Opus-in-Ogg (``.ogg``, the
    true PTT voice-note codec → a voice bubble). If NOT → AAC (``.m4a``), which
    WhatsApp still plays as an audio attachment (the file rung) rather than a
    silent, unplayable ogg. Mirrors the W1 lesson — check the cheap capability
    BEFORE spending on the encode, so we never emit a broken artefact silently.
    Raises ``subprocess.CalledProcessError`` only if the chosen encode fails.
    """
    if _opus_available():
        return _encode(src, "libopus", ".ogg")
    print("[voice_note] libopus unavailable; laddering to AAC (.m4a)",
          file=sys.stderr)
    return _encode(src, "aac", ".m4a")


def _default_send(audio: Path, recipient: str) -> str:
    """Send ``audio`` to ``recipient`` over grip-channel MCP. Ladder:

    1. ``channel_send_voice`` → real voice-note bubble (returns ``"voice"``).
    2. ``channel_send_file`` → playable audio attachment (returns ``"file"``).

    Raises ``RuntimeError`` if both rungs fail.
    """
    try:
        res = call_tool("channel_send_voice",
                        {"recipient": recipient, "file_path": str(audio)})
        if _send_ok(res):
            return "voice"
    except Exception as exc:  # noqa: BLE001 — fall through to the file rung
        print(f"[voice_note] channel_send_voice unavailable ({exc}); "
              f"trying channel_send_file", file=sys.stderr)

    res = call_tool("channel_send_file",
                    {"recipient": recipient, "file_path": str(audio)})
    if _send_ok(res):
        return "file"
    raise RuntimeError(f"both voice and file send failed: {res!r}")


def _send_ok(res: object) -> bool:
    """A grip-channel tool result is a success unless it carries FAILED/not-connected."""
    text = str(res).lower()
    return "failed" not in text and "not connected" not in text and "not available" not in text


# Q2 — per-utterance character budget. Gemini TTS times out on multi-minute
# input; ~600 chars is a safe sentence-bounded ceiling well under the
# model's ~30s synthesis window. Text at or below this is a single shot;
# longer text is sentence-chunked and the audio concatenated.
MAX_TTS_CHARS = 600

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _atomic_units(text: str, max_chars: int) -> list[str]:
    """Sentences of ``text``, each already ``<= max_chars``.

    Splits on sentence boundaries and hard-splits any single over-long
    sentence into character-bounded pieces (via ``_hard_split``), dropping
    whitespace-only fragments (the W1 lesson at chunk granularity). The result
    is a flat list of pack-ready units — total work is linear in ``len(text)``.
    """
    units: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        sentence = sentence.strip()
        if sentence:
            units.extend(_hard_split(sentence, max_chars))
    return units


def _hard_split(unit: str, max_chars: int) -> list[str]:
    """A single unit → character-bounded pieces ``<= max_chars`` (never lost)."""
    if len(unit) <= max_chars:
        return [unit]
    return [unit[i:i + max_chars] for i in range(0, len(unit), max_chars)]


def _chunk_sentences(text: str, max_chars: int = MAX_TTS_CHARS) -> list[str]:
    """Split ``text`` into sentence-bounded chunks, each ``<= max_chars``.

    Greedy single pass over the pre-bounded units from ``_atomic_units``: pack
    whole sentences together up to ``max_chars``, never splitting a unit here
    (the over-long case is already handled upstream). Pure + total; the
    hard-split concern lives in its own helper so this stays a flat loop.
    """
    chunks: list[str] = []
    current = ""
    for unit in _atomic_units(text, max_chars):
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= max_chars:
            current = f"{current} {unit}"
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _default_concat(segments: list[Path]) -> Path:
    """Concatenate same-codec audio ``segments`` into one file (ffmpeg demuxer).

    A single segment is returned unchanged (no re-encode). Otherwise an ffmpeg
    concat-demuxer list joins them losslessly into the first segment's codec.
    """
    if len(segments) == 1:
        return segments[0]
    out = segments[0].with_name(f"joined-{uuid.uuid4().hex[:10]}{segments[0].suffix}")
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{s}'\n" for s in segments), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(out)],
        check=True, capture_output=True, timeout=120,
    )
    return out


def _synth_long(
    text: str,
    profile_arg: Optional[str],
    tts: "Callable[[str, Optional[str]], Path]",
    convert: "Callable[[Path], Path]",
    concat: "Callable[[list[Path]], Path]",
) -> Path:
    """Synthesise long ``text`` as chunks → convert each → concat (Q2).

    Each chunk runs the SAME tts→convert pipeline as the single-shot path
    (so the W2 opus→aac ladder applies per segment), then the per-codec
    segments are concatenated into one voice note. Raises on any backend
    failure; the caller maps it to a plain-language error.
    """
    segments = [convert(tts(chunk, profile_arg)) for chunk in _chunk_sentences(text)]
    return concat(segments)


def speak_to_whatsapp(
    text: str,
    recipient: str,
    profile: str = "default",
    *,
    tts: Callable[[str, Optional[str]], Path] = _default_tts,
    convert: Callable[[Path], Path] = _default_convert,
    send: Callable[[Path, str], str] = _default_send,
    concat: Callable[[list], Path] = _default_concat,
) -> dict:
    """Speak ``text`` to ``recipient`` as a WhatsApp voice note.

    ``profile`` selects a grip-speak voice/style ("default" → grip-speak's
    DEFAULT_STYLE; "grip"/"child"/"warm" → named profiles). ``tts``,
    ``convert`` and ``send`` are DIP seams (tests inject fakes).

    Returns ``{ok, path, sent_as, error}``:
      * ``ok`` — True on a delivered voice note or audio file.
      * ``path`` — the local ogg/audio path (str) or None.
      * ``sent_as`` — "voice" | "file" | None.
      * ``error`` — plain-language failure, or None on success.

    Never raises on a transport/synthesis error — always a result dict. Raises
    ``ValueError`` only for a malformed JID (cheap up-front guard, mirroring
    ``lib.whatsapp_transport.post``).
    """
    if not recipient or "@" not in recipient:
        raise ValueError(
            f"Invalid WhatsApp JID {recipient!r}. "
            "Expected '<digits>@s.whatsapp.net' or '<id>@g.us'."
        )

    # Q3 — empty/whitespace guard. Gemini TTS rejects an empty or
    # whitespace-only transcript with an error; an empty payload is also never
    # a meaningful voice note. Refuse it up front (before the STOP brake and
    # before any TTS spend) with a plain-language error — never a silent send.
    if not text or not text.strip():
        return _err("empty text: nothing to speak (refused before TTS)")

    # Fail-closed brake — identical contract to the text path.
    if STOP_MARKER.exists():
        return {
            "ok": False,
            "path": None,
            "sent_as": None,
            "error": "outbound-stop engaged (release with `grip-stop off`)",
        }

    # "default" is grip-speak's no-profile path (its DEFAULT_STYLE, empty).
    # Any other value is a named grip-speak profile passed straight through.
    profile_arg = None if profile == "default" else profile

    # Q2 — long text is sentence-chunked + concatenated; short text takes the
    # original single-shot path (byte-for-byte unchanged behaviour <= the
    # threshold, so the existing single-shot contract is preserved exactly).
    if len(text) > MAX_TTS_CHARS:
        try:
            audio = _synth_long(text, profile_arg, tts, convert, concat)
        except subprocess.CalledProcessError as exc:
            return _err(f"audio conversion failed (ffmpeg): {str(exc)[:160]}")
        except Exception as exc:  # noqa: BLE001
            return _err(f"voice synthesis failed: {str(exc)[:160]}")
    else:
        try:
            raw = tts(text, profile_arg)
        except Exception as exc:  # noqa: BLE001
            return _err(f"voice synthesis failed: {str(exc)[:160]}")
        try:
            audio = convert(raw)
        except subprocess.CalledProcessError as exc:
            return _err(f"audio conversion failed (ffmpeg): {str(exc)[:160]}")
        except Exception as exc:  # noqa: BLE001
            return _err(f"audio conversion failed: {str(exc)[:160]}")

    try:
        sent_as = send(audio, recipient)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "path": str(audio), "sent_as": None,
                "error": f"send failed: {str(exc)[:160]}"}

    return {"ok": True, "path": str(audio), "sent_as": sent_as, "error": None}


def _err(message: str) -> dict:
    return {"ok": False, "path": None, "sent_as": None, "error": message}
