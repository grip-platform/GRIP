"""Congruent Continuity — PAIRING: join an outcome label to the trajectory
that preceded the decision, so a (feature, label) row exists.

Why this module exists. ``lib/continuity_labels.py`` mines outcome labels
from three stores and ``lib/continuity_features.py`` extracts a trajectory
vector from a session transcript, but nothing joined them, so the predictor
trio in ``lib/continuity_trio.py`` was a library nothing trained on and the
precision number that build-order decision D3 requires
(``docs/congruent-continuity-genesis.md:81``) could not be computed. A label
without a vector is BASE-RATE evidence only; counting it toward a predictor's
precision manufactures a measurement out of rows that cannot disagree with
the predictor. This module draws that line mechanically: a row is PAIRED only
when the transcript event that made the decision is located, and the vector
is extracted AS-OF that event (``extract_features(..., until=epoch)``), never
from the session's end.

Two join keys, both artefact-based, never the agent's prose:

- **git proxies** — a commit is paired when a transcript carries a Bash
  ``git commit`` tool_use whose command text contains the commit's subject
  line (``subject_key``: an ASCII prefix that survives JSON escaping). The
  decision instant is that event's timestamp. Subjects shorter than
  ``MIN_SUBJECT_CHARS`` are too ambiguous to key on and are reported as
  unpairable, never guessed. Candidate transcripts come from ONE ripgrep pass
  over the corpus (``rg -F -f``, fixed strings), and only candidates are
  parsed.
- **chain commitments** — a ``continuity-commit`` record is paired when its
  ``decision.intended_action.transcript_path`` names the transcript (the push
  recorder in ``lib/continuity_recorder.py`` writes it), else when a
  transcript mentions the record id within ``GRACE_S`` after the record's own
  timestamp. The creating session prints the id as it commits; a later
  session that merely READS the chain mentions the same id days later and is
  excluded by the window. The decision instant is the record's ``ts``.

Chain-state features (``chain_present`` and friends) are extracted with
``chain_path=None`` for every paired row: the chain's state AS-OF a past
instant is not reconstructible by the extractor, and reading today's state
into a past vector would be a leak.

LEAKAGE: pass ``exclude`` (transcript paths) to drop the session being
predicted, mirroring the rule in ``lib/continuity_labels.py``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.continuity_chain import (  # noqa: E402
    COMMIT_KIND,
    commit_addr_of,
    read_idr_chain,
    settlement_of,
)
from lib.continuity_features import _epoch, extract_features_many  # noqa: E402

__all__ = [
    "MIN_SUBJECT_CHARS",
    "SUBJECT_KEY_CHARS",
    "GRACE_S",
    "PairedRow",
    "subject_key",
    "commit_subjects",
    "rg_candidates",
    "locate_commit_events",
    "locate_id_mentions",
    "pair_git_rows",
    "pair_chain_rows",
]

MIN_SUBJECT_CHARS = 24
SUBJECT_KEY_CHARS = 60
GRACE_S = 600.0
_LABEL_OF_VERDICT = {"INCONGRUENT": 1, "CONGRUENT": 0}


@dataclass(frozen=True)
class PairedRow:
    source: str
    ref: str
    label: int
    transcript: str
    epoch: float
    features: dict[str, float]

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------

def subject_key(subject: str) -> str | None:
    """ASCII prefix of a commit subject that is safe to search for VERBATIM
    inside a JSONL transcript line: stops before the first quote, backslash,
    control or non-ASCII character (each is escaped differently by JSON) and
    at ``SUBJECT_KEY_CHARS``. ``None`` when fewer than ``MIN_SUBJECT_CHARS``
    survive — a short key matches prose as readily as the commit command."""
    out: list[str] = []
    for ch in subject:
        code = ord(ch)
        if ch in '"\\' or code < 32 or code > 126:
            break
        out.append(ch)
        if len(out) >= SUBJECT_KEY_CHARS:
            break
    key = "".join(out).rstrip()
    return key if len(key) >= MIN_SUBJECT_CHARS else None


def commit_subjects(repo: Path | str) -> dict[str, str]:
    """``{full_sha: subject}`` for every commit reachable from any ref."""
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--all", "--format=%H%x1f%s"],
        capture_output=True, text=True, check=True,
    ).stdout
    subjects: dict[str, str] = {}
    for line in out.splitlines():
        sha, sep, subject = line.partition("\x1f")
        if sep:
            subjects[sha] = subject
    return subjects


# --------------------------------------------------------------------------
# the one-pass candidate finder
# --------------------------------------------------------------------------

def rg_candidates(patterns: Iterable[str], transcripts: Iterable[Path | str]) -> dict[str, set[Path]]:
    """``{pattern: {transcript, ...}}`` for every fixed-string pattern that
    occurs in at least one transcript — a single ripgrep pass (``-F -f``)
    over the whole corpus. Requires ``rg`` on PATH (a ``RuntimeError`` naming
    ripgrep otherwise); a ripgrep failure other than "no match" raises."""
    pats = sorted({p for p in patterns if p})
    files = [str(t) for t in transcripts]
    if not pats or not files:
        return {}
    with tempfile.NamedTemporaryFile("w", suffix=".pat", delete=False) as fh:
        fh.write("\n".join(pats) + "\n")
        patfile = fh.name
    try:
        proc = subprocess.run(
            ["rg", "-F", "-f", patfile, "-o", "--with-filename", "--no-line-number", *files],
            capture_output=True, text=True, errors="replace",
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ripgrep (rg) is required for the one-pass candidate search; install it, "
            "or pass precomputed `candidates` to the pairing functions"
        ) from exc
    finally:
        os.unlink(patfile)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"rg failed ({proc.returncode}): {proc.stderr.strip()[:400]}")
    hits: dict[str, set[Path]] = {}
    for line in proc.stdout.splitlines():
        cut = line.find(".jsonl:")
        if cut < 0:
            continue
        path, match = line[: cut + 6], line[cut + 7:]
        if match in hits or match in pats:
            hits.setdefault(match, set()).add(Path(path))
    return hits


# --------------------------------------------------------------------------
# locating the decision event
# --------------------------------------------------------------------------

def _iter_records(transcript: Path, needle: str):
    """Yield parsed JSON objects for lines containing ``needle`` (cheap
    prefilter before the parse); damaged lines are skipped."""
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if needle not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def locate_commit_events(transcript: Path | str, keys: dict[str, str]) -> dict[str, float]:
    """``{sha: epoch}`` of the first Bash ``git commit`` tool_use whose
    command contains each subject key (``keys`` maps key -> sha)."""
    found: dict[str, float] = {}
    wanted = dict(keys)
    for rec in _iter_records(Path(transcript), "git commit"):
        if rec.get("type") != "assistant" or not wanted:
            continue
        epoch = _epoch(rec.get("timestamp"))
        if epoch is None:
            continue
        message = rec.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Bash":
                continue
            payload = block.get("input")
            command = payload.get("command") if isinstance(payload, dict) else None
            if not isinstance(command, str) or "git commit" not in command:
                continue
            for key in [k for k in wanted if k in command]:
                found.setdefault(wanted.pop(key), epoch)
    return found


def locate_id_mentions(transcript: Path | str, ids: dict[str, float], *, grace_s: float = GRACE_S) -> dict[str, float]:
    """``{record_id: epoch_of_mention}`` for each id whose first mention in
    ``transcript`` is timestamped within ``[ts, ts + grace_s]`` of the
    record's own timestamp (``ids`` maps id -> ts epoch). A mention outside
    that window is a reader, not the creator, and is ignored."""
    found: dict[str, float] = {}
    for rid, ts in ids.items():
        for rec in _iter_records(Path(transcript), rid):
            epoch = _epoch(rec.get("timestamp"))
            if epoch is None:
                continue
            if ts <= epoch <= ts + grace_s:
                found[rid] = epoch
            break  # first mention decides; earlier-than-ts is impossible for the creator
    return found


# --------------------------------------------------------------------------
# pairing
# --------------------------------------------------------------------------

def _transcript_list(transcripts: Iterable[Path | str], exclude: Iterable[Path | str]) -> list[Path]:
    excluded = {Path(e).resolve() for e in exclude}
    out = []
    for t in transcripts:
        p = Path(t)
        if p.resolve() not in excluded and p.is_file():
            out.append(p)
    return out


def pair_git_rows(
    rows: Sequence[dict],
    repo: Path | str,
    transcripts: Iterable[Path | str],
    *,
    window: int = 50,
    exclude: Iterable[Path | str] = (),
    candidates: dict[str, set[Path]] | None = None,
) -> tuple[list[PairedRow], dict]:
    """Pair ``mine_git`` rows to the transcript event that created each
    commit. Returns ``(paired_rows, stats)``; ``stats`` accounts for EVERY
    input row so the unpaired remainder is visible, never silently dropped.
    ``candidates`` may carry a precomputed ``rg_candidates`` result (one pass
    shared with other sources); ``None`` runs the pass here."""
    files = _transcript_list(transcripts, exclude)
    subjects = commit_subjects(repo)
    key_of_sha: dict[str, str] = {}
    stats = {"rows": len(rows), "no_subject": 0, "short_subject": 0,
             "candidate_commits": 0, "located": 0, "paired": 0,
             "paired_by_label": {0: 0, 1: 0}}
    label_of: dict[str, int] = {}
    for row in rows:
        sha = row["ref"]
        label_of[sha] = int(row["label"])
        subject = subjects.get(sha)
        if subject is None:
            stats["no_subject"] += 1
            continue
        key = subject_key(subject)
        if key is None:
            stats["short_subject"] += 1
            continue
        key_of_sha[sha] = key
    sha_of_key = {k: s for s, k in key_of_sha.items()}
    if candidates is None:
        candidates = rg_candidates(sha_of_key, files)
    allowed = set(files)
    by_file: dict[Path, dict[str, str]] = {}
    hit_shas: set[str] = set()
    for key, paths in candidates.items():
        sha = sha_of_key.get(key)
        if sha is None:
            continue
        for path in paths:
            if path in allowed:
                by_file.setdefault(path, {})[key] = sha
                hit_shas.add(sha)
    stats["candidate_commits"] = len(hit_shas)
    located: dict[str, tuple[Path, float]] = {}
    for path, keys in by_file.items():
        for sha, epoch in locate_commit_events(path, keys).items():
            prev = located.get(sha)
            if prev is None or epoch < prev[1]:
                located[sha] = (path, epoch)
    stats["located"] = len(located)
    # ripgrep visits files in thread order, so ``candidates`` and therefore
    # ``located`` arrive in an order that varies run to run; the cross-validation
    # folds are order-sensitive, so the pairing order is fixed here.
    per_file: dict[Path, list[tuple[str, float]]] = {}
    for sha, (path, epoch) in sorted(located.items(), key=lambda kv: (str(kv[1][0]), kv[1][1], kv[0])):
        per_file.setdefault(path, []).append((sha, epoch))
    paired: list[PairedRow] = []
    for path, items in per_file.items():
        vectors = extract_features_many(path, [e for _, e in items], chain_path=None, window=window)
        for (sha, epoch), features in zip(items, vectors):
            label = label_of[sha]
            paired.append(PairedRow("git", sha, label, str(path), epoch, features))
            stats["paired_by_label"][label] += 1
    stats["paired"] = len(paired)
    return paired, stats


def pair_chain_rows(
    chain_path: Path | str,
    transcripts: Iterable[Path | str],
    *,
    window: int = 50,
    exclude: Iterable[Path | str] = (),
    grace_s: float = GRACE_S,
    candidates: dict[str, set[Path]] | None = None,
) -> tuple[list[PairedRow], dict]:
    """Pair every SETTLED chain commitment (CONGRUENT -> 0, INCONGRUENT -> 1;
    UNVERIFIABLE and unsettled carry no label) to its creating transcript."""
    files = _transcript_list(transcripts, exclude)
    allowed = set(files)
    chain = Path(chain_path)
    nodes = read_idr_chain(path=chain) if chain.is_file() else []
    stats = {"commits": 0, "settled": 0, "unlabelled": 0, "declared_transcript": 0,
             "declared_missing": 0, "mention_search": 0, "located": 0, "paired": 0,
             "paired_by_label": {0: 0, 1: 0}}
    labelled: list[tuple[object, int]] = []
    for node in nodes:
        if node.kind != COMMIT_KIND:
            continue
        stats["commits"] += 1
        verdict = settlement_of(commit_addr_of(node), path=chain)
        label = _LABEL_OF_VERDICT.get(str(verdict.decision.get("verdict"))) if verdict is not None else None
        if verdict is not None:
            stats["settled"] += 1
        if label is None:
            stats["unlabelled"] += 1
            continue
        labelled.append((node, label))
    located: dict[str, tuple[Path, float, int, str]] = {}
    to_search: dict[str, tuple[object, int, float]] = {}
    for node, label in labelled:
        ts = _epoch(node.ts)
        if ts is None:
            stats["unlabelled"] += 1
            continue
        action = node.decision.get("intended_action")
        declared = action.get("transcript_path") if isinstance(action, dict) else None
        if isinstance(declared, str) and declared:
            stats["declared_transcript"] += 1
            path = Path(declared)
            if path in allowed:
                located[node.id] = (path, ts, label, commit_addr_of(node))
                continue
            stats["declared_missing"] += 1
        to_search[node.id] = (node, label, ts)
    stats["mention_search"] = len(to_search)
    if to_search:
        if candidates is None:
            candidates = rg_candidates(to_search, files)
        by_file: dict[Path, dict[str, float]] = {}
        for rid, (_node, _label, ts) in to_search.items():
            for path in candidates.get(rid, ()):
                if path in allowed:
                    by_file.setdefault(path, {})[rid] = ts
        for path, ids in by_file.items():
            for rid, _mention in locate_id_mentions(path, ids, grace_s=grace_s).items():
                node, label, ts = to_search[rid]
                if rid not in located:
                    located[rid] = (path, ts, label, commit_addr_of(node))
    stats["located"] = len(located)
    per_file: dict[Path, list[tuple[str, float, int, str]]] = {}
    for rid, (path, ts, label, addr) in sorted(located.items(), key=lambda kv: (str(kv[1][0]), kv[1][1], kv[0])):
        per_file.setdefault(path, []).append((rid, ts, label, addr))
    paired: list[PairedRow] = []
    for path, items in per_file.items():
        vectors = extract_features_many(path, [ts for _, ts, _, _ in items], chain_path=None, window=window)
        for (rid, ts, label, addr), features in zip(items, vectors):
            paired.append(PairedRow("chain", addr, label, str(path), ts, features))
            stats["paired_by_label"][label] += 1
    stats["paired"] = len(paired)
    return paired, stats
