"""Congruent Continuity — cold-start LABEL MINING for the statistical layer (L1).

The predictor trio (CONGRUENCE.md:137-152) needs supervised labels before the
settlement worker has accumulated a live corpus. This module mines the outcome
labels GRIP ALREADY OWNS — artefacts and outcomes only, never the agent's prose
self-reports (the banned signal class; "nothing in L1 may consume the agent's
prose self-reports", CONGRUENCE.md:147):

(a) hypothesis-ledger verdicts — ``learning/hypotheses.jsonl`` rows whose
    ``status`` is confirmed/refuted (path: lib/hypothesis_engine.py:49;
    status vocabulary: lib/hypothesis_engine.py:92). refuted -> 1,
    confirmed -> 0; pending / inconclusive / expired carry no outcome and are
    skipped. Status is compared case-insensitively because the store is
    edited out-of-band (lib/hypothesis_engine.py:114-116) while the engine
    itself writes lowercase (lib/hypothesis_engine.py:883).
(b) git wrong-track proxies — a commit is labelled 1 when it was reverted
    (revert-commit body "This reverts commit <sha>") or when a fix-titled
    commit touching at least one of the same files landed within the next
    ``window`` commits. A non-correction commit that survived a full window
    unmarked is labelled 0 (held). Correction commits themselves (fix/revert
    subjects) are evidence carriers, not subjects, and emit no row of their
    own unless they were later marked wrong in turn. Commits with fewer than
    ``window`` successors in the scanned range are right-censored: their
    held-ness is not yet established, so they emit NO row.
(c) continuity-verdict records — the chain's own settlements
    (lib/continuity_chain.py:64-70): INCONGRUENT -> 1, CONGRUENT -> 0,
    UNVERIFIABLE is abstention, not a label. The LATEST verdict per
    commitment wins, mirroring ``settlement_of``
    (lib/continuity_chain.py:255-261).

Row schema — every row carries the same six keys:

    {"source":   "hypothesis" | "git" | "chain",
     "label":    1 (wrong-track / refuted)  or  0 (held),
     "ref":      hypothesis id | commit sha | commitment content_addr,
     "ts":       ISO-8601 where the artefact carries one, else "",
     "session":  chain-file stem for chain rows (the session-namespace key,
                 lib/continuity_chain.py:81-88 + lib/context_head.py:29-51);
                 None for git/hypothesis rows,
     "evidence": auditable pointer to the labelling artefact — the fixing or
                 reverting sha (git), the verdict node id (chain), None
                 (hypothesis: the ledger row named by ``ref`` IS the artefact)}

LEAKAGE RULE (documented contract): a consumer training a predictor for a
session must NEVER train on rows mined from the session being predicted —
exclude every row whose ``session`` equals the target session's chain-file
stem, and any row whose ``ref`` was authored by that session. Rows carry
``session`` and ``ref`` precisely so this exclusion is mechanical, not
judgement-based.

Per-source ``ts`` semantics: hypothesis -> ``verification_date`` falling back
to ``registered``; git -> the subject commit's committer time (for a revert
target outside the scanned range, the reverting commit's time); chain -> the
verdict node's ``ts``. All store reads are fail-open — a missing file yields
zero rows (lib/precog/idr.py:388-389 for the chain; mirrored here for the
ledger). Only a failing ``git`` invocation raises, loudly: silence on a
mistyped repo path would be a signal that cannot disagree with reality.

This module is read-only by construction: it opens stores, spawns read-only
``git log`` subprocesses, and writes nothing anywhere.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path

from lib.continuity_chain import VERDICT_KIND
from lib.continuity_chain import chain_path as _resolve_chain
from lib.continuity_chain import read_continuity_chain

__all__ = [
    "LABEL_WRONG",
    "LABEL_HELD",
    "build_dataset",
    "mine_hypotheses",
    "mine_git",
    "mine_chain",
]

LABEL_WRONG = 1  # wrong-track / refuted
LABEL_HELD = 0   # held / confirmed

# Ledger status -> label (lib/hypothesis_engine.py:92 vocabulary; compared
# lowercased). Everything else (pending/inconclusive/expired) is unlabelled.
_HYP_LABELS = {"refuted": LABEL_WRONG, "confirmed": LABEL_HELD}

# Chain verdict -> label (lib/continuity_chain.py:67). UNVERIFIABLE absent by
# design: abstention is a first-class outcome, not a class.
_VERDICT_LABELS = {"INCONGRUENT": LABEL_WRONG, "CONGRUENT": LABEL_HELD}

# Subject-line proxies. ``\b`` keeps "fixture(s)" / "reverting" out: a word
# character follows the stem there, so the boundary cannot match.
_FIX_RE = re.compile(r"^(fix|bugfix|hotfix)\b", re.IGNORECASE)
_REVERT_RE = re.compile(r"^revert\b", re.IGNORECASE)
# ``git revert`` writes the full 40-hex sha; hand-written reverts may use a
# short one, so accept 7..40 and prefix-match against scanned entries.
_REVERTS_COMMIT_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})")

# Record/unit separators for one-call git log parsing. Control chars never
# appear in normal commit metadata, so the split is unambiguous in practice.
_RS, _US = "\x1e", "\x1f"


def _row(source: str, label: int, ref: str, ts: str, *,
         session: str | None = None, evidence: str | None = None) -> dict:
    return {"source": source, "label": label, "ref": ref, "ts": ts,
            "session": session, "evidence": evidence}


def _default_hypothesis_path() -> Path:
    # Mirrors HYPOTHESIS_FILE (lib/hypothesis_engine.py:47-49) without
    # importing the engine module for one constant. Resolved at call time so
    # a test-set CLAUDE_DIR is honoured.
    base = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
    return base / "learning" / "hypotheses.jsonl"


def _hypothesis_row(line: str) -> dict | None:
    """One ledger line -> row, or None when the line carries no label."""
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(rec, dict):
        return None
    label = _HYP_LABELS.get(str(rec.get("status", "")).lower())
    ref = str(rec.get("id") or "").strip()
    if label is None or not ref:
        return None
    ts = str(rec.get("verification_date") or rec.get("registered") or "")
    return _row("hypothesis", label, ref, ts)


def mine_hypotheses(path: Path | str | None = None) -> list[dict]:
    """Label rows from the hypothesis ledger. Fail-open per line: malformed
    JSON, non-dict records, unlabelled statuses, and id-less rows are skipped
    (the store predates strict typing, lib/hypothesis_engine.py:114-116)."""
    p = Path(path) if path is not None else _default_hypothesis_path()
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        row = _hypothesis_row(line.strip()) if line.strip() else None
        if row is not None:
            rows.append(row)
    return rows


def _iso(unix: str) -> str:
    try:
        stamp = datetime.fromtimestamp(int(unix), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return ""
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise ValueError(
            f"git {' '.join(args[:2])} failed in {repo}: "
            f"{proc.stderr.strip()[:200]}"
        )
    return proc.stdout


def _parse_block(block: str) -> dict:
    """One %x1e-delimited ``git log --name-only`` record -> entry dict."""
    lines = block.splitlines()
    sha, unix_ts, subject = (lines[0].split(_US, 2) + ["", ""])[:3]
    files = {ln.strip() for ln in lines[1:] if ln.strip()}
    return {"sha": sha, "ts": _iso(unix_ts), "subject": subject, "files": files}


def _log_entries(repo: Path, limit: int | None) -> list[dict]:
    """Oldest-first [{sha, ts, subject, files}] from ONE ``git log`` call."""
    args = ["log", "--no-merges",
            f"--pretty=format:%x1e%H{_US}%ct{_US}%s", "--name-only"]
    if limit is not None:
        args += ["-n", str(limit)]
    entries = [_parse_block(b) for b in _git(repo, *args).split(_RS) if b.strip()]
    entries.reverse()
    return entries


def _note_targets(targets: dict[str, str], body: str, evidence: str) -> None:
    """Record each sha the revert body names; the first evidence wins."""
    for target in _REVERTS_COMMIT_RE.findall(body):
        targets.setdefault(target, evidence)


def _revert_targets(repo: Path, entries: list[dict],
                    limit: int | None) -> dict[str, str]:
    """{reverted sha (as written in the body) -> reverting sha}. One extra
    ``git log`` call, made only when a revert-titled subject exists. A revert
    whose body names no sha is unattributable and contributes nothing."""
    reverts = [e for e in entries if _REVERT_RE.match(e["subject"])]
    if not reverts:
        return {}
    args = ["log", "--no-merges", f"--pretty=format:%x1e%H{_US}%B"]
    if limit is not None:
        args += ["-n", str(limit)]
    bodies: dict[str, str] = {}
    for block in _git(repo, *args).split(_RS):
        if _US in block:
            sha, body = block.split(_US, 1)
            bodies[sha.strip()] = body
    targets: dict[str, str] = {}
    for rev in reverts:  # oldest-first: the earliest revert evidence wins
        _note_targets(targets, bodies.get(rev["sha"], ""), rev["sha"])
    return targets


def _index_files(touched: dict[str, list[int]], files: set[str], i: int) -> None:
    """Append commit index ``i`` to each of its files' (sorted) index lists."""
    for f in files:
        touched.setdefault(f, []).append(i)


