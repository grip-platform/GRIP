"""Pure-stdlib notification chime generator for claude-handoff and GRIP.

Synthesises a two-tone bell (880 Hz fundamental + 1320 Hz perfect fifth)
with an ADSR envelope, 16-bit PCM mono, 44.1 kHz sample rate, ~0.4 s.

Zero non-stdlib dependencies — uses only `wave`, `struct`, and `math`.
Output is bit-identical for the same input parameters (Goodhart-safe
hash testable). Designed to be portable to the claude-handoff OSS
plugin in Wave 4 (priority + sounds) without modification.

CLI:
    python3 -m lib.handoff_chime                    # write to cache/grip-chime.wav
    python3 -m lib.handoff_chime --output ./bell.wav
    python3 -m lib.handoff_chime --play             # generate + play via afplay
"""

import argparse
import math
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List


# The cached chime, inside the GRIP root this file ships in -- never /tmp. /tmp is
# shared by every local user, so a fixed name there can be planted as a symlink
# first, and writing the chime then follows it (GRIP#6626). bin/grip-chime cannot
# import this module on its hot path, so it names the same file itself;
# tests/test_chime_asset_location.py holds the two to one path.
CHIME_WAV = Path(__file__).resolve().parent.parent / "cache" / "grip-chime.wav"

# Tuning: A5 + perfect fifth above (E6) — bright, attention-getting, not shrill.
DEFAULT_FUNDAMENTAL_HZ = 880.0
DEFAULT_HARMONIC_HZ = 1320.0
DEFAULT_DURATION_S = 0.4
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_AMPLITUDE = 0.55  # leave headroom — avoids clipping when summed

# ADSR envelope segments (proportions of total duration).
ATTACK_FRAC = 0.025   # 10 ms at 0.4 s
DECAY_FRAC = 0.10     # 40 ms
SUSTAIN_LEVEL = 0.55  # of peak
RELEASE_FRAC = 0.50   # 200 ms — long bell-like decay


@dataclass(frozen=True)
class ChimeSpec:
    """Immutable chime parameters. Same spec → bit-identical WAV bytes."""

    fundamental_hz: float = DEFAULT_FUNDAMENTAL_HZ
    harmonic_hz: float = DEFAULT_HARMONIC_HZ
    duration_s: float = DEFAULT_DURATION_S
    sample_rate: int = DEFAULT_SAMPLE_RATE
    amplitude: float = DEFAULT_AMPLITUDE


def _adsr_envelope(t: float, duration: float) -> float:
    """ADSR envelope evaluated at time `t` (seconds).

    Returns a value in [0.0, 1.0]. Pure function, no state.
    Attack rises linearly to 1.0; decay falls to SUSTAIN_LEVEL; sustain holds;
    release falls linearly back to 0.0.
    """
    if t < 0 or t >= duration:
        return 0.0
    attack_end = ATTACK_FRAC * duration
    decay_end = attack_end + DECAY_FRAC * duration
    release_start = duration - RELEASE_FRAC * duration
    if t < attack_end:
        return t / attack_end
    if t < decay_end:
        decay_progress = (t - attack_end) / (decay_end - attack_end)
        return 1.0 - decay_progress * (1.0 - SUSTAIN_LEVEL)
    if t < release_start:
        return SUSTAIN_LEVEL
    release_progress = (t - release_start) / (duration - release_start)
    return SUSTAIN_LEVEL * (1.0 - release_progress)


def synthesise_samples(spec: ChimeSpec) -> List[int]:
    """Synthesise the chime as a list of int16 samples.

    Pure function: same spec → identical sample list (Goodhart-safe).
    """
    n_samples = int(spec.sample_rate * spec.duration_s)
    samples: List[int] = []
    two_pi = 2.0 * math.pi
    for i in range(n_samples):
        t = i / spec.sample_rate
        env = _adsr_envelope(t, spec.duration_s)
        fund = math.sin(two_pi * spec.fundamental_hz * t)
        harm = math.sin(two_pi * spec.harmonic_hz * t)
        signal = env * spec.amplitude * (fund + 0.5 * harm) / 1.5
        sample = max(-32767, min(32767, int(round(signal * 32767))))
        samples.append(sample)
    return samples


def write_wav(path: Path, spec: ChimeSpec) -> Path:
    """Write the chime to a WAV file. Returns the resolved path."""
    samples = synthesise_samples(spec)
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(spec.sample_rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def play_wav(path: Path) -> int:
    """Play a WAV via macOS afplay. Returns the subprocess exit code.

    Non-macOS hosts return 127 (command not found) without raising.
    """
    try:
        result = subprocess.run(
            ["afplay", str(path)],
            check=False,
            capture_output=True,
            timeout=10,
        )
        return result.returncode
    except FileNotFoundError:
        return 127


def play_chime(spec: ChimeSpec | None = None,
               cache_path: Path = CHIME_WAV) -> int:
    """Generate (if missing) and play the default chime via afplay.

    Returns the subprocess exit code. Non-macOS hosts return 127 silently.
    Re-uses an existing cached WAV when present (idempotent / fast).
    """
    cache_path = Path(cache_path)
    spec = spec or ChimeSpec()
    if not cache_path.exists():
        write_wav(cache_path, spec)
    return play_wav(cache_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="GRIP/claude-handoff notification chime")
    parser.add_argument("--output", default=str(CHIME_WAV), help="output WAV path")
    parser.add_argument("--play", action="store_true", help="play after generating (macOS afplay)")
    parser.add_argument("--fundamental", type=float, default=DEFAULT_FUNDAMENTAL_HZ)
    parser.add_argument("--harmonic", type=float, default=DEFAULT_HARMONIC_HZ)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_S)
    args = parser.parse_args()
    spec = ChimeSpec(
        fundamental_hz=args.fundamental,
        harmonic_hz=args.harmonic,
        duration_s=args.duration,
    )
    path = write_wav(Path(args.output), spec)
    print(f"wrote {path} ({path.stat().st_size} bytes, {spec.duration_s}s, "
          f"{int(spec.fundamental_hz)}+{int(spec.harmonic_hz)} Hz)")
    if args.play:
        rc = play_wav(path)
        print(f"played: exit={rc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
