#!/usr/bin/env python3
"""Live meeting listener — room mic -> whisper -> rolling transcript.

Promoted from the inline ``python -c`` heredoc used during the 2026-06-09
live session (V>> + a legal-AI partner + Andre). Two defects observed live, fixed here:

1. **Junk capture** ("Ultra now is first captured junk", screen-share audio
   producing "false positive" chunks): whisper hallucinates filler on
   silence/noise ("Thank you.", "Thanks for watching!", bare "you"). A
   hallucination filter drops those chunks instead of appending them, and
   they count toward the silence exit.
2. **Fragility**: an inline heredoc cannot be tested, supervised, or reused
   (R0 argv-passthrough pattern). This module is the canonical listener;
   ``scripts/intel_act_loop.py`` is the canonical brain.

Capability lock (by construction): this process touches the microphone and
ONE transcript file. No network imports, no subprocess beyond the two fixed
binaries (ffmpeg, whisper), never through a shell.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
STATE_DIR = CLAUDE_DIR / "state" / "meet"

# Whisper silence/noise hallucinations observed across runs (normalised,
# lowercase, stripped of punctuation). Conservative: only unambiguous filler.
_HALLUCINATIONS = {
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "you",
    "bye",
    "uh",
    "um",
    "so",
    ".",
    "",
}

_TS_LINE = re.compile(r"\]\s")  # whisper stdout lines look like "[00:00.000 --> 00:04.000] text"

# B2: how many consecutive silent/junk chunks before the listener gives up.
# At 60s chunks the old value (8) auto-stopped after only ~8 min of quiet and
# lost the 2026-06-17 demo's mid-meeting silent stretch (audio had moved to the
# laptop speakers). 30 → ~30 min of contiguous silence tolerated, so a normal
# pause (screen-share, a break) no longer ends capture; --window-secs still
# bounds total runtime, and the 150s silence ALARM still warns of a deaf device.
DEFAULT_MAX_SILENT = 30


def default_transcript() -> Path:
    day = _dt.date.today().isoformat()
    return CLAUDE_DIR / "drafts" / f"meeting-transcript-{day}.md"


def parse_whisper_stdout(stdout: str) -> str:
    """Extract plain text from whisper CLI stdout timestamp lines."""
    parts = [
        line.split("] ", 1)[-1].strip()
        for line in stdout.splitlines()
        if _TS_LINE.search(line)
    ]
    return " ".join(p for p in parts if p).strip()


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def is_junk(text: str, min_words: int = 4) -> bool:
    """True when a chunk is a whisper hallucination / too thin to keep.

    A junk chunk counts as silence: it is neither appended nor spoken-for.
    """
    norm = normalise(text)
    if norm in _HALLUCINATIONS:
        return True
    words = norm.split()
    if len(words) < min_words:
        return True
    # A single token repeated over the whole chunk (noise-loop artefact).
    if len(set(words)) == 1:
        return True
    return False


def capture_chunk(wav: str, device: str, secs: int) -> bool:
    """Record one mic chunk via ffmpeg. False on capture failure."""
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "avfoundation",
         "-i", device, "-t", str(secs), "-ac", "1", "-ar", "16000", wav],
        capture_output=True, timeout=secs + 30,
    )
    return proc.returncode == 0


def transcribe_chunk(wav: str, model: str) -> str:
    """Run whisper on a wav, return extracted text ('' on failure)."""
    try:
        proc = subprocess.run(
            ["whisper", wav, "--model", model, "--output_format", "txt",
             "--output_dir", "/tmp", "--fp16", "False"],
            capture_output=True, text=True, timeout=240,
        )
    except subprocess.TimeoutExpired:
        return ""
    if proc.returncode != 0:
        return ""
    return parse_whisper_stdout(proc.stdout)


def append_chunk(transcript: Path, started: float, text: str) -> None:
    elapsed = int(time.time() - started)
    stamp = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
    with transcript.open("a") as fh:
        fh.write(f"**[{stamp}]** {text}\n\n")


def ensure_header(transcript: Path, title: str) -> None:
    if not transcript.exists() or not transcript.read_text().strip():
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(f"# {title}\n\nCaptured by GRIP intel-listen.\n\n")


def write_exit_sentinel(reason: str) -> None:
    """B2 — record why intel_listen exited so the watchdog can distinguish
    a clean stop from a mid-meeting crash.  Idempotent (overwrites on repeat).
    reason in {"silence", "window", "stopped", "crash"}."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    sentinel = STATE_DIR / "listener.exit"
    sentinel.write_text(json.dumps({
        "reason": reason,
        "at": _dt.datetime.now().isoformat(timespec="seconds"),
    }))


