#!/usr/bin/env python3
"""substrate_merge.py - cross-platform git merge driver for GRIP's tracked
learning/memory/science substrate.

GRIP's substrate files are tracked on purpose (cross-machine propagation of
learned rules, hypothesis verification, genome breeding). Concurrent sessions
on multiple machines all append/regenerate them every session, so a plain
`git pull` hits merge conflicts on substrate and blocks everyone.

This driver reconciles substrate so churn never blocks a pull, WITHOUT
untracking and WITHOUT corrupting files. It is git-merge-driver compatible:
git invokes it as `<driver> %O %A %B %P` where

    %O = ancestor version (path to temp file; may be empty/absent)
    %A = current/ours version  (path; driver MUST overwrite this in place)
    %B = other/theirs version  (path)
    %P = the real pathname being merged (for diagnostics only)

Exit 0 => merge resolved (result written to the %A path).
Exit non-zero => leave conflict for git (safe fallback).

Two modes, selected by argv[1]:

  jsonl-union  Class-1 append-only line-independent JSONL
               (insights.jsonl, evo-convergence.jsonl,
                insights-convergence.jsonl, dispatch-failures.jsonl).
               Union of all lines from ours+theirs, DEDUPLICATED on exact
               line content, original order preserved (ours first, then
               theirs-only). Plain `merge=union` would keep duplicate
               identical records when two machines append the same line;
               this dedups them.

  jsonl-keyed  Class-2 keyed JSONL with in-place updates
               (hypotheses.jsonl). Each record is keyed by its `id` field
               and a record's `status` mutates pending->confirmed/refuted
               across sessions. Plain `merge=union` DUPLICATES and creates
               contradictory records for the same id. This reconciles per
               id, three ways against the ancestor (%O): a record only one
               side changed takes that side's rows; a record both sides
               changed is weighed, where terminal status beats `pending`;
               if both sides hold DIFFERENT terminal statuses for the same
               id, the later verification_date (then registered) wins AND
               the surviving record is annotated with a `_merge_conflict`
               note so the scientific divergence is visible, never silently
               dropped. Every row the merge does not alter is written back
               with the exact bytes it came in with (until 2026-10-12 the
               whole file was re-serialised, and an id held twice was
               folded to one row, on every merge that ran this driver).
               An id held more than once that both sides changed is
               settled row by row while all three versions hold the same
               number of its rows; with differing counts, or no ancestor,
               its rows are weighed as one group and one row survives.

Class-3 (wholesale-regenerated structured: rules/learned-insights.md,
data/genome-shared.json, data/genomes/*.json) and Class-4 (local timestamp
markers) are NOT handled here - Class-3 uses the built-in `merge=ours`
(interpreter-free, git-crypt-safe; the regenerator re-emits) and Class-4 is
migrated into the already-gitignored state/ directory.

Pure Python 3 standard library only (collections, functools, json, sys). No
third-party imports, no bashisms - safe under Windows Git Bash (registered
via `python` vs `python3` detection in scripts/setup-local-git.sh).
"""
import collections
import functools
import json
import sys

# Terminal statuses outrank "pending"; among terminals, divergence is a real
# scientific disagreement and must be surfaced, not silently resolved.
_TERMINAL = {"confirmed", "refuted", "inconclusive", "expired"}

# How lib/hypothesis_engine.py writes a ledger row. The one record this driver
# alters (a `_merge_conflict` survivor) is written the same way, so a merge
# never brings a second byte form into the ledger.
_ROW_SEPARATORS = (",", ":")


def _read_lines(path):
    """Return list of (rstripped) non-empty lines; missing/empty file -> []."""
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return [ln.rstrip("\n") for ln in fh if ln.strip()]
    except (OSError, UnicodeDecodeError):
        return []


def _write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln + "\n")


def _merge_union(ours, theirs):
    """Class-1: dedup union, ours order first then theirs-only."""
    seen = set()
    out = []
    for ln in ours + theirs:
        if ln in seen:
            continue
        seen.add(ln)
        out.append(ln)
    return out


def _rank(rec):
    """Sort key for choosing the surviving record of a divergent id.
    Higher tuple wins. Terminal beats pending; then later verification_date;
    then later registered timestamp; strings sort lexicographically which is
    correct for ISO-8601."""
    status = str(rec.get("status", "")).lower()
    is_terminal = 1 if status in _TERMINAL else 0
    return (is_terminal, str(rec.get("verification_date", "")),
            str(rec.get("registered", "")))


def _key(line):
    """The merge key for one line, with its parsed record: the record's id, or
    the line itself when it is not a record that carries an id."""
    try:
        rec = json.loads(line)
    except (ValueError, TypeError):
        return ("line", line), None
    if not isinstance(rec, dict) or "id" not in rec:
        return ("line", line), None
    return ("id", json.dumps(rec["id"], sort_keys=True)), rec


def _parse(lines):
    """One version as [(key, line, record)], in file order."""
    out = []
    for ln in lines:
        key, rec = _key(ln)
        out.append((key, ln, rec))
    return out


def _groups(parsed):
    """{key: [(line, record)]}. An id that appears more than once keeps every
    one of its rows."""
    groups = {}
    for key, ln, rec in parsed:
        groups.setdefault(key, []).append((ln, rec))
    return groups


def _lines(group):
    return [ln for ln, _ in group]


