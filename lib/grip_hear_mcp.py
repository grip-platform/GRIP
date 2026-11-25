#!/usr/bin/env python3
"""grip-hear MCP combiner — the thin, testable core behind the `hear` MCP tool.

This module owns ZERO music intelligence of its own. It composes the two
existing grip-hear pieces into a single structured reading the MCP server can
return mid-conversation:

    scripts/grip-hear.py :: hear() / hear_structured()  -> the model's hearing
    lib/grip_hear_floor.py :: analyze_floor()           -> deterministic floor

`hear_track(audio_path, ...)` returns the model's structured reading MERGED
with the librosa floor (ground-truth, never hallucinated) plus `provenance`
fields recording where each value came from. It deliberately does NOT import
`mcp` — the MCP wiring lives in mcp-servers/grip-hear/server.py — so this core
is unit-testable under any Python; the server's own `mcp` dependency is not
needed to exercise this half.

It returns a reading ONLY when the audio was genuinely measured and genuinely
audible. Two fabrications in two days forced that rule: on 2026-08-05 a slow
ballad about a man's death was reported as a high-energy club track, and on
2026-08-06 a deliberately silent control clip was reported as "a baby
giggling". Both carried `floor_source: "empty"` — nothing had been measured
either time, and the shape of the answer hid it. The checks live in
lib/grip_hear_grounding.py and none of them can degrade to a default.

Syscall-doctrine: the model call and the floor are the syscalls; this is the
shim that composes them. It calls them, it never reimplements them.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable

# Resolve the GRIP install dir without hardcoding a home path (path-portability).
_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# The grounding checks (lib/grip_hear_grounding.py) are loaded FAIL-CLOSED, and
# from this module's own directory rather than through grip-hear, so the "was
# anything measured?" gate can run before anything model-side is even imported.
_GROUNDING = None
_GROUNDING_LOCK = threading.Lock()


def _grounding():
    """Load the sibling grounding module, once, sharing one module identity.

    GRIP#5399: the module used to be registered in ``sys.modules`` BEFORE
    ``exec_module`` ran, so a concurrent caller found the name already present
    and received the half-built object — 1,712 consecutive AttributeError
    failures under 8 workers ("module 'grip_hear_grounding' has no attribute
    'assert_floor_measured'").

    The load now executes the module to completion FIRST and registers it
    AFTER, under a lock, so every caller receives either a fully-built module
    or a loud failure — never a half-built one. An already-registered module
    is reused only when it provably finished building (the hasattr guard).
    """
    global _GROUNDING
    with _GROUNDING_LOCK:
        if _GROUNDING is not None:
            return _GROUNDING
        existing = sys.modules.get("grip_hear_grounding")
        if existing is not None and hasattr(existing, "assert_floor_measured"):
            _GROUNDING = existing
            return _GROUNDING
        path = Path(__file__).resolve().parent / "grip_hear_grounding.py"
        try:
            spec = importlib.util.spec_from_file_location("grip_hear_grounding", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # fully built BEFORE any registration
            sys.modules["grip_hear_grounding"] = mod  # one identity, no half-built window
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
    """Re-export the grounding error classes lazily (PEP 562), one definition.

    Delegates by SHAPE rather than by an explicit list, so a grounding error
    class added later is re-exported automatically. A hardcoded list silently
    goes stale, which it already did once.
    """
    if name.endswith("Error"):
        return getattr(_grounding(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Keys the librosa floor OWNS. Per grip_hear_floor.py, tempo and key are "the
# two facts a language model has no business guessing" — so any value for these
# coming from the model is dropped, and they are populated ONLY from the floor.
# `rms_dbfs` joined them on 2026-08-06: how loud a recording actually is decides
# whether there is anything in it to describe, so it is measured, never guessed.
# Floor absent => these stay absent rather than carrying a model hallucination.
_FLOOR_OWNED_KEYS = ("tempo_bpm", "key", "mode", "duration_s", "rms_dbfs")


def _load_grip_hear():
    """Import scripts/grip-hear.py (hyphenated filename → SourceFileLoader).

    A normal `import` can't load a module whose filename contains a hyphen, so
    grip-hear.py is loaded by path. Kept lazy + cached on the function object so
    importing this module never triggers grip-hear's stdlib-only import cost
    until a hearing is actually requested.
    """
    cached = getattr(_load_grip_hear, "_mod", None)
    if cached is not None:
        return cached
    path = _CLAUDE_DIR / "scripts" / "grip-hear.py"
    loader = importlib.machinery.SourceFileLoader("grip_hear", str(path))
    spec = importlib.util.spec_from_loader("grip_hear", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    _load_grip_hear._mod = mod
    return mod


def _default_model() -> str | None:
    """grip-hear's default MODEL, resolved independently of any DI seam.

    Reported in provenance when no explicit model override was passed. Loading
    grip-hear is cheap (stdlib-only import) and cached, so this is safe to call
    even when the hearer itself was injected for tests.
    """
    try:
        return getattr(_load_grip_hear(), "MODEL", None)
    except Exception:
        return None


def _analyze_floor(audio_path: Path) -> dict:
    """Call lib/grip_hear_floor.analyze_floor; degrade to {} if the module is absent.

    analyze_floor is already best-effort (returns {} on any failure, never
    raises). This wrapper adds one more graceful layer: if grip_hear_floor
    itself can't be imported (e.g. running under a Python without librosa on
    the path), the floor is simply empty and the model reading still stands.

    The except is narrowed to ImportError on purpose: only "module/dependency
    not importable" should degrade to an empty floor here. analyze_floor's own
    internal try/except already absorbs decode/analysis errors and returns {},
    so a non-ImportError escaping it would be a genuine bug worth surfacing,
    not something to swallow silently.
    """
    try:
        import grip_hear_floor  # on sys.path via the server / test harness
    except ImportError:
        return {}
    return grip_hear_floor.analyze_floor(audio_path)


def hear_track(
    audio_path: str | Path,
    model: str | None = None,
    prompt: str | None = None,
    *,
    _hearer: Callable[..., Any] | None = None,
    _floor: Callable[[Path], dict] | None = None,
) -> dict:
    """Hear a local audio track: model reading + deterministic tempo/key floor.

    Returns a single dict combining the structured model hearing (genre, mood,
    energy, darkness, lyrics, …) with the librosa floor (tempo_bpm, key, mode,
    duration_s) and a `provenance` block. The floor values are authoritative
    where present; they OVERRIDE any tempo/key the model might have guessed.

    A reading is only ever returned when the audio was actually measured. If the
    deterministic floor produced nothing — librosa missing, the file undecodable
    — this raises instead of describing the track, and it raises BEFORE the model
    is called so the refusal costs nothing. Returning a fully-populated reading
    over an empty floor is what let a slow ballad about a death be reported as a
    workout track on 2026-08-05.

    Args:
        audio_path: path to a local audio file.
        model: optional model override (defaults to grip-hear's gemini-3.5-flash-lite).
        prompt: optional custom prompt. When set, the model returns a free-text
            description under `description` instead of the structured fields.
        _hearer / _floor: dependency-injection seams for tests (the Gemini call
            and the librosa floor). Production leaves them None. Neither seam can
            be used to skip the grounding checks.

    Raises:
        FileNotFoundError: if audio_path does not point at a real file.
        FloorUnavailableError: if nothing was measured from the signal.
        NoAudioAdmissionError: if the model answered by saying it cannot hear.
        AudioNotAttachedError: if the audio never made it into the request.
        RuntimeError: propagated from the model call (no key, HTTP error, …).
    """
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"No such audio file: {path}")

    grounding = _grounding()
    floor_fn = _floor if _floor is not None else _analyze_floor
    floor = floor_fn(path)  # deterministic, offline, never raises
    # Gate FIRST: with nothing measured there is nothing honest to report, and
    # no reason to pay for a model call whose answer would have to be discarded.
    measured = grounding.assert_floor_measured(
        floor, audio_path=path, diagnosis=_floor_diagnosis(used_default=_floor is None))
    # Measuring something is necessary but not sufficient: a near-silent clip
    # still decodes to a real duration, and one was described as "a baby
    # giggling" before this check existed. Nothing audible, nothing to describe.
    grounding.assert_audible(floor, audio_path=path)

    gh = None
    if _hearer is None:
        gh = _load_grip_hear()

    reading: dict[str, Any] = {}
    if prompt is not None:
        # Custom prompt → free-text description (not the structured contract).
        hearer = _hearer if _hearer is not None else gh.hear
        kw = {"model": model} if model is not None else {}
        reading["description"] = hearer(path, prompt, **kw)
        reading["structured"] = False
    else:
        hearer = _hearer if _hearer is not None else gh.hear_structured
        kw = {"model": model} if model is not None else {}
        reading.update(hearer(path, **kw))
        reading["structured"] = True

    # Strip any floor-owned key the model emitted: tempo/key/mode/duration are
    # librosa's to assert, never the model's.
    for k in _FLOOR_OWNED_KEYS:
        reading.pop(k, None)
    # Floor is ground-truth: it sets tempo/key/mode/duration regardless of model.
    reading.update(floor)
    # Anything the model said in its own words is checked for an admission that
    # it never heard the track. On the structured path an admission would
    # normally break JSON parsing upstream, but a hearer supplied through the
    # injection seam reaches here directly, so the check belongs at both ends.
    for field in ("description", "one_line"):
        if isinstance(reading.get(field), str):
            grounding.assert_heard(reading[field], rung="combiner")
    # Which model produced the reading. An explicit override always wins;
    # otherwise fall back to grip-hear's default MODEL. Resolved independently of
    # whether `gh` was loaded (when the hearer is injected, gh stays None but an
    # explicit `model` must still be reported) — so this is never silently None
    # when a model name was supplied.
    floor_source = _floor_source(used_default=_floor is None)
    reading["provenance"] = {
        "model_reading": "gemini",
        "tempo_key": floor_source,
        # Where the measurements came from. Only "librosa" (the real floor) or
        # "injected" (a test's floor) can appear: an unmeasured reading is not
        # returned at all, so this field can no longer say "empty" or "absent"
        # while every perceptual field is confidently filled in.
        "floor_source": floor_source,
        # The honest split. `measured` came from analysing the waveform;
        # `generated` is the model's impression of it. A transcribed lyric layer
        # is neither, and states its own origin in `lyrics_source`.
        "measured": list(measured),
        "generated": _generated_keys(reading, floor),
        "model": model or _default_model(),
        "audio_path": str(path),
    }
    return reading


# Keys that describe the payload itself rather than the track, plus the lyric
# layer, which carries its own `lyrics_source` provenance.
_NON_READING_KEYS = ("structured", "provenance")


def _generated_keys(reading: dict, floor: dict) -> list:
    """The reading's fields that are the model's impression, not measurements."""
    return sorted(
        k for k in reading
        if k not in floor and k not in _NON_READING_KEYS and not k.startswith("lyrics")
    )


def _floor_source(used_default: bool) -> str:
    """Where the measurements came from: the real floor, or a test's.

    Only reachable on the success path, where the floor is non-empty by
    construction — `assert_floor_measured` has already refused everything else.
    """
    return "librosa" if used_default else "injected"


def _floor_diagnosis(used_default: bool) -> str:
    """Why the floor measured nothing, in terms that point at the actual fix.

    "no tempo was found", "librosa is not installed" and "the module is missing"
    need three completely different responses, and the operator should not have
    to guess which one happened.
    """
    if not used_default:
        return "an injected floor returned no measurements"
    if not _floor_module_importable():
        return "lib/grip_hear_floor.py could not be imported in this interpreter"
    if not _librosa_importable():
        return "librosa is not installed in this interpreter, so no signal analysis ran"
    return ("the floor ran but measured nothing — the file may be silent, "
            "truncated, or in a format librosa could not decode")


def _librosa_importable() -> bool:
    """True iff librosa is available to the floor in this interpreter."""
    try:
        return importlib.util.find_spec("librosa") is not None
    except (ImportError, ValueError):
        return False


def _floor_module_importable() -> bool:
    """True iff lib/grip_hear_floor is importable in this interpreter."""
    try:
        import grip_hear_floor  # noqa: F401
        return True
    except ImportError:
        return False
