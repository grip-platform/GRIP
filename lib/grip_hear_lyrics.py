#!/usr/bin/env python3
"""grip_hear_lyrics — the LYRIC layer for grip-hear.

grip-hear's model hearing returns a *guessed* `key_lyric`. This module adds a
REAL transcription layer: it sends a vocal track's audio through Lelapa.ai's
Vulavula STT (the same provider + keychain key + call shape grip-listen.py
already uses for speech) and returns the transcribed lyrics tagged with
provenance, so the reading can distinguish "vulavula" (transcribed) from
"model" (Gemini's guess).

Vulavula supports a fixed set of (mostly South African) languages — afr/eng/
zul/xho/sot/tsn — so it gives accurate lyrics for the Afrikaans tracks (Kry
Kontrole, Ek Is Die Een Wat Omgee, Ongemaklike Situasies, …) the Gemini
hearing can only approximate. For languages OUTSIDE that set (or when Vulavula
is reachable but returns nothing) an OPTIONAL Whisper fallback transcribes
instead, tagged "whisper" so provenance still distinguishes a real
transcription from the model's guess.

Design — best-effort, NEVER crash (mirrors grip_hear_floor's contract):
    transcribe_lyrics(audio_path, lang="afr") -> dict

On Vulavula success returns
    {"lyrics": <text>, "lyrics_source": "vulavula", "lyrics_lang": <lang>}.
When Vulavula is unavailable for the language (or returns empty) and Whisper
is installed and succeeds, returns
    {"lyrics": <text>, "lyrics_source": "whisper", "lyrics_lang": <lang>}.
On any failure (no keychain key, ~/.hal / VulavulaProvider unavailable, API
error, empty transcription, Whisper not installed or erroring) it returns {} —
the caller merges nothing and the model's guessed lyric remains as the
secondary source. Stdlib only at import time.

This does NOT reimplement the STT: the Vulavula path reuses grip-listen's
VulavulaProvider verbatim (import from ~/.hal/lib, keychain "grip-vulavula",
provider.transcribe(audio_path, language=lang).text). The Whisper path is a
LAZY, opt-in extra: openai-whisper (→ torch) is a HEAVY dependency, imported
only inside the fallback and only when it actually fires, so the module stays
importable — and the pipeline keeps working — with neither whisper nor torch
present. torch/whisper are intentionally NOT in any required requirements.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Same keychain entry + HAL provider path grip-listen.py uses (DRY: one STT path).
_KEYCHAIN_SERVICE = "grip-vulavula"
# Import root is ~/.hal (NOT ~/.hal/lib): the provider's package
# `hal/__init__.py` does an absolute `from lib.hal.code_gen import ...`, so the
# package only resolves as `lib.hal.providers.vulavula` with ~/.hal on sys.path.
# Inserting ~/.hal/lib + importing `hal.providers...` raises ModuleNotFoundError
# and silently degraded the whole lyric layer to {}.
_HAL_ROOT = os.path.expanduser("~/.hal")
_DEFAULT_LANG = "afr"  # Afrikaans — Vulavula's language code (verified vs provider map).

# Languages Vulavula covers. Outside this set the Whisper fallback is the only
# real-transcription path. Vulavula's codes are ISO-639-3-ish (afr/zul/xho/…).
_VULAVULA_LANGS = frozenset({"afr", "eng", "zul", "xho", "sot", "tsn"})

# Whisper takes ISO-639-1 (2-letter); map the 3-letter codes grip-hear passes.
# Unmapped -> language unset (Whisper auto-detects), never a crash.
_WHISPER_LANG = {
    "afr": "af", "eng": "en", "zul": "zu", "xho": "xh", "sot": "st", "tsn": "tn",
    "fra": "fr", "fre": "fr", "deu": "de", "ger": "de", "spa": "es", "por": "pt",
    "nld": "nl", "dut": "nl", "ita": "it", "swa": "sw",
}


def _keychain_secret(service: str) -> str | None:
    """Return the keychain password for `service`, or None if absent/unreadable.

    macOS-specific (the `security` CLI). On Linux/Windows the binary is absent,
    so subprocess.run raises FileNotFoundError (an OSError) -> caught -> None ->
    the lyric layer degrades to {} by construction. This is intentional
    best-effort: no keychain access means no transcription, never a crash.
    """
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
        )
    except OSError:
        # Missing `security` binary (non-macOS) or any spawn failure -> no key.
        return None
    if result.returncode != 0:
        return None
    secret = result.stdout.strip()
    return secret or None


def _vulavula_lyrics(audio_path: str | Path, lang: str) -> str:
    """Transcribe via Vulavula STT, returning the text or "" on any miss.

    "" covers every best-effort failure (no keychain key, ~/.hal /
    VulavulaProvider unavailable, API/network error, empty transcription).
    Never raises — the orchestrator decides whether to fall back.
    """
    api_key = _keychain_secret(_KEYCHAIN_SERVICE)
    if not api_key:
        return ""

    if _HAL_ROOT not in sys.path:
        sys.path.insert(0, _HAL_ROOT)
    try:
        from lib.hal.providers.vulavula import VulavulaProvider
    except ImportError:
        return ""

    try:
        # The provider resolves its own key from the keychain source string
        # (`_resolve_api_key`), so pass the canonical `keychain:<service>`, NOT
        # the raw token — handing it the bare JWT bypasses resolution.
        provider = VulavulaProvider(api_key_source=f"keychain:{_KEYCHAIN_SERVICE}")
        result = provider.transcribe(str(audio_path), language=lang)
        return (getattr(result, "text", "") or "").strip()
    except Exception:
        # Any provider/network/API error -> graceful empty (best-effort layer).
        return ""


def _whisper_lyrics(audio_path: str | Path, lang: str) -> str:
    """Transcribe via openai-whisper, returning the text or "" on any miss.

    LAZY import: openai-whisper (→ torch) is a HEAVY optional extra, so it is
    imported only here, only when the fallback actually fires. If whisper is
    not installed the ImportError is swallowed and "" is returned, so the
    module stays importable and the pipeline keeps working without it. Any
    model-load / decode / runtime error degrades to "" the same way — never a
    crash. torch/whisper must NOT be added to any required requirements.
    """
    try:
        import whisper  # noqa: PLC0415 — deliberate lazy heavy-dep import
    except Exception:
        # Not installed (ImportError) or broken native deps -> no fallback.
        return ""

    try:
        model = whisper.load_model("base")
        iso1 = _WHISPER_LANG.get(lang)  # None -> let Whisper auto-detect
        result = model.transcribe(str(audio_path), language=iso1)
        if isinstance(result, dict):
            text = result.get("text", "")
        else:  # defensive: a non-dict return is still handled, never crashes
            text = getattr(result, "text", "")
        return (text or "").strip()
    except Exception:
        return ""


def transcribe_lyrics(audio_path: str | Path, lang: str = _DEFAULT_LANG) -> dict:
    """Transcribe a vocal track's lyrics, best-effort, with provenance.

    Vulavula is preferred for the languages it covers (_VULAVULA_LANGS). For an
    unsupported language — or when Vulavula is reachable but returns nothing —
    the optional Whisper fallback is tried. Returns
    {"lyrics", "lyrics_source": "vulavula"|"whisper", "lyrics_lang"} on success;
    {} on any failure (no key, import/API error, empty result, whisper absent).
    Never raises — a transcription miss must not break the hearing contract.
    """
    if lang in _VULAVULA_LANGS:
        text = _vulavula_lyrics(audio_path, lang)
        if text:
            return {"lyrics": text, "lyrics_source": "vulavula", "lyrics_lang": lang}

    # Unsupported language, or Vulavula returned nothing -> optional Whisper.
    text = _whisper_lyrics(audio_path, lang)
    if text:
        return {"lyrics": text, "lyrics_source": "whisper", "lyrics_lang": lang}
    return {}
