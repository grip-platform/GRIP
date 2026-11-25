"""grip_ownership -- the Clean Ledger's reader: attribute every dirty file and stash.

lib/grip_dirty_snapshot.py writes one manifest per session under
<git-common-dir>/grip-ledger/. This module reads all of them against the live
state of a repository and answers one question per dirty path and per stash:
which session owns it?

File states:
    OWNED           one session claimed it, and it still looks the way that
                    session left it (status equal, and stat or sha256 equal)
    OWNED-DRIFTED   one session claimed it, and it has changed since
    CONTESTED       two or more sessions hold a claim on it
    UNATTRIBUTED    nobody claimed it -- dirtied by an editor, a Write/Edit
                    tool, bare `git`, or a session that predates the ledger

Stash states: OWNED when the subject carries a grip-stash:<sid>:... prefix
(the prefix is authoritative, so a stash stays attributed after its session's
manifest is gone), CONTESTED when the manifest recording that SHA belongs to a
different session than the prefix names, UNATTRIBUTED otherwise, with a `kind`
that says where a legacy stash probably came from.

Verdicts and exit codes, which bin/grip-ownership returns unchanged:
    ATTRIBUTED     0  every dirty file and stash has an owner (CONTESTED counts,
                      unless strict)
    UNATTRIBUTED   1  at least one does not
    INDETERMINATE  3  a manifest is corrupt -- reported, never read as "no
                      claims", because that would manufacture false UNATTRIBUTEDs
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from collections import Counter
from itertools import chain
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from grip_dirty_snapshot import (  # noqa: E402
    CorruptManifest,
    GitCtx,
    content_hash,
    dirty_status,
    fingerprint,
    list_stashes,
    load_manifest,
    parse_stash_subject,
)

OWNED = "OWNED"
DRIFTED = "OWNED-DRIFTED"
CONTESTED = "CONTESTED"
UNATTRIBUTED = "UNATTRIBUTED"
ATTRIBUTED = "ATTRIBUTED"
INDETERMINATE = "INDETERMINATE"
REPORT_SCHEMA = 1

# Worst first: a grouped row shows the worst state among its files.
_SEVERITY = (UNATTRIBUTED, CONTESTED, DRIFTED, OWNED)
_LEGACY_KINDS = (
    ("git-autostash", re.compile(r"(?:^|: )autostash$")),
    ("grip-legacy-tool", re.compile(r"\bgrip-(?:repair|update) auto-stash\b")),
    ("wip", re.compile(r"^WIP on ")),
)
# Case-insensitive on purpose: git writes "WIP on x:" for a bare stash but
# "On x:" for one made with -m.
_LEGACY_BRANCH = re.compile(r"^(?:WIP )?on ([^:]+):", re.IGNORECASE)


# ---------------------------------------------------------------------------
# manifests
# ---------------------------------------------------------------------------

def load_manifests(ledger: Path) -> tuple[dict, list[str]]:
    """({sid: manifest}, [corruption messages]) for every manifest in `ledger`."""
    manifests: dict = {}
    corrupt: list[str] = []
    if not ledger.is_dir():
        return manifests, corrupt
    for path in sorted(ledger.glob("*.json")):
        try:
            data = load_manifest(path)
        except CorruptManifest as exc:
            corrupt.append(str(exc))
            continue
        if data is not None:
            manifests[data["session_id"]] = data
    return manifests, corrupt


def _claims_of(sid: str, manifest: dict) -> Iterable[tuple[str, dict]]:
    return ((sid, claim) for claim in manifest.get("claims", {}).values())


def all_claims(manifests: dict) -> Iterable[tuple[str, dict]]:
    """Every (sid, claim) across all manifests: one linear pass over the claims."""
    return chain.from_iterable(_claims_of(sid, m) for sid, m in manifests.items())


def claims_for_tree(manifests: dict, tree: str) -> dict[str, list[tuple[str, dict]]]:
    """relpath -> [(sid, claim)] for every claim recorded against `tree`."""
    by_path: dict[str, list[tuple[str, dict]]] = {}
    for sid, claim in all_claims(manifests):
        if claim.get("tree") == tree:
            by_path.setdefault(claim.get("path", ""), []).append((sid, claim))
    return by_path


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

class _HashCache:
    """Current sha256 per path, computed at most once and only when asked."""

    def __init__(self, tree: str) -> None:
        self.tree = tree
        self.cache: dict[str, Optional[str]] = {}

    def get(self, rel: str) -> Optional[str]:
        if rel not in self.cache:
            self.cache[rel] = content_hash(self.tree, rel)
        return self.cache[rel]


def claim_matches(claim: dict, xy: str, current: tuple,
                  current_hash: Callable[[], Optional[str]]) -> bool:
    """Does the path still look the way the claim recorded it?

    Status must match; then the cheap stat tuple, and only on a stat mismatch
    the sha256 -- so a touch that leaves the bytes alone keeps ownership, and
    a rewrite of the same length does not.
    """
    if claim.get("status") != xy:
        return False
    if (claim.get("size"), claim.get("mtime_ns")) == tuple(current):
        return True
    return claim.get("sha256") == current_hash()


def file_state(owners: Sequence[dict]) -> str:
    if not owners:
        return UNATTRIBUTED
    if len({owner["sid"] for owner in owners}) > 1:
        return CONTESTED
    return OWNED if owners[0]["match"] else DRIFTED


def attribute_file(tree: str, rel: str, xy: str, claims: Sequence[tuple[str, dict]],
                   hashes: _HashCache) -> dict:
    current = fingerprint(tree, rel)
    owners = [{"sid": sid, "at": claim.get("at"), "op": claim.get("op"),
               "match": claim_matches(claim, xy, current, lambda: hashes.get(rel))}
              for sid, claim in claims]
    return {"path": rel, "status": xy, "state": file_state(owners), "owners": owners}


def attribute_files(tree: str, status: dict[str, str], manifests: dict) -> list[dict]:
    claims = claims_for_tree(manifests, tree)
    hashes = _HashCache(tree)
    return [attribute_file(tree, rel, xy, claims.get(rel, []), hashes)
            for rel, xy in sorted(status.items())]


def stale_claims(manifests: dict, tree: str, status: dict[str, str]) -> int:
    """Claims on paths that are clean now -- harmless, counted for the record."""
    return sum(len(claims) for rel, claims in claims_for_tree(manifests, tree).items()
               if rel not in status)


def scan_tree(ctx: GitCtx, manifests: dict) -> dict:
    """Attribution for every dirty path in the context's working tree."""
    tree = ctx.toplevel()
    status = dirty_status(ctx, "all")
    rolled = [rel for rel, xy in dirty_status(ctx, "normal").items()
              if xy == "??" and rel.endswith("/")]
    return {"tree": tree, "branch": ctx.branch_label(), "missing": False,
            "files": attribute_files(tree, status, manifests),
            "untracked_dirs": sorted(rolled),
            "stale_claims": stale_claims(manifests, tree, status)}


