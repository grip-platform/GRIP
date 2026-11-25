#!/usr/bin/env python3
"""
grip-listen — GRIP's ears.

Capture audio from the default Mac microphone and transcribe via Lelapa.ai
Vulavula STT. Pairs with grip-speak.py to complete the bidirectional voice
loop. Stdlib only — no pip installs.

Usage:
    grip-listen.py                        record 10s, transcribe, print
    grip-listen.py --duration 15          15-second recording
    grip-listen.py --lang zul             Zulu transcription
    grip-listen.py --audio /path/to.wav   skip recording, transcribe existing
    grip-listen.py --help                 show this message
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# HAL provider path
sys.path.insert(0, os.path.expanduser("~/.hal/lib"))

_KEYCHAIN_SERVICE = "grip-vulavula"
_DEFAULT_DURATION = 10
_DEFAULT_LANG = "eng"
_SAMPLE_RATE = 16000


def keychain_secret(service: str) -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Keychain entry '{service}' not found.\n"
            f"Add it with:\n"
            f"  security add-generic-password -s {service} -a v -w YOUR_KEY"
        )
    return result.stdout.strip()


def find_recorder() -> tuple[str, list[str]]:
    """Return (tool_name, base_args) for the first available recorder."""
    candidates = [
        ("/opt/homebrew/bin/sox", "sox"),
        ("/usr/local/bin/sox", "sox"),
    ]
    # Also check PATH
    try:
        which = subprocess.run(["which", "sox"], capture_output=True, text=True)
        if which.returncode == 0:
            candidates.insert(0, (which.stdout.strip(), "sox"))
    except OSError:
        pass

    for path, kind in candidates:
        if Path(path).exists():
            return kind, [path]

    # rec is a sox symlink — try it directly
    try:
        which = subprocess.run(["which", "rec"], capture_output=True, text=True)
        if which.returncode == 0:
            return "rec", [which.stdout.strip()]
    except OSError:
        pass

    # macOS built-in fallback
    try:
        which = subprocess.run(["which", "afrecord"], capture_output=True, text=True)
        if which.returncode == 0:
            return "afrecord", [which.stdout.strip()]
    except OSError:
        pass

    raise RuntimeError(
        "No audio recorder found (tried sox, rec, afrecord).\n"
        "Install sox:  brew install sox"
    )


def record(duration: int, out_path: str) -> None:
    """Record duration seconds of mic audio to out_path as 16kHz mono WAV."""
    kind, base = find_recorder()
    print(f"recording {duration}s... (speak now)", file=sys.stderr)

    if kind in ("sox", "rec"):
        cmd = base + [
            "-d",                         # default input device
            "-r", str(_SAMPLE_RATE),
            "-c", "1",
            "-b", "16",
            "-e", "signed-integer",
            "-t", "wav",
            out_path,
            "trim", "0", str(duration),
        ]
        if kind == "rec":
            # rec omits the input device argument
            cmd = base + [
                "-r", str(_SAMPLE_RATE),
                "-c", "1",
                "-b", "16",
                out_path,
                "trim", "0", str(duration),
            ]
    else:  # afrecord
        cmd = base + [
            "-f", "CAF",          # afrecord writes CAF; convert not available here
            "-d", str(duration),
            out_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Recorder failed: {result.stderr.strip()}")


def transcribe(audio_path: str, lang: str) -> str:
    """Send audio file to Vulavula and return transcription text."""
    try:
        from hal.providers.vulavula import VulavulaProvider
    except ImportError as e:
        raise RuntimeError(f"Cannot import VulavulaProvider from ~/.hal/lib: {e}") from e

    api_key = keychain_secret(_KEYCHAIN_SERVICE)
    provider = VulavulaProvider(api_key_source=api_key)
    result = provider.transcribe(audio_path, language=lang)
    return result.text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record mic audio and transcribe via Vulavula STT.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--duration", "-d", type=int, default=_DEFAULT_DURATION,
                        metavar="N", help=f"recording length in seconds (default {_DEFAULT_DURATION})")
    parser.add_argument("--lang", "-l", default=_DEFAULT_LANG,
                        metavar="CODE", help="language code: eng zul xho afr sot … (default eng)")
    parser.add_argument("--audio", "-a", metavar="PATH",
                        help="skip recording, transcribe an existing WAV file")
    args = parser.parse_args()

    audio_path = args.audio
    tmp_path = None

    try:
        if audio_path:
            if not Path(audio_path).exists():
                print(f"error: file not found: {audio_path}", file=sys.stderr)
                return 1
            print(f"transcribing {audio_path} ({args.lang})...", file=sys.stderr)
        else:
            tmp_path = f"/tmp/grip-listen-{int(time.time())}.wav"
            record(args.duration, tmp_path)
            audio_path = tmp_path
            print(f"transcribing ({args.lang})...", file=sys.stderr)

        text = transcribe(audio_path, args.lang)

        if not text:
            print("warning: empty transcription returned", file=sys.stderr)
            return 1

        print(text)
        return 0

    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        if tmp_path and Path(tmp_path).exists():
            Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
