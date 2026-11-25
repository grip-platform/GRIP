#!/usr/bin/env python3
"""
grip-speak — GRIP's first voice.

One-way GRIP -> operator audio output. Where the text goes, exactly: this script
POSTs the reply text straight to Google's Interactions API on gemini-3.8-flash-tts
(generativelanguage.googleapis.com/v1beta/interactions) with the `grip-gemini` key
from the keychain, sent as the `x-goog-api-key` header. Nothing in HAL sits
between; an earlier version of this docstring said "routed from the HAL council",
which was never true of the request path. Since 2026-10-16 every reply passes
through lib/redaction_shield (the llm_egress profile, pinned pseudonyms) BEFORE
the request is built, so a name, phone number, email address or ID number in a
spoken reply never reaches Google; a shield failure stops the send rather than
sending unshielded. Stdlib only, plus GRIP's own lib/ -- no pip installs.

Moved off gemini-2.5-flash-preview-tts onto gemini-3.8-flash-tts (GRIP#9447;
Google shuts the old model down 18 December 2026). The new model treats `text`
strictly as a verbatim transcript -- it reads a prefixed directive aloud instead
of silently parsing it as an instruction (measured 2026-11-24: a clip made with
the old directive ran 9.20s against 2.40s without it, and a transcriber read the
directive text back). Delivery now travels beside the text as a short
`speech_metadata.style` phrase, never inside it, and a unary response is already
a complete WAV -- `as_wav` passes it through instead of double-wrapping it.

Gen 509 / ultrado W1. V>>'s criterion: "GRIP speaks to V>>. Only GRIP -> V>> first."
"""

import base64
import json
import os
import struct
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# GRIP's own lib/ sits beside scripts/, and the redaction shield lives there. The
# path is resolved ONCE at import, the way every GRIP module does it, never inside a
# per-call function (AGORA on GRIP#8425: a per-call sys.path edit is a global side
# effect that outlives the call). Without the shield nothing may be spoken, so a
# missing module is recorded here and refused in shield_text, not papered over.
_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:
    from redaction_shield import (  # noqa: E402
        PROFILE_LLM_EGRESS, Denylist, RedactionConfigError, Shield, load_denylist,
    )
except ImportError:  # pragma: no cover - GRIP's own tree always carries lib/redaction_shield.py
    Shield = None  # type: ignore[assignment]

MODEL = "gemini-3.8-flash-tts"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_VOICE = "Charon"  # deep male voice — fits GRIP tone
DEFAULT_MESSAGE = (
    "V. This is GRIP. First voice. "
    "I'm speaking through Gemini 3.8 Flash, "
    "routed from your HAL council. Not OpenAI. "
    "One-way for now. GRIP to V. Over."
)

# Gemini 3.8 TTS treats `text` strictly as a verbatim transcript -- unlike the
# retired 2.5 preview model, it never silently parses a prefixed directive as
# an instruction; it reads the directive aloud instead (measured 2026-11-24).
# Delivery now goes in speech_metadata.style, kept to a short phrase per
# Google's own prompting guide: a long-form "Audio Profile" or multi-bullet
# "Director's Notes" block is the most common cause of voice drift, and most
# requests need no style at all. Empty is valid and deliberate -- it is also
# why the old one-word-input failure doesn't reproduce: "ready" with no style
# gave 0.96s of audio on gemini-3.8-flash-tts (measured 2026-11-24), where the
# retired model refused it as ambiguous transcript-vs-instruction.
DEFAULT_STYLE = ""

# Profile system — bakes a voice together with a short speech_metadata.style
# phrase. Google's prompting guide: keep style to sustained delivery
# attributes (emotion, pace, prosody) and let the voice carry the persona.
PROFILES = {
    "child": {
        "voice": "Iapetus",
        "style": "warm, cool-older-brother tone, gentle and unhurried, speaking to a young boy",
    },
    "grip": {
        "voice": "Charon",
        # None falls through to DEFAULT_STYLE (empty) -- GRIP's normal
        # register needs no style tweak.
        "style": None,
    },
    "warm": {
        "voice": "Charon",
        "style": "warm, sincere, measured, slight reverence",
    },
}


def resolve_profile(profile_name: str | None) -> tuple[str, str]:
    """Return ``(style, voice)`` for the given profile name.

    ``style`` is a short ``speech_metadata.style`` phrase — empty string is
    valid and is what the ``grip`` profile and no-profile-named both resolve
    to. Raises ``ValueError`` for unknown profile names.
    """
    if profile_name is None:
        return DEFAULT_STYLE, os.environ.get("GRIP_VOICE", DEFAULT_VOICE)
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise ValueError(f"unknown profile: {profile_name}. available: {list(PROFILES)}")
    style = profile.get("style")
    return (style if style is not None else DEFAULT_STYLE), profile["voice"]


