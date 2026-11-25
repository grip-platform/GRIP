#!/usr/bin/env python3
"""Typewriter renderer for grip ask — streams claude --print output character by character.

Usage:
    python3 typewriter.py [claude flags] -- "prompt text"
    python3 typewriter.py --resume UUID "follow-up prompt"

Environment:
    GRIP_TYPEWRITER_DELAY  Seconds per character (default: 0.015 = ~67 chars/sec)
"""
import json
import os
import subprocess
import sys
import time

CHAR_DELAY = float(os.environ.get("GRIP_TYPEWRITER_DELAY", "0.015"))


def stream_with_typewriter(args):
    cmd = [
        "claude", "--print",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--dangerously-skip-permissions",
    ] + args

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    prev_text = ""

    for raw in proc.stdout:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    full = block["text"]
                    delta = full[len(prev_text):]
                    for ch in delta:
                        sys.stdout.write(ch)
                        sys.stdout.flush()
                        time.sleep(CHAR_DELAY)
                    prev_text = full

    sys.stdout.write("\n")
    sys.stdout.flush()
    proc.wait()
    return proc.returncode


if __name__ == "__main__":
    sys.exit(stream_with_typewriter(sys.argv[1:]))
