"""cos_whisper — server-side speech-to-text for the Chief-of-Staff voice loop.

Transcribes a recorded audio clip via Groq's hosted Whisper (whisper-large-v3)
so the iPhone Shortcut can RECORD AUDIO instead of using Apple Dictation (V>>'s
ask, 2026-06-26: "Apple Dictation sucks"). The Groq API key stays SERVER-SIDE
(read from the keychain in this process), never baked into the distributed
shortcut — the shortcut only uploads the raw audio.

Groq's API is behind Cloudflare, which 1010-bot-blocks a header-less client, so
a ``User-Agent`` is MANDATORY (verified 2026-06-27 — without it the request is
rejected before reaching Groq).

Syscall-doctrine: the HTTP boundary is injected (``poster``) so the whole
transcription is unit-testable with zero network.
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from typing import Callable, Optional

_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_MODEL = "whisper-large-v3"
_UA = "grip-cos/1.0"
_TIMEOUT_S = 30.0


class TranscribeError(RuntimeError):
    """Transcription failed — no key, Groq error, empty audio, empty transcript."""


def _groq_key() -> str:
    out = subprocess.run(
        ["security", "find-generic-password", "-a", "grip", "-s", "grip-groq", "-w"],
        capture_output=True, text=True,
    )
    key = out.stdout.strip() if out.returncode == 0 else ""
    if not key:
        raise TranscribeError("grip-groq key not found in keychain")
    return key


def _build_multipart(audio: bytes, filename: str, content_type: str, model: str):
    """Return (body_bytes, boundary). Binary-safe (audio is delimited, not encoded)."""
    boundary = "----gripcosaudioboundary"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
    ).encode()
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
    ).encode() + audio + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, boundary


def _default_post(url: str, body: bytes, headers: dict, timeout: float) -> dict:
    """Real HTTP POST → parsed JSON. Raises TranscribeError with Groq's message."""
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise TranscribeError(
            f"Groq HTTP {e.code}: {e.read().decode(errors='replace')[:160]}"
        ) from None
    except Exception as e:  # noqa: BLE001 — surface any transport failure uniformly
        raise TranscribeError(f"Groq transcription failed: {e}") from None


def transcribe(
    audio: bytes,
    *,
    filename: str = "audio.m4a",
    content_type: str = "audio/m4a",
    model: str = _MODEL,
    key: Optional[str] = None,
    poster: Optional[Callable[[str, bytes, dict, float], dict]] = None,
) -> str:
    """Transcribe ``audio`` to text via Groq Whisper. Raises ``TranscribeError``.

    ``key`` and ``poster`` are injectable (DIP) so the function is testable with
    zero network/keychain. Returns the stripped transcript; empty input or an
    empty transcript raises — a chief-of-staff turn must never run on silence.
    """
    if not audio:
        raise TranscribeError("empty audio")
    key = key or _groq_key()
    poster = poster or _default_post
    body, boundary = _build_multipart(audio, filename, content_type, model)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": _UA,  # MANDATORY: Groq behind Cloudflare 1010-blocks without it
    }
    data = poster(_GROQ_URL, body, headers, _TIMEOUT_S)
    text = (data.get("text") or "").strip() if isinstance(data, dict) else ""
    if not text:
        raise TranscribeError("Groq returned an empty transcript")
    return text
