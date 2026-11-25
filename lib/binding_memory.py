"""Binding memory records — the pinned tier of the harness memory index.

A dispatched subagent receives MEMORY.md and the safety-core rules, and
nothing else. For that actor MEMORY.md is not an index of memory, it *is*
memory. Whatever is not on it does not exist.

Eviction from that index has always been positional: ``lines[:195]``, a
tail-drop that makes AGE the predictor of whether a record reaches a
subagent. Measured on the live corpus, 10 of the 10 newest memory files had
a live pointer and 0 of the 100 oldest did — a cliff, not a gradient. So the
records that most need to travel (the ones written after an irreversible
harm, which are by definition old) are exactly the ones the policy drops.

This module adds the missing axis. A record may declare itself BINDING, and
a binding record is partitioned out of the evictable tail rather than
competing with the newest arrival for position.

The field is deliberately hard to reach for:

* One accepted value, ``irreversible``. There is no middle rung, because a
  middle rung is what an author reaches for when they cannot honestly claim
  the top one. The question a marker must answer is not "is this
  important?" — everything in the corpus is important — but "can I name the
  incident where a subagent lacking this line caused harm that could not be
  undone?".
* That name is required (``binding_incident``) and must RESOLVE, against
  the same corpus resolver the memory lint uses. An incident nobody can
  open is a claim, not evidence.
* The block is capped. The cap is the anti-Goodhart defence: it makes the
  set rival. Past the cap, marking one more record does not add protection,
  it forces an explicit trade against a named incumbent — see
  ``check_pinned_budget``.
* An auto-captured record can never pin, whatever its frontmatter says.

Sits beside ``rules/binding-memory-records.md`` (the doctrine) and
``lib/unified_memory_brief.py`` (the writer that honours the partition).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------

BINDING_FIELD = "binding"
BINDING_INCIDENT_FIELD = "binding_incident"

#: The ONLY value that pins. Absent means not pinned. There is deliberately
#: no third state: see the module docstring on why a middle rung is worse
#: than no rung.
PINNED_VALUE = "irreversible"

#: Fixed markers delimiting the pinned block inside MEMORY.md.
PINNED_START = "<!-- GRIP-PINNED-START -->"
PINNED_END = "<!-- GRIP-PINNED-END -->"

#: Cap on the pinned ENTRY lines, in characters. The two marker lines are
#: fixed overhead outside the cap so that editing a marker cannot change how
#: much an author is allowed to pin.
#:
#: 1,200 chars is roughly 12-14 one-line imperatives. The number is not a
#: measurement of what fits — the harness load budget is twenty times
#: larger — it is a deliberate scarcity. An unbounded pinned tier decays
#: into a second copy of the index, at which point pinning protects nothing
#: because everything is pinned.
PINNED_BLOCK_CHAR_CAP = 1_200

#: Written into every file the auto-capture pipeline produces.
AUTOCAPTURE_MARKER = "auto-captured from ★ Insight block"

_TOP_LEVEL_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
_INDENTED_KEY = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


class BindingError(ValueError):
    """A record declares a binding the reader refuses to honour."""


class PinnedBudgetExceeded(Exception):
    """The pinned set does not fit, and a named incumbent must give way."""


@dataclass(frozen=True)
class PinnedEntry:
    """One line of the pinned block, and the record it addresses."""

    record: str
    line: str

    def cost(self) -> int:
        """Characters this entry consumes, including its newline."""
        return len(self.line) + 1


# --------------------------------------------------------------------------
# Frontmatter
# --------------------------------------------------------------------------


def parse_top_level_frontmatter(text: str) -> dict[str, str]:
    """Return the column-0 scalar keys of the leading ``---`` block.

    Only top-level keys are returned. The corpus nests most of its metadata
    under a ``metadata:`` mapping, and a parser that flattened nesting would
    let a nested key masquerade as a top-level declaration.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for raw in lines[1:]:
        if raw.strip() == "---":
            break
        match = _TOP_LEVEL_KEY.match(raw)
        if match:
            out[match.group(1)] = match.group(2).strip().strip("\"'")
    return out


