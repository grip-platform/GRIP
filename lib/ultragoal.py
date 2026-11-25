#!/usr/bin/env python3
"""ultragoal.py — deterministic kernel for the /ultragoal skill.

Claude Code's native /goal installs a session-scoped Stop hook whose CONDITION
string is capped at 4000 chars. Operators hit the cap by stuffing the whole
brief (context, links, constraints) into the condition. /ultragoal splits the
two concerns:

  brief     — a durable markdown file (plans/ULTRAGOAL-<slug>-<date>.md): no
              length limit, survives compaction and /clear, carries the
              Deliverables checklist with per-item DONE-predicates.
  condition — a SHORT falsifiable POINTER at the brief, derived here,
              <= CONDITION_BUDGET (900) by construction, hence always under
              the native GOAL_CONDITION_MAX (4000).

Everything in this stack is a pure projection: no LLM, no network. The fold
is Banach-idempotent — fold_brief(fold_brief(x)) == fold_brief(x) — and
compose_brief() emits fixed points of the fold (precedents:
lib/fold_search.py, lib/learning_proposer.py::selftest_idempotent).

Layered per the AGORA DEFER review on the /ultragoal PR (#3914) —
one module, one concern; this module is the PUBLIC SURFACE and re-exports
the full API, so `from lib.ultragoal import ...` always works:

    lib/ultragoal_fold.py    fold core + converge runtime witness
    lib/ultragoal_probes.py  checklist parse + live probe execution
    lib/ultragoal.py         composition, condition, cards, CLI (this file)

CLI:
    bin/grip-py lib/ultragoal.py compose --goal "..." [--deliverable "item :: probe"]...
    bin/grip-py lib/ultragoal.py derive <path> [--oneliner]
    bin/grip-py lib/ultragoal.py verify <path> [--tick]
    bin/grip-py lib/ultragoal.py next <path>
    bin/grip-py lib/ultragoal.py status <path-or-slug>
    bin/grip-py lib/ultragoal.py fold <path>
    bin/grip-py lib/ultragoal.py selftest
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import shlex
import sys
from pathlib import Path
from typing import Optional, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from ultragoal_fold import (  # noqa: E402,F401 — re-exported public API
    FoldError,
    fold_brief,
    fold_hash,
    goal_hash,
)
from ultragoal_probes import (  # noqa: E402,F401 — re-exported public API
    _PREDICATE_SPLIT,
    ChecklistItem,
    Probe,
    ProbeResult,
    VerifyReport,
    lint_probe,
    next_item,
    parse_checklist,
    parse_probes,
    progress,
    run_probe,
    verify,
)

#: Hard cap the Claude Code harness enforces on a /goal condition string.
GOAL_CONDITION_MAX = 4000
#: Our own budget for a derived condition — a pointer, never a payload.
#: The condition now embeds THREE absolute paths (bin/grip-py,
#: lib/ultragoal.py, and the brief itself), not one, so the old 600 is too
#: tight: a real plans/ path under the install dir measures 323 chars for
#: ULTRAGOAL-frankenstein-linkedin-comment-2026-11-12.md (measured
#: 2026-11-16), and GRIP's own test harness -- whose hermetic CLAUDE_DIR
#: override nests several tmp-dir levels deep -- measures up to 621 for
#: the same shape. 900 clears both with room to spare while staying 4.4x
#: under the native cap.
CONDITION_BUDGET = 900


# ---------------------------------------------------------------------------
# naming + location
# ---------------------------------------------------------------------------

def slugify(text: str, max_len: int = 48) -> str:
    """Deterministic filesystem-safe slug for a goal title/prose."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "goal"


def _plans_root(root: Optional[Path]) -> Path:
    return Path(root) if root is not None else get_claude_dir() / "plans"


