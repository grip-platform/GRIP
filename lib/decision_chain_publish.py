#!/usr/bin/env python3
"""decision_chain_publish.py — the dedicated decision-chain publisher.

Renders GRIP's signed public IDR chain (``data/idr-public.jsonl``) into the
self-contained static site that ships to the public gh-pages repo
``CodeTonight-SA/grip-decision-chain`` (live at
https://codetonight-sa.github.io/grip-decision-chain/), idempotently.

It is the *render → build → publish* half that the emitter
(``lib/idr_public_emitter.py``) and the sync script
(``bin/grip-chain-sync``) left implicit:

  - the emitter OWNS the chain (allowlist {sha,subject,ts,prev_sha,
    what,why,how,when}, leak scan, prev_sha verify) — reused verbatim here,
    never re-implemented;
  - ``bin/grip-chain-sync`` clones + fetch/reset/diff-gates + pushes the JSONL —
    its publish mechanics are mirrored here for the WHOLE rendered tree.

THE BANACH CONTRACT (the reason this module exists):

    publish(publish(chain)) == publish(chain)      — byte-identical site
    unchanged chain  ⇒  no git diff  ⇒  NO spurious commit

``render_site(rows, dest)`` is a PURE deterministic function of ``rows``: same
chain in ⇒ byte-identical tree out. ``selftest_idempotent()`` proves the fixed
point and FAILS LOUDLY (AssertionError) on any drift — mirroring
``lib/learning_proposer.selftest_idempotent``.

SAFETY — dry-run by default:
  - ``dry_run_publish()`` renders to a TEMP dir, verifies chain + leak + the
    Banach fixed point, and NEVER touches the public repo. This is the build.
  - ``live_publish()`` is the SEPARATELY-GATED irreversible public action: it
    mirrors the rendered tree into the clone, diff-gates, and pushes only when
    the tree actually changed. A build can never reach a push.
  - The public commit names the publisher's role identity, never the machine's
    git identity, and the push goes through ``chain_push`` in
    ``scripts/lib/chain-push.sh``: as the GitHub App, or not at all. It is the
    gate ``bin/grip-chain-sync`` and ``bin/grip-chain-anchor`` push through too.

Usage:
    venv/bin/python -m lib.decision_chain_publish dry-run     # build + verify, no push
    venv/bin/python -m lib.decision_chain_publish selftest    # prove the fixed point
    venv/bin/python -m lib.decision_chain_publish live        # GATED: render + push if changed
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
import idr_public_emitter as emitter  # noqa: E402

CLAUDE_DIR = get_claude_dir()
LEDGER = CLAUDE_DIR / "data" / "idr-public.jsonl"
ASSETS = _LIB / "decision_chain_assets"
# The static template assets copied verbatim into every published tree. They are
# version-controlled HERE (this repo) as the source of truth; the publisher owns
# them. Order is fixed so the manifest is deterministic.
_STATIC_ASSETS = ("index.html", "verify.js", "README.md")
# The chain-derived artifact — the ONLY file that varies with the chain.
_CHAIN_FILE = "idr-public.jsonl"

# Public gh-pages mirror (same defaults as bin/grip-chain-sync; env-overridable).
DEFAULT_REPO = "https://github.com/CodeTonight-SA/grip-decision-chain.git"
# The one push gate for the public chain, in the checkout that holds this file.
CHAIN_PUSH = _LIB.parent / "scripts" / "lib" / "chain-push.sh"
# The public commit's author and committer: a role identity, never a person. The
# defaults and the GRIP_CHAIN_AUTHOR_NAME / GRIP_CHAIN_AUTHOR_EMAIL overrides are
# the ones bin/grip-chain-sync and bin/grip-chain-anchor use.
_AUTHOR_NAME_DEFAULT = "GRIP Chain Sync"
_AUTHOR_EMAIL_DEFAULT = "development@codetonight.co.za"


# ---------------------------------------------------------------------------
# Read side — the chain is the source of truth
# ---------------------------------------------------------------------------


def load_rows(ledger: Path = LEDGER) -> list[dict]:
    """Parse the allowlisted JSONL ledger into rows (torn final line tolerated).

    The ledger is the canonical chain; we never mutate it here — the emitter is
    the sole writer. Reading is order-preserving (the chain reads root→head)."""
    if not ledger.exists():
        return []
    rows = []
    for line in ledger.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # torn line — never fatal (matches emitter tolerance)
    return rows


def _canonical_jsonl(rows: list[dict]) -> str:
    """Deterministic JSONL serialisation: one row per line, keys in the emitter's
    canonical allowlist order, only keys present (old 4-key + new 8-key rows
    both round-trip). Pure function of ``rows`` — the heart of byte-identity."""
    lines = []
    for row in rows:
        ordered = {k: row[k] for k in emitter._FIELDS if k in row}
        lines.append(json.dumps(ordered))
    return "\n".join(lines) + ("\n" if lines else "")


# ---------------------------------------------------------------------------
# Render side — PURE: same rows in ⇒ byte-identical tree out
# ---------------------------------------------------------------------------


def render_site(rows: list[dict], dest: Path) -> Path:
    """Render the full publish tree into ``dest`` (created if absent).

    Deterministic by construction:
      - the chain file is the canonical JSONL of ``rows`` (sorted-key, fixed
        order) — the only chain-derived byte;
      - the static assets are copied verbatim from the vendored source.
    Returns ``dest``. Re-rendering the same ``rows`` yields a byte-identical
    tree (the Banach fixed point ``render`` half).
    """
    dest.mkdir(parents=True, exist_ok=True)
    (dest / _CHAIN_FILE).write_text(_canonical_jsonl(rows))
    for name in _STATIC_ASSETS:
        shutil.copyfile(ASSETS / name, dest / name)
    return dest


def tree_manifest(root: Path) -> dict[str, str]:
    """SHA-256 of every published file, keyed by name — the byte-identity probe.

    Two renders are the same site iff their manifests are equal. Used by the
    Banach selftest and as the human-readable fingerprint a dry-run reports."""
    manifest = {}
    for name in (_CHAIN_FILE, *_STATIC_ASSETS):
        path = root / name
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
    return manifest


# ---------------------------------------------------------------------------
# Pre-publish gates — reuse the emitter's tested chain + leak contracts
# ---------------------------------------------------------------------------


def verify_publishable(rows: list[dict], ledger: Path = LEDGER) -> tuple[bool, str]:
    """(ok, reason). A chain is publishable iff it has rows, the prev_sha chain
    verifies, and no free-text field carries a leak marker. Delegates to the
    emitter's tested functions — the single source of truth for both checks."""
    if not rows:
        return False, "empty chain — nothing to publish"
    if not emitter.verify_chain(ledger):
        return False, "prev_sha chain BROKEN — refusing to publish"
    leaks = emitter.subjects_with_leaks(ledger)
    if leaks:
        return False, f"leak marker in {len(leaks)} field(s) — NOT publishing"
    return True, f"publishable — {len(rows)} entries, chain verified, no leaks"


