"""Sync the Anthropic credential into Open-LLM-VTuber's conf.yaml claude_llm block.

Called by ``bin/voice-ai`` at every launch so the value on disk always matches
the keychain (rotation-proof). The credential arrives on **stdin** — piped
straight from the keychain lookup — never via argv (ps-visible) and never via
a named environment variable (inherited by child processes).

The replacement targets the ``llm_api_key`` line *inside* the ``claude_llm:``
mapping only: the anchor requires the literal ``claude_llm:`` key followed by
a newline, so quoted mentions in comments (``# ... 'claude_llm', ...``) never
match. Other providers' ``llm_api_key`` lines are untouched.

Anthropic credentials are URL-safe characters, so single-quoted YAML needs no
escaping; the value is still validated to be quote-free before writing.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_KEY_LINE = re.compile(r"(claude_llm:\n(?:[^\n]*\n)*?\s*llm_api_key:\s*)'[^']*'")


def sync(conf_path: str | Path, credential: str) -> None:
    """Rewrite the claude_llm llm_api_key value in ``conf_path``.

    Raises SystemExit with a plain-language message when the block is absent
    (misconfigured file) or the value cannot be safely single-quoted.
    """
    if "'" in credential or "\n" in credential:
        raise SystemExit("voice-ai keysync: value contains characters unsafe for single-quoted YAML")
    path = Path(conf_path)
    text = path.read_text(encoding="utf-8")
    new, n = _KEY_LINE.subn(lambda m: m.group(1) + "'" + credential + "'", text, count=1)
    if n != 1:
        raise SystemExit(f"voice-ai keysync: claude_llm llm_api_key line not found in {path}")
    path.write_text(new, encoding="utf-8")
    os.chmod(path, 0o600)
    # The server's boot-time config migration copies conf.yaml to
    # conf.yaml.backup (credential included) — keep that private too.
    backup = path.with_name(path.name + ".backup")
    if backup.exists():
        os.chmod(backup, 0o600)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: <keychain-lookup> | voice_ai_keysync.py <path-to-conf.yaml>")
    credential = sys.stdin.read().strip()
    if not credential:
        raise SystemExit("voice-ai keysync: no value received on stdin")
    sync(argv[1], credential)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
