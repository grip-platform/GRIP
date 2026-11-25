"""Extract a mode's prompt_injection payload — the missing half of /mode (GRIP#5242).

WHY THIS EXISTS. `/mode <name>` printed "[MODE ACTIVATED]" plus a skill list and
never emitted the mode's `prompt_injection` — the multi-line protocol that IS the
mode's value (brainstorm's is a 126-line SEED->DIVERGE->CLUSTER->CONVERGE->DEVELOP
protocol). No hook reads the YAML either, so in a Claude Code session the payload
reached nothing. The delivery mechanism that DOES work, verified in live session
transcripts, is plain stdout: the model reads a tool call's output, and SessionStart
hook stdout lands in context as hook_additional_context. So the fix is simply to
print the payload where activation and restore already print — and this module is
the one place that knows how to get it out of the YAML.

PARSING REALITY (measured 2026-09-01): 30 of 31 mode files carry prompt_injection
as a multi-line SINGLE-QUOTED YAML scalar running to end-of-file;
server-mode.yaml has no key at all and must stay silent, not error. PyYAML is
present in the GRIP venv but NOT in system python3, so this module tries yaml and
falls back to a scalar extractor that handles exactly the shape the corpus uses.
An unparseable or absent payload returns "" — a mode without a protocol is legal,
and activation must never fail because injection could not be read.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _fallback_extract(text: str) -> str:
    """Pull a single-quoted multi-line prompt_injection scalar without PyYAML.

    Matches the corpus shape: `prompt_injection: '` ... final `'`. The payload runs
    to the last single-quote in the remainder of the file (measured: the block is
    the final key in all 30 carrying files), and YAML's '' escape is unfolded.
    Returns "" on any mismatch rather than guessing at other YAML forms.
    """
    m = re.search(r"^prompt_injection:\s*'", text, flags=re.MULTILINE)
    if not m:
        return ""
    rest = text[m.end():]
    end = rest.rfind("'")
    if end < 0:
        return ""
    return rest[:end].replace("''", "'").strip()


def extract(yaml_path: str | Path) -> str:
    """Return the prompt_injection payload for one mode YAML, or ""."""
    path = Path(yaml_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        import yaml  # venv has it; system python3 may not

        data = yaml.safe_load(text)
        if isinstance(data, dict):
            value = data.get("prompt_injection")
            return value.strip() if isinstance(value, str) else ""
        return ""
    except Exception:
        return _fallback_extract(text)


def _write_utf8() -> None:
    """Make stdout UTF-8 whatever the locale, as ensure_utf8_stdio in lib/_compat.py
    does for the bin/ entry points (GRIP#4659).

    A Windows pipe defaults to cp1252, which cannot write the arrows and symbols
    in six modes' protocols, so this script exited 1 and the loader, under set -e,
    stopped after its banner with the mode's state already written: the mode was
    on and its protocol never arrived (GRIP#9334). Kept inline, with no import,
    because the script must run standalone: the loader's tests copy it alone into
    a test CLAUDE_DIR. A stream that cannot be reconfigured is left as it is.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: mode_prompt.py <mode-yaml> [more.yaml ...]", file=sys.stderr)
        return 2
    _write_utf8()
    out: list[str] = []
    for arg in argv[1:]:
        payload = extract(arg)
        if payload:
            out.append(payload)
    if out:
        print("\n\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
