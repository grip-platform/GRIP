#!/usr/bin/env python3
"""/idea -- one operator idea as the root of a signed, replayable causal chain.

One idea is one tree in its own ledger, ``state/precog/ideas.jsonl`` (resolved
at call time). The tree's root hangs off the exogenous anchor
``human:idea-<id>``, because the idea is the operator's own words. There are
five node kinds, and each node is an ordinary signed ``PrecogIDR`` from
``lib/precog/idr.py``:

    idea-root     the operator's words, verbatim, and their sha256
    idea-meaning  every reading, the one recommended and the one chosen; with
                  two or more readings, also the question asked and the
                  operator's verbatim answer
    idea-claim    a consequence and its falsifier, recorded BEFORE it is
                  tested; it hangs from the meaning or from another claim
    idea-verdict  CONGRUENT, INCONGRUENT or UNVERIFIABLE for one claim, with
                  the evidence, the verifier and an optional checked quote
    idea-outcome  what came of the idea: a merged change, an open PR, an
                  issue, a hypothesis, or nothing

The tool refuses rather than advises (``IdeaRefused``):

* Rule A, one idea: ``open`` refuses a text with two or more IDEA headings.
* Rule B, settled meaning: no claim or outcome before the meaning node, and a
  meaning with two or more readings needs the question asked and the
  operator's verbatim answer. Two plausible readings mean confidence is below
  the bar, so the gate encodes the threshold.
* A verdict settles one existing claim, once, and a quote must be found in its
  source by ``lib.prove_it.verify_quote``.

Deterministic and idempotent: a node's fingerprint is sha256 over the RFC 8785
form of ``{idea, kind, parent, body}`` (the root's is ``compute_fingerprint`` of
the text). A step repeated with the same arguments finds its fingerprint and
writes nothing, and the idea digest (sha256 over the sorted fingerprints) is
the same for any two recordings of the same steps. A verdict's quote binds its
source's path and content. A source inside this checkout is stored relative to
it, so the same verdict has the same fingerprint on every machine; a source
outside it keeps the path as given. A repeat after the source changed or moved
is refused, not a no-op. The Merkle root commits to ONE signed
recording, because it covers each record's random id.

An explicit ledger path means ``append_idr`` never moves the main decision
chain's HEAD (``lib.precog.idr._advance_head``), so an idea draws no false
causal edge and nothing here reaches the public chain.

Honest limits: tamper-EVIDENT, not tamper-proof. A changed record fails its
signature, but a whole line can be deleted and the signing key is local. The
tool cannot prove that an answer came from the operator; the transcript holds
that exchange. Rule B counts the readings it is given, so it is a speed bump,
not a lock: an agent that lists one reading passes it, and ``list`` cannot see
that. A verdict is only as good as the evidence recorded with it.

CLI: ``bin/grip-py -m lib.idea_chain {open,meaning,claim,verdict,outcome,show,
verify,list} --help``. Exit 0 on success, 3 when a rule refuses a step, and 1
when a file cannot be read (printed as UNREADABLE). ``verify`` also exits 1
when anything is BROKEN or a rule is broken, and 2 when the worst it found is
DEGRADED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:  # run as a script, lib/ is sys.path[0] and the repo root is not
    sys.path.insert(0, str(_REPO))

from lib import jcs  # noqa: E402
from lib._compat import fcntl  # noqa: E402
from lib.audit_chain import Verdict  # noqa: E402
from lib.claude_dir import get_claude_dir  # noqa: E402
from lib.idr_forest import (  # noqa: E402
    IdrForestError,
    add_idr,
    empty_forest,
    forest_merkle_root,
    verify_chain_integrity,
)
from lib.precog.fingerprint import compute_fingerprint  # noqa: E402
from lib.precog.idr import (  # noqa: E402
    PrecogIDR,
    _canonical_json,
    append_idr,
    build_idr,
    read_idr_chain,
)
from lib.prove_it import STATUS_FUZZY, STATUS_NOT_FOUND, STATUS_VERIFIED, verify_quote  # noqa: E402

FORMAT = "idea/1"
ROOT = "idea-root"
MEANING = "idea-meaning"
CLAIM = "idea-claim"
VERDICT = "idea-verdict"
OUTCOME = "idea-outcome"
KINDS = (ROOT, MEANING, CLAIM, VERDICT, OUTCOME)
VERDICTS = ("CONGRUENT", "INCONGRUENT", "UNVERIFIABLE")
OUTCOME_STATUSES = ("merged", "pr-open", "issue", "hypothesis", "dropped")
ANCHOR_PREFIX = "human:idea-"
ID_LENGTH = 16
EXIT_REFUSED = 3

#: The kinds each kind may hang from. The meaning hangs from the root, a claim
#: from the meaning or from another claim (a branch), a verdict from its claim,
#: and an outcome from the meaning.
PARENT_KINDS = {
    MEANING: (ROOT,),
    CLAIM: (MEANING, CLAIM),
    VERDICT: (CLAIM,),
    OUTCOME: (MEANING,),
}

#: A heading line that names an idea: ``IDEA``, ``IDEA:``, ``# IDEA 2``,
#: ``**IDEA**``. Capitals only, so prose such as "the idea is" never counts.
_IDEA_HEADING = re.compile(r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?IDEA\b", re.MULTILINE)


class IdeaRefused(ValueError):
    """A step the /idea rules forbid. The message names the rule and the fix."""


class IdeaUnreadable(Exception):
    """A file a step needs cannot be read: missing, forbidden, or not UTF-8 text.

    Kept apart from ``OSError`` because a decode failure is not an OS failure.
    The command line prints it, like a ledger ``OSError``, as UNREADABLE with
    exit 1.
    """


# --- identity ---------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ledger_path(path: Path | str | None = None) -> Path:
    """The idea ledger: ``path`` when given, else ``$CLAUDE_DIR/state/precog/ideas.jsonl``.

    Resolved at call time, never at import, so a test or a worktree that sets
    CLAUDE_DIR later still writes where it means to (the trap that
    ``lib.precog.idr._default_idr_path`` documents).
    """
    if path:
        return Path(path)
    return get_claude_dir() / "state" / "precog" / "ideas.jsonl"


def root_fingerprint(text: str) -> str:
    """The idea's own fingerprint. Whitespace-only variants of one text share it."""
    return compute_fingerprint(text, FORMAT)