def _mark_window(marked: dict[str, str], entries: list[dict],
                 touched: dict[str, list[int]], fix: dict, i: int,
                 window: int) -> None:
    """Mark prior commits within [i-window, i) sharing a file with ``fix``.

    Per-file index lists are appended in increasing order, so bisect finds
    the window tail in O(log n) — the pass stays O(n) overall (window and
    files-per-commit are small constants)."""
    lo = i - window
    hits: set[int] = set()
    for f in fix["files"]:
        lst = touched.get(f, [])
        hits.update(lst[bisect_left(lst, lo):])
    for j in sorted(hits):
        marked.setdefault(entries[j]["sha"], fix["sha"])


def _fix_marks(entries: list[dict], window: int) -> dict[str, str]:
    """{subject sha -> fixing sha} for the fix-follow-up proxy."""
    marked: dict[str, str] = {}
    touched: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        if _FIX_RE.match(e["subject"]):
            _mark_window(marked, entries, touched, e, i, window)
        _index_files(touched, e["files"], i)
    return marked


def _match_sha(shas_sorted: list[str], sha_set: set[str],
               target: str) -> str | None:
    """Resolve a (possibly short) sha against the scanned entries: exact set
    hit first, else sorted-list bisect prefix match."""
    if target in sha_set:
        return target
    i = bisect_left(shas_sorted, target)
    if i < len(shas_sorted) and shas_sorted[i].startswith(target):
        return shas_sorted[i]
    return None