def _same(a, b):
    """Two versions hold the same records for a key, whatever their bytes."""
    if a is None or b is None:
        return False
    return [ln if rec is None else rec for ln, rec in a] == \
        [ln if rec is None else rec for ln, rec in b]


def _three_way(ours, theirs, base):
    """The group that survives for one key when a side left it alone. None when
    both sides changed it, so the records have to be weighed."""
    if ours is None or theirs is None:
        return ours if theirs is None else theirs  # on one side only: it survives
    if _same(ours, theirs):
        # The same records. Prefer the bytes the ancestor had, so a branch that
        # an older driver rewrote returns to main's bytes on its next merge.
        if base is not None and _lines(theirs) == _lines(base):
            return theirs
        return ours
    if _same(ours, base):
        return theirs
    if _same(theirs, base):
        return ours
    return None


def _annotate(winner, loser):
    """A copy of ``winner`` that records the different verdict it displaced."""
    note = str(winner.get("notes", "")).strip()
    marker = ("[_merge_conflict] divergent terminal status "
              "across machines: kept %s, dropped %s"
              % (winner.get("status"), loser.get("status")))
    out = dict(winner)
    out["notes"] = (note + " " + marker).strip() if note else marker
    out["_merge_conflict"] = True
    return out


def _weigh(prev, cur):
    """Choose between two (line, record) pairs of one id. The line is kept only
    while the record is exactly what one side wrote; an annotated record has
    no line yet."""
    ps = str(prev[1].get("status", "")).lower()
    cs = str(cur[1].get("status", "")).lower()
    winner, loser = (cur, prev) if _rank(cur[1]) >= _rank(prev[1]) else (prev, cur)
    if ps in _TERMINAL and cs in _TERMINAL and ps != cs:
        return None, _annotate(winner[1], loser[1])
    return winner


def _fold(entries):
    """Reduce every (line, record) of one key, ours first, to one output line."""
    if entries[0][1] is None:
        return entries[0][0]  # a line that is not a record: one copy
    line, rec = functools.reduce(_weigh, entries)
    if line is not None:
        return line
    return json.dumps(rec, separators=_ROW_SEPARATORS)


def _reconcile_keyed(ours, theirs, base=()):
    """Class-2: three-way reconcile per record id.

    A record that only one side changed since the ancestor takes that side's
    rows. A record both sides changed is weighed: terminal status beats
    pending, then the later verification, and two different verdicts leave a
    `_merge_conflict` note on the survivor. Every row the merge does not
    alter is written back with the exact bytes it came in with, in ours'
    order, followed by rows only theirs holds.

    A row one side REMOVED comes back from the other side, as it always has.
    One case still loses a row: an id held more than once that BOTH sides
    changed, when the number of its rows differs between the three versions
    or there is no ancestor. Its rows cannot be paired then, so they are
    weighed as one group and one row survives. With the same number of rows
    in all three versions the id is settled row by row and none is lost."""
    mine, other = _parse(ours), _parse(theirs)
    o, t, b = _groups(mine), _groups(other), _groups(_parse(base))
    resolved = {key: _resolve(o.get(key), t.get(key), b.get(key))
                for key in set(o) | set(t)}
    return (_placed([key for key, _, _ in mine], resolved)
            + _placed([key for key, _, _ in other if key not in o], resolved))


def _resolve(ours, theirs, base):
    """The output rows for one key."""
    kept = _three_way(ours, theirs, base)
    if kept is not None:
        return _lines(kept)
    if base is not None and len(ours) == len(theirs) == len(base) > 1:
        # An id held more than once, changed on both sides, with every row
        # still present: settle it row by row, so a row only one side changed
        # is never weighed against a different row of the same id.
        return [_resolve([mine], [other], [was])[0]
                for mine, other, was in zip(ours, theirs, base)]
    return [_fold(ours + theirs)]


def _placed(keys, resolved):
    """Lay each key's resolved rows over one side's own line order: row for
    row while the key still has as many rows as that side held, so an id held
    twice keeps both rows where they were, and otherwise all of them at the
    key's first row. Every key in ``keys`` must be in ``resolved``; the caller
    builds ``resolved`` from the keys of both sides, so it is."""
    held = collections.Counter(keys)
    seen = collections.Counter()
    out = []
    for key in keys:
        rows, index = resolved[key], seen[key]
        seen[key] += 1
        if len(rows) == held[key]:
            out.append(rows[index])
        elif index == 0:
            out.extend(rows)
    return out


def main(argv):
    # All four of git's placeholders must arrive. With one missing the rest
    # shift left and cannot be told apart: a registration that dropped %O
    # used to be read as (ancestor=ours, ours=theirs), was written into
    # theirs' temporary file and exited 0, so git kept ours and lost theirs.
    if len(argv) < 6:
        sys.stderr.write(
            "substrate_merge.py: usage: <mode> %O %A %B %P "
            "(all four placeholders are required)\n")
        return 2
    mode, base_path, ours_path, theirs_path = argv[1], argv[2], argv[3], argv[4]
    ours = _read_lines(ours_path)
    theirs = _read_lines(theirs_path)
    if mode == "jsonl-union":
        result = _merge_union(ours, theirs)
    elif mode == "jsonl-keyed":
        result = _reconcile_keyed(ours, theirs, _read_lines(base_path))
    else:
        sys.stderr.write("substrate_merge.py: unknown mode %r\n" % mode)
        return 2
    try:
        _write_lines(ours_path, result)
    except OSError as exc:
        sys.stderr.write("substrate_merge.py: write failed: %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
