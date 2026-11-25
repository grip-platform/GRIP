#!/usr/bin/env python3
"""grip-hear — GRIP's ear for MUSIC. The semantic inverse of grip-speak.

The GRIP audio trio:
    grip-speak.py   text  -> Gemini TTS            -> audio        (GRIP talks)
    grip-listen.py  mic   -> Lelapa Vulavula STT   -> words        (GRIP transcribes speech)
    grip-hear.py    music -> Gemini multimodal     -> description  (GRIP understands music)  <- this

grip-listen hears *words*; grip-hear hears *music* — genre, mood, instruments,
arrangement, production. Same provider + key (keychain grip-gemini) and the same
`generateContent` endpoint grip-speak uses for TTS, but audio goes IN as
`inlineData` (or, for tracks over the ~20MB inline cap, a Files API `fileData`
URI uploaded first) and a grounded description comes back. Stdlib only.

Model: gemini-3.5-flash-lite (native audio input per ~/.hal/config/providers.yaml,
repointed from gemini-2.5-flash on 2026-11-24, GRIP#9389) — NOT the -tts model.
gemini-3.1-pro-preview for deeper analysis (--model).

Usage:
    python3 grip-hear.py <audio-file> [--model gemini-3.5-flash-lite] [--prompt "..."]
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "gemini-3.5-flash-lite"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Files API resumable-upload endpoint — used for tracks over the inline cap.
UPLOAD_ENDPOINT = "https://generativelanguage.googleapis.com/upload/v1beta/files"

# HAL routing (rung 1 of the hear ladder). grip-hear stays stdlib-self-contained:
# a thin urllib POST to hal-server's /api/infer media path (HAL #414) rather than
# importing lib/hal_llm_adapter — so the script has no GRIP-lib dependency. HAL
# reads + base64-encodes the file server-side and routes through its audio-capable
# provider ladder, giving failover + audit. Port: read HAL_PORT, default 3850.
HAL_TIMEOUT_S = 120  # audio encode + multimodal call; matches the REST rung's bound
HAL_MODEL = "gemini/gemini-3.5-flash-lite"  # HAL-namespaced; audio-capable via OpenAI-compat


def _hal_infer_url() -> str:
    """hal-server's /api/infer URL, reading HAL_PORT at CALL time (default 3850)
    so a port set after import — or in a multi-process caller — is honoured, not
    frozen to the import-time value."""
    return f"http://localhost:{os.environ.get('HAL_PORT', '3850')}/api/infer"

# Music-listening directive — elicits grounded, track-specific perception.
DEFAULT_PROMPT = (
    "Listen to this audio recording and describe what you ACTUALLY hear in THIS "
    "specific track. Cover: genre/style, mood/feeling, tempo feel, the main "
    "instruments, whether there are vocals and in what language, the song "
    "structure, and production/recording qualities (lo-fi, polished, live, demo). "
    "Ground every claim in the audio; if you are unsure, say so. Be specific and "
    "concise — no generic filler."
)

# Structured directive (--json): a machine-readable hearing for the catalogue
# contract. energy/darkness are the two axes of the emotional-field map.
STRUCTURED_PROMPT = (
    "Listen to this audio recording and return ONLY a JSON object (no prose, no "
    "code fence) describing what you ACTUALLY hear in THIS specific track, with "
    "these exact keys: "
    '{"genre": short style string, '
    '"mood": 2-4 mood words, '
    '"language": "English"|"Afrikaans"|"instrumental"|other, '
    '"instrumental": true|false, '
    '"vocals": true|false, '
    '"key_lyric": one short memorable lyric line (or null if instrumental), '
    '"energy": number 0.0-1.0 (0=still/sparse/quiet, 1=intense/loud/frantic), '
    '"darkness": number 0.0-1.0 (0=light/hopeful/warm, 1=dark/dread/grief/aggression), '
    '"one_line": one vivid sentence capturing this track}. '
    "Ground every value in the audio. Output the JSON object and nothing else."
)
_STRUCT_KEYS = ("genre", "mood", "language", "instrumental", "vocals",
                "key_lyric", "energy", "darkness", "one_line")

# extension -> IANA mime type Gemini accepts for audio input.
_MIME = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".aif": "audio/aiff", ".aiff": "audio/aiff", ".wav": "audio/wav",
    ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/opus",
}
_MAX_INLINE_BYTES = 19 * 1024 * 1024  # generateContent inline cap ~20MB; stay under.


# The grounding checks live in lib/grip_hear_grounding.py: is the audio really
# attached, did the model just say it cannot hear, was anything really measured.
# Unlike the OPTIONAL floor and lyric layers further down, this one is loaded
# FAIL-CLOSED — without it a hearing cannot be checked, and an unchecked hearing
# is exactly how grip-hear once described a track it never received.
_GROUNDING = None


def _grounding():
    """Load lib/grip_hear_grounding.py, once, and never degrade if it is missing.

    Registered in ``sys.modules`` under its own name BEFORE execution so that a
    caller which imported it normally (lib/ is on the MCP server's path) and
    this by-path load share ONE module object. Two copies would mean two
    distinct exception classes, and an ``except NoAudioAdmissionError`` in the
    combiner would silently miss the one raised here.
    """
    global _GROUNDING
    if _GROUNDING is not None:
        return _GROUNDING
    existing = sys.modules.get("grip_hear_grounding")
    if existing is not None:
        _GROUNDING = existing
        return _GROUNDING
    path = Path(__file__).resolve().parent.parent / "lib" / "grip_hear_grounding.py"
    try:
        spec = importlib.util.spec_from_file_location("grip_hear_grounding", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["grip_hear_grounding"] = mod  # before exec: one identity
        spec.loader.exec_module(mod)
    except Exception as exc:
        sys.modules.pop("grip_hear_grounding", None)
        raise RuntimeError(
            f"grip-hear cannot check its own hearing: the grounding module "
            f"{path} would not load ({exc}). Refusing to listen rather than "
            f"return an unverifiable reading."
        ) from exc
    _GROUNDING = mod
    return mod


def __getattr__(name):
    """Re-export the grounding error classes without importing them eagerly.

    Lets callers write ``grip_hear.NoAudioAdmissionError`` while the classes
    keep a single definition in the grounding module (PEP 562). Delegates by
    SHAPE rather than an explicit list, so a class added to the grounding module
    later is re-exported automatically instead of going stale.
    """
    if name.endswith("Error"):
        return getattr(_grounding(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def gemini_key() -> str:
    for service in ("grip-gemini", "grip-gemini-api-key"):
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    raise RuntimeError("No Gemini key in keychain (tried grip-gemini, grip-gemini-api-key)")


def mime_for(path: Path) -> str:
    mime = _MIME.get(path.suffix.lower())
    if mime is None:
        raise RuntimeError(f"Unsupported audio type {path.suffix!r}. Known: {sorted(_MIME)}")
    return mime


def load_audio(path: Path) -> tuple[str, str]:
    """Return (base64_data, mime_type) for the inline path. Caller gates on size."""
    return base64.b64encode(path.read_bytes()).decode("ascii"), mime_for(path)


def _upload_start(name: str, mime: str, size: int, key: str) -> str:
    """Begin a resumable upload; return the one-shot upload URL Gemini hands back."""
    req = urllib.request.Request(
        UPLOAD_ENDPOINT + f"?key={key}",
        data=json.dumps({"file": {"display_name": name}}).encode("utf-8"),
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        url = resp.headers.get("X-Goog-Upload-URL")
    if not url:
        raise RuntimeError("Files API did not return an upload URL.")
    return url


def _upload_finalize(url: str, data: bytes) -> dict:
    """Send the bytes + finalise the resumable upload; return the file-metadata JSON."""
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Length": str(len(data)),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upload_file(path: Path) -> str:
    """Upload an oversize track via the Gemini Files API (resumable). Return its file URI.

    Reuses the keychain key + mime map — no duplication. Raises plain-language on
    any failure (never silently falls back to a doomed inline call on a big file).
    """
    data = path.read_bytes()
    try:
        url = _upload_start(path.name, mime_for(path), len(data), gemini_key())
        info = _upload_finalize(url, data)
    except urllib.error.HTTPError as e:  # HTTPError is a URLError subclass — keep first for .code/.read()
        raise RuntimeError(
            f"Gemini Files API upload of {path.name} failed (HTTP {e.code}): "
            f"{e.read().decode('utf-8', 'replace')[:300]}"
        ) from e
    except (urllib.error.URLError, TimeoutError) as e:  # DNS/connection/timeout — surface plainly, no raw traceback
        raise RuntimeError(f"Gemini Files API upload of {path.name} failed: {e.reason}") from e
    except ValueError as e:  # malformed finalize JSON (json.JSONDecodeError subclasses ValueError)
        raise RuntimeError(f"Gemini Files API returned an unreadable response for {path.name}: {e}") from e
    uri = (info.get("file") or {}).get("uri")
    if not uri:
        raise RuntimeError(f"Files API upload returned no file URI for {path.name}.")
    return uri


def extract_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates (feedback={payload.get('promptFeedback')}).")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError(f"Gemini returned no text (finishReason={candidates[0].get('finishReason')}).")
    return text


def audio_part(path: Path) -> dict:
    """Build the request audio part: inline under the cap, Files-API fileData over it.

    Small files keep the existing inline path (KISS — no behaviour change under
    the threshold). Oversize tracks are uploaded first, then referenced by URI.

    The part is verified before it is returned: real bytes of the right length,
    the right mime type, a real upload URI. An empty or unreadable file used to
    encode to ``""`` and be posted as if it were music, which left the model
    answering from the prompt text alone.
    """
    mime = mime_for(path)
    size = path.stat().st_size
    if size > _MAX_INLINE_BYTES:
        part = {"fileData": {"fileUri": upload_file(path), "mimeType": mime}}
        return _grounding().assert_audio_attached(part, path=path, expected_mime=mime)
    b64, mime = load_audio(path)
    part = {"inlineData": {"mimeType": mime, "data": b64}}
    return _grounding().assert_audio_attached(part, path=path, expected_mime=mime,
                                              expected_bytes=size)


def _hal_model(model: str) -> str:
    """HAL wants a provider-namespaced model id (``gemini/<id>``). A bare id gets
    the ``gemini/`` prefix; an already-namespaced id passes through unchanged."""
    return model if "/" in model else f"gemini/{model}"


def _rest_model(model: str) -> str:
    """The direct Gemini REST endpoint wants the BARE model id — strip any
    ``provider/`` prefix the ladder/caller may have used."""
    return model.split("/", 1)[-1]


def _hear_hal(path: Path, prompt: str, model: str) -> str | None:
    """Rung 1: hear via HAL ``/api/infer`` (media path, HAL #414). HAL reads +
    base64-encodes the file server-side and routes through its audio-capable
    provider ladder — failover + audit + provider-agnostic routing.

    Returns the grounded description on ok; None on ANY miss (HAL unreachable,
    ok=false, empty text, or media attachment unproven) so the caller tries the
    next rung. The remaining rungs are also audio rungs (local mlx_vlm, then
    direct Gemini REST), never a text-only degrade.

    HAL answers ``ok: true`` with text whether or not the audio actually reached
    an audio-capable provider, and its response echoes nothing about the media
    it was handed. A bare ``ok`` therefore proves nothing, and trusting it is
    how a prompt-only answer was once returned as a hearing. Attachment must be
    ATTESTED (see grip_hear_grounding.HAL_MEDIA_ATTESTATION_KEYS); unproven
    attachment is a miss, and the ladder moves to a rung it can check.

    Thin stdlib urllib POST — grip-hear stays self-contained (no GRIP-lib import).
    """
    payload = _hal_post(path, prompt, model)
    if not (isinstance(payload, dict) and payload.get("ok")):
        return None  # unreachable / ok=false — try the next rung, never degrade
    return _hal_verified_text(payload)


def _hal_post(path: Path, prompt: str, model: str) -> dict | None:
    """POST the hearing request to hal-server; the decoded JSON, or None on a miss."""
    body = {
        "prompt": prompt,
        "model": _hal_model(model),
        "media": [str(path)],
        "audit": False,
    }
    req = urllib.request.Request(
        _hal_infer_url(),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HAL_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        print(f"grip-hear: HAL /api/infer rung unavailable ({e}) — trying local/REST",
              file=sys.stderr)
        return None


def _hal_verified_text(payload: dict) -> str | None:
    """HAL's answer, but only if HAL proved it attached the audio.

    Unproven attachment is a MISS (None), so the ladder tries a rung whose
    attachment can be checked. An ATTESTED answer that still says it cannot hear
    is a contradiction, not a miss, and is raised rather than quietly retried
    somewhere that might guess instead.
    """
    g = _grounding()
    text = (payload.get("text") or "").strip()
    if not text:
        return None
    if not g.hal_media_attested(payload):
        admission = g.no_audio_admission(text)
        print(f"grip-hear: HAL answered without proving it attached the audio "
              f"(no {'/'.join(g.HAL_MEDIA_ATTESTATION_KEYS)} field in its "
              f"response) — discarding that answer and trying local/REST."
              + (f" It said: {admission!r}" if admission else ""),
              file=sys.stderr)
        return None
    return g.assert_heard(text, rung="hal")


def _hear_mlx_vlm(path: Path, prompt: str) -> str | None:
    """Rung 2: hear via HAL's local mlx_vlm audio adapter (offline, Apple-Silicon)
    — for when hal-server is unreachable but the local multimodal stack is present.

    Best-effort: returns None on ANY failure (no ~/.hal, import/registry error, no
    local model, runtime error) so the caller falls to the direct REST rung.
    Mirrors grip-hear's other optional layers (librosa floor, Whisper lyrics):
    present in the ladder, never fatal when its dependency is absent.

    GRIP and HAL both ship a top-level ``lib/`` package; importing HAL's
    ``lib.hal`` would shadow an already-imported GRIP ``lib``. So this only
    attempts the import in a process that has NOT already bound ``lib`` (a
    standalone ``grip-hear`` run); an embedded caller skips straight to REST.
    """
    if "lib" in sys.modules:                      # avoid the GRIP/HAL lib-name collision
        return None
    hal_home = Path(os.environ.get("HAL_HOME") or (Path.home() / ".hal"))
    if not (hal_home / "lib" / "hal").is_dir():
        return None
    g = _grounding()  # resolved before the try, so the handler below is safe
    inserted = False
    try:
        if str(hal_home) not in sys.path:
            sys.path.insert(0, str(hal_home))
            inserted = True
        from lib.hal.registry import get_registry          # type: ignore  # HAL-side import
        from lib.hal.providers.mlx_vlm import MlxVlmAdapter  # type: ignore
        resp = MlxVlmAdapter(get_registry()).chat_with_audio(str(path), prompt)
        text = (getattr(resp, "text", None) or "").strip()
        # The adapter is handed the file itself, so attachment is by
        # construction here — but a model that answers "I cannot hear audio"
        # is still telling us the pipeline is broken, and that must surface.
        return g.assert_heard(text, rung="mlx-vlm") if text else None
    except g.NoAudioAdmissionError:
        raise  # a stated incapacity is a finding, not an unavailable rung
    except Exception as e:  # best-effort offline rung — never fatal
        print(f"grip-hear: local mlx_vlm rung unavailable ({e}) — trying direct REST",
              file=sys.stderr)
        return None
    finally:
        # Bound the sys.path mutation to this call so it can never shadow a later
        # GRIP `lib` import in the same process. (The "lib in sys.modules" guard
        # above already prevents the embedded case; this also tidies standalone.)
        if inserted:
            try:
                sys.path.remove(str(hal_home))
            except ValueError:
                pass


def _hear_direct_rest(path: Path, prompt: str = DEFAULT_PROMPT, model: str = MODEL) -> str:
    """Rung 3 (last resort): send audio straight to Gemini ``generateContent``
    (inline, or Files API for oversize tracks) — the original self-contained
    path. Raises a plain-language RuntimeError on HTTP failure (callers already
    expect this), so hearing surfaces a clear error rather than hard-failing.

    The audio is verified in the body that is about to be POSTed, not merely in
    the code that built it, so a refactor which drops or mangles the audio part
    fails loudly here instead of producing a confident prompt-only answer."""
    g = _grounding()
    body = {"contents": [{"parts": [
        audio_part(path),
        {"text": prompt},
    ]}]}
    _assert_body_carries_audio(body, path)
    req = urllib.request.Request(
        ENDPOINT.format(model=model) + f"?key={gemini_key()}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}") from e
    return g.assert_heard(extract_text(payload), rung="direct-rest")


def _assert_body_carries_audio(body: dict, path: Path) -> None:
    """Verify the outgoing request really contains this file's audio.

    Asserts on the request payload itself — the dict that is about to be
    serialised and sent — rather than on the intention of the code that built
    it. `expected_bytes` is checked only for the inline shape; a Files API part
    carries a URI, and its bytes were verified at upload.
    """
    parts = (body.get("contents") or [{}])[0].get("parts") or []
    audio = next((p for p in parts if "inlineData" in p or "fileData" in p), None)
    expected_bytes = path.stat().st_size if isinstance(audio, dict) and "inlineData" in audio else None
    _grounding().assert_audio_attached(audio, path=path, expected_mime=mime_for(path),
                                       expected_bytes=expected_bytes)


def hear(path: Path, prompt: str = DEFAULT_PROMPT, model: str = MODEL) -> str:
    """Hear music through the failover ladder: HAL → local mlx_vlm → direct REST.

    1. HAL ``/api/infer`` (media path) — provider-agnostic routing + failover +
       audit; HAL encodes the audio server-side.
    2. local mlx_vlm (HAL's offline audio rung) — when hal-server is unreachable.
    3. direct Gemini REST — the self-contained last resort, so hearing never
       hard-fails silently.

    Rungs 1-2 return None on a miss and the next rung is tried; rung 3 is
    authoritative (it returns the description or raises a plain-language error).
    The non-text rungs never degrade audio to a text-only answer."""
    via_hal = _hear_hal(path, prompt, model)
    if via_hal is not None:
        return via_hal
    via_local = _hear_mlx_vlm(path, prompt)
    if via_local is not None:
        return via_local
    return _hear_direct_rest(path, prompt, _rest_model(model))


def _clamp01(v) -> float:
    """Coerce to float in [0,1]; fail-safe to 0.5 on garbage (never crash the contract)."""
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def parse_structured(text: str) -> dict:
    """Parse the model's JSON hearing; tolerate ```json fences; clamp the axes."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t.strip("`")
        t = t[4:].strip() if t.lower().startswith("json") else t.strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"No JSON object in model output: {text[:200]}")
    obj = json.loads(t[start:end + 1])
    obj["energy"] = _clamp01(obj.get("energy"))
    obj["darkness"] = _clamp01(obj.get("darkness"))
    obj["instrumental"] = bool(obj.get("instrumental", False))
    obj["vocals"] = bool(obj.get("vocals", not obj["instrumental"]))
    for k in _STRUCT_KEYS:
        obj.setdefault(k, None)
    return obj


# The deterministic floor lives in lib/grip_hear_floor.py. It is loaded ONCE, by
# explicit file path via importlib, into this module-level cache — never via a
# sys.path mutation (which would risk import-order side effects / shadowing on
# repeated calls). _FLOOR_FN is the resolved analyze_floor callable, or None if
# the module could not be loaded (then _floor_for degrades to {}).
_FLOOR_FN = None
_FLOOR_LOADED = False


def _load_floor_fn():
    """Resolve analyze_floor from lib/grip_hear_floor.py by path, cached. None on failure.

    Uses importlib (no sys.path mutation) so calling the floor repeatedly has no
    global import-path side effects — the structured-parser contract is untouched
    when librosa/the floor module is unavailable."""
    global _FLOOR_FN, _FLOOR_LOADED
    if _FLOOR_LOADED:
        return _FLOOR_FN
    _FLOOR_LOADED = True
    floor_path = Path(__file__).resolve().parent.parent / "lib" / "grip_hear_floor.py"
    try:
        spec = importlib.util.spec_from_file_location("grip_hear_floor", floor_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _FLOOR_FN = mod.analyze_floor
    except Exception as exc:
        # Degrade to {} (tempo/key fall back to the model) — but surface WHY once,
        # so a packaging/symlink move that hides the module is not silently None.
        print(f"grip-hear: mechanical floor unavailable ({floor_path}: {exc}) — "
              f"tempo/key fall back to the model", file=sys.stderr)
        _FLOOR_FN = None
    return _FLOOR_FN


def _floor_for(path: Path) -> dict:
    """Run the deterministic librosa floor; {} if librosa/the floor is unavailable."""
    fn = _load_floor_fn()
    if fn is None:
        return {}
    try:
        return fn(path)
    except Exception:
        return {}


def apply_floor(reading: dict, floor: dict) -> dict:
    """Anchor tempo/key/duration to the mechanical floor; record provenance.

    The model may describe how a track *feels*; tempo and key are FACTS, so when
    librosa produced them they overwrite whatever the model said and the source
    is stamped "librosa". With no floor, the model's own values stand (source
    "model"). Mutates and returns `reading` for caller convenience."""
    if floor.get("tempo_bpm"):
        reading["tempo_bpm"] = floor["tempo_bpm"]
        reading["tempo_source"] = "librosa"
    else:
        reading.setdefault("tempo_bpm", None)
        reading["tempo_source"] = "model"
    if floor.get("key"):
        reading["key"] = floor["key"]
        reading["mode"] = floor.get("mode")
        reading["key_source"] = "librosa"
    else:
        reading.setdefault("key", None)
        reading.setdefault("mode", None)
        reading["key_source"] = "model"
    if floor.get("duration_s"):
        reading["duration_s"] = floor["duration_s"]
    return reading


# Map the model's free-text `language` value -> Vulavula STT language code.
# Vulavula supports a fixed set of (mostly South African) languages; English
# STT too. Anything mapped here transcribes via Vulavula.
_LYRIC_LANG = {
    "afrikaans": "afr", "english": "eng", "zulu": "zul", "isizulu": "zul",
    "xhosa": "xho", "isixhosa": "xho", "sotho": "sot", "sesotho": "sot",
    "tswana": "tsn", "setswana": "tsn",
}

# Languages OUTSIDE Vulavula's set that the optional Whisper fallback can still
# transcribe. Mapped to a 3-letter code transcribe_lyrics forwards to Whisper
# (which lazily handles the openai-whisper/torch optional dep, degrading to {}
# when absent). Anything in NEITHER map (or instrumental) still gets no layer.
_WHISPER_ONLY_LANG = {
    "french": "fra", "german": "deu", "spanish": "spa", "portuguese": "por",
    "dutch": "nld", "italian": "ita", "swahili": "swa",
}

# The lyric layer lives in lib/grip_hear_lyrics.py. Loaded ONCE by explicit file
# path via importlib into this cache — same pattern as the librosa floor above
# (no sys.path mutation, no import-order side effects on repeated calls).
_LYRIC_FN = None
_LYRIC_LOADED = False


def _load_lyric_fn():
    """Resolve transcribe_lyrics from lib/grip_hear_lyrics.py by path, cached.

    None if the module can't be loaded -> _transcribe_lyrics degrades to {}.
    importlib-by-path (not sys.path.insert) so calling it repeatedly has no
    global import-path side effects, mirroring _load_floor_fn."""
    global _LYRIC_FN, _LYRIC_LOADED
    if _LYRIC_LOADED:
        return _LYRIC_FN
    _LYRIC_LOADED = True
    lyric_path = Path(__file__).resolve().parent.parent / "lib" / "grip_hear_lyrics.py"
    try:
        spec = importlib.util.spec_from_file_location("grip_hear_lyrics", lyric_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _LYRIC_FN = mod.transcribe_lyrics
    except Exception:
        _LYRIC_FN = None
    return _LYRIC_FN


def _lyric_lang_for(hearing: dict) -> str | None:
    """Pick a transcription language code for a vocal track, or None to skip.

    Returns a Vulavula code (_LYRIC_LANG) when the language is Vulavula-covered;
    otherwise a 3-letter code from _WHISPER_ONLY_LANG so transcribe_lyrics can
    route it to the optional Whisper fallback. None when instrumental, non-vocal,
    or the language is in neither map.

    `parse_structured` always sets a bool `vocals` (defaulting to
    `not instrumental`), so post-parse the key is guaranteed. We still default a
    MISSING `vocals` to "vocal unless explicitly instrumental" here so the
    helper is robust if ever handed a raw dict — a vocal track is never silently
    skipped just because the key is absent."""
    if hearing.get("instrumental"):
        return None
    if not hearing.get("vocals", True):  # absent -> treat as vocal
        return None
    lang = (hearing.get("language") or "").strip().lower()
    return _LYRIC_LANG.get(lang) or _WHISPER_ONLY_LANG.get(lang)


def _transcribe_lyrics(path: Path, lang: str) -> dict:
    """Bridge to the lyric layer; best-effort even if the module is unreachable."""
    fn = _load_lyric_fn()
    if fn is None:
        return {}
    try:
        return fn(path, lang=lang)
    except Exception:
        return {}


def hear_structured(path: Path, model: str = MODEL, *, lyrics: bool = True) -> dict:
    """Structured hearing: audio -> validated dict (the catalogue contract).

    tempo/key are anchored to the deterministic librosa floor (never the model's
    guess) when it is available; provenance is recorded in *_source keys. When
    `lyrics` is on and the track has vocals in a Vulavula-supported language, a
    REAL transcribed lyric layer (lyrics/lyrics_source/lyrics_lang) is attached
    alongside the model's guessed `key_lyric`. Both layers are best-effort: a
    missing floor or a transcription miss leaves the hearing otherwise intact."""
    reading = apply_floor(parse_structured(hear(path, STRUCTURED_PROMPT, model)),
                          _floor_for(path))
    if lyrics:
        lang = _lyric_lang_for(reading)
        if lang:
            reading.update(_transcribe_lyrics(path, lang))
    return reading


def main() -> int:
    ap = argparse.ArgumentParser(prog="grip-hear", description="GRIP hears (understands) music.")
    ap.add_argument("audio", type=Path, help="path to an audio file")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--json", action="store_true",
                    help="emit a structured JSON hearing (the catalogue contract)")
    args = ap.parse_args()
    if not args.audio.is_file():
        print(f"No such file: {args.audio}", file=sys.stderr)
        return 2
    try:
        if args.json:
            print(json.dumps(hear_structured(args.audio, args.model), ensure_ascii=False))
        else:
            print(hear(args.audio, args.prompt, args.model))
    except RuntimeError as e:
        print(f"grip-hear error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
