"""The clause ledger: every kernel line tied to the annex section that explains it.

A kernel keeps only operative clauses; the reasons behind them move to an annex,
docs/doctrine/<rule>.md, which is never deleted. The annex carries a section:

    ## Kernel ledger
    ### <a kernel line, verbatim>
    <why the clause exists, its origin, its evidence, its falsifier>

The ledger is checked in BOTH directions, the way lib/binding_memory.py's
audit_block reconciles a pinned block against its declarations: every clause
needs a section (nothing loaded is unexplained) and every section needs a live
clause (nothing explained has silently left the kernel).

A stored ledger, docs/doctrine/ledger.json, adds the Annex Warden. It records
for each clause the sha1 prefix of the kernel line and the sha256 of its annex
section. Editing either one changes a digest and turns the check red until the
ledger is regenerated, which is a deliberate, reviewable step. It stores only
digests, never clause text, because some rules are git-crypt encrypted and a
plaintext ledger would publish them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ._util import duplicates

LEDGER_SECTION = "## Kernel ledger"
LEDGER_REL = "docs/doctrine/ledger.json"
ANNEX_DIR = "docs/doctrine"


def clause_id(line: str) -> str:
    """A short, stable identity for one kernel line."""
    return hashlib.sha1(line.encode("utf-8")).hexdigest()[:8]


def span_digest(body: str) -> str:
    """sha256 of an annex section's body, trailing whitespace ignored."""
    normalised = "\n".join(line.rstrip() for line in body.strip().split("\n"))
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def annex_path(root: Path, rule: str) -> Path:
    return root / ANNEX_DIR / f"{rule}.md"


def _ledger_block(annex_text: str) -> list[str]:
    """The lines of the ``## Kernel ledger`` section, up to the next ``## `` heading."""
    lines = annex_text.split("\n")
    start = next((i for i, line in enumerate(lines) if line.strip() == LEDGER_SECTION), None)
    if start is None:
        return []
    block = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        block.append(line)
    return block


def sections(annex_text: str) -> list[tuple[str, str]]:
    """(heading, body) for every ``### `` subsection of the ledger section, in order."""
    out: list[tuple[str, list[str]]] = []
    for line in _ledger_block(annex_text):
        if line.startswith("### "):
            out.append((line[4:], []))
        elif out:
            out[-1][1].append(line)
    return [(heading, "\n".join(body).strip()) for heading, body in out]


@dataclass(frozen=True)
class Gap:
    kind: str    # missing | orphan | duplicate-clause | duplicate-section | empty | stale
    detail: str


def audit(clauses: list[str], annex_text: str) -> list[Gap]:
    """Two-sided reconciliation of kernel clauses against annex sections."""
    found = sections(annex_text)
    headings = [h for h, _ in found]
    gaps = [Gap("duplicate-clause", c) for c in duplicates(clauses)]
    gaps += [Gap("duplicate-section", h) for h in duplicates(headings)]
    have, want = set(headings), set(clauses)
    gaps += [Gap("missing", c) for c in clauses if c not in have]
    gaps += [Gap("orphan", h) for h in headings if h not in want]
    gaps += [Gap("empty", h) for h, body in found if h in want and not body]
    return gaps


def entries(clauses: list[str], annex_text: str) -> list[dict]:
    """The stored-ledger rows for one rule: clause id and annex-span digest, in order."""
    bodies = dict(sections(annex_text))
    return [{"id": clause_id(c), "span": span_digest(bodies.get(c, ""))} for c in clauses]


def warden(rule: str, current: list[dict], stored: dict) -> list[Gap]:
    """Compare freshly computed rows against the stored ledger for one rule."""
    rows = (stored.get("rules") or {}).get(rule)
    if rows is None:
        return [Gap("stale", f"{rule}: not in {LEDGER_REL}; run grip-lexicon ledger --write")]
    if rows != current:
        return [Gap("stale", f"{rule}: kernel or annex changed since the ledger was "
                             "written; review, then run grip-lexicon ledger --write")]
    return []


def load(root: Path) -> dict:
    path = root / LEDGER_REL
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write(root: Path, rows: dict[str, list[dict]]) -> Path:
    """Write the stored ledger for every kernel, sorted, so the file is diff-stable."""
    path = root / LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"_doc": "KERN clause ledger. Digests only, never clause text: some rules are "
                   "git-crypt encrypted. Regenerate with `grip-lexicon ledger --write`.",
           "rules": {rule: rows[rule] for rule in sorted(rows)}}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path
