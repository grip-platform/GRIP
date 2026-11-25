---
name: handoff-chime
description: Pure-stdlib WAV notification chime generator for GRIP and the claude-handoff OSS plugin. Two-tone bell (880 Hz + 1320 Hz, perfect fifth) with ADSR envelope, 16-bit PCM mono, 44.1 kHz, ~0.4 s. Bit-identical across runs (SHA-256 testable). Zero non-stdlib dependencies — uses only wave/struct/math/subprocess. Designed to be portable into the claude-handoff package in Wave 4 (priority + sounds).
status: Active
effort: low
version: 1.0.0
created: 2026-04-11
generation: 509
triggers:
  - "play the chime"
  - "notification sound"
  - /chime
integrates:
  - lib/handoff_chime.py
  - lib/grip_notify.py
  - claude-handoff W4 (planned)
priority: Standard
single_pass_review:
  reviewer: gemini-2.5-flash (lib/external_reviewer.py)
  score: 0.95
  hallucination_risk: none
  reasoning_excerpt: "very well-written, clean, and efficient... excellent software engineering practices"
  caveat: "Single-reviewer point estimate. Falsified as 'validation' by the multi_provider_council below."
  framing_corrected: "2026-04-13 Truth Correction Sprint — original frontmatter said 'external_validation' which conflated single-pass review with validation"
multi_provider_council:
  date: 2026-04-13
  scorer: lib/moe/external_scorer.py:external_score(mode=standard)
  providers:
    llama-3.3-70b: 0.79
  composite: 0.79
  dimension_scores:
    correctness: 0.85
    design: 0.80
    simplicity: 0.70
  verdict: "FALSIFIES the original 0.95 single-pass framing. Combined with the original Gemini Flash 0.95, the two-reviewer range is 0.79-0.95 (16-point spread, mean ~0.87). Anthropic Haiku-external did not return for this file. Single-pass scores must not be cited as validation."
  raw_data: state/truth-corrections/multi-provider-council-2026-04-13.json
---

# handoff-chime — Pure-stdlib notification bell

## Why this exists

Notifications are silent. The macOS Notification Center default sound is the system tritone, which is fine but not GRIP's. The `claude-handoff` OSS plugin (W4 priority + sounds wave) needs a chime that:

- Has zero external dependencies (matches the v1 "no external services" ethos)
- Is bit-identical across runs (testable, no hidden state)
- Sounds bright and attention-getting without being shrill
- Fits in a single Python file under 150 LOC

This skill is the answer: pure stdlib `wave` + `math`, two-tone bell with ADSR envelope, 35 KB output. Ships now in `~/.claude/lib/` so it can be ported into `claude_handoff/sounds/` during W4 without modification.

## Mathematical specification

| Parameter | Value | Justification |
|---|---|---|
| Fundamental | 880 Hz | A5 — bright, mid-range |
| Harmonic | 1320 Hz | E6 — perfect fifth above (3:2 just intonation) |
| Duration | 0.4 s | Long enough to register, short enough not to annoy |
| Sample rate | 44.1 kHz | CD quality, standard |
| Bit depth | 16-bit signed | int16 PCM, smallest standard |
| Channels | 1 (mono) | Notification, not music |
| Amplitude | 0.55 | Headroom — avoids clipping when fundamental + harmonic sum |

ADSR envelope (proportions of total duration):

- **Attack**: 2.5% (10 ms) — linear rise from 0 to peak
- **Decay**: 10% (40 ms) — fall from peak to 55% sustain
- **Sustain**: until release — held at 55%
- **Release**: 50% (200 ms) — long bell-like linear decay

## Usage

```bash
# CLI
python3 -m lib.handoff_chime                    # → cache/grip-chime.wav in the GRIP root
python3 -m lib.handoff_chime --output ~/bell.wav
python3 -m lib.handoff_chime --play             # generate + afplay (macOS)
python3 -m lib.handoff_chime --fundamental 660 --harmonic 990 --duration 0.6

# Library
from lib.handoff_chime import ChimeSpec, write_wav, play_wav
spec = ChimeSpec()  # all defaults
path = write_wav("/tmp/chime.wav", spec)
play_wav(path)
```

