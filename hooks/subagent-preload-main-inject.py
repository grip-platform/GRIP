#!/usr/bin/env python3
"""SessionStart hook: give the MAIN session an index of the EXTENDED rules
(#464 subagent-preload split).

Why: config/rules-scope.json's `extended` rules are removed from Claude Code's
native memory load via settings.claudeMdExcludes, so they do not inflate the
Task-tool subagent preload. The main session still has to know they exist.

This hook used to inject every extended rule's full text, about 405,000
characters, and its docstring claimed "main = full stack". That was false:
Claude Code persists any hook output over 10,000 characters to a file and
shows the model a 2KB preview (GRIP#6446), so the model saw roughly the first
page of the first rule and nothing else. It now injects an index that arrives
in full: one line per extended rule, with its title and where to Read it.

Contract:
- Emits hookSpecificOutput.additionalContext (SessionStart injects it as context).
- Stays under MAX_CHARS; if titles ever push it over, it lists names only.
- Fail-open: ANY error -> emit nothing, exit 0. Never breaks session start.
- session-context.md embeds the HOLISTIC kernel (kono_preload builds it in), so
  standalone HOLISTIC.md is dropped from the index.
- On source=="clear", kono_preload already surfaces session-context.md, so it is
  left out here. Every other source lists it.
- A main-tier rule (config `main_tier`) is never listed: hooks/main-tier-inject.py
  delivers its full text, so an index line would hand it over a second time.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

MAX_CHARS = 9_500  # under the 10,000 at which the harness shows only a preview
_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

_HEADER = (
    "# GRIP Extended Rules (main session only): an index\n"
    "The rules below apply to THIS main session exactly as if they were loaded. "
    "Only this index is injected: hook output over 10,000 characters reaches the "
    "model as a 2KB preview, so their full texts never arrived (GRIP#6446). "
    "Before acting in a rule's area, Read its file. Safety-core rules load "
    "natively everywhere, subagents included, and are not listed here.\n"
    "Directory: {rules_dir}\n"
)


def _read_source() -> str:
    try:
        raw = sys.stdin.read() or "{}"
        payload = json.loads(raw) if raw.strip() else {}
        return str(payload.get("source", "startup"))
    except Exception:
        return "startup"


def _select_names(cfg: dict, rules_dir: Path, source: str) -> list[str]:
    # A main-tier rule arrives whole from hooks/main-tier-inject.py, so it is never
    # also an index line, even under a config that lists it in both places.
    main_tier = set(cfg.get("main_tier") or [])
    names = [n for n in cfg.get("extended", []) if n not in main_tier and (rules_dir / n).is_file()]
    # session-context.md embeds HOLISTIC.md -> drop the standalone dup.
    if "session-context.md" in names and "HOLISTIC.md" in names:
        names.remove("HOLISTIC.md")
    # kono_preload surfaces session-context.md on /clear -> avoid double-surface.
    if source == "clear" and "session-context.md" in names:
        names.remove("session-context.md")
    return names


def _title(path: Path) -> str:
    """The file's first level-one heading, or '' (a git-crypt blob has none)."""
    try:
        match = _H1.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    return match.group(1) if match else ""


def _line(name: str, title: str) -> str:
    return f"- {name} — {title}" if title else f"- {name}"


def _render_index(names: list[str], rules_dir: Path, state_names: frozenset[str] = frozenset(),
                  with_titles: bool = True) -> str:
    titles = {n: _title(rules_dir / n) if with_titles else "" for n in names}
    rules = [n for n in names if n not in state_names]
    ranked = sorted(rules, key=lambda n: ("PARAMOUNT" not in titles[n], n))
    state = [n for n in names if n in state_names]
    parts = [_HEADER.format(rules_dir=rules_dir), "Rules (PARAMOUNT first):"]
    parts += [_line(n, titles[n]) for n in ranked]
    if state:
        parts += ["", "Session state (Read it when continuing earlier work):"]
        parts += [_line(n, titles[n]) for n in state]
    return "\n".join(parts) + "\n"


def _render(names: list[str], rules_dir: Path, state_names: frozenset[str] = frozenset()) -> str:
    """The index, falling back to bare names if titles would overflow MAX_CHARS.

    ``state_names`` are the generated session-state files, listed apart; the
    hook takes them from config/rules-scope.json ``runtime_generated``, the one
    place that list is kept.
    """
    text = _render_index(names, rules_dir, state_names)
    if len(text) <= MAX_CHARS:
        return text
    return _render_index(names, rules_dir, state_names, with_titles=False)


def _emit(additional_context: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }))


def main() -> int:
    source = _read_source()
    try:
        from claude_dir import get_claude_dir
        import rules_scope

        rules_dir = get_claude_dir() / "rules"
        cfg = rules_scope.load_config()
        names = _select_names(cfg, rules_dir, source)
        if names:
            _emit(_render(names, rules_dir, frozenset(cfg.get("runtime_generated", []))))
    except Exception:
        pass  # Fail-open: never block session start over rule injection.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
