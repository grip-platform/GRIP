#!/usr/bin/env python3
"""GRIP frontier-model opt-in — set the Claude Code model in settings.json (#394).

The activate-grip wizard's frontier-model step writes the operator's chosen
Claude model to `settings.json` `model` — a USER field that install.sh's merge
preserves across `--update` (per the settings.json `_comment`). The `default`
choice is the routine default, and it REMOVES the key rather than writing it:
with no `model` key Claude Code uses the account's runtime default (Opus 5.5 at
1M on Max), which is also what bin/grip's `--model default` launch pin resolves
to. The literal word must never be written. Measured 2026-10-02 on Claude Code
2.1.283: with `"model": "default"` in settings.json a fresh headless session
sent `default` as the model id, its first request failed with "There's an issue
with the selected model (default)", and `/context` budgeted 200K. On the command
line, `--model default` resolves correctly; only the settings file breaks it.
Every other id is written in its `[1m]` spelling, because on that build only the
`[1m]` spellings get a 1M window. Opus 4.8 1M and the
Fable generation are opt-ins (Claude 5, Mythos-class); Sonnet 5 is opt-in too
(Sonnet 1M needs usage credits on Max). Adding a choice here does NOT change
what a session runs — only an operator picking it, or an explicit settings.json
edit, does that.

Design (CBC / KISS / no-stubs):
- Only `settings.json` `.model` is touched; every other key is preserved
  byte-for-byte (load JSON → set one key → dump). The write is atomic
  (temp file + os.replace).
- An UNKNOWN choice raises ``ValueError`` rather than writing a garbage model
  id that would break Claude Code on next launch (Rule 7 confidence gate made
  mechanical). ``resolve_model`` is the pure, unit-testable core.
- Model ids are the authoritative forms from the session environment: the
  ``default`` alias, the 1M-context alias ``sonnet[1m]`` already proven in
  settings.json, the exact ``claude-opus-4-8[1m]``, ``claude-fable-5``, and
  ``claude-fable-5-1``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

# Wizard choice key -> Claude Code model id. Order = the wizard's option order.
#
# `default` is an ALIAS, not a literal id: it clears any model override and
# reverts to the account's runtime default (Opus 5.5 at 1M on Max). bin/grip
# pins it on the command line (commit 3b6837d25, 2026-09-28), where it resolves
# to claude-opus-5-5[1m]. In settings.json the same word is read as a model id
# and fails, so set_model() expresses the choice by removing the key.
#
# Every vendor id below carries [1m]. Anthropic documents Opus 4.7+, Sonnet 5+
# and Fable as 1M without the suffix, and the API itself answers 404 to an id
# with it. Claude Code is a separate layer, though, and on 2.1.283 it budgets
# the plain spellings at 200K. Measured 2026-10-02 with the free `/context`
# command: claude-opus-5-5 200k, claude-fable-5-1 200k, claude-sonnet-5 200k,
# and every [1m] spelling 950k (the 1M window capped by
# CLAUDE_CODE_AUTO_COMPACT_WINDOW=950000). A real request on plain
# claude-fable-5-1 reported contextWindow 200000; on claude-fable-5-1[1m] it
# reported 1000000. Claude Code strips the suffix before calling the API, so the
# API's 404 never reaches a settings.json value. It only matters to callers that
# talk to the API directly.
FRONTIER_MODELS = {
    "default": "default",               # ALIAS — set_model() REMOVES the key for it, never writes it
    "sonnet-5": "sonnet[1m]",           # opt-in; Sonnet 1M needs usage credits on Max
    "sonnet-5-5": "claude-sonnet-5-5[1m]",  # the Pro tier's launch default; grip-tier pro pins the PLAIN id
    "opus-5-5": "claude-opus-5-5[1m]",  # what `default` resolves to on Max; cheaper than Opus 5
    "opus-4-8": "claude-opus-4-8[1m]",  # high-effort / VERIFY frontier (Opus 1M included on Max)
    "fable-5-1": "claude-fable-5-1[1m]",  # current Fable; 4x cheaper cache reads
    "fable-5": "claude-fable-5[1m]",    # previous Fable; vendor now lists it legacy
}

DEFAULT_CHOICE = "default"


def self_mapping_keys(models: dict[str, str]) -> list[str]:
    """Return keys whose values equal themselves, in input insertion order.

    Only ``FRONTIER_MODELS`` defines a canonical order; any other dict follows
    its own insertion history. The invariant for ``FRONTIER_MODELS`` is that
    this returns exactly ``["default"]``: only the `default` alias may map to
    itself, every other key maps to a real vendor id. Extracted so the invariant
    test and its counter-example (a non-default self-mapping entry) share one
    code path — without it the falsifier would re-derive the comprehension by
    hand.
    """
    return [k for k, v in models.items() if k == v]


def _settings_path() -> Path:
    return get_claude_dir() / "settings.json"


def resolve_model(choice: str) -> str:
    """Map a wizard choice key to its Claude Code model id.

    ``"default"`` is the one ALIAS, not a vendor model id: it maps to the
    literal string ``"default"``, which ``set_model`` turns into "no model key"
    (the account's runtime default) rather than writing it or a resolved vendor
    id. Every other key maps to a real model id.

    Raises ValueError on an unknown choice — never returns a garbage id that
    would break Claude Code."""
    try:
        return FRONTIER_MODELS[choice]
    except KeyError:
        raise ValueError(
            f"unknown model choice {choice!r} — expected one of "
            f"{sorted(FRONTIER_MODELS)}"
        )


def set_model(choice: str, settings_path: Path | None = None) -> str:
    """Write the chosen frontier model id to settings.json `.model`, preserving
    every other key. Returns the model id chosen. Atomic (temp + os.replace).

    The `default` alias removes `.model` instead of writing it: the literal word
    in settings.json is read as a model id and fails (see the module docstring)."""
    model_id = resolve_model(choice)
    path = settings_path or _settings_path()
    data: dict = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    if model_id == DEFAULT_CHOICE:
        # Never write the word: settings.json reads it as a model id.
        if "model" not in data:
            return model_id   # already the default; leave a watched file untouched
        data.pop("model")
    else:
        data["model"] = model_id
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return model_id


def _main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(f"usage: grip_model.py <choice>   ({'|'.join(FRONTIER_MODELS)})",
              file=sys.stderr)
        return 0 if argv else 2
    try:
        print(set_model(argv[0]))
        return 0
    except (ValueError, OSError) as e:
        print(f"grip_model: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