def missing_tree(path: str) -> dict:
    return {"tree": path, "branch": None, "missing": True, "files": [],
            "untracked_dirs": [], "stale_claims": 0}


def worktree_paths(ctx: GitCtx) -> list[str]:
    """Every registered worktree, main first, from `git worktree list --porcelain`."""
    raw = ctx.run("worktree", "list", "--porcelain")
    return [line[len("worktree "):] for line in raw.splitlines()
            if line.startswith("worktree ")]


# ---------------------------------------------------------------------------
# stashes
# ---------------------------------------------------------------------------

def classify_legacy(subject: str) -> str:
    """Where an unprefixed stash probably came from."""
    for kind, pattern in _LEGACY_KINDS:
        if pattern.search(subject):
            return kind
    return "freeform"


def legacy_branch(subject: str) -> str:
    match = _LEGACY_BRANCH.search(subject)
    return match.group(1).strip() if match else "(unknown)"


def attribute_stash(entry: dict, recorded: dict[str, str]) -> dict:
    """Owner of one stash: the prefix decides; the manifests cross-check."""
    manifest_sid = recorded.get(entry["sha"])
    base = {"sha": entry["sha"], "ref": entry["ref"],
            "in_manifest": manifest_sid is not None, "manifest_sid": manifest_sid}
    parsed = parse_stash_subject(entry["subject"])
    if parsed is None:
        return {**base, "state": UNATTRIBUTED, "kind": classify_legacy(entry["subject"]),
                "sid": None, "branch": legacy_branch(entry["subject"]),
                "reason": entry["subject"]}
    disagree = manifest_sid is not None and manifest_sid != parsed["sid"]
    return {**base, "state": CONTESTED if disagree else OWNED, "kind": "grip",
            "sid": parsed["sid"], "branch": parsed["branch"],
            "reason": parsed["reason"]}