def _sigterm_handler(signum: int, frame: object) -> None:  # noqa: ARG001
    """SIGTERM from `grip-meet stop` — write a clean sentinel and exit.

    Without this handler Python's default SIGTERM exits immediately with no
    sentinel written, so the watchdog reads gone+no-sentinel = 'died' and
    fires a false alarm on a deliberate stop (AGORA fix #2)."""
    write_exit_sentinel("stopped")
    sys.exit(0)


def exit_reason_for(silent: int, max_silent: int, deadline: float) -> str:
    """Pure helper: determine the clean exit reason from loop state.

    Extracted so tests can verify the mapping without I/O.
    Returns "silence" when silent count reached the max; "window" otherwise.
    """
    return "silence" if silent >= max_silent else "window"


def listen(device: str, chunk_secs: int, window_secs: int, max_silent: int,
           transcript: Path, model: str, title: str) -> int:
    """Main capture loop. Returns 0 on clean window/silence exit."""
    # Fix #2: install SIGTERM handler so `grip-meet stop` writes a clean
    # sentinel instead of leaving the watchdog to read gone+no-sentinel="died".
    signal.signal(signal.SIGTERM, _sigterm_handler)

    ensure_header(transcript, title)
    started = time.time()
    deadline = started + window_secs
    silent = 0
    chunk = 0
    exc_to_raise = None
    print(f"[intel-listen] live -> {transcript} (device {device}, "
          f"{chunk_secs}s chunks, exit after {max_silent} silent)")
    try:
        while time.time() < deadline and silent < max_silent:
            chunk += 1
            wav = f"/tmp/intel-chunk-{chunk}.wav"
            if not capture_chunk(wav, device, chunk_secs):
                silent += 1
                time.sleep(5)
                continue
            text = transcribe_chunk(wav, model)
            if not text or is_junk(text):
                silent += 1
                continue
            silent = 0
            append_chunk(transcript, started, text)
    except Exception as exc:
        exc_to_raise = exc

    # Fix #1: write the correct sentinel EXACTLY ONCE regardless of how we exit.
    # If an exception was caught, reason="crash"; for clean loop exits use the
    # loop-state helper.  The bare re-raise was skipping clean sentinel writes.
    if exc_to_raise is not None:
        write_exit_sentinel("crash")
        print(f"[intel-listen] crashed after {chunk} chunks")
        raise exc_to_raise  # re-raise AFTER writing sentinel

    reason = exit_reason_for(silent, max_silent, deadline)
    write_exit_sentinel(reason)
    print(f"[intel-listen] stopped ({reason}) after {chunk} chunks")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", default=":1", help="avfoundation audio device (default :1 = room mic)")
    ap.add_argument("--chunk-secs", type=int, default=60)
    ap.add_argument("--window-secs", type=int, default=3600)
    ap.add_argument("--max-silent", type=int, default=DEFAULT_MAX_SILENT)
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--transcript", type=Path, default=None)
    ap.add_argument("--title", default=None)
    args = ap.parse_args(argv)
    transcript = args.transcript or default_transcript()
    title = args.title or f"Live meeting transcript — {_dt.date.today().isoformat()}"
    return listen(args.device, args.chunk_secs, args.window_secs,
                  args.max_silent, transcript, args.model, title)


if __name__ == "__main__":
    raise SystemExit(main())