def idea_id_of(text: str) -> str:
    """The short id an idea is known by: the first 16 hex digits of its fingerprint."""
    return root_fingerprint(text)[:ID_LENGTH]


def anchor_of(idea_id: str) -> str:
    """The exogenous anchor an idea's root hangs from (``human:``, the operator's words)."""
    return ANCHOR_PREFIX + idea_id


def node_fingerprint(idea_id: str, kind: str, parent_fp: str, body: dict[str, Any]) -> str:
    """sha256 over the RFC 8785 form of ``{idea, kind, parent, body}``.

    No clock, random id or signature goes in, so a step given the same
    arguments has the same fingerprint in any recording. A verdict's source
    inside this checkout is stored relative to it (``_portable``), so that step
    is also the same on every machine.
    """
    payload = {"idea": idea_id, "kind": kind, "parent": parent_fp, "body": body}
    return hashlib.sha256(jcs.dumps(payload)).hexdigest()


def _safe_fingerprint(idea_id: str, node: PrecogIDR, parent_fp: str) -> str | None:
    try:
        return node_fingerprint(idea_id, node.kind, parent_fp, _body(node))
    except jcs.JcsError:  # a body RFC 8785 cannot encode was never written by this tool
        return None


# --- reading ----------------------------------------------------------------

def _body(node: PrecogIDR | None) -> dict[str, Any]:
    decision = getattr(node, "decision", None)
    return decision if isinstance(decision, dict) else {}


def _inputs(node: PrecogIDR) -> dict[str, Any]:
    return node.inputs if isinstance(node.inputs, dict) else {}


def _idea_of(node: PrecogIDR) -> str:
    return str(_inputs(node).get("idea", ""))


def _parent_fp(node: PrecogIDR) -> str | None:
    return _inputs(node).get("parent")


def _short(node: PrecogIDR) -> str:
    return f"{node.kind} {node.fingerprint[:12]}"


def read_ledger(path: Path | str | None = None) -> list[PrecogIDR]:
    """Every idea record in the ledger, in file order."""
    return [n for n in read_idr_chain(path=ledger_path(path)) if n.kind in KINDS]


def nodes_of(idea_id: str, path: Path | str | None = None) -> list[PrecogIDR]:
    """The records of one idea, in file order, so a parent always precedes its children."""
    return [n for n in read_ledger(path) if _idea_of(n) == idea_id]


def idea_ids(path: Path | str | None = None) -> list[str]:
    """Every opened idea, in the order it was opened."""
    return list(dict.fromkeys(_idea_of(n) for n in read_ledger(path) if n.kind == ROOT))


def resolve_idea(prefix: str, path: Path | str | None = None) -> str:
    """The one opened idea whose id starts with ``prefix``."""
    hits = [i for i in idea_ids(path) if prefix and i.startswith(prefix)]
    if len(hits) != 1:
        raise IdeaRefused(
            f"{len(hits)} opened ideas match {prefix!r}; give more of the id (see `list`)"
        )
    return hits[0]


def _find(nodes: list[PrecogIDR], kind: str, fingerprint: str) -> PrecogIDR | None:
    return next((n for n in nodes if n.kind == kind and n.fingerprint == fingerprint), None)


def _pick(nodes: list[PrecogIDR], kind: str, prefix: str) -> PrecogIDR:
    hits = [n for n in nodes if n.kind == kind and n.fingerprint.startswith(prefix)]
    if len(hits) != 1:
        raise IdeaRefused(
            f"{len(hits)} {kind} records of this idea match {prefix!r}; "
            "give more of the fingerprint (see `show`)"
        )
    return hits[0]


# --- writing ----------------------------------------------------------------

@contextmanager
def _locked(ledger: Path) -> Iterator[None]:
    """Hold the ledger's lock across the read, the checks and the append.

    ``append_idr`` locks only its own write, so without this two identical
    steps running at once could each miss the other and write a duplicate.
    """
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger.with_name(ledger.name + ".lock"), "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _append(
    ledger: Path,
    idea_id: str,
    kind: str,
    fingerprint: str,
    body: dict[str, Any],
    parent: PrecogIDR | None,
) -> PrecogIDR:
    """Sign one node and append it to the idea ledger, never to the main chain."""
    idr = build_idr(
        prompt="",
        fingerprint=fingerprint,
        decision=body,
        predecessor_idr=parent.id if parent else anchor_of(idea_id),
        depth=parent.depth + 1 if parent else 0,
        kind=kind,
        inputs={
            "idea": idea_id,
            "parent": parent.fingerprint if parent else None,
            "format": FORMAT,
        },
    )
    append_idr(idr, path=ledger)  # an explicit path, so the main HEAD never moves
    return idr