def brief_path(slug: str, date: Optional[str] = None,
               root: Optional[Path] = None) -> Path:
    """plans/ULTRAGOAL-<slug>-<YYYY-MM-DD>.md (date defaults to today)."""
    day = date or _dt.date.today().isoformat()
    return _plans_root(root) / f"ULTRAGOAL-{slug}-{day}.md"


def find_brief(slug: str, root: Optional[Path] = None) -> Optional[Path]:
    """Newest existing brief for this slug (date-in-name sorts chronologically)."""
    base = _plans_root(root)
    if not base.is_dir():
        return None
    matches = sorted(base.glob(f"ULTRAGOAL-{slug}-*.md"))
    return matches[-1] if matches else None


# ---------------------------------------------------------------------------
# brief composition
# ---------------------------------------------------------------------------

def _deliverable_lines(deliverables: Sequence) -> list[str]:
    """Render Deliverables entries: '(item, probe)' pairs or plain strings."""
    if not deliverables:
        return ["- [ ] (fill: falsifiable item — DONE-predicate: probe)"]
    lines: list[str] = []
    for entry in deliverables:
        if isinstance(entry, (tuple, list)) and len(entry) == 2:
            item, probe = entry
            lines.append(f"- [ ] {item} — DONE-predicate: {probe}")
        else:
            lines.append(f"- [ ] {entry}")
    return lines


def compose_brief(goal_text: str, *,
                  title: Optional[str] = None,
                  why: str = "",
                  context: Sequence[str] = (),
                  deliverables: Sequence = (),
                  held: Sequence[str] = (),
                  guardrails: Sequence[str] = (),
                  date: Optional[str] = None,
                  run_mode: str = "depth-0 main thread; escalate per dfi-fib",
                  path_hint: Optional[Path] = None) -> str:
    """Render the canonical ULTRAGOAL brief (a fold fixed point).

    ``deliverables`` entries are either plain strings or ``(item, probe)``
    pairs; a probe is the verify-canonical DONE-predicate (command / SHA /
    endpoint) that makes the item falsifiable per exogenous-anchor-law.
    With ``path_hint`` + real deliverables, the derived armed condition is
    embedded under ## Criterion so ARM is reproducible from the file alone.
    """
    day = date or _dt.date.today().isoformat()
    head = title or goal_text.strip().splitlines()[0][:72]
    parts: list[str] = [f"# ULTRAGOAL — {head}", "",
                        f"Created: {day}", f"Run: {run_mode}", ""]
    parts += ["## Goal", "", goal_text.strip(), ""]
    parts += ["## Why", "", why.strip() or "(fill: the operator goal this serves)", ""]
    parts += ["## Context — verify-canonical anchors", ""]
    parts += [f"- {c}" for c in context] or ["- (fill: state claims, each with its probe)"]
    parts += ["", "## Deliverables", ""]
    parts += _deliverable_lines(deliverables)
    parts += ["", "## Criterion (converge exit)", "",
              "`bin/grip-py lib/ultragoal.py verify <this file>` exits 0 — every",
              "DONE-predicate passes as a live probe (exogenous; ticked boxes",
              "are telemetry, never the criterion). The /goal condition that",
              "`derive` prints names the tool and this file by absolute path.", ""]
    parts += ["## Held / gates", ""]
    parts += [f"- {h}" for h in held] or ["- (none — fully autonomous)"]
    parts += ["", "## Guardrails", ""]
    parts += [f"- {g}" for g in guardrails] or ["- (session-specific lessons to carry)"]
    text = fold_brief("\n".join(parts))
    if path_hint is not None and deliverables:
        cond = derive_condition(path_hint, text)
        text = fold_brief(text.replace(
            "## Held / gates", f"Armed condition: {cond}\n\n## Held / gates", 1))
    return text