# ---------------------------------------------------------------------------
# Banach fixed-point selftest — publish∘publish == publish (FAILS LOUDLY)
# ---------------------------------------------------------------------------


def selftest_idempotent(rows: list[dict] | None = None) -> bool:
    """Prove ``render(render(R)) == render(R)`` byte-for-byte — the fixed point.

    Renders ``rows`` (defaults to the live chain) twice into two temp dirs and
    asserts identical manifests AND a clean ``filecmp`` of every file. Then
    re-renders from the FIRST tree's parsed JSONL and asserts the manifest is
    unchanged (a re-derivation reproduces identical bytes). Raises
    AssertionError (loud) on any divergence — the falsifier the contract
    mandates. No network, no git, no LLM — pure determinism.
    """
    if rows is None:
        rows = load_rows(LEDGER)
    with tempfile.TemporaryDirectory(prefix="dcp-self-") as tmp:
        base = Path(tmp)
        a = render_site(rows, base / "a")
        b = render_site(rows, base / "b")
        man_a, man_b = tree_manifest(a), tree_manifest(b)
        assert man_a == man_b, (
            "decision_chain_publish NOT idempotent: render(R) != render(R) on "
            f"identical input.\nfirst={man_a}\nsecond={man_b}"
        )
        # Byte-level confirmation (manifest is sha-based; this catches a hash
        # collision or a manifest-coverage gap).
        match, mismatch, errors = filecmp.cmpfiles(
            a, b, [_CHAIN_FILE, *_STATIC_ASSETS], shallow=False
        )
        assert not mismatch and not errors, (
            f"decision_chain_publish trees differ: mismatch={mismatch} errors={errors}"
        )
        # Re-derivation fixed point: parse the rendered JSONL back and re-render;
        # the manifest must be a fixed point (a second pass adds no new bytes).
        refed = load_rows(a / _CHAIN_FILE)
        c = render_site(refed, base / "c")
        assert tree_manifest(c) == man_a, (
            "decision_chain_publish NOT a fixed point: re-render from rendered "
            f"chain changed bytes.\nfirst={man_a}\nrefed={tree_manifest(c)}"
        )
    return True