def _step(
    path: Path | str | None,
    idea_id: str,
    kind: str,
    body: dict[str, Any],
    parent_of: Callable[[list[PrecogIDR]], PrecogIDR],
    conflict: Callable[[list[PrecogIDR], PrecogIDR], None] | None = None,
) -> tuple[PrecogIDR, bool]:
    """Read, check and append one node under the ledger lock.

    ``parent_of`` raises ``IdeaRefused`` when a rule forbids the step. An
    identical repeat returns the existing node before ``conflict`` runs, so a
    repeat is a no-op, never a refusal.
    """
    ledger = ledger_path(path)
    with _locked(ledger):
        nodes = [n for n in read_ledger(ledger) if _idea_of(n) == idea_id]
        parent = parent_of(nodes)
        fingerprint = node_fingerprint(idea_id, kind, parent.fingerprint, body)
        found = _find(nodes, kind, fingerprint)
        if found is not None:
            return found, False
        if conflict is not None:
            conflict(nodes, parent)
        return _append(ledger, idea_id, kind, fingerprint, body, parent), True


def _required(value: str | None, label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise IdeaRefused(f"the {label} must not be empty")
    return text


def _read_text(path: Path | str) -> str:
    """A file's text, or ``IdeaUnreadable``, which the command line prints as
    UNREADABLE with exit 1, the same as for a ledger it cannot read."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IdeaUnreadable(f"cannot read {path}: {exc}") from exc


def _portable(source: Path | str) -> str:
    """The source path to record: relative to this checkout when the file lies
    inside it, so one verdict has one fingerprint on every machine; any other
    path is kept as given."""
    try:
        return Path(source).resolve().relative_to(_REPO.resolve()).as_posix()
    except ValueError:
        return str(source)


# --- the five steps ---------------------------------------------------------

def open_idea(text: str, *, path: Path | str | None = None) -> tuple[PrecogIDR, bool]:
    """Record the operator's idea, verbatim, as the root of its chain (rule A).

    Returns ``(root, written)``. ``written`` is False when this idea, or a
    whitespace-only variant of it, is already open.
    """
    _require_one_idea(text)
    ledger = ledger_path(path)
    fingerprint = root_fingerprint(text)
    idea_id = fingerprint[:ID_LENGTH]
    with _locked(ledger):
        found = _find(read_ledger(ledger), ROOT, fingerprint)
        if found is not None:
            return found, False
        body = {"text": text, "text_sha256": _sha256(text)}
        return _append(ledger, idea_id, ROOT, fingerprint, body, None), True


def _require_one_idea(text: str) -> None:
    if not text or not text.strip():
        raise IdeaRefused("rule A: an idea needs words, and this text is empty")
    headings = len(_IDEA_HEADING.findall(text))
    if headings > 1:
        raise IdeaRefused(
            f"rule A: one idea per prompt, and this text has {headings} IDEA headings. "
            "Open each idea on its own."
        )


def settle_meaning(
    idea_id: str,
    readings: Sequence[str],
    *,
    chosen: int | str | None = None,
    recommended: int = 1,
    question: str | None = None,
    answer: str | None = None,
    path: Path | str | None = None,
) -> tuple[PrecogIDR, bool]:
    """Record what the idea means (rule B).

    ``chosen`` is the 1-based number of the reading the operator picked, or
    the operator's own words when their reading was not on the list. With one
    reading it defaults to that reading. With two or more readings, the
    question asked and the operator's verbatim answer are required.
    """
    body = _meaning_body(list(readings), chosen, recommended, question, answer)
    return _step(path, idea_id, MEANING, body, _root_of, _one_meaning)


def _meaning_body(
    readings: list[str],
    chosen: int | str | None,
    recommended: int,
    question: str | None,
    answer: str | None,
) -> dict[str, Any]:
    readings = [r.strip() for r in readings if r and r.strip()]
    if not readings:
        raise IdeaRefused("rule B: list at least one reading of the idea")
    if not 1 <= recommended <= len(readings):
        raise IdeaRefused(f"the recommended reading must be 1 to {len(readings)}")
    body: dict[str, Any] = {
        "readings": readings,
        "recommended": recommended,
        "chosen": _chosen_text(readings, chosen),
    }
    body.update(_asked(question, answer, count=len(readings)))
    return body


def _chosen_text(readings: list[str], chosen: int | str | None) -> str:
    if isinstance(chosen, str):
        chosen = chosen.strip() or None
    if chosen is None:
        if len(readings) == 1:
            return readings[0]
        raise IdeaRefused("rule B: there are two or more readings, so record which one was chosen")
    if isinstance(chosen, str):
        return chosen
    if not 1 <= chosen <= len(readings):
        raise IdeaRefused(f"the chosen reading must be 1 to {len(readings)}")
    return readings[chosen - 1]


def _asked(question: str | None, answer: str | None, *, count: int) -> dict[str, str]:
    q = (question or "").strip()
    a = answer or ""
    if not q and not a.strip():
        if count > 1:
            raise IdeaRefused(
                f"rule B: the idea has {count} readings, so its meaning is not settled. "
                "Halt and ask the operator (/hitl), then record --question and the "
                "verbatim --answer."
            )
        return {}
    if not q or not a.strip():
        raise IdeaRefused("rule B: record both the question asked and the operator's answer")
    return {"question": q, "answer": a}


def _root_of(nodes: list[PrecogIDR]) -> PrecogIDR:
    root = next((n for n in nodes if n.kind == ROOT), None)
    if root is None:
        raise IdeaRefused("no opened idea has that id; `open` it first")
    return root


def _one_meaning(nodes: list[PrecogIDR], parent: PrecogIDR) -> None:
    if any(n.kind == MEANING for n in nodes):
        raise IdeaRefused(
            "this idea's meaning is already settled. A different meaning is a different "
            "idea, so open it in words that carry that meaning."
        )


def _meaning_of(nodes: list[PrecogIDR]) -> PrecogIDR:
    _root_of(nodes)
    meaning = next((n for n in nodes if n.kind == MEANING), None)
    if meaning is None:
        raise IdeaRefused(
            "rule B: settle what the idea means first (`meaning`). No claim or outcome "
            "may hang from an idea whose meaning is open."
        )
    return meaning


def add_claim(
    idea_id: str,
    claim: str,
    falsifier: str,
    *,
    parent: str | None = None,
    path: Path | str | None = None,
) -> tuple[PrecogIDR, bool]:
    """Record one consequence and the result that would show it false, BEFORE testing it.

    ``parent`` names an earlier claim (a fingerprint prefix) to branch from.
    Without it the claim hangs from the settled meaning.
    """
    body = {"claim": _required(claim, "claim"), "falsifier": _required(falsifier, "falsifier")}
    return _step(path, idea_id, CLAIM, body, lambda nodes: _claim_parent(nodes, parent))


def _claim_parent(nodes: list[PrecogIDR], parent: str | None) -> PrecogIDR:
    meaning = _meaning_of(nodes)
    return _pick(nodes, CLAIM, parent) if parent else meaning


def settle_claim(
    idea_id: str,
    claim_ref: str,
    verdict: str,
    *,
    evidence: str,
    verifier: str,
    quote: str | None = None,
    source: Path | str | None = None,
    path: Path | str | None = None,
) -> tuple[PrecogIDR, bool]:
    """Record the verdict on one claim. A claim gets one verdict, and a quote must be in its source."""
    ref = _required(claim_ref, "claim reference")
    body = _verdict_body(verdict, evidence, verifier, quote, source)
    return _step(path, idea_id, VERDICT, body, lambda nodes: _pick(nodes, CLAIM, ref), _one_verdict)


def _verdict_body(
    verdict: str,
    evidence: str,
    verifier: str,
    quote: str | None,
    source: Path | str | None,
) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise IdeaRefused(f"the verdict must be one of {', '.join(VERDICTS)}")
    body: dict[str, Any] = {
        "verdict": verdict,
        "evidence": _required(evidence, "evidence"),
        "verifier": _required(verifier, "verifier"),
    }
    body.update(_quote_part(quote, source))
    return body


def _quote_part(quote: str | None, source: Path | str | None) -> dict[str, Any]:
    if not quote and not source:
        return {}
    if not quote or not source:
        raise IdeaRefused("a quote needs its --source file, and a --source needs its --quote")
    text = _read_text(source)
    status, start, end = verify_quote(quote, text)
    if status == STATUS_NOT_FOUND:
        raise IdeaRefused(f"the quote is not in {source}; a verdict may only quote what its source says")
    return {
        "quote": quote,
        "source": _portable(source),
        "source_sha256": _sha256(text),
        "quote_status": status,
        "quote_span": [start, end],
    }


def _one_verdict(nodes: list[PrecogIDR], parent: PrecogIDR) -> None:
    if any(n.kind == VERDICT and _parent_fp(n) == parent.fingerprint for n in nodes):
        raise IdeaRefused(
            "this claim already has its verdict. One claim, one verdict: record a new "
            "claim to test it again."
        )


def record_outcome(
    idea_id: str,
    outcome: str,
    *,
    status: str,
    refs: Sequence[str] = (),
    path: Path | str | None = None,
) -> tuple[PrecogIDR, bool]:
    """Record what came of the idea: a merged change, an open PR, an issue, a hypothesis or nothing."""
    if status not in OUTCOME_STATUSES:
        raise IdeaRefused(f"the status must be one of {', '.join(OUTCOME_STATUSES)}")
    body = {
        "outcome": _required(outcome, "outcome"),
        "status": status,
        "refs": [r.strip() for r in refs if r and r.strip()],
    }
    return _step(path, idea_id, OUTCOME, body, _meaning_of)


# --- verification -----------------------------------------------------------

@dataclass(frozen=True)
class Report:
    """What ``verify_idea`` found. ``ok`` is the one bit a script needs."""

    idea: str
    nodes: int
    integrity: str  # VERIFIED, DEGRADED or BROKEN, over this idea's records
    ledger: str  # the same verdict over every record in the ledger
    problems: tuple[str, ...]
    digest: str  # sha256 of the sorted fingerprints: equal across recordings
    merkle_root: str  # RFC 6962 root of THIS signed recording

    @property
    def ok(self) -> bool:
        return self.integrity == self.ledger == Verdict.VERIFIED.name and not self.problems


def verify_idea(idea_id: str, path: Path | str | None = None) -> Report:
    """Signatures, rules, digest and Merkle root for one idea, plus the whole ledger's signatures.

    The forests are built and checked by the unmodified ``lib.idr_forest``
    functions: the idea ledger is checked exactly as the main chain is.
    """
    return verify_ideas([idea_id], path)[0]


def verify_ideas(ids: Sequence[str], path: Path | str | None = None) -> list[Report]:
    """One report per idea, from ONE read of the ledger and ONE check of its signatures.

    The ledger-wide facts are the same for every idea, so they are computed
    once. Checking them once per idea made ``verify`` with no ``--idea``
    quadratic in the size of the ledger (AGORA on GRIP#9973).
    """
    ledger = ledger_path(path)
    every = read_idr_chain(path=ledger)
    records = [n for n in every if n.kind in KINDS]
    shared = _ledger_problems(ledger, records, len(every))
    ledger_verdict = _ledger_integrity(records)
    by_idea: dict[str, list[PrecogIDR]] = {}
    for node in records:
        by_idea.setdefault(_idea_of(node), []).append(node)
    return [_report(idea_id, by_idea.get(idea_id, []), shared, ledger_verdict) for idea_id in ids]


def _report(idea_id: str, nodes: list[PrecogIDR], shared: list[str], ledger_verdict: str) -> Report:
    forest, not_a_tree = _forest((anchor_of(idea_id),), nodes)
    problems = shared + rule_problems(idea_id, nodes) + ([not_a_tree] if not_a_tree else [])
    return Report(
        idea=idea_id,
        nodes=len(nodes),
        integrity=_integrity(forest),
        ledger=ledger_verdict,
        problems=tuple(problems),
        digest=idea_digest(nodes),
        merkle_root=forest_merkle_root(forest) if forest is not None else "",
    )


def idea_digest(nodes: Sequence[PrecogIDR]) -> str:
    """sha256 over the sorted fingerprints. Any recording of the same steps has the same digest."""
    return _sha256("\n".join(sorted(n.fingerprint for n in nodes)))


def _forest(anchors: tuple[str, ...], nodes: Sequence[PrecogIDR]):
    """The records as an ``lib.idr_forest`` forest, built by its own unmodified builder."""
    try:
        forest = empty_forest(anchors)
        for node in nodes:
            forest = add_idr(forest, node)
    except IdrForestError as exc:
        return None, f"the records do not form a tree: {exc}"
    return forest, None


def _integrity(forest) -> str:
    if forest is None:
        return Verdict.BROKEN.name
    return verify_chain_integrity(forest).name


def _ledger_integrity(records: list[PrecogIDR]) -> str:
    """Every record's signature, so a record moved to another idea is still checked."""
    if not records:
        return Verdict.VERIFIED.name
    anchors = {str(r.predecessor_idr) for r in records}
    forest, _ = _forest(tuple(sorted(a for a in anchors if a.startswith(ANCHOR_PREFIX))), records)
    return _integrity(forest)


def _ledger_problems(ledger: Path, records: list[PrecogIDR], parsed: int) -> list[str]:
    problems = []
    unread = _raw_lines(ledger) - parsed
    if unread:
        problems.append(f"{unread} ledger line(s) could not be read as records")
    opened = {_idea_of(n) for n in records if n.kind == ROOT}
    orphans = sum(1 for n in records if _idea_of(n) not in opened)
    if orphans:
        problems.append(f"{orphans} ledger record(s) belong to no opened idea")
    return problems


def _raw_lines(ledger: Path) -> int:
    """Non-empty lines in the ledger. ``read_idr_chain`` drops a line it cannot
    parse, silently, so the shortfall against this count is reported."""
    if not ledger.exists():
        return 0
    with open(ledger, encoding="utf-8", errors="replace") as fh:
        return sum(1 for line in fh if line.strip())


def rule_problems(idea_id: str, nodes: Sequence[PrecogIDR]) -> list[str]:
    """Re-check every /idea rule against the records themselves.

    The tool refuses a forbidden step when it is written. This catches a
    record that reached the ledger some other way.
    """
    by_id = {n.id: n for n in nodes}
    problems = _root_problems(idea_id, nodes)
    for node in nodes:
        if node.kind == ROOT:
            continue
        problems += _link_problems(idea_id, node, by_id.get(str(node.predecessor_idr)))
        problems += _BODY_CHECKS.get(node.kind, _no_problems)(node)
    return problems + _count_problems(nodes)


def _root_problems(idea_id: str, nodes: Sequence[PrecogIDR]) -> list[str]:
    roots = [n for n in nodes if n.kind == ROOT]
    if len(roots) != 1:
        return [f"the idea has {len(roots)} roots; it must have exactly one"]
    root = roots[0]
    text = str(_body(root).get("text", ""))
    checks = (
        (root.fingerprint == root_fingerprint(text), "its fingerprint does not match its text"),
        (root.fingerprint[:ID_LENGTH] == idea_id, "its fingerprint does not match the idea id"),
        (root.predecessor_idr == anchor_of(idea_id), "it does not hang from the idea's anchor"),
        (_body(root).get("text_sha256") == _sha256(text), "its sha256 does not match its text"),
    )
    return [f"{_short(root)}: {message}" for holds, message in checks if not holds]


def _link_problems(idea_id: str, node: PrecogIDR, parent: PrecogIDR | None) -> list[str]:
    name = _short(node)
    if parent is None:
        return [f"{name}: its parent is not a record of this idea"]
    problems = []
    if parent.kind not in PARENT_KINDS.get(node.kind, ()):
        before = " (a claim before the meaning, rule B)" if node.kind == CLAIM else ""
        problems.append(f"{name}: a {node.kind} may not hang from a {parent.kind}{before}")
    if _parent_fp(node) != parent.fingerprint:
        problems.append(f"{name}: the parent fingerprint it names is not its parent's")
    if node.fingerprint != _safe_fingerprint(idea_id, node, parent.fingerprint):
        problems.append(f"{name}: its fingerprint does not match its content")
    return problems


def _meaning_problems(node: PrecogIDR) -> list[str]:
    body = _body(node)
    readings = body.get("readings")
    if not isinstance(readings, list) or not readings:
        return [f"{_short(node)}: the meaning lists no readings"]
    asked = str(body.get("question", "")).strip() and str(body.get("answer", "")).strip()
    if len(readings) > 1 and not asked:
        return [f"{_short(node)}: {len(readings)} readings but no question and answer (rule B)"]
    return []


def _verdict_problems(node: PrecogIDR) -> list[str]:
    body = _body(node)
    problems = []
    if body.get("verdict") not in VERDICTS:
        problems.append(f"{_short(node)}: {body.get('verdict')!r} is not a verdict")
    if "quote" in body and body.get("quote_status") not in (STATUS_VERIFIED, STATUS_FUZZY):
        problems.append(f"{_short(node)}: its quote was not found in its source")
    return problems


def _outcome_problems(node: PrecogIDR) -> list[str]:
    if _body(node).get("status") in OUTCOME_STATUSES:
        return []
    return [f"{_short(node)}: {_body(node).get('status')!r} is not an outcome status"]


def _no_problems(node: PrecogIDR) -> list[str]:
    return []


_BODY_CHECKS: dict[str, Callable[[PrecogIDR], list[str]]] = {
    MEANING: _meaning_problems,
    VERDICT: _verdict_problems,
    OUTCOME: _outcome_problems,
}


def _count_problems(nodes: Sequence[PrecogIDR]) -> list[str]:
    problems = []
    meanings = sum(1 for n in nodes if n.kind == MEANING)
    if meanings > 1:
        problems.append(f"the idea has {meanings} meanings; it must have one")
    settled = [_parent_fp(n) for n in nodes if n.kind == VERDICT]
    if len(settled) != len(set(settled)):
        problems.append(f"{len(settled) - len(set(settled))} claim(s) carry more than one verdict")
    prints = [n.fingerprint for n in nodes]
    if len(prints) != len(set(prints)):
        problems.append(f"{len(prints) - len(set(prints))} step(s) were recorded twice")
    return problems


# --- showing ----------------------------------------------------------------

_LABELS = {ROOT: "IDEA", MEANING: "MEANING", CLAIM: "CLAIM", VERDICT: "VERDICT", OUTCOME: "OUTCOME"}


def _lines(label: str, value: Any) -> list[str]:
    first, *rest = str(value).splitlines() or [""]
    return [f"{label}: {first}", *(f"  {line}" for line in rest)]


def _root_details(body: dict[str, Any]) -> list[str]:
    return [*_lines("text", body.get("text", "")), f"sha256: {body.get('text_sha256', '')}"]


def _meaning_details(body: dict[str, Any]) -> list[str]:
    recommended = body.get("recommended")
    out = [
        f"{i}. {reading}" + ("  (recommended)" if i == recommended else "")
        for i, reading in enumerate(body.get("readings") or [], 1)
    ]
    out += _lines("chosen", body.get("chosen", ""))
    if body.get("question"):
        out += _lines("question", body["question"]) + _lines("answer", body.get("answer", ""))
    return out


def _claim_details(body: dict[str, Any]) -> list[str]:
    return _lines("claim", body.get("claim", "")) + _lines("falsifier", body.get("falsifier", ""))


def _verdict_details(body: dict[str, Any]) -> list[str]:
    out = [f"verdict: {body.get('verdict')}  (verifier: {body.get('verifier')})"]
    out += _lines("evidence", body.get("evidence", ""))
    if body.get("quote"):
        out += _lines("quote", body["quote"])
        out.append(f"source: {body.get('source')} ({body.get('quote_status')})")
    return out


def _outcome_details(body: dict[str, Any]) -> list[str]:
    refs = ", ".join(body.get("refs") or []) or "none"
    return [f"status: {body.get('status')}  refs: {refs}", *_lines("outcome", body.get("outcome", ""))]


_DETAILS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    ROOT: _root_details,
    MEANING: _meaning_details,
    CLAIM: _claim_details,
    VERDICT: _verdict_details,
    OUTCOME: _outcome_details,
}


def render_tree(idea_id: str, nodes: Sequence[PrecogIDR]) -> str:
    """The idea's chain as an indented plain-text tree, in recording order."""
    children: dict[str, list[PrecogIDR]] = {}
    for node in nodes:
        children.setdefault(str(node.predecessor_idr), []).append(node)
    lines: list[str] = []
    _render(anchor_of(idea_id), children, 0, lines, set())
    return "\n".join(lines)


def _render(
    parent_id: str,
    children: dict[str, list[PrecogIDR]],
    level: int,
    lines: list[str],
    seen: set[int],
) -> None:
    pad = "    " * level
    for node in children.get(parent_id, []):
        if id(node) in seen:  # a hand-edited ledger can repeat an id; never loop on it
            continue
        seen.add(id(node))
        lines.append(f"{pad}{_LABELS.get(node.kind, node.kind)} {node.fingerprint[:12]}")
        details = _DETAILS.get(node.kind, lambda body: [])(_body(node))
        lines.extend(f"{pad}    {line}" for line in details)
        _render(node.id, children, level + 1, lines, seen)


def _title(text: Any) -> str:
    """The first words of the idea that are not an IDEA heading, cut to 72 characters."""
    for line in str(text).splitlines():
        heading = _IDEA_HEADING.match(line)
        line = (line[heading.end():] if heading else line).strip(" \t:.-*#")
        if line:
            return line if len(line) <= 72 else line[:72] + "..."
    return ""


def _recommended_text(body: dict[str, Any]) -> str | None:
    readings = body.get("readings") or []
    index = body.get("recommended")
    if isinstance(index, int) and 1 <= index <= len(readings):
        return readings[index - 1]
    return None


def _row(idea_id: str, nodes: list[PrecogIDR]) -> dict[str, Any]:
    root = next(n for n in nodes if n.kind == ROOT)
    meaning = next((n for n in nodes if n.kind == MEANING), None)
    body = _body(meaning)
    statuses = {_body(n).get("status") for n in nodes if n.kind == OUTCOME}
    return {
        "idea": idea_id,
        "title": _title(_body(root).get("text", "")),
        "nodes": len(nodes),
        "meaning": meaning is not None,
        "readings": len(body.get("readings") or []),
        "chose_recommended": body.get("chosen") == _recommended_text(body) if meaning else None,
        "statuses": sorted(s for s in statuses if isinstance(s, str)),
    }


def summarise(path: Path | str | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One row per opened idea, and the counts that test whether the meaning gate earns its keep."""
    records = read_ledger(path)
    rows = [
        _row(idea_id, [n for n in records if _idea_of(n) == idea_id])
        for idea_id in dict.fromkeys(_idea_of(n) for n in records if n.kind == ROOT)
    ]
    return rows, gate_counts(rows)


def gate_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    """The falsifier's numbers: did the gate ever change the reading, and did any idea ship?"""
    fired = [r for r in rows if r["readings"] > 1]
    return {
        "ideas": len(rows),
        "meanings": sum(1 for r in rows if r["meaning"]),
        "gate_fired": len(fired),
        "chose_other": sum(1 for r in fired if r["chose_recommended"] is False),
        "merged": sum(1 for r in rows if "merged" in r["statuses"]),
    }


# --- command line -----------------------------------------------------------

def _emit(node: PrecogIDR, written: bool) -> int:
    print(json.dumps({
        "idea": _idea_of(node),
        "kind": node.kind,
        "node": node.fingerprint,
        "written": written,
    }))
    return 0


def _idea(ns: argparse.Namespace) -> str:
    return resolve_idea(ns.idea, ns.ledger)


def _cli_open(ns: argparse.Namespace) -> int:
    text = ns.text if ns.text is not None else _read_text(ns.file)
    return _emit(*open_idea(text, path=ns.ledger))


def _cli_meaning(ns: argparse.Namespace) -> int:
    chosen = ns.chosen if ns.chosen is not None else ns.chosen_text
    return _emit(*settle_meaning(
        _idea(ns), ns.reading, chosen=chosen, recommended=ns.recommended,
        question=ns.question, answer=ns.answer, path=ns.ledger,
    ))


def _cli_claim(ns: argparse.Namespace) -> int:
    return _emit(*add_claim(_idea(ns), ns.claim, ns.falsifier, parent=ns.parent, path=ns.ledger))


def _cli_verdict(ns: argparse.Namespace) -> int:
    evidence = ns.evidence if ns.evidence is not None else _read_text(ns.evidence_file)
    return _emit(*settle_claim(
        _idea(ns), ns.claim, ns.verdict, evidence=evidence, verifier=ns.verifier,
        quote=ns.quote, source=ns.source, path=ns.ledger,
    ))


def _cli_outcome(ns: argparse.Namespace) -> int:
    return _emit(*record_outcome(_idea(ns), ns.outcome, status=ns.status, refs=ns.ref, path=ns.ledger))


def _record(node: PrecogIDR) -> dict[str, Any]:
    """The record as ``append_idr`` stored it: an absent anatomy is dropped, not null."""
    body = asdict(node)
    if body.get("decision_anatomy") is None:
        body.pop("decision_anatomy", None)
    return body


def _cli_show(ns: argparse.Namespace) -> int:
    idea_id = _idea(ns)
    nodes = nodes_of(idea_id, ns.ledger)
    if ns.records:
        for node in nodes:
            print(_canonical_json(_record(node)))
    else:
        print(render_tree(idea_id, nodes))
    return 0


def _print_report(report: Report) -> None:
    print(f"idea {report.idea}: {report.integrity} ({report.nodes} records); ledger: {report.ledger}")
    for problem in report.problems:
        print(f"  problem: {problem}")
    print(f"  idea digest: {report.digest}")
    print(f"  merkle root: {report.merkle_root}")


def _exit_code(reports: Sequence[Report]) -> int:
    if any(r.problems or Verdict.BROKEN.name in (r.integrity, r.ledger) for r in reports):
        return 1
    if any(Verdict.DEGRADED.name in (r.integrity, r.ledger) for r in reports):
        return 2
    return 0


def _cli_verify(ns: argparse.Namespace) -> int:
    ids = [_idea(ns)] if ns.idea else idea_ids(ns.ledger)
    reports = verify_ideas(ids, ns.ledger)
    for report in reports:
        _print_report(report)
    if not reports:
        print("no ideas in the ledger")
    return _exit_code(reports)


def _meaning_label(row: dict[str, Any]) -> str:
    if not row["meaning"]:
        return "meaning open"
    if row["readings"] < 2:
        return "1 reading"
    pick = "the recommended" if row["chose_recommended"] else "another"
    return f"{row['readings']} readings, chose {pick}"


def _cli_list(ns: argparse.Namespace) -> int:
    rows, counts = summarise(ns.ledger)
    for row in rows:
        outcomes = ", ".join(row["statuses"]) or "none"
        print(f"{row['idea']}  {row['nodes']} records  {_meaning_label(row)}  "
              f"outcome: {outcomes}  {row['title']}")
    print(
        f"gate: {counts['ideas']} idea(s); {counts['meanings']} meaning(s) settled; "
        f"the gate fired on {counts['gate_fired']}; a reading other than the recommended one "
        f"was chosen on {counts['chose_other']}; {counts['merged']} ended in a merged change"
    )
    return 0


_CLI: dict[str, Callable[[argparse.Namespace], int]] = {
    "open": _cli_open,
    "meaning": _cli_meaning,
    "claim": _cli_claim,
    "verdict": _cli_verdict,
    "outcome": _cli_outcome,
    "show": _cli_show,
    "verify": _cli_verify,
    "list": _cli_list,
}


def _add_step_parsers(sub: Any) -> None:
    o = sub.add_parser("open", help="record the operator's idea, verbatim, as a chain root")
    text = o.add_mutually_exclusive_group(required=True)
    text.add_argument("--text", help="the idea, verbatim")
    text.add_argument("--file", help="a file holding the idea, verbatim")

    m = sub.add_parser("meaning", help="record what the idea means (rule B)")
    m.add_argument("--reading", action="append", required=True, help="one reading; repeat it")
    m.add_argument("--recommended", type=int, default=1, help="the number of the reading you recommend")
    pick = m.add_mutually_exclusive_group()
    pick.add_argument("--chosen", type=int, help="the number of the reading the operator chose")
    pick.add_argument("--chosen-text", dest="chosen_text", help="the operator's own reading")
    m.add_argument("--question", help="the question asked; required with two or more readings")
    m.add_argument("--answer", help="the operator's verbatim answer; required with the question")

    c = sub.add_parser("claim", help="record a consequence and its falsifier, before testing it")
    c.add_argument("--claim", required=True)
    c.add_argument("--falsifier", required=True, help="the result that would show the claim false")
    c.add_argument("--parent", help="an earlier claim's fingerprint prefix, to branch from it")

    v = sub.add_parser("verdict", help="settle one claim with its evidence")
    v.add_argument("--claim", required=True, help="the claim's fingerprint prefix")
    v.add_argument("--verdict", required=True, choices=VERDICTS)
    evidence = v.add_mutually_exclusive_group(required=True)
    evidence.add_argument("--evidence", help="the command and its output")
    evidence.add_argument("--evidence-file", dest="evidence_file", help="a file holding the evidence")
    v.add_argument("--verifier", required=True, help="what checked it, for example a command or a council seal")
    v.add_argument("--quote", help="a quote that must be found in --source")
    v.add_argument("--source", help="the file the quote comes from")

    u = sub.add_parser("outcome", help="record what came of the idea")
    u.add_argument("--outcome", required=True)
    u.add_argument("--status", required=True, choices=OUTCOME_STATUSES)
    u.add_argument("--ref", action="append", default=[], help="a PR, issue or hypothesis id; repeat it")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="idea_chain", description="Record one idea as the root of a signed causal chain.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    _add_step_parsers(sub)
    s = sub.add_parser("show", help="print the idea's chain as a tree")
    s.add_argument("--records", action="store_true", help="print the signed records instead")
    sub.add_parser("verify", help="check signatures, rules, the digest and the Merkle root")
    sub.add_parser("list", help="every opened idea, and whether the meaning gate earns its keep")
    for name, sp in sub.choices.items():
        sp.add_argument("--ledger", help="the ledger file (default $CLAUDE_DIR/state/precog/ideas.jsonl)")
        if name not in ("open", "list"):
            sp.add_argument("--idea", required=name != "verify", help="the idea id, or a unique prefix")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    try:
        return _CLI[ns.cmd](ns)
    except IdeaRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (IdeaUnreadable, OSError) as exc:
        # A file the process cannot read or write, an argument file or the ledger,
        # is a named status, never a traceback: the /mental-model contract
        # (GRIP#9967) composes this tool and reads UNREADABLE from a closed set.
        # Found by /idea's own dogfood run.
        print(f"UNREADABLE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # `/usr/bin/env python3` need not be an interpreter that can sign, and every
    # step writes a signed record, so re-exec into one that can first
    # (lib/pq_reexec.py, rules/never-system-python-for-grip.md). Imported as the
    # top-level `pq_reexec` off THIS checkout, never as `lib.pq_reexec`.
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(_Path(__file__))
    _sys.exit(main())