def selftest_idempotent() -> bool:
    """Loud Banach proof: fold∘fold == fold, and compose emits fixed points.

    Raises AssertionError on any drift; returns True when the fixed point
    holds (mirrors lib/learning_proposer.py::selftest_idempotent).
    """
    sample = compose_brief(
        "Ship the widget",
        deliverables=[("widget shipped", "gh pr view 1 --json state")],
        context=["repo is clean — git status"],
    )
    if fold_brief(sample) != sample:
        raise AssertionError("compose_brief() output is not a fold fixed point")
    noisy = "\r\n\r\n" + sample.replace("\n## Why\n", "\n\n\n## Why   \n") + "   \n\n\n"
    once, twice = fold_brief(noisy), fold_brief(fold_brief(noisy))
    if once != twice:
        raise AssertionError("fold_brief is not idempotent: fold(fold(x)) != fold(x)")
    if fold_brief(once) != once:
        raise AssertionError("folded output is not a fixed point")
    return True


# ---------------------------------------------------------------------------
# condition derivation — the pointer that goes into native /goal
# ---------------------------------------------------------------------------

def _install_dir() -> Path:
    """The install dir in the spelling ``get_claude_dir()`` returns.

    Both branches of ``get_claude_dir()`` are absolute: an exported
    ``$CLAUDE_DIR`` is expanded and resolved there, and the default
    ``$HOME/.claude`` is left unresolved, which keeps a symlinked install
    (``~/.claude`` -> ``~/.grip``) in its own spelling in the pointer.
    """
    return Path(get_claude_dir())


def _absolute_path(path: Path) -> str:
    """Absolute, working-folder-independent display form of ``path``.

    When ``path`` resolves inside the install dir, the result is printed in
    the install dir's OWN spelling (the ``~/.claude`` -> ``~/.grip`` symlink
    stays intact), so a session on a machine where that symlink is real
    prints ``/Users/<you>/.claude/...``, never the resolved ``~/.grip``
    target. Outside the install dir, the plain resolved path is returned.
    Replaces the old install-relative ``_display_path``, whose output
    resolved only from the install dir: a condition pasted from ANY cwd --
    a worktree, another project, ``~/.hal`` -- must resolve without
    depending on the pasting session's working folder.
    """
    install = _install_dir()
    resolved = Path(path).resolve()
    try:
        rel = resolved.relative_to(install.resolve())
    except ValueError:
        return str(resolved)
    return str(install / rel)


def _verify_command(brief_abs: str) -> str:
    """The shell command the derived /goal condition points at.

    Always the INSTALL dir's own copy of this tool (stable -- a worktree's
    copy disappears when the worktree is reaped), run through ``bin/grip-py``
    per ``rules/never-system-python-for-grip.md`` -- never the system
    ``python3``. ``CLAUDE_DIR`` is pinned to the install dir for this one
    command, because ``bin/grip-py`` finds its venv through ``$CLAUDE_DIR``:
    a session that exports another value (a private shim, a relative path)
    would otherwise look for a venv that is not there. Each element is
    ``shlex.quote``-d so a path containing a space still round-trips through
    ``shlex.split``.
    """
    install = _install_dir()
    parts = [str(install / "bin" / "grip-py"),
             str(install / "lib" / "ultragoal.py"), "verify", brief_abs]
    pin = "CLAUDE_DIR=" + shlex.quote(str(install))
    return " ".join([pin] + [shlex.quote(part) for part in parts])