# ---------------------------------------------------------------------------
# DRY-RUN publish (default) — build + verify, NEVER push
# ---------------------------------------------------------------------------


def dry_run_publish(ledger: Path = LEDGER) -> dict:
    """Build the publish tree in a TEMP dir, verify it, prove the fixed point.

    NEVER touches the public repo — the build can never become a push. Returns a
    plain-language result dict {ok, reason, entries, manifest, idempotent}. The
    HITL (live activation) reads this before any irreversible public action.
    """
    rows = load_rows(ledger)
    ok, reason = verify_publishable(rows, ledger)
    if not ok:
        return {"ok": False, "reason": reason, "entries": len(rows)}
    idempotent = selftest_idempotent(rows)
    with tempfile.TemporaryDirectory(prefix="dcp-dry-") as tmp:
        tree = render_site(rows, Path(tmp) / "site")
        manifest = tree_manifest(tree)
    return {
        "ok": True,
        "reason": reason,
        "entries": len(rows),
        "manifest": manifest,
        "idempotent": idempotent,
    }


# ---------------------------------------------------------------------------
# LIVE publish (separately gated) — render whole tree, diff-gate, push
# ---------------------------------------------------------------------------


def _git(clone: Path, *args: str, env: dict | None = None) -> str:
    out = subprocess.run(
        ["git", "-C", str(clone), *args], capture_output=True, text=True, env=env
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()[:200]}")
    return out.stdout


def _ensure_pristine_clone(clone: Path, repo: str) -> None:
    """Clone if absent, then hard-reset to origin/main so no dirty/divergent
    state carries a stale or conflicted file into the publish (mirrors
    bin/grip-chain-sync step 4)."""
    if not (clone / ".git").exists():
        subprocess.run(["git", "clone", "-q", "--", repo, str(clone)], check=True)
    _git(clone, "fetch", "-q", "origin", "main")
    _git(clone, "reset", "-q", "--hard", "origin/main")


def _mirror_tree(tree: Path, clone: Path) -> None:
    """Copy every rendered file into the clone (overwriting). The static assets
    are copied too so an asset edit publishes — the tree is the full site."""
    for name in (_CHAIN_FILE, *_STATIC_ASSETS):
        shutil.copyfile(tree / name, clone / name)


