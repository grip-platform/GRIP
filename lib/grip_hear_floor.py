#!/usr/bin/env python3
"""grip-hear mechanical floor — deterministic ground-truth for tempo and key.

grip-hear's model reading describes how a track *feels*; this module pins the
two facts a language model has no business guessing: tempo (BPM) and musical
key. Both come from librosa via signal analysis — never hallucinated.

`analyze_floor(audio_path)` returns {tempo_bpm, key, mode, duration_s} or, on
ANY failure (librosa absent, file unreadable, empty audio), an empty dict. It
is pure and offline: no network, no global state, never raises into the
pipeline. The caller treats a populated dict as ground-truth and an empty dict
as "fall back to the model".

Key estimation is Krumhansl-Schmuckler: the track's mean chroma vector is
correlated against the 24 major/minor key profiles (rotated to each of the 12
pitch classes); the highest correlation wins. This is the standard, fully
deterministic key-finding method — same audio in, same key out, every time.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

# The twelve pitch classes, sharp spelling, indexed to librosa's chroma bins
# (bin 0 = C). Used to name the estimated key's tonic.
_PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Krumhansl-Kessler key profiles (the canonical published weights). Index 0 is
# the tonic; the vector is rotated to test each of the 12 possible tonics.
_MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


# Below this standard deviation a chroma vector is treated as flat (silence /
# near-zero energy): correlation is undefined, so return 0.0 rather than risk a
# NaN from np.corrcoef on a (near-)constant vector. An epsilon, NOT exact == 0 —
# float noise can leave a silent vector at std ~1e-17, which == 0 would miss.
_FLAT_STD_EPS = 1e-9


def _correlate(chroma_mean: "list[float]", profile: tuple[float, ...], shift: int) -> float:
    """Pearson correlation between the chroma vector and a profile rotated by `shift`.

    Returns 0.0 when either vector is effectively flat (std below _FLAT_STD_EPS),
    which keeps a near-silent track from producing a NaN correlation that would
    make estimate_key's max() pick an arbitrary winner."""
    import numpy as np

    rotated = np.roll(np.asarray(profile, dtype=float), shift)
    a = np.asarray(chroma_mean, dtype=float)
    if a.std() < _FLAT_STD_EPS or rotated.std() < _FLAT_STD_EPS:
        return 0.0
    return float(np.corrcoef(a, rotated)[0, 1])


# The 24 candidate keys as a flat table: (tonic_index, profile, mode_name). Built
# once at import; estimate_key scans it linearly (no nested loop).
_KEY_CANDIDATES = tuple(
    (tonic, profile, mode)
    for tonic in range(12)
    for profile, mode in ((_MAJOR_PROFILE, "major"), (_MINOR_PROFILE, "minor"))
)


# Below this best-correlation the chroma carries no tonal content (silence,
# pure noise, or a perfectly flat vector) — there is no honest key to report,
# so estimate_key returns None rather than the arbitrary first max() winner.
_MIN_KEY_CORRELATION = 1e-6


def estimate_key(chroma_mean: "list[float]") -> "tuple[str, str] | None":
    """Krumhansl-Schmuckler: best (tonic, mode) over the 24 major/minor keys.

    Returns None when the strongest correlation is effectively zero — i.e. the
    signal has no tonal content. In that case max() would pick an arbitrary
    first entry (a misleading "C major"); reporting no key is the honest result.
    The mechanical floor never asserts a key the audio does not support."""
    best = max(
        _KEY_CANDIDATES,
        key=lambda c: _correlate(chroma_mean, c[1], c[0]),
    )
    if _correlate(chroma_mean, best[1], best[0]) <= _MIN_KEY_CORRELATION:
        return None
    tonic, _profile, mode = best
    return _PITCH_CLASSES[tonic], mode


def _coerce_tempo(tempo: Any) -> float:
    """librosa's tempo estimator returns a 1-element array; coerce to plain float BPM."""
    try:
        val = float(tempo[0]) if hasattr(tempo, "__len__") else float(tempo)
    except (TypeError, ValueError, IndexError):
        return 0.0
    return round(val, 1) if val > 0 else 0.0


def _tempo_bpm(y, sr) -> float:
    """Estimate global tempo, one float BPM.

    Canonical path is `from librosa.feature.rhythm import tempo` (librosa>=0.10) —
    note the submodule import: librosa's lazy loader blocks the attribute form
    `librosa.feature.rhythm.tempo`, and `librosa.beat.tempo` is a deprecated alias
    that emits a FutureWarning. Fall back to that alias only for older librosa."""
    try:
        from librosa.feature.rhythm import tempo as estimator  # canonical, no warning
    except ImportError:
        from librosa.beat import tempo as estimator            # older librosa (deprecated)
    return _coerce_tempo(estimator(y=y, sr=sr))


def _median_rms_dbfs(y) -> float:
    """Typical loudness of the track, in dBFS, as the MEDIAN of per-frame RMS.

    The median rather than a global RMS deliberately: one click or a moment of
    handling noise in an otherwise silent recording lifts a global RMS above any
    silence threshold, while the median still reports the recording for what it
    is. Roughly, music sits near -20 dBFS, quiet ambient near -35, and a
    near-silent room tone below -55.

    Returns a large negative number for digital silence rather than -inf, so the
    value stays JSON-serialisable and comparable.
    """
    import librosa
    import numpy as np

    frame_rms = librosa.feature.rms(y=y)[0]
    median = float(np.median(frame_rms)) if len(frame_rms) else 0.0
    return round(20.0 * math.log10(max(median, 1e-10)), 1)


def analyze_floor(audio_path) -> dict:
    """Deterministic ground-truth {tempo_bpm, key, mode, duration_s, rms_dbfs}.

    Best-effort and pure: returns {} on any failure (missing librosa, unreadable
    or empty file) and NEVER raises — the hearing pipeline must not crash because
    the mechanical floor could not run.

    `rms_dbfs` (added 2026-08-06) is how loud the track actually is. It exists
    because duration and tempo cannot tell a real recording from a silent one:
    a near-silent clip still decodes to a real duration, and on 2026-08-06 a
    deliberately silent control clip (median -61.5 dBFS, no acoustic events) was
    confidently described as "a baby giggling". Loudness is the measurement that
    makes that claim checkable.
    """
    path = Path(audio_path)
    if not path.is_file():
        return {}
    try:
        import librosa  # local import: missing librosa => {} below, never a crash
        import numpy as np

        y, sr = librosa.load(str(path), mono=True)
        if y is None or len(y) == 0:
            return {}
        duration_s = round(float(librosa.get_duration(y=y, sr=sr)), 1)
        tempo_bpm = _tempo_bpm(y, sr)
        chroma_mean = np.mean(librosa.feature.chroma_cqt(y=y, sr=sr), axis=1).tolist()
        result = {"tempo_bpm": tempo_bpm, "duration_s": duration_s,
                  "rms_dbfs": _median_rms_dbfs(y)}
        # estimate_key returns None on a tonally-flat signal — omit key/mode then
        # rather than report an arbitrary one. Tempo/duration still stand.
        key_mode = estimate_key(chroma_mean)
        if key_mode is not None:
            result["key"], result["mode"] = key_mode
        return result
    except Exception:
        # Any failure (import, decode, analysis) degrades to "no floor" — the
        # caller falls back to the model. The floor never breaks the pipeline.
        return {}