def derive_condition(path: Path, text: Optional[str] = None) -> str:
    """Derive the short, falsifiable /goal condition pointing at the brief.

    Guaranteed <= CONDITION_BUDGET (900) by construction — the condition is a
    POINTER; the brief file is the payload. Fails loud (ValueError) when the
    brief has no Deliverables checklist: an unfalsifiable goal is refused,
    never armed (exogenous-anchor-law). Every path in the printed command --
    the tool and the brief -- is absolute, so the condition resolves from
    ANY cwd once pasted, not only the install dir.
    """
    body = text if text is not None else Path(path).read_text(encoding="utf-8")
    _done, total = progress(body)
    if total == 0:
        raise ValueError(
            f"No '## Deliverables' checklist found in {path} — refusing to "
            "derive an unfalsifiable condition. Add '- [ ] item — "
            "DONE-predicate: <probe>' lines first."
        )
    brief_abs = _absolute_path(Path(path))
    cmd = _verify_command(brief_abs)
    cond = (
        f"Goal met only when `{cmd}` has "
        f"been run this session and exited 0 -- all {total} '## Deliverables' "
        f"DONE-predicates pass as live probes (exogenous; ticked boxes alone "
        f"do not count)."
    )
    if len(cond) > CONDITION_BUDGET:
        raise ValueError(
            f"Derived condition is {len(cond)} chars (> {CONDITION_BUDGET}). "
            f"The paths are too long -- shorten the slug, the brief's folder "
            f"(--root) or the install path."
        )
    return cond


# ---------------------------------------------------------------------------
# delight — deterministic ARMED card
# ---------------------------------------------------------------------------

def _bar(done: int, total: int, width: int = 12) -> str:
    filled = 0 if total == 0 else round(width * done / total)
    return "■" * filled + "□" * (width - filled)