def _revert_marks(entries: list[dict],
                  targets: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """(in-range marks: subject sha -> reverting sha, unresolved: out-of-range
    target -> reverting sha)."""
    sha_set = {e["sha"] for e in entries}
    shas_sorted = sorted(sha_set)
    marked: dict[str, str] = {}
    unresolved: dict[str, str] = {}
    for target, evidence in targets.items():
        full = _match_sha(shas_sorted, sha_set, target)
        if full is None:
            unresolved[target] = evidence
        else:
            marked.setdefault(full, evidence)
    return marked, unresolved


def _is_correction(subject: str) -> bool:
    return bool(_FIX_RE.match(subject) or _REVERT_RE.match(subject))


def mine_git(repo: Path | str, *, window: int = 5,
             limit: int | None = None) -> list[dict]:
    """Wrong-track proxy rows from git history (module docstring, source b).

    Emits, in oldest-first commit order: label 1 for every marked commit
    (reverted, or fix-followed-within-``window`` sharing a file — fix
    evidence wins when both apply); label 0 for every non-correction commit
    that survived >= ``window`` successors unmarked; nothing for
    right-censored or correction commits. Revert targets outside the scanned
    range are appended last with the reverting commit's timestamp. Raises
    ValueError when ``git`` itself fails (not a repo, empty repo) — a silent
    [] there would fake a clean history.
    """
    repo = Path(repo)
    entries = _log_entries(repo, limit)
    marked = _fix_marks(entries, window)
    rev_marked, unresolved = _revert_marks(
        entries, _revert_targets(repo, entries, limit))
    for sha, evidence in rev_marked.items():
        marked.setdefault(sha, evidence)
    ts_of = {e["sha"]: e["ts"] for e in entries}
    rows: list[dict] = []
    last = len(entries) - 1
    for i, e in enumerate(entries):
        if e["sha"] in marked:
            rows.append(_row("git", LABEL_WRONG, e["sha"], e["ts"],
                             evidence=marked[e["sha"]]))
        elif not _is_correction(e["subject"]) and (last - i) >= window:
            rows.append(_row("git", LABEL_HELD, e["sha"], e["ts"]))
    for target, evidence in unresolved.items():
        rows.append(_row("git", LABEL_WRONG, target, ts_of.get(evidence, ""),
                         evidence=evidence))
    return rows


def mine_chain(chain_path: Path | str | None = None) -> list[dict]:
    """Label rows from continuity-verdict records (module docstring, source c).

    ``chain_path=None`` resolves to the live session-namespaced chain
    (read-only). The latest verdict per commitment wins
    (lib/continuity_chain.py:255-261); a commitment whose latest verdict is
    UNVERIFIABLE emits no row. ``session`` is the chain file's stem — the
    leakage-exclusion key. ``evidence`` is the verdict node's id.
    """
    resolved = _resolve_chain(Path(chain_path) if chain_path is not None else None)
    latest: dict[str, object] = {}
    for node in read_continuity_chain(path=resolved):
        if node.kind != VERDICT_KIND:
            continue
        addr = str(node.decision.get("commit_addr", ""))
        if addr:
            latest[addr] = node
    rows: list[dict] = []
    for addr, node in latest.items():
        label = _VERDICT_LABELS.get(str(node.decision.get("verdict", "")))
        if label is not None:
            rows.append(_row("chain", label, addr, node.ts,
                             session=resolved.stem, evidence=node.id))
    return rows


def build_dataset(*, repo: Path | str | None = None,
                  chain_path: Path | str | None = None,
                  hypothesis_path: Path | str | None = None,
                  window: int = 5,
                  git_limit: int | None = None) -> list[dict]:
    """Concatenate all mined label rows: hypothesis, then git, then chain.

    ``repo=None`` skips the git source (there is no canonical single repo to
    default to); ``chain_path=None`` / ``hypothesis_path=None`` fall back to
    the live stores, read-only. A missing store contributes zero rows.

    LEAKAGE RULE: a consumer must never train on rows from the session being
    predicted. Every row carries ``session`` (chain rows: the chain-file
    stem; git/hypothesis rows: None) and ``ref`` so the caller can exclude
    the target session's rows mechanically before training.
    """
    rows = mine_hypotheses(hypothesis_path)
    if repo is not None:
        rows += mine_git(repo, window=window, limit=git_limit)
    rows += mine_chain(chain_path)
    return rows