def recorded_stashes(manifests: dict) -> dict[str, str]:
    """stash SHA -> the session whose manifest recorded it."""
    recorded: dict[str, str] = {}
    for sid, manifest in manifests.items():
        recorded.update(dict.fromkeys(manifest.get("stashes", {}), sid))
    return recorded


def attribute_stashes(stashes: Sequence[dict], manifests: dict) -> list[dict]:
    recorded = recorded_stashes(manifests)
    return [attribute_stash(entry, recorded) for entry in stashes]


def scan_stashes(ctx: GitCtx, manifests: dict) -> list[dict]:
    return attribute_stashes(list_stashes(ctx), manifests)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def summarise(trees: Sequence[dict], stashes: Sequence[dict]) -> dict:
    files = list(chain.from_iterable(tree["files"] for tree in trees))
    by_file = Counter(item["state"] for item in files)
    by_stash = Counter(item["state"] for item in stashes)
    return {"files": len(files),
            "unattributed_files": by_file[UNATTRIBUTED],
            "contested": by_file[CONTESTED],
            "drifted": by_file[DRIFTED],
            "stale_claims": sum(tree["stale_claims"] for tree in trees),
            "stashes": len(stashes),
            "unattributed_stashes": by_stash[UNATTRIBUTED],
            "contested_stashes": by_stash[CONTESTED]}


def verdict(summary: dict, corrupt: Sequence[str], strict: bool) -> tuple[str, int]:
    if corrupt:
        return INDETERMINATE, 3
    unowned = summary["unattributed_files"] + summary["unattributed_stashes"]
    if strict:
        unowned += summary["contested"] + summary["contested_stashes"]
    return (UNATTRIBUTED, 1) if unowned else (ATTRIBUTED, 0)


def build_report(trees: Sequence[dict], stashes: Sequence[dict], corrupt: Sequence[str],
                 session_id: Optional[str], strict: bool = False) -> dict:
    summary = summarise(trees, stashes)
    state, code = verdict(summary, corrupt, strict)
    return {"schema": REPORT_SCHEMA, "session_id": session_id, "trees": list(trees),
            "stashes": list(stashes), "corrupt_manifests": list(corrupt),
            "summary": summary, "verdict": state, "exit_code": code}


# ---------------------------------------------------------------------------
# human table
# ---------------------------------------------------------------------------

def _short(sid: Optional[str], you: Optional[str]) -> str:
    if not sid:
        return "-"
    return sid[:8] + (" (you)" if sid == you else "")