## Integration with grip_notify

**W4 shipped.** `lib/grip_notify.GripNotifier.send()` calls
`_play_urgent_chime_if_needed(notif)` before the backend fan-out when
`notif.priority == "urgent"`. The helper synthesises
`cache/grip-chime.wav` in the GRIP root (once per process, in-memory flag) via
`write_wav`, then calls `play_wav`. Any failure is swallowed and logged
to stderr — the chime is UX, not a delivery guarantee.

```python
from lib.grip_notify import default_notifier, Notification, PRIORITY_URGENT

default_notifier().send(Notification(
    title="GRIP",
    body="Action required.",
    priority=PRIORITY_URGENT,   # plays bell, then fans out
))
```

The next step for `claude-handoff` OSS portability is to copy
`lib/handoff_chime.py` into `claude_handoff/sounds.py` unchanged —
the module has zero non-stdlib dependencies by design.

## Goodhart safety

18 hermetic tests in `tests/test_handoff_chime.py` assert specific values:

- **Tuning ratio**: `1320 / 880 == 1.5` (perfect fifth, not approximately)
- **ADSR shape**: zero outside duration, linear attack rise, decay to sustain, release to zero
- **Sample count**: exactly `int(sample_rate * duration_s)` (e.g. 17640 for default)
- **Sample range**: every sample fits in int16 `[-32767, 32767]`
- **Audibility**: peak amplitude > 16000 (>50% of int16 range)
- **Boundary samples**: first and last sample magnitude < 100 (envelope guarantees)
- **Determinism**: same `ChimeSpec` → identical sample list → identical bytes → identical SHA-256
- **WAV structure**: RIFF header, WAVE format, channel/rate/depth round-trip via `wave` module
- **Path resilience**: parent directories auto-created
- **Spec independence**: different fundamentals produce different samples

**Single-pass review** (NOT validation): one pass through `lib/external_reviewer.py` returned a score of 0.95/1.0 from `gemini-2.5-flash` with zero hallucination risk and the comment "very well-written, clean, and efficient... excellent software engineering practices". This is one reviewer, one pass, one score — a point estimate, not validation. Statistically suspicious: `lib/rave_card.py` got the identical 0.95 from the same reviewer, suggesting Gemini Flash may default-score well-shaped Python at 0.95 regardless of actual quality (probe H028). The real multi-provider council path is `lib/moe/external_scorer.py:236` (`external_score(mode="fast")` → 2 providers; `mode="standard"` → 4). The producing model (Claude) does not score its own work — but a single non-Claude reviewer is also not enough.

## Falsification

This skill is wrong if:

- The output WAV is not playable on macOS via `afplay` (currently confirmed working — `afplay` on the generated WAV returns exit 0)
- Two runs of the same `ChimeSpec` produce different bytes (caught by `test_write_wav_is_bit_identical_across_runs`)
- The chime is inaudible or shrill (subjective — V>>'s feedback after first listen is the falsification signal)
- A future Python release breaks the `wave.open(..., "wb")` API (bound to Python ≥ 3.9; current target is 3.11)

If V>> hears the chime once and never asks for it again, the aesthetic value is falsified. Status check: 2026-04-25.

## Why pure stdlib

Adding numpy or scipy for sine-wave math would have added ~50 MB of dependency for ~10 lines of math. The Python stdlib `math.sin` is fast enough at 17,640 samples (synthesis takes <10 ms). The `wave` module handles RIFF headers correctly and ships with every Python install. The `struct.pack(f"<{n}h", *samples)` packs all samples in one call (O(n) but C-implemented). Total synthesis + write time: under 30 ms on V>>'s M-series Mac.