def _nested_keys(text: str) -> set[str]:
    """Indented keys inside the leading frontmatter block."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return set()
    out: set[str] = set()
    for raw in lines[1:]:
        if raw.strip() == "---":
            break
        match = _INDENTED_KEY.match(raw)
        if match:
            out.add(match.group(1))
    return out


# --------------------------------------------------------------------------
# Incident resolution — reuses the memory lint's corpus resolver
# --------------------------------------------------------------------------


_LINT_CACHE: dict[str, object] = {}


def _lint_module(root: Path):
    """Import ``bin/grip-memory-lint`` as a module.

    The resolver ladder (exact, hyphen/underscore-folded, tier-prefix
    insensitive, rules, skills) already exists there and is calibrated
    against the live corpus. Duplicating it would give the two readers
    independent opinions about whether a reference resolves, which is how a
    checked fact becomes two unchecked ones.
    """
    script = root / "bin" / "grip-memory-lint"
    if not script.is_file():
        raise BindingError(
            f"cannot resolve binding_incident: {script} is missing, so no "
            "incident can be checked and no record may pin"
        )
    key = str(script.resolve())
    cached = _LINT_CACHE.get(key)
    if cached is not None:
        return cached
    loader = importlib.machinery.SourceFileLoader("grip_memory_lint", key)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:  # pragma: no cover - importlib contract
        raise BindingError(f"cannot load {script} as a module")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: @dataclass resolves its annotations through
    # sys.modules[cls.__module__], which is None for an unregistered module.
    sys.modules[loader.name] = module
    loader.exec_module(module)
    _LINT_CACHE[key] = module
    return module


def build_corpus(root: Path, memory_dir: Path):
    """The lint's corpus for ``root``, built once per caller.

    ``build_corpus`` walks rules/, skills/ and the memory dir, so building it
    per record would rescan the tree once for every file in it. The ledger
    path is stored and never read by the resolver, so a missing hypothesis
    ledger is irrelevant here.
    """
    lint = _lint_module(root)
    return lint.build_corpus(root, memory_dir, root / "state" / "hypotheses.json")


def resolve_incident(
    incident: str, root: Path, memory_dir: Path, *, corpus=None
) -> str | None:
    """Return where ``incident`` lands, or None if it resolves nowhere."""
    lint = _lint_module(root)
    if corpus is None:
        corpus = build_corpus(root, memory_dir)
    return lint.resolve_reference(incident, corpus).target


# --------------------------------------------------------------------------
# The binding declaration
# --------------------------------------------------------------------------


def binding_of(text: str, *, name: str = "<memory>") -> str | None:
    """Return the declared binding, or None.

    Raises ``BindingError`` rather than returning None whenever a record
    *tried* to declare a binding and got it wrong. A malformed declaration
    that silently read as "not pinned" would be the failure this whole
    mechanism exists to remove: a record believing it travels when it does
    not.
    """
    front = parse_top_level_frontmatter(text)
    declared = front.get(BINDING_FIELD)

    if declared is None:
        if BINDING_FIELD in _nested_keys(text):
            raise BindingError(
                f"{name}: '{BINDING_FIELD}' is nested inside another mapping. "
                "It must be a top-level frontmatter key, or it pins nothing."
            )
        return None

    if declared != PINNED_VALUE:
        raise BindingError(
            f"{name}: {BINDING_FIELD}='{declared}' is not a value. The only "
            f"value is '{PINNED_VALUE}'. There is no middle rung on purpose: "
            "if you cannot name an incident that could not be undone, the "
            "record is not binding."
        )

    if AUTOCAPTURE_MARKER in front.get("source", ""):
        raise BindingError(
            f"{name}: an auto-captured record can never pin. Binding is an "
            "operator judgement about irreversible harm; no automatic "
            "pipeline is entitled to make it."
        )

    if not front.get(BINDING_INCIDENT_FIELD):
        raise BindingError(
            f"{name}: {BINDING_FIELD}='{PINNED_VALUE}' requires "
            f"'{BINDING_INCIDENT_FIELD}' naming the incident where a "
            "subagent lacking this line caused harm that could not be undone."
        )
    return declared


def _same_record(incident: str, stem: str) -> bool:
    """True when an incident names the very record declaring it.

    Compared with the hyphen/underscore fold and tier prefixes the corpus uses
    interchangeably, so `feedback_x`, `feedback-x` and `x` all count as the
    same self-reference rather than only the literal spelling.
    """
    def fold(value: str) -> str:
        key = value.strip().removesuffix(".md").replace("-", "_").lower()
        for prefix in ("feedback_", "reference_", "project_", "user_"):
            if key.startswith(prefix):
                return key[len(prefix):]
        return key

    return fold(incident) == fold(stem)


def is_pinned(path: Path, *, root: Path | None = None, corpus=None) -> bool:
    """True when ``path`` declares a valid, resolvable binding."""
    text = path.read_text(encoding="utf-8")
    if binding_of(text, name=path.name) is None:
        return False
    incident = parse_top_level_frontmatter(text)[BINDING_INCIDENT_FIELD]
    if _same_record(incident, path.stem):
        raise BindingError(
            f"{path.name}: {BINDING_INCIDENT_FIELD} points at the record "
            "itself. Self-reference always resolves, so it would make the "
            "requirement vacuous — name the SECOND artefact: the sibling "
            "record, rule, or path where the harm is accounted for."
        )
    base = root or get_claude_dir()
    if resolve_incident(incident, base, path.parent, corpus=corpus) is None:
        raise BindingError(
            f"{path.name}: {BINDING_INCIDENT_FIELD} '{incident}' resolves "
            "nowhere. An incident nobody can open is a claim, not evidence."
        )
    return True


def _log_skip(path: Path, exc: Exception) -> None:
    """Leave a trace for a record ``pinned_records`` excludes.

    Names the file and the exception's CLASS only — never ``str(exc)``,
    which can echo back a malformed frontmatter value or a slice of body
    text. A skip that leaves no trace is indistinguishable from a record
    that never declared at all, which is the gap this closes.
    """
    logger.warning("pinned_records: skipping %s (%s)", path.name, type(exc).__name__)


def pinned_records(memory_dir: Path, *, root: Path | None = None) -> list[str]:
    """Stems of every record in ``memory_dir`` that validly declares a binding.

    This scans the WHOLE corpus, so one record's malformed or unresolvable
    declaration — or one record that cannot even be READ, such as an
    invalid-encoding or permission-denied file — must not crash the audit of
    every other record in it. ``binding_of`` and ``is_pinned`` still raise
    for a caller validating a SINGLE record — there, a malformed declaration
    must not read as quiet success. Here, a record whose declaration raises
    is simply excluded, the same as a record that never declared at all; if
    it is also rendered in the pinned block, ``audit_block`` surfaces that as
    "unbacked", which is the drift this whole mechanism exists to catch.

    The same three exceptions are tolerated at BOTH read sites — the first
    pass over every file in the directory, and the second pass that re-reads
    each declared record through ``is_pinned`` to resolve its incident — so a
    file that turns unreadable in the window between the two passes is
    excluded rather than aborting the whole scan. Every exclusion is logged
    (``_log_skip``): the file name and the exception's class, never its
    content, so a skip leaves a trace instead of vanishing silently.
    """
    base = root or get_claude_dir()
    paths = sorted(memory_dir.glob("*.md"))
    declared = []
    for p in paths:
        try:
            if binding_of(p.read_text(encoding="utf-8"), name=p.name):
                declared.append(p)
        except (BindingError, OSError, UnicodeDecodeError) as exc:
            _log_skip(p, exc)
            continue
    if not declared:
        return []
    corpus = build_corpus(base, memory_dir)
    pinned = []
    for p in declared:
        try:
            if is_pinned(p, root=base, corpus=corpus):
                pinned.append(p.stem)
        except (BindingError, OSError, UnicodeDecodeError) as exc:
            _log_skip(p, exc)
            continue
    return pinned


# --------------------------------------------------------------------------
# The cap
# --------------------------------------------------------------------------


def check_pinned_budget(
    entries: Sequence[PinnedEntry],
    *,
    cap: int = PINNED_BLOCK_CHAR_CAP,
    newcomer: str | None = None,
) -> int:
    """Return the pinned block's cost, or raise naming who must give way.

    The error names ONE incumbent rather than reporting a number, because a
    number invites the author to widen the budget and a name invites them to
    make the trade the cap exists to force. The incumbent chosen is the
    smallest one whose removal actually makes room — the least you can give
    up — falling back to the largest when no single record suffices.
    """
    total = sum(entry.cost() for entry in entries)
    if total <= cap:
        return total

    overflow = total - cap
    candidates = sorted(
        (e for e in entries if e.record != newcomer), key=PinnedEntry.cost
    )
    if not candidates:
        raise PinnedBudgetExceeded(
            f"the pinned block is {total} chars, over the {cap}-char cap, and "
            "there is no incumbent to trade against."
        )
    sufficient = [e for e in candidates if e.cost() >= overflow]
    victim = sufficient[0] if sufficient else candidates[-1]
    subject = newcomer or "this record"
    raise PinnedBudgetExceeded(
        f"the pinned block would be {total} chars, {overflow} over the "
        f"{cap}-char cap. To pin {subject} you must first unpin "
        f"{victim.record} (frees {victim.cost()} chars). The cap is not a "
        "budget to widen: past it, pinning one more record protects nothing, "
        "it only dilutes what pinning means."
    )


# --------------------------------------------------------------------------
# The block, inside MEMORY.md
# --------------------------------------------------------------------------


def _block_bounds(lines: Sequence[str]) -> tuple[int, int] | None:
    """Inclusive (start, end) indices of the pinned block, or None."""
    try:
        start = lines.index(PINNED_START)
        end = lines.index(PINNED_END, start + 1)
    except ValueError:
        return None
    return start, end


def partition_pinned(lines: Sequence[str]) -> tuple[list[str], list[str], int]:
    """Split ``lines`` into (pinned, evictable, anchor).

    ``anchor`` is where the pinned block sat within the evictable remainder,
    so a caller that trims the remainder can put the block back where the
    author left it rather than relocating it.

    With no block present this returns ``([], list(lines), len(lines))`` and
    a caller reassembling from it produces the input unchanged — so the
    no-pin path is byte-identical to the behaviour that predates this
    module, by construction rather than by care.
    """
    bounds = _block_bounds(lines)
    if bounds is None:
        return [], list(lines), len(lines)
    start, end = bounds
    pinned = list(lines[start : end + 1])
    rest = list(lines[:start]) + list(lines[end + 1 :])
    return pinned, rest, start


def block_entries(lines: Sequence[str]) -> list[PinnedEntry]:
    """Parse the pinned block's entry lines into ``PinnedEntry`` records.

    An entry reads ``- <imperative> :: <record>``. The record name is the
    address a reader follows for the full account; the imperative is what a
    subagent acts on when it follows nothing, which is the normal case.
    """
    bounds = _block_bounds(lines)
    if bounds is None:
        return []
    start, end = bounds
    out: list[PinnedEntry] = []
    for line in lines[start + 1 : end]:
        if not line.startswith("- ") or " :: " not in line:
            continue
        record = line.rsplit(" :: ", 1)[1].strip()
        out.append(PinnedEntry(record=record.removesuffix(".md"), line=line))
    return out


def audit_block(lines: Sequence[str], declared: Iterable[str]) -> dict[str, list[str]]:
    """Compare the rendered block against the records that declare a binding.

    Returns ``{"unlisted": [...], "unbacked": [...]}``.

    Without this the frontmatter field would be decorative — a declared
    input that reaches no actor, which
    ``rules/declared-input-must-reach-the-actor.md`` names as forbidden
    precisely because it looks like it works.
    """
    listed = {entry.record for entry in block_entries(lines)}
    declared_set = set(declared)
    return {
        "unlisted": sorted(declared_set - listed),
        "unbacked": sorted(listed - declared_set),
    }