def keychain_secret(service: str) -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_gemini_key() -> str:
    for service in ("grip-gemini", "grip-gemini-api-key"):
        try:
            return keychain_secret(service)
        except subprocess.CalledProcessError:
            continue
    raise RuntimeError("No Gemini key in keychain (tried grip-gemini, grip-gemini-api-key)")


def parse_rate(mime: str) -> int:
    """Pull sample rate from 'audio/L16;codec=pcm;rate=24000'."""
    for piece in mime.split(";"):
        piece = piece.strip()
        if piece.startswith("rate="):
            return int(piece.split("=", 1)[1])
    return 24000


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw PCM in a RIFF/WAVE container so afplay accepts it."""
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm)
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
        + b"data"
        + struct.pack("<I", data_size)
    )
    return header + pcm


def as_wav(audio: bytes, mime: str) -> bytes:
    """Return ``audio`` unchanged if it is already a WAV container, else wrap it.

    Gemini 3.8 TTS returns a finished WAV (RIFF/WAVE header, 24 kHz mono
    16-bit PCM) by default on a unary request — wrapping it again in
    ``pcm_to_wav`` would add a second header. A caller that ever requests raw
    PCM (``response_format.mime_type`` "audio/l16") still gets correctly
    wrapped audio, at the rate ``mime`` names.
    """
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        return audio
    return pcm_to_wav(audio, sample_rate=parse_rate(mime))


def _model_output_content(payload: dict) -> list[dict]:
    """Flatten every content item from every ``model_output`` step, in order."""
    items: list[dict] = []
    for step in payload.get("steps") or []:
        if step.get("type") == "model_output":
            items.extend(step.get("content") or [])
    return items


def _extract_audio(payload: dict) -> tuple[bytes, str]:
    """Extract ``(audio_bytes, mime_type)`` from an Interactions API response.

    Pure function — no I/O. The audio is the LAST audio-typed content item
    across every ``model_output`` step — Google's own extraction path is
    ``steps[] -> model_output -> content[] -> type==audio -> data``, last one
    wins. Raises ``RuntimeError`` with a meaningful message for each known
    failure shape (no steps / no model_output step / text-only).
    """
    steps = payload.get("steps") or []
    if not steps:
        raise RuntimeError(
            f"Gemini returned no steps (payload keys={list(payload)}). "
            f"Likely a malformed request or an empty interaction."
        )
    content_items = _model_output_content(payload)
    if not content_items:
        step_types = [s.get("type") for s in steps]
        raise RuntimeError(
            f"Gemini returned no model_output step (step types={step_types}). "
            f"Likely a safety block or a malformed request."
        )
    audio_items = [item for item in content_items if item.get("type") == "audio"]
    if not audio_items:
        text_parts = [item.get("text", "") for item in content_items if item.get("type") == "text"]
        raise RuntimeError(
            f"Gemini returned no audio content (text content: {text_parts!r}). "
            f"This usually means the request was malformed or safety-blocked — "
            f"verify the style annotation and model name in grip-speak.py."
        )
    audio_item = audio_items[-1]
    mime = audio_item.get("mime_type", "audio/wav")
    audio = base64.b64decode(audio_item["data"])
    return audio, mime


def build_request_body(text: str, voice: str, style: str = "") -> dict:
    """Build the Interactions API request body for one TTS turn.

    ``text`` is sent verbatim — Gemini 3.8 TTS treats it strictly as a
    transcript to read aloud, never as an instruction. ``style`` is a short
    ``speech_metadata.style`` phrase; empty omits the annotation entirely
    rather than sending an empty string (Google's guide: most requests need
    no style instruction at all).
    """
    content_item: dict = {"type": "text", "text": text}
    if style:
        content_item["annotations"] = [{"type": "speech_metadata", "style": style}]
    return {
        "model": MODEL,
        "input": [{"type": "user_input", "content": [content_item]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }


def tts_request(
    text: str, voice: str, api_key: str, style: str = "", timeout: int = 30,
) -> tuple[bytes, str]:
    body = build_request_body(text, voice, style)
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {e.code}: {err}") from e
    return _extract_audio(payload)


_DENYLIST_WARNED = False


def _denylist():
    """The live denylist, or the generic floors alone when no usable list exists.

    load_denylist raises on a missing or git-crypt-locked file. On a fresh gift
    install the file is the kit's empty skeleton and loads; on a CI runner there is
    none at CLAUDE_DIR and the checkout's copy is ciphertext. The generic floors
    (people, phone numbers, email addresses, ID numbers) run in every case, and
    the fallback is said once on stderr, never silently (the shield's own rule:
    never run with a weaker list than the caller believes).
    """
    global _DENYLIST_WARNED
    try:
        return load_denylist()
    except (FileNotFoundError, RedactionConfigError) as exc:
        if not _DENYLIST_WARNED:
            print(f"grip-speak: no usable redaction denylist ({exc}); generic redaction only",
                  file=sys.stderr)
            _DENYLIST_WARNED = True
        return Denylist.empty()


def shield_text(text: str) -> str:
    """Redact the reply before it leaves the machine (rules: redaction is paramount).

    The llm_egress profile replaces people, phone numbers, email addresses, ID
    numbers and every denylisted name with pinned pseudonyms, so the spoken reply
    still reads naturally. Raises rather than returning unshielded text: a reply
    that cannot be shielded is not spoken.
    """
    if Shield is None:
        raise RuntimeError("lib/redaction_shield.py is missing beside scripts/; nothing is spoken unshielded")
    return Shield(_denylist(), profile=PROFILE_LLM_EGRESS).redact(text).text


def speak(
    text: str,
    voice: str = DEFAULT_VOICE,
    style: str = "",
    play: bool = True,
    timeout: int = 30,
) -> Path:
    api_key = get_gemini_key()
    audio, mime = tts_request(shield_text(text), voice, api_key, style=style, timeout=timeout)
    wav = as_wav(audio, mime)

    out_dir = Path.home() / ".claude" / "state" / "voice"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"grip-speak-{stamp}.wav"
    out_path.write_bytes(wav)

    if play:
        subprocess.run(["afplay", str(out_path)], check=False)

    return out_path


USAGE = (
    "usage: grip-speak.py [-h | --help] [--profile NAME] [--timeout SECONDS] [-- | -] [TEXT ...]\n"
    "  Speaks TEXT aloud, or a short greeting when there is none. \"-\" reads the\n"
    "  text from stdin. \"--\" ends the options, so text that starts with a dash,\n"
    "  even a lone \"-\", is spoken as text. -h or --help prints this and speaks\n"
    "  nothing.")


def _options(args: list[str], profile_name: "str | None",
             timeout: int) -> "tuple[str | None, int, list[str], bool] | int":
    """(profile_name, timeout, text_args, literal), or an exit code once the
    reason is printed. *literal* is True after "--", so a lone "-" is then text,
    not a request to read stdin (AGORA on GRIP#9754).

    Leading flags (any order): --profile <name>, --timeout <seconds>. --timeout
    raises the per-request read cap for long passages; the 30s default times out
    on multi-minute syntheses (chunk, or pass --timeout). An option grip-speak
    does not know is refused, never spoken: --help used to be read aloud as text
    (GRIP#9181)."""
    while args and args[0] in ("--profile", "--timeout"):
        if len(args) < 2:
            print(f"{args[0]} needs a value\n{USAGE}", file=sys.stderr)
            return 2
        if args[0] == "--profile":
            profile_name = args[1]
        else:
            try:
                timeout = int(args[1])
            except ValueError:
                print(f"--timeout expects an integer, got: {args[1]!r}", file=sys.stderr)
                return 2
        args = args[2:]
    first = args[0] if args else ""
    if first == "--":
        return profile_name, timeout, args[1:], True
    if first in ("-h", "--help"):
        print(USAGE)
        return 0
    if first.startswith("-") and first != "-":
        print(f"unknown option {first!r}; put -- before text that starts with a dash\n{USAGE}",
              file=sys.stderr)
        return 2
    return profile_name, timeout, args, False


def main() -> int:
    parsed = _options(sys.argv[1:], os.environ.get("GRIP_PROFILE"), 30)
    if isinstance(parsed, int):
        return parsed
    profile_name, timeout, args, literal = parsed

    if not args:
        text = DEFAULT_MESSAGE
    elif args[0] == "-" and not literal:
        text = sys.stdin.read().strip()
    else:
        text = " ".join(args)

    try:
        style, voice = resolve_profile(profile_name)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    path = speak(text, voice=voice, style=style, timeout=timeout)
    tag = f" / profile={profile_name}" if profile_name else ""
    print(f"spoken via {MODEL} / voice={voice}{tag}")
    print(f"wav: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
