"""One condensed block of GRIP's Canonical Output Style, written into every harness.

The authority is rules/canonical-output-style.md. Claude Code receives it through
output-styles/grip-canonical.md and the SessionStart directive. Every other harness
GRIP launches (Codex, OpenCode, Hermes, agy) received, until 2026-10-18, a pointer
paragraph in the repository's AGENTS.md and nothing in its own home. This module
closes that gap with ONE generated block and FIVE native deliveries:

  repository AGENTS.md          the block sits under "## Canonical Output Style
                                (PARAMOUNT)", so any harness run inside the tree
                                reads it; a test keeps it equal to condensed_block()
  $CODEX_HOME/AGENTS.md         Codex reads the global AGENTS.md at session start
                                (~/.codex, ~/.codex-hal, every home in
                                config/codex-accounts.json when present)
  ~/.config/opencode/AGENTS.md  OpenCode's global rules file; integrations/opencode/
                                grip-style.js also appends the block to the system
                                prompt on every request, so it survives compaction
  ~/.hermes/SOUL.md             the persona file Hermes loads every message
  ~/.gemini/AGENTS.md           the context file agy's settings name (context.fileName)

The block is CONTENT, generated once from a structured core where every clause
names the rule section it summarises (CLAUSES). Delivery is per harness: the block
is written inside a Markdown GRIP-MANAGED fence with its own marker,
"canonical-style", so a re-run rewrites only that span, a user's own text around
it survives, and a Kiro steering file's fence is never mistaken for it.

The Roundtable of 2026-10-18 (seal f2f95ad543163b2f) refuted one AGENTS.md block as
the delivery for every harness: content generation is one source, delivery is per
harness, and OpenCode needs the per-turn transform. That is the shape here.

Pure functions plus two writers. bin/grip-harness calls project_for() after it
wires a harness, so every `gg++ <name>` launch projects; `--fix` regenerates the
repository block; `--check` reports drift. Nothing here opens a connection.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from . import grip_harness  # type: ignore[attr-defined]  # noqa: PLC0415
    from .full_prose_marker import ESCAPE_PHRASES  # noqa: PLC0415
except ImportError:
    import grip_harness  # type: ignore[no-redef]  # noqa: PLC0415
    from full_prose_marker import ESCAPE_PHRASES  # type: ignore[no-redef]  # noqa: PLC0415

PROJECTION_VERSION = "1"
RULE_RELPATH = "rules/canonical-output-style.md"
AGENTS_RELPATH = "AGENTS.md"
AGENTS_HEADING = "## Canonical Output Style (PARAMOUNT)"
MARKER = "canonical-style"
BEGIN = f"<!-- >>> GRIP-MANAGED ({MARKER}) — do not edit inside this block -->"
END = f"<!-- <<< GRIP-MANAGED ({MARKER}) -->"
#: The sentence every projected block carries: the i-have-adhd override O6, which
#: the Roundtable asked to be stated where the harness reads it.
HOST_OUTRANKS = "The host's system prompt outranks this block. The shape stays."
#: (section of rules/canonical-output-style.md, clause). Each clause summarises the
#: named section; tests/test_canonical_style_projection.py checks every section
#: exists in the rule and every clause lints to zero STE errors.
CLAUSES = (
    ("The canonical style",
     "Write plain, fluent English, as a sharp colleague explains the work at a whiteboard. "
     "Use ordinary words, full sentences and one idea at a time."),
    ("The canonical style",
     "Keep the articles. A descriptive sentence has at most 25 words and an instruction at most 20, "
     "with one instruction a sentence and no semicolon in prose."),
    ("The canonical style",
     "Trim only after the meaning is clear: cut the preamble, empty hedging, pleasantries and "
     "repetition, and never buy brevity with the reader's effort. Start with the answer. The "
     "openers \"Great question\", \"Sure!\", \"Let me\", \"I'll\", \"Looking at your\" and \"To answer "
     "your question\" are forbidden. Before sending, delete an announcing first sentence, a "
     "recapping last sentence, any sidebar, any empty hedge and any idiom."),
    ("The canonical style",
     "Keep every technical token verbatim: paths, commands, identifiers, numbers, SHAs and error "
     "strings. Concision removes words, never facts. Write each reference as a link whose words "
     "say what it is, with the id inside. An issue or a pull request links to GitHub, and a "
     "hypothesis links to its card on the Reference Desk. Build the links with `bin/grip-ref links`."),
    ("The canonical style",
     "Number a multi-step instruction, with one bounded action to a step and the fewest steps that "
     "work. Keep one position line in multi-step work. Its form is `Step N of M done: X`, or "
     "`Step N done: X` when no total is known. Give an estimate only with its basis, for a step "
     "the reader waits on."),
    ("The canonical style",
     "Report an error by location and symptom. Then give the cause with its evidence, a safe "
     "remedy with the cause marked unknown, or the next measurement. After three failed fixes, "
     "name the assumption that may be wrong. Ask one diagnostic question."),
    ("Session report contract",
     "A substantive reply opens with one `/tldr` line: the action, the win or the fact. A recap of "
     "at most six sentences of prose follows. Above the closer, a block headed **Where this "
     "stands** and the UTC time ends the reply, for a reader who opens the session cold. Its three "
     "plain lines are Happened, Happening now (or \"Nothing is running\") and Who acts next, which "
     "names the actor and why. For a step of mine, it also says what happens if this session ends "
     "first."),
    ("Session report contract",
     "A close-out may add a manifest of at most six bullets, one complete fact each, with every "
     "link, path and seal inline. The cap shapes presentation only."),
    ("The Task Card for substantive reports of shipped work",
     "A report of shipped work uses the Task Card: TASK, REASON, EFFECT, PROCEDURE, NOTES, "
     "INSPECTION and NEXT TASK, with no empty section."),
    ("The recommended next step (the block's last line)",
     "Every substantive reply ends with one line, `RECOMMENDED NEXT STEP (mine|yours|<party>)`, "
     "a statement and never a question. A command only the operator can run sits alone on the "
     "line below it. Apart from that line, nothing follows the answer. The closers \"Hope this "
     "helps\", \"Let me know if you need anything else\", \"Happy to clarify\" and \"Feel free to "
     "ask\" are forbidden."),
    ("The recommended next step (the block's last line)",
     "The step is (yours) only for an irreversible act, a fork no rule settles, a fact only the "
     "operator holds, or a commercial term. Anything the agent can do is (mine), and the agent "
     "does it in the same turn. Name an outside party, such as (GitHub), only when the agent "
     "cannot move it. A GRIP tool is never that party: a `bin/` executable, a `com.grip.*` job, "
     "the merge sweep or AGORA."),
    ("Non-negotiable rails",
     "Plain language wins on substance. Channel relays and client or legal deliverables keep "
     "full plain English, and a security warning or an irreversible action gets full prose."),
    ("Copy-paste-ready output (PARAMOUNT)",
     "Anything the operator will copy to another surface goes in a fenced code block, never a "
     "blockquote."),
    ("Non-negotiable rails",
     f"{HOST_OUTRANKS} `{ESCAPE_PHRASES[0]}`, `{ESCAPE_PHRASES[1]}` or `{ESCAPE_PHRASES[2]}` restores "
     "full prose for the session."),
)

INTRO = (
    "**GRIP Canonical Output Style.** One voice for every harness. The authority is "
    f"`{RULE_RELPATH}` in the GRIP checkout. `lib/canonical_style_projection.py` generates this "
    "block from it and rewrites it at every launch. Edit the rule, never this block."
)


def condensed_block() -> str:
    """The Markdown body: an intro line and one numbered clause per CLAUSES entry."""
    lines = [INTRO, ""]
    lines += [f"{number}. {clause}" for number, (_, clause) in enumerate(CLAUSES, 1)]
    return "\n".join(lines) + "\n"


def fenced_block() -> str:
    """The body inside its own GRIP-MANAGED fence, ready for a Markdown surface."""
    return f"{BEGIN}\n{condensed_block()}{END}\n"


def merge(existing: str) -> str:
    """``existing`` with this block inserted or replaced in place. A file with no
    fence gets the block appended; a truncated fence is replaced to the end of the
    file, which is why the writer keeps a backup."""
    return grip_harness.merge_markdown_managed_block(existing, fenced_block(), begin=BEGIN, end=END)


def extract(existing: str):
    """The body between the markers of ``existing``, or None when there is no fence."""
    begin = existing.find(BEGIN)
    if begin == -1:
        return None
    end = existing.find(END, begin)
    if end == -1:
        return None
    return existing[begin + len(BEGIN):end].lstrip("\n")


def grip_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ---- the repository AGENTS.md -------------------------------------------------------

def _section_span(text: str, heading: str) -> tuple:
    """(start, end) of the body under ``heading``: from the line after it to the next
    `## ` heading or the end of the text. (-1, -1) when the heading is absent."""
    lines = text.splitlines(keepends=True)
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    for index, line in enumerate(lines):
        if line.rstrip("\n") == heading:
            start = offsets[index] + len(line)
            end = next((offsets[j] for j in range(index + 1, len(lines)) if lines[j].startswith("## ")), len(text))
            return start, end
    return -1, -1


def merged_agents(existing: str) -> str:
    """The repository AGENTS.md with this block under its Canonical Output Style
    heading. A fence already present is replaced where it is; with no fence, the
    section body between the heading and the next `## ` heading becomes the block."""
    if BEGIN in existing:
        return merge(existing)
    start, end = _section_span(existing, AGENTS_HEADING)
    if start == -1:
        return merge(existing)
    return existing[:start] + "\n" + fenced_block() + "\n" + existing[end:]


def repository_block(root: Path | None = None):
    """The body of the fence in the repository AGENTS.md, or None."""
    path = (root or grip_root()) / AGENTS_RELPATH
    return extract(path.read_text(encoding="utf-8")) if path.exists() else None


def check_repository(root: Path | None = None) -> bool:
    """True when the repository block is exactly condensed_block()."""
    return repository_block(root) == condensed_block()


def fix_repository(root: Path | None = None) -> bool:
    """Rewrite the repository block; True when the file changed."""
    path = (root or grip_root()) / AGENTS_RELPATH
    existing = path.read_text(encoding="utf-8")
    merged = merged_agents(existing)
    if merged == existing:
        return False
    grip_harness.atomic_write_text(path, merged)
    return True


# ---- the per-harness surfaces --------------------------------------------------------

@dataclass(frozen=True)
class Projection:
    harness: str
    path: Path
    changed: bool
    repaired: bool


def _strings_under(value, keys: tuple) -> list:
    """Every string value stored under one of ``keys``, at any depth of a JSON value."""
    found = []
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in keys and isinstance(inner, str):
                found.append(inner)
            found += _strings_under(inner, keys)
    elif isinstance(value, list):
        for inner in value:
            found += _strings_under(inner, keys)
    return found


def codex_homes(home: Path, root: Path | None = None) -> list:
    """Every Codex home on this machine, in order and without repeats: ~/.codex,
    ~/.codex-hal, then each home config/codex-accounts.json names when that file
    exists. Only homes that exist are returned."""
    candidates = [home / ".codex", home / ".codex-hal"]
    accounts = (root or grip_root()) / "config" / "codex-accounts.json"
    if accounts.exists():
        try:
            data = json.loads(accounts.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        for raw in _strings_under(data, ("home", "codex_home", "CODEX_HOME", "path")):
            candidates.append(Path(os.path.expanduser(raw)))
    seen, out = set(), []
    for candidate in candidates:
        resolved = candidate.expanduser()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def targets(home: Path | None = None, env: dict | None = None, root: Path | None = None) -> list:
    """(harness, surface path) for every surface of the delivery matrix whose
    harness home exists on this machine. The surface file itself may not exist yet."""
    home = Path(home) if home is not None else Path.home()
    env = os.environ if env is None else env
    out = [("codex", codex_home / "AGENTS.md") for codex_home in codex_homes(home, root)]
    xdg = Path(env.get("XDG_CONFIG_HOME") or (home / ".config"))
    if (xdg / "opencode").is_dir():
        out.append(("opencode", xdg / "opencode" / "AGENTS.md"))
    if (home / ".hermes").is_dir():
        out.append(("hermes", home / ".hermes" / "SOUL.md"))
    if (home / ".gemini").is_dir():
        out.append(("agy", home / ".gemini" / "AGENTS.md"))
    return out


def project(path: Path, harness: str = "") -> Projection:
    """Write the block into ``path`` inside its fence. Idempotent: a second call
    changes nothing. A truncated fence is repaired, and because the end of such a
    fence cannot be known, grip_harness.write_config_text keeps the original beside
    the file as <name>.grip-backup before the first rewrite."""
    path = Path(path)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    repaired = grip_harness.has_truncated_markdown_fence(existing, begin=BEGIN, end=END)
    merged = merge(existing)
    if merged == existing:
        return Projection(harness, path, changed=False, repaired=False)
    grip_harness.write_config_text(path, merged)
    return Projection(harness, path, changed=True, repaired=repaired)


def project_for(harness: str, home: Path | None = None, env: dict | None = None,
                root: Path | None = None) -> list:
    """Project into every surface of one harness; the list is empty when the harness
    has no home on this machine."""
    return [project(path, name) for name, path in targets(home, env, root) if name == harness]


def project_all(home: Path | None = None, env: dict | None = None, root: Path | None = None) -> list:
    return [project(path, name) for name, path in targets(home, env, root)]


# ---- command line ----------------------------------------------------------------------

def _parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project GRIP's canonical style into every harness.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--print", action="store_true", help="print the fenced block")
    group.add_argument("--check", action="store_true", help="exit 1 when the repository AGENTS.md block drifted")
    group.add_argument("--fix", action="store_true", help="regenerate the repository AGENTS.md block")
    group.add_argument("--targets", action="store_true", help="list the surfaces on this machine")
    group.add_argument("--project", nargs="*", metavar="HARNESS",
                       help="write the block into the surfaces of these harnesses (all, with no name)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse(argv)
    if args.check:
        ok = check_repository()
        print("repository AGENTS.md block: " + ("level with condensed_block()" if ok else "DRIFTED; run --fix"))
        return 0 if ok else 1
    if args.fix:
        print("repository AGENTS.md: " + ("rewritten" if fix_repository() else "unchanged"))
        return 0
    if args.targets:
        for name, path in targets():
            print(f"{name:9} {path}")
        return 0
    if args.project is not None:
        wanted = set(args.project)
        results = [r for r in project_all() if not wanted or r.harness in wanted]
        for result in results:
            state = "repaired" if result.repaired else ("rewrote" if result.changed else "unchanged")
            print(f"{result.harness:9} {result.path}  {state}")
        return 0
    sys.stdout.write(fenced_block())
    return 0


if __name__ == "__main__":
    sys.exit(main())