def render_armed_card(path: Path, condition: str, done: int, total: int) -> str:
    """Deterministic ASCII card shown when a goal is armed (no LLM).

    ``goal`` is the PROGRESS-INVARIANT identity hash — ticking boxes never
    changes it, editing the contract (labels/probes) does. The escape line
    keeps the native disarm visible (gate-resolution-escape-hatch)."""
    p = Path(path)
    h = goal_hash(p.read_text(encoding="utf-8")) if p.exists() else "--------"
    lines = [
        "┌─ ULTRAGOAL ARMED ─────────────────────────────────────",
        f"│ brief : {_absolute_path(p)}",
        f"│ goal  : {h}   deliverables: {done}/{total}  [{_bar(done, total)}]",
        f"│ paste : /goal {condition}",
        "│ escape: /goal clear (native disarm) · ultragoal.py status",
        "└───────────────────────────────────────────────────────",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI — dispatch table, one handler per verb (Rule 18: small functions)
# ---------------------------------------------------------------------------

def _parse_deliverable(raw: str):
    if "::" in raw:
        item, probe = raw.split("::", 1)
        return item.strip(), probe.strip()
    return raw.strip()


def _resolve(target: str, root: Optional[Path]) -> Path:
    """Resolve a brief argument from ANY cwd: literal path, then the same
    path under the install dir, then the slug.

    `derive` now prints the brief as an absolute path (see `_absolute_path`),
    so a freshly pasted condition always resolves from any cwd without this
    fallback. The install-relative fallback stays for backward
    compatibility: a pointer derived BEFORE this change --
    `plans/ULTRAGOAL-<slug>-<date>.md` -- may already be pasted into an
    armed /goal, and that string must keep resolving from a worktree whose
    cwd is not the install dir. Measured 2026-10-05: `verify
    plans/ULTRAGOAL-x.md` from a worktree raised FileNotFoundError while
    `status` found the same file.
    """
    p = Path(target)
    if p.exists():
        return p
    under_install = Path(get_claude_dir()) / target
    if under_install.exists():
        return under_install
    found = find_brief(slugify(target), root)
    if found is None:
        raise SystemExit(
            f"No brief found for '{target}' (looked for the path, the path under "
            f"the install dir, and the slug)."
        )
    return found


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ultragoal", description=__doc__)
    ap.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compose", help="write the brief file (Banach no-op if it exists)")
    c.add_argument("--goal", required=True)
    c.add_argument("--title", default=None)
    c.add_argument("--why", default="")
    c.add_argument("--context", action="append", default=[])
    c.add_argument("--deliverable", action="append", default=[],
                   help="'item :: probe' (probe = DONE-predicate)")
    c.add_argument("--held", action="append", default=[])
    c.add_argument("--guardrail", action="append", default=[])
    c.add_argument("--date", default=None)
    d = sub.add_parser("derive", help="print the short /goal condition for a brief")
    d.add_argument("path")
    d.add_argument("--oneliner", action="store_true",
                   help="print the ready-to-paste '/goal <condition>' line")
    s = sub.add_parser("status", help="progress + ARMED card for a brief")
    s.add_argument("target", help="brief path or goal slug")
    f = sub.add_parser("fold", help="rewrite a brief in canonical folded form")
    f.add_argument("path")
    v = sub.add_parser("verify", help="re-run every DONE-predicate live; exit 0 iff all pass")
    v.add_argument("path")
    v.add_argument("--tick", action="store_true",
                   help="write newly-passing items back as [x] (ONLY verify may tick)")
    v.add_argument("--timeout", type=float, default=60.0)
    n = sub.add_parser("next", help="print the first undone deliverable (the improve target)")
    n.add_argument("path")
    sub.add_parser("selftest", help="prove fold idempotence (f∘f == f)")
    return ap


def _cmd_compose(args: argparse.Namespace) -> int:
    slug = slugify(args.title or args.goal)
    existing = find_brief(slug, args.root)  # cross-DATE: identity is the
    if existing is not None:                # slug; the date is metadata
        print(f"exists (Banach no-op): {existing}")
        return 0
    path = brief_path(slug, args.date, args.root)
    body = compose_brief(
        args.goal, title=args.title, why=args.why, context=args.context,
        deliverables=[_parse_deliverable(x) for x in args.deliverable],
        held=args.held, guardrails=args.guardrail, date=args.date,
        path_hint=path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    print(path)
    return 0


def _cmd_derive(args: argparse.Namespace) -> int:
    cond = derive_condition(_resolve(args.path, args.root))
    print(f"/goal {cond}" if args.oneliner else cond)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    path = _resolve(args.target, args.root)
    body = path.read_text(encoding="utf-8")
    done, total = progress(body)
    try:
        cond = derive_condition(path, body)
    except ValueError as exc:  # degrade, never crash; name the real reason
        cond = ("(underivable — add '- [ ] item — DONE-predicate: probe' lines)"
                if total == 0 else f"(underivable -- {exc})")
    print(render_armed_card(path, cond, done, total))
    return 0


def _cmd_fold(args: argparse.Namespace) -> int:
    p = _resolve(args.path, args.root)
    folded = fold_brief(p.read_text(encoding="utf-8"))
    p.write_text(folded, encoding="utf-8")
    print(f"{fold_hash(folded)}  {p}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    rep = verify(_resolve(args.path, args.root), tick=args.tick, timeout=args.timeout)
    for item, reason in rep.failures:
        print(f"✗ {item} — {reason}")
    for full in rep.newly_done:
        print(f"✓ NEW {_PREDICATE_SPLIT.split(full, maxsplit=1)[0]}")
    verdict = " — ALL DONE (goal condition satisfied)" if rep.all_done else ""
    print(f"{rep.passed}/{rep.total} probes pass{verdict}")
    return 0 if rep.all_done else 1


def _cmd_next(args: argparse.Namespace) -> int:
    path = _resolve(args.path, args.root)
    body = path.read_text(encoding="utf-8")
    if not parse_probes(body):
        raise SystemExit(f"No '## Deliverables' checklist in {args.path}")
    nxt = next_item(path)
    print(f"{nxt.item} — DONE-predicate: {nxt.cmd}" if nxt
          else "all deliverables done")
    return 0


def _cmd_selftest(_args: argparse.Namespace) -> int:
    selftest_idempotent()
    print("selftest: fold is idempotent (f∘f == f); compose emits fixed points")
    return 0


_DISPATCH = {
    "compose": _cmd_compose,
    "derive": _cmd_derive,
    "status": _cmd_status,
    "fold": _cmd_fold,
    "verify": _cmd_verify,
    "next": _cmd_next,
    "selftest": _cmd_selftest,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    return _DISPATCH[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
