# grip-meet — live meeting intelligence in one command

GRIP listens to your meeting, understands what's needed as it happens, acts on
simple requests immediately, wakes a full GRIP session for anything complex, and
writes the minutes when the meeting ends. You speak; the infrastructure does the
rest.

Born live on 2026-06-09: during a Zoom call (V>> + a client contact + Andre), a bug was
filed **by saying it into the microphone** — and fixed, tested and merged before
the call ended. Hardened on 2026-06-17 after a live demo lost 15 minutes to a
deaf audio device → that failure became the watchdog, the start probe, and
`doctor`. This is that work, packaged.

## Get ready (once)

```bash
grip-meet-setup        # installs ffmpeg + BlackHole + whisper, pre-warms the
                       # speech model, narrates the audio-routing step, then
                       # runs `grip-meet doctor` as the readiness gate
grip-meet doctor       # "am I ready?" — deps, speech model, hal-server, audio
                       # devices (probed), loopback recommendation, ✓/✗ verdict
```

`grip-meet doctor` exits `0` only when everything is in place, so you can trust a
green check before a real call. Run it any time you're unsure.

## Quickstart

```bash
grip-meet start                  # ears + brain + watchdog (room mic, 60-min window)
grip-meet start --device :2      # capture a specific input (see `doctor` for indexes)
grip-meet status                 # liveness, chunk count, why-ears-down, hal reachability
grip-meet tail                   # latest transcript lines
grip-meet-dash                   # live browser view (http://127.0.0.1:8675)
grip-meet summary                # plain-English digest -> drafts/ (no posting)
grip-meet summary --post         # ALSO post ONE digest to the team group (opt-in)
grip-meet stop                   # stop every loop (own pids only, reaps children)
```

Discoverable from the main CLI too: `grip meet <subcommand>` (e.g. `grip meet
doctor`) is a pass-through to `grip-meet`.

The first time you run `grip-meet start`, a one-time orientation prints the four
steps above plus the headphone-capture recipe.

## Capturing a HEADPHONE call (the #1 gotcha)

A microphone only hears the **room** (your speakers). On a headphone call, the
other people's audio goes into your ears, not the room — so a mic captures
nothing. To capture the call you need a **system-audio loopback**:

1. `brew install blackhole-2ch` (or let `grip-meet-setup` do it).
2. In **Audio MIDI Setup**, create a **Multi-Output Device** = BlackHole + your
   headphones (so you still hear everyone).
3. Set that Multi-Output Device as the call's output.
4. `grip-meet doctor` prints the BlackHole device index; `grip-meet start
   --device :<that index>`.

## Try it — a 6-step demo

With headphones on and a Multi-Output device set up (see above):

```bash
grip-meet doctor                              # 1. confirm ✓ ready; note the device index
grip-meet start --device :<loopback> --title "Demo call"   # 2. ears + brain + watchdog
grip-meet-dash                                # 3. open http://127.0.0.1:8675 in a browser
#                                             # 4. say a sentence or two on the call
grip-meet tail                                # 5. watch the transcript fill in
grip-meet summary                             # 6. write a plain-English digest to drafts/
grip-meet stop                                # done — stops every loop cleanly
```

Meeting **decisions** can also be turned into signed, tamper-evident records via
`lib/meet_idr_bridge.py` — the "provable minutes" angle, if a demo wants the moat.

## Architecture — ears, brain, watchdog, hands

| Part | Process | Can do | Can never do |
|------|---------|--------|--------------|
| **Ears** | `scripts/intel_listen.py` | mic → ffmpeg → whisper → ONE transcript file | network, shell, any other file |
| **Brain** | `scripts/intel_act_loop.py` | read transcript deltas → hal-server classifies `{none\|acknowledge\|answer\|complex}` → post short text to the team group (rate-limited ≥3 min) → exit to wake the main session | execute commands, edit files, merge |
| **Watchdog** | `grip-meet _watchdog` (child of `start`) | poll liveness, AUTO-RESTART a crashed listener (capped), alert on a deaf/silent device, warn before the capture window closes | touch the transcript or post on its own |
| **Hands** | the main GRIP session | everything, behind the full gate stack | act on meeting audio without the brain's exit signal |

Meeting audio is **semi-trusted input** (open mic, transcription errors), so
capability is locked where the audio acts: the ears write one file, the brain
posts text and exits. Hands stay behind GRIP's gates
(`rules/untrusted-content-capability-lock.md`).