def _age(at: Optional[str], now: Optional[_dt.datetime] = None) -> str:
    try:
        then = _dt.datetime.strptime(at or "", "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return "-"
    seconds = int(((now or _dt.datetime.now(_dt.timezone.utc)) - then).total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{max(seconds, 0)}s"


def _file_row(item: dict, you: Optional[str]) -> str:
    owners = item["owners"]
    who = ",".join(_short(o["sid"], you) for o in owners) or "-"
    latest = max(owners, key=lambda o: o.get("at") or "") if owners else {}
    return (f"  {item['state']:<13} {item['status']:<3} {who:<22} "
            f"{_age(latest.get('at')):>4} {latest.get('op') or '-':<8} {item['path']}")


def _group_row(directory: str, items: Sequence[dict]) -> str:
    counts = Counter(item["state"] for item in items)
    present = [state for state in _SEVERITY if counts[state]]
    detail = ", ".join(f"{counts[state]} {state.lower()}" for state in present)
    return f"  {present[0]:<13} ??  {len(items)} files: {detail:<40} {directory}"


def _home_group(path: str, groups: dict) -> Optional[str]:
    """The collapsed untracked directory holding `path`, by ancestor lookup.

    `git status -unormal` collapses at the top-most untracked directory, so
    at most one ancestor of a path can be a group key: O(depth), not O(groups).
    """
    parts = path.split("/")[:-1]
    for depth in range(1, len(parts) + 1):
        candidate = "/".join(parts[:depth]) + "/"
        if candidate in groups:
            return candidate
    return None


def _group_files(tree: dict) -> tuple[list[dict], dict[str, list[dict]]]:
    """Split a tree's files into single rows and untracked-directory groups."""
    groups: dict[str, list[dict]] = {d: [] for d in tree["untracked_dirs"]}
    singles: list[dict] = []
    for item in tree["files"]:
        home = _home_group(item["path"], groups) if item["status"] == "??" else None
        if home is None:
            singles.append(item)
        else:
            groups[home].append(item)
    return singles, groups


def render_tree(tree: dict, you: Optional[str]) -> list[str]:
    head = f"== {tree['tree']}"
    if tree["missing"]:
        return [f"{head}  MISSING (a registered worktree whose path is gone)"]
    lines = [f"{head}  ({tree['branch']})  {len(tree['files'])} dirty"]
    if not tree["files"]:
        return lines + ["  (clean)"]
    singles, groups = _group_files(tree)
    lines.append(f"  {'STATE':<13} {'XY':<3} {'OWNER':<22} {'AGE':>4} {'OP':<8} PATH")
    lines += [_file_row(item, you) for item in singles]
    lines += [_group_row(d, items) for d, items in sorted(groups.items()) if items]
    return lines


def _stash_row(item: dict, you: Optional[str]) -> str:
    detail = item["reason"] if item["kind"] == "grip" else f"[{item['kind']}] {item['reason']}"
    return (f"  {item['state']:<13} {item['sha'][:10]:<10} {item['ref']:<11} "
            f"{_short(item['sid'], you):<16} {item['branch']:<24} {detail}")


def render_stashes(stashes: Sequence[dict], you: Optional[str]) -> list[str]:
    if not stashes:
        return ["== stashes  (none)"]
    header = (f"  {'STATE':<13} {'SHA':<10} {'REF':<11} {'OWNER':<16} "
              f"{'BRANCH':<24} REASON / KIND")
    return [f"== stashes  {len(stashes)}", header] + [_stash_row(i, you) for i in stashes]


def render_table(report: dict) -> str:
    you = report["session_id"]
    lines: list[str] = list(chain.from_iterable(
        render_tree(tree, you) for tree in report["trees"]))
    lines += render_stashes(report["stashes"], you)
    lines += [f"CORRUPT  {message}" for message in report["corrupt_manifests"]]
    summary = report["summary"]
    lines.append(
        f"{report['verdict']}: {summary['files']} dirty ({summary['unattributed_files']} "
        f"unattributed, {summary['contested']} contested, {summary['drifted']} drifted), "
        f"{summary['stashes']} stashes ({summary['unattributed_stashes']} unattributed)"
        f" -> exit {report['exit_code']}"
    )
    return "\n".join(lines)