def _publisher_env() -> dict:
    """The environment the public commit is made in. Author and committer are
    the publisher's role identity, set as environment variables because those
    outrank the ``user.name`` and ``user.email`` of any git config on the
    machine, so the commit cannot name a person by accident."""
    name = os.environ.get("GRIP_CHAIN_AUTHOR_NAME") or _AUTHOR_NAME_DEFAULT
    email = os.environ.get("GRIP_CHAIN_AUTHOR_EMAIL") or _AUTHOR_EMAIL_DEFAULT
    return {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def _chain_push(clone: Path) -> str:
    """Push *clone* through ``chain_push``: "" on success, else the reason.

    chain_push pushes as the GitHub App when its config exists and refuses when
    it cannot; with no config it refuses unless ``GRIP_CHAIN_REQUIRE_APP=0``
    explicitly allows the stored credential. Its stderr is passed on, so that
    notice reaches the operator. The paths travel as arguments, never inside the
    script text. ``sys.executable`` mints the App token: the interpreter running
    this module is, by construction, one that runs. The reason always names the
    exit code beside the last three stderr lines that are not blank, so a gate
    that crashed reads differently from a gate that refused.
    """
    proc = subprocess.run(
        ["bash", "-c", 'source "$1" && chain_push "$2" "$3"', "chain-push",
         str(CHAIN_PUSH), str(clone), sys.executable],
        capture_output=True, text=True)
    sys.stderr.write(proc.stderr)
    if proc.returncode == 0:
        return ""
    said = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
    tail = " | ".join(said[-3:])
    return f"chain_push exited {proc.returncode}" + (f": {tail}" if tail else "")


def _publish_rendered(rows: list[dict], repo: str, clone: Path) -> dict:
    """Mirror the rendered tree into a pristine clone and, only when that changes
    it, commit as the publisher and push through ``chain_push``. A refused push
    leaves its commit in the clone alone; the next run's reset discards it."""
    _ensure_pristine_clone(clone, repo)
    with tempfile.TemporaryDirectory(prefix="dcp-live-") as tmp:
        tree = render_site(rows, Path(tmp) / "site")
        _mirror_tree(tree, clone)
    if not _git(clone, "status", "--porcelain").strip():
        return {"ok": True, "reason": f"up to date — {len(rows)} entries, nothing to publish",
                "entries": len(rows), "published": False}
    _git(clone, "add", "--", _CHAIN_FILE, *_STATIC_ASSETS)
    _git(clone, "commit", "-q", "-m", f"chain: sync to {len(rows)} entries",
         env=_publisher_env())
    refused = _chain_push(clone)
    if refused:
        return {"ok": False, "published": False, "entries": len(rows),
                "reason": f"push refused or failed — nothing published: {refused}"}
    return {"ok": True, "reason": f"published — {len(rows)} entries → {repo}",
            "entries": len(rows), "published": True}


_UNSHIELDED_ENV = "GRIP_ALLOW_UNSHIELDED_PUBLISH"
"""The SOLE escape hatch past the redaction gate, and it is opt-IN only.

It is read by `live_publish` and by nothing else. `dry_run_publish` never
touches the public repo, so it neither consults nor needs this.
"""


_DENYLIST_REL = "config/redaction-denylist.json"


def _unreadable_denylist(local: Path) -> str:
    """Why the denylist at *local* cannot shield anything, or "" when it is
    readable JSON. Readable is necessary and never sufficient: see
    ``denylist_currency``."""
    try:
        raw = local.read_bytes()
    except OSError as exc:
        return f"cannot read {local}: {exc}"
    if raw[:9] == b"\x00GITCRYPT":
        return (
            f"{local} is still ENCRYPTED — run `git-crypt unlock`. An unreadable "
            "denylist cannot shield anything."
        )
    try:
        json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        return f"{local} is not parseable JSON: {exc}"
    return ""


def _denylist_changes_behind(root: Path, ref: str) -> tuple:
    """The question ``denylist_currency`` asks without decrypting anything: did
    any commit between ``HEAD`` and *ref* touch the denylist?"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--count", f"HEAD..{ref}",
             "--", _DENYLIST_REL],
            capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        # A bounded wait, and the timeout is CAUGHT rather than raised: an
        # AGORA seat asked for `timeout=10`, which alone would abort the publish
        # with TimeoutExpired instead of refusing it. `git rev-list` can block on
        # a concurrently-held index.lock -- observed in this very worktree while
        # writing this -- and a gate that crashes is not a gate that refuses.
        return "unverifiable", (
            f"`git rev-list` against {ref} did not finish within 10s (a "
            "concurrently-held index.lock will do this). Refusing rather than "
            "assuming the denylist is current."
        )
    if proc.returncode != 0:
        return "unverifiable", (
            f"cannot compare against {ref} ({proc.stderr.strip()[:120]}). Fetch "
            "first; without it this host cannot establish its denylist is current."
        )
    behind = proc.stdout.strip()
    if not behind.isdigit():
        return "unverifiable", f"unexpected rev-list output {behind!r}"
    if int(behind) > 0:
        return "divergent", (
            f"{behind} commit(s) on {ref} change {_DENYLIST_REL} and are not in "
            "this checkout, so publishing would shield against a superseded list. "
            "Run `git pull` on this host."
        )
    return "current", f"denylist matches {ref} (0 unmerged changes to it)"


def denylist_currency(root: "Path | None" = None, ref: str = "origin/main") -> tuple:
    """``('current'|'divergent'|'unverifiable', detail)`` — LOADABLE IS NOT CURRENT.

    THE HOLE THIS CLOSES, measured on the live publisher 2026-09-17. The gate
    below refuses when ``redaction_state()`` is not ``ok``, and its comment gives
    the right reason: a locked checkout "cannot read the denylist, so it cannot
    KNOW the chain is clean". But a locked checkout carrying a LEFTOVER PLAINTEXT
    denylist reads as ``ok`` and cannot know either. That is the state the VPS is
    in: git-crypt locked, **1,185 commits behind**, holding a plaintext snapshot
    dated ``2026-07-03T20:51:48Z`` with **50** items while canonical carries
    **134** updated today. Its cron publishes every fifteen minutes, faithfully
    shielding against a list missing 84 entries — and the published chain needs 32
    replacements as a result.

    The snapshot is not an accident: it IS the remediation for the 2026-07-03 leak
    that ``_unshielded_publish_allowed`` below describes. That fix turned a
    fail-open into a fail-closed and then pinned its input at fix-time, with
    nothing to revalidate it — a check at a coarser resolution than the property
    it guards, readability standing in for correctness.

    ASKED WITHOUT DECRYPTING ANYTHING, and the first version of this function got
    that wrong. Comparing content via ``git show <ref>:<path>`` returns the raw
    blob WITHOUT the smudge filter, so git-crypt ciphertext comes back even on a
    correctly unlocked host — that version answered ``unverifiable`` everywhere
    and would have refused every publish on every machine. The question that needs
    no plaintext is simply whether any commit between ``HEAD`` and *ref* touched
    the denylist. It also never reads the denylist's contents, which are
    themselves the personal data being protected.

    ``unverifiable`` is deliberately NOT ``current``: "I could not ask" must never
    read as "mine is fine", the discipline ``_pypi`` and ``_canonical_version``
    already carry.

    Escape-hatch audit (``gate-resolution-escape-hatch.md`` Invariant 1): the
    blocked condition is "cannot confirm the denylist is canonical" and its
    resolutions are ``git fetch``/``git pull`` and ``git-crypt unlock`` on that
    host. None goes through this function, so the refusal cannot block its remedy.
    """
    root = Path(root) if root else CLAUDE_DIR
    unreadable = _unreadable_denylist(root / _DENYLIST_REL)
    if unreadable:
        return "unverifiable", unreadable
    return _denylist_changes_behind(root, ref)


def _unshielded_publish_allowed() -> bool:
    """True only when the operator has EXPLICITLY opted out of the shield.

    `missing` used to fall through this gate silently: the refusal covered only
    `ciphertext` and `invalid`, and the remediation ran only on `ok`, so a state
    of `missing` was neither refused nor cleaned — it published raw. The comment
    called it "legacy-permitted (forks without the feature)", which is a real
    need served the wrong way: a fork with no denylist and a PRODUCTION host that
    has LOST its denylist are indistinguishable to this function, and exactly the
    second case is what leaked on 2026-07-03, when the file was absent from the
    VPS and the shield failed open.

    `redaction_state()` also returns `missing` when the shield MODULE cannot be
    imported, so an ImportError anywhere in that chain silently disabled
    redaction for a public push.

    Fail-closed now, with the fork's need met explicitly instead of implicitly.
    The sibling cron path (`bin/grip-chain-sync`) has always been fail-closed
    here — it routes through `remediate()`, which raises on any state but `ok` —
    so this only brings `live_publish` into line with the path that actually
    runs every fifteen minutes.
    """
    # Delegates to the emitter, which owns the canonical definition: the WRITE
    # path and the PUBLISH path must not be able to disagree about whether an
    # unshielded run was authorised. (Import direction is emitter <- publisher,
    # so the constant cannot live here without a cycle.)
    return emitter.unshielded_allowed()


def live_publish(ledger: Path = LEDGER, *, repo: str | None = None,
                 clone: Path | None = None) -> dict:
    """SEPARATELY-GATED irreversible public action: render the whole tree, mirror
    it into the gh-pages clone, and push ONLY when the tree actually changed.

    Pre-gated by the SAME verify (chain + leak + Banach) as the dry run — a
    broken/leaky/non-idempotent chain can never be pushed. No-op (no commit)
    when the rendered tree matches origin/main: the Banach property at the git
    layer. The commit names the publisher and the push goes through
    ``chain_push`` (see ``_publish_rendered``). Returns {ok, reason, entries,
    published(bool)}.
    """
    repo = repo or DEFAULT_REPO
    clone = clone or (CLAUDE_DIR / "state" / "grip-decision-chain")
    # Redaction fail-closed gate (2026-07-02): a git-crypt-locked checkout
    # cannot read the denylist, so it cannot KNOW the chain is clean — the
    # irreversible public action refuses rather than publishing unshielded
    # text. 'missing' refuses too unless the operator opts out (see
    # _unshielded_publish_allowed); dry runs never reach the public repo.
    redaction = emitter.redaction_state()
    if redaction != "ok" and not _unshielded_publish_allowed():
        return {"ok": False, "published": False, "entries": 0,
                "reason": f"redaction denylist unusable ({redaction}) — "
                          "refusing live publish. 'ciphertext' means run "
                          "`git-crypt unlock`; 'missing' means the denylist "
                          "file or the shield module is absent. Set "
                          f"{_UNSHIELDED_ENV}=1 to publish unshielded on a "
                          "fork that genuinely has no denylist."}
    # Currency gate (2026-09-17): `ok` only means the denylist LOADED — see
    # `denylist_currency`. `unverifiable` refuses too, because "I could not
    # ask" is not "I am fine".
    currency, detail = denylist_currency()
    if currency != "current" and not _unshielded_publish_allowed():
        return {"ok": False, "published": False, "entries": 0,
                "reason": f"denylist not confirmed current ({currency}): {detail}"}
    # Self-cleaning publish (2026-07-03 concurrent-race guard): re-redact the
    # ledger with the CURRENT denylist before rendering, so a stale ledger from
    # another session can never publish PII past this gate. Idempotent.
    if redaction == "ok":
        emitter.remediate(ledger)
    rows = load_rows(ledger)
    ok, reason = verify_publishable(rows, ledger)
    if not ok:
        return {"ok": False, "reason": reason, "entries": len(rows), "published": False}
    selftest_idempotent(rows)  # loud-fail before any push
    return _publish_rendered(rows, repo, clone)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="GRIP decision-chain publisher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dry-run", help="build + verify the site in a temp dir; NEVER push")
    sub.add_parser("selftest", help="prove render(render(R))==render(R) (exit 1 on drift)")
    sub.add_parser("live", help="GATED: render the whole tree + push only if changed")
    ns = ap.parse_args(argv)

    if ns.cmd == "selftest":
        try:
            selftest_idempotent()
        except AssertionError as exc:
            print(f"FIXED-POINT BROKEN: {exc}", file=sys.stderr)
            return 1
        print("fixed point OK — render(render(R)) == render(R)")
        return 0
    if ns.cmd == "dry-run":
        result = dry_run_publish()
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    result = live_publish()
    print(json.dumps({k: v for k, v in result.items() if k != "manifest"}, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
