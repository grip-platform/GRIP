#!/usr/bin/env python3
"""Canonical GRIP generation resolver — one source of truth for the
generation shown by the startup splash AND the KONO preload status line.

Before this module two code paths disagreed:

  * grip-splash.sh read top-level  index.json["generation"]              (stale)
  * kono_preload.py read           instances[-1]["lineage"]["generation"] (current)

On the GRIP repo project those were 430 vs 484 in the *same* file. Two
startup lines, same fact, different answers.

Generation is a property of the GRIP *installation* — its one continuous
RSI lineage — NOT of the directory you happen to launch in. Running GRIP
in ~/.hal is still the same GRIP at the same generation as running it in
~/.claude; only the folder-scoped *memory* differs (a separate concern,
owned by the KONO recall, not this resolver). Keying generation off the
cwd produced the "~/.hal -> new project (no GRIP history here)" bug while
KONO simultaneously recalled that folder's history — a contradiction
visible on one startup screen.

This resolver makes both callers read one value, one way, from the
install's canonical lineage (the ``$CLAUDE_DIR`` install's project index —
resolved via the path-portability shim ``claude_dir.get_claude_dir``, NOT a
hardcoded ``~/.claude``), regardless of the cwd passed in:

  authoritative generation = latest serialized instance's lineage.generation
                             of the GRIP install
                             (falls back to top-level "generation" only when
                              there are no instances at all)

Only a pristine install with zero serialized instances anywhere gets the
honest plain-language "new project" label; a working install resolves its
real "Gen N" in every directory.

CLI (used by grip-splash.sh, stdlib-only so it runs under system python3):

    python3 lib/grip_generation.py label [project_path]
        -> "Gen 484"
        -> "new project (no GRIP history here)"
    python3 lib/grip_generation.py json [project_path]
        -> {"has_lineage": true, "generation": 484, "label": "Gen 484"}

Import (used by kono_preload.py):

    from grip_generation import resolve_generation
    info = resolve_generation("/path/to/project")
    info.has_lineage, info.generation, info.label
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Resolve the GRIP install dir via the canonical portability shim
# (rules/path-portability.md) — NOT a hardcoded ``~/.claude``. The shim
# honours ``$CLAUDE_DIR`` so a non-default install (e.g. ~/.grip, or a
# container / CI / multi-user path) resolves its OWN lineage instead of
# falsely reporting "new project (no GRIP history here)" while its real
# Gen N sits in ``$CLAUDE_DIR/projects/<enc>/grip/index.json``. Bootstrap
# sys.path from ``__file__`` (env-independent) so the shim import can never
# depend on the value it is resolving.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:
    from claude_dir import get_claude_dir

    CLAUDE_DIR = get_claude_dir()
except ImportError:
    # claude_dir is a core stdlib-only shim, always present in a real install —
    # this is the last-resort fallback if it is genuinely absent. Emit a LOUD
    # diagnostic rather than silently masking it: a non-default $CLAUDE_DIR
    # install would otherwise mis-resolve its generation with no signal (AGORA
    # #3646). NOTE: a SyntaxError inside claude_dir.py is NOT caught here — only
    # a true "module absent" ImportError is — so this never hides a code defect.
    print(
        "[grip_generation] claude_dir shim not importable; falling back to "
        "~/.claude. A non-default $CLAUDE_DIR install will mis-resolve its "
        "generation until the shim is restored.",
        file=sys.stderr,
    )
    CLAUDE_DIR = Path.home() / ".claude"

# Plain language, no GRIP jargon — a first-time viewer must understand it.
NO_LINEAGE_LABEL = "new project (no GRIP history here)"

# DRY: route through next_step_block's single D2-correct instance selector so
# the splash and the memory line can never disagree again (the 430-vs-484 bug
# class). Guarded like path_encoding — a splash must never crash on import.
if str(_LIB) not in sys.path:  # own lib (GRIP#9857), not CLAUDE_DIR's
    sys.path.insert(0, str(_LIB))
try:
    from next_step_block import select_latest_instance
except ImportError:  # pragma: no cover — present in a real install
    select_latest_instance = None


@dataclass(frozen=True)
class GenerationInfo:
    """Resolved generation for one project. ``label`` is display-ready."""

    has_lineage: bool
    generation: Optional[int]
    label: str

    def to_dict(self) -> dict:
        return {
            "has_lineage": self.has_lineage,
            "generation": self.generation,
            "label": self.label,
        }


def _index_path(project_path: str) -> Optional[Path]:
    """Canonical ``projects/<encoded>/grip/index.json`` path.

    Uses the same encoder kono_preload uses (path_encoding.encode_project_path)
    so the splash and KONO can never key off different encodings. Returns None
    only if the encoder module is unavailable.
    """
    # This checkout's own lib (_LIB) is already on sys.path via the guarded
    # module-level insert above, never CLAUDE_DIR's (GRIP#9857 pass 4 — this
    # comment used to say "CLAUDE_DIR/lib", which stopped being true once the
    # insert above started naming _LIB instead; no second, unguarded sys.path
    # mutation here — AGORA #3425 Speaker C).
    try:
        from path_encoding import encode_project_path
    except ImportError:
        return None
    encoded = encode_project_path(project_path)
    return CLAUDE_DIR / "projects" / encoded / "grip" / "index.json"


def _read_generation(index_path: Path) -> Optional[int]:
    """Authoritative generation, or None if there is no GRIP history.

    Priority: latest serialized instance's ``lineage.generation`` (this is the
    live value). Top-level ``generation`` is only a fallback for the
    no-instances case because it is written less often and goes stale.
    """
    if not index_path.exists():
        return None
    try:
        data = json.loads(index_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    # D2-correct: the latest instance is max(serialized_at), NOT instances[-1]
    # (concurrent/retried saves append out of wall-clock order). The shared
    # selector fully guards malformed input (non-list, and non-dict entries).
    # If it could not import (a broken install — never in practice), degrade to
    # the top-level 'generation' fallback below rather than hand-rolling a
    # second, less-guarded selector — that divergence is exactly the bug this
    # PR fixes (AGORA #3425 Speaker C: an unguarded fallback would TypeError on
    # a malformed entry the primary selector handles safely).
    latest = (
        select_latest_instance(data.get("instances", []))
        if select_latest_instance is not None
        else None
    )
    if latest:
        gen = latest.get("lineage", {}).get("generation")
        if isinstance(gen, int):
            return gen
    top = data.get("generation")
    return top if isinstance(top, int) else None


def resolve_generation(project_path: Optional[str] = None) -> GenerationInfo:
    """The single function both the splash and KONO preload call.

    Generation is install-global: it resolves from the GRIP install's
    canonical lineage (the ``~/.claude`` project index), NOT from
    ``project_path``. The parameter is accepted for call-site/API
    stability (grip-splash.sh and kono_preload pass the cwd) and is
    deliberately ignored — folder-scoped memory is resolved separately by
    the KONO recall. This is what makes launching in ~/.hal report the
    real "Gen N" instead of a false "new project (no GRIP history here)".
    """
    # Resolve the install dir's realpath before encoding. A symlinked install
    # (e.g. ~/.claude -> ~/.grip, or $CLAUDE_DIR absent from a hook env that did
    # not source the shell profile) must key off its CANONICAL path, else the
    # symlink path encodes to a different projects/<enc>/ dir than the real one
    # and the lineage index is missed — producing a false "new project (no GRIP
    # history here)" while 1076 instances + the real Gen N sit one symlink away
    # (proven 2026-06-27: ~/.claude -> $HOME/.grip resolved -claude vs -grip).
    index = _index_path(str(Path(CLAUDE_DIR).resolve()))  # install lineage, never the cwd
    gen = _read_generation(index) if index is not None else None
    if gen is None:
        return GenerationInfo(False, None, NO_LINEAGE_LABEL)
    return GenerationInfo(True, gen, f"Gen {gen}")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("label", "json"):
        print(
            "usage: grip_generation.py {label|json} [project_path]",
            file=sys.stderr,
        )
        return 2
    project = argv[2] if len(argv) > 2 else str(Path.cwd())
    info = resolve_generation(project)
    print(info.label if argv[1] == "label" else json.dumps(info.to_dict()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