Comprehension economics: every chunk is read by the local **hal-server**
(`:3850/api/infer`, provider-laddered, effectively free) — the expensive main
session wakes only when the room asks for something that needs hands. Keyword
matching survives only as the hal-unreachable fallback. (`:3850` is the local
hal-server, distinct from the HAL gateway on `:4010`.)

## Hardening (most of it earned from real failures)

- **Fail-fast start probe** — `start` records ~5s on the chosen device and
  refuses to start on a SILENT (deaf) device, instead of looking live and losing
  the whole meeting. Override with `--no-probe`.
- **Active watchdog** — auto-restarts a crashed listener (capped to avoid a
  crash→restart storm), and rings a terminal bell + macOS notification when the
  device goes deaf, so silence is never silent.
- **Silence tolerance** — a long legitimate quiet stretch (screen-share, a break)
  no longer ends capture early; the deaf-device alarm still warns within ~150s.
- **Window-closing warning** — a heads-up ~5 minutes before the `--window-secs`
  limit, so a long meeting isn't cut off without notice.
- **Dependency pre-flight** — `start` refuses immediately if `ffmpeg`/`whisper`
  are missing, naming the install command.
- **Clean stop** — `stop` signals each loop's whole process group, so `ffmpeg`/
  `whisper` children are reaped (no orphans holding the audio device).
- **Whisper hallucination filter** — silence/noise produces fake filler ("Thank
  you.", "Thanks for watching!"); junk chunks are dropped, not transcribed.
- **One inbound, one reply** — a grip-channel dedupe guard stops duplicate
  replies after WhatsApp connection blips (PR #3495).
- **No channel spam** — posting is rate-limited; `summary --post` is strictly
  opt-in, one message, GRIP-prefixed.

## Live dashboard

`grip-meet-dash` serves a read-only, self-refreshing page (`127.0.0.1:8675`,
stdlib only, zero deps) showing the pipeline, the watchdog state, a ticking chunk
counter, and the live transcript feed. It follows the meeting's actual transcript
(from `meta.json`), and never touches the capture — it's the "look, GRIP is
listening" glance during a call.

## State + artefacts

| Path | What |
|------|------|
| `drafts/meeting-transcript-<date>.md` | rolling transcript (`**[MM:SS]** text`) |
| `drafts/meeting-transcript-<date>-summary.md` | digest (Summary / Decisions / Actions / Open questions) |
| `state/meet/{listener,actloop,watchdog}.pid` + `meta.json` | supervised pids + run metadata |
| `state/meet/{listener,actloop}.log` | loop logs |
| `state/meet/listener.exit` | exit sentinel — why ears went down (silence / window / stopped / crash) |
| `state/meet/.onboarded` | first-run orientation marker (shown once) |

## Tests

Goodhart-resistant suites across the stack — `tests/test_grip_meet.py`,
`tests/test_grip_meet_watchdog.py`, `tests/test_grip_meet_silence.py`,
`tests/test_grip_meet_probe.py`, `tests/test_grip_meet_health.py`,
`tests/test_grip_meet_doctor.py`, `tests/test_grip_meet_stop.py`,
`tests/test_grip_meet_window.py`, `tests/test_grip_meet_dash.py`,
`tests/test_grip_meet_setup.py`, `tests/test_grip_meet_onboarding.py`,
`tests/test_intel_listen.py`, `tests/test_intel_act_loop.py` — covering the
rate-limit floor, capability locks, junk filter, classify parsing, opt-in post
default, pid bookkeeping, the watchdog verdicts, the readiness gate, and the
dashboard snapshot. Run them:

```bash
grip-test tests/test_grip_meet*.py tests/test_intel_*.py
```

## Requirements

macOS only for v1 (the capture stack is avfoundation/BlackHole/`osascript`-bound).
Everything below is installed + verified by `grip-meet-setup` + `grip-meet
doctor`:

- `ffmpeg` — mic capture, device enumeration, the start probe.
- `whisper` CLI (`openai-whisper`) + the `small.en` model (~244 MB; pre-warmed by
  setup so the first meeting doesn't stall on a download).
- **BlackHole** (or any loopback/Multi-Output device) — required to capture a
  headphone call's downlink.
- a running local **hal-server** (`:3850/api/infer`) for comprehension — degrades
  to keyword-only without it.
- the Mac **grip-channel** daemon — only for `summary --post` / the brain's live
  posts to WhatsApp.
