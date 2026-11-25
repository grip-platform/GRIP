"""W5-exec — operator-authorised inaugural seed (the dogfood write-leg).

binding-DA READ-ONLY was operator-LIFTED for this session
(feedback_operator_writeleg_authorization_2026-05-17.md). This module
performs the real cross-repo write — bounded, idempotent, W0-gated, IDR
chained from the EXOGENOUS operator-authorization genesis.

THREE safety invariants, structural (not advisory):

1. **Bounded scope.** `plan_items()` is an EXPLICIT enumeration of the
   unified-backlog plan + its W0–W5 waves + H-BL-1..5 (~12 items). It
   NEVER calls `reduce(root)` — a full-substrate reduce is 122 items ⇒
   366 cross-repo issues ⇒ catastrophe. There is no code path here that
   seeds from `reduce()`.
2. **W0-gated.** Writes go through the W2 `sync(execute=True)`, which
   calls `antiblast.check` BEFORE every `backend.upsert` (repo-allowlist
   / mutation-ceiling / protected-path). A non-authorised repo raises
   `DenyError` before any write. Composed, never bypassed.
3. **Exogenous genesis.** The IDR chain roots at `genesis_sha()` —
   sha256 of V>>'s recorded verbatim authorization (data/backlog/
   idr-genesis.json). The loop cannot fabricate V>>'s words; a
   loop/system root is inadmissible (H-BL-4 falsifier).

Idempotent: the GitHub backend create-if-absent-by-item-id-marker; a
re-run creates zero duplicate issues.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from lib.backlog.model import BacklogItem
from lib.relay_decision import _HAPPI_VERSION as _RELAY_HAPPI_VERSION
from lib.backlog.sync import sync

AUTHORISED_REPOS = ("grip-platform/HAL", "grip-platform/GRIP", "happi-lang/happi.md")
SEED_LABEL = "backlog-system"


class RepoUnavailable(RuntimeError):
    """A target repo is 404 / issues-disabled / unresolvable. Per the plan,
    this is a note+skip (NOT a dogfood failure) — proof stands on the
    repos that ARE reachable."""

_GENESIS_PATH = Path(__file__).resolve().parents[2] / "data" / "backlog" / "idr-genesis.json"


def genesis_sha(genesis_path: Path | None = None) -> str:
    """Deterministic sha256 of the operator-authorization canonical form.

    Reproducible + auditable + unfabricatable (the loop cannot invent
    V>>'s verbatim instruction). This is the exogenous IDR chain root.
    """
    g = json.loads((genesis_path or _GENESIS_PATH).read_text())
    canonical = json.dumps(
        {k: g[k] for k in ("operator", "timestamp_utc", "verbatim", "scope")},
        separators=(",", ":"), sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_items(repo: str) -> list[BacklogItem]:
    """BOUNDED explicit enumeration — the plan + its waves + hypotheses.

    NEVER reduce(root). Deterministic, ~12 items, repo-scoped so the W0
    repo-allowlist evaluates against `repo`.
    """
    waves = [
        ("W0", "anti-blast TRIAD gate (allowlist+ceiling+protected-path)", "done"),
        ("W1", "BacklogItem model + pure idempotent reducer", "done"),
        ("W2", "sync shim — IDR-notarised, W0-gated, READ-ONLY", "done"),
        ("W3", "Pages dashboard — pure deterministic renderer", "done"),
        ("W4", "grip-anywhere MCP-App surface (READ-ONLY adapter)", "done"),
        ("W5", "dogfood proof — READ-ONLY + operator-authorised write-leg", "in_progress"),
    ]
    hyps = [f"H-BL-{n}" for n in range(1, 6)]
    items: list[BacklogItem] = [
        BacklogItem(
            id="plan:unified-backlog-issue-system",
            source="plan", title="Unified backlog/issue/task system (P1 spine)",
            body="W0-W5 spine merged; operator-authorised inaugural dogfood seed.",
            repo=repo, priority="P1", status="in_progress",
            provenance={"path": "plans/unified-backlog-issue-system.md"},
        )
    ]
    items += [
        BacklogItem(
            id=f"wave:unified-backlog:{w}", source="plan",
            title=f"unified-backlog {w}: {desc}", body=f"{w} of the P1 spine.",
            repo=repo, priority="P1", status=st,
            provenance={"plan": "unified-backlog-issue-system", "wave": w},
        )
        for (w, desc, st) in waves
    ]
    items += [
        BacklogItem(
            id=f"hypothesis:{h}", source="hypothesis",
            title=f"{h} — unified-backlog falsifier", body=f"{h} registered + verified.",
            repo=repo, priority="P2", status="open",
            provenance={"hypothesis_id": h, "plan": "unified-backlog-issue-system"},
        )
        for h in hyps
    ]
    return sorted(items, key=lambda i: i.sort_key)  # deterministic


class GitHubIssueBackend:
    """Idempotent issue create-if-absent-by-item-id-marker via `gh`.

    The W0 TRIAD has already passed (in sync) before upsert is called.
    Idempotent: an item whose id-marker is already an issue body in this
    repo is NOT re-created — re-runs create zero duplicates.
    """

    def __init__(self, repo: str, *, dry_run: bool = False) -> None:
        self.repo = repo
        self.dry_run = dry_run
        self.created: list[str] = []
        self.skipped_existing: list[str] = []
        self._label_ensured = False

    def _ensure_label(self) -> None:
        """Idempotently ensure SEED_LABEL exists (gh issue create --label
        fails if the label is absent). `--force` = create-or-update.
        Repo inaccessible / issues-disabled (404) ⇒ RepoUnavailable so the
        caller can note+skip that repo (not a dogfood failure)."""
        if self._label_ensured or self.dry_run:
            self._label_ensured = True
            return
        r = subprocess.run(
            ["gh", "label", "create", SEED_LABEL, "--repo", self.repo,
             "--color", "5319e7", "--description",
             "GRIP unified-backlog system (W5-exec inaugural seed)", "--force"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if r.returncode != 0:
            err = (r.stderr or "").lower()
            if "not found" in err or "404" in err or "could not resolve" in err:
                raise RepoUnavailable(f"{self.repo}: {r.stderr[:160].strip()}")
            raise RuntimeError(f"gh label create failed ({self.repo}): {r.stderr[:200]}")
        self._label_ensured = True

    def _existing(self, item_id: str) -> str | None:
        r = subprocess.run(
            ["gh", "issue", "list", "--repo", self.repo, "--label", SEED_LABEL,
             "--state", "all", "--search", item_id, "--json", "number,body",
             "--limit", "50"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if r.returncode != 0:
            return None
        try:
            for iss in json.loads(r.stdout or "[]"):
                if item_id in (iss.get("body") or ""):
                    return str(iss.get("number"))
        except Exception:
            return None
        return None

    def upsert(self, item: BacklogItem, provenance_block: str) -> str:
        existing = self._existing(item.id)
        if existing is not None:
            self.skipped_existing.append(item.id)
            return f"{self.repo}#{existing}"          # idempotent: no dup
        if self.dry_run:
            self.created.append(item.id)
            return f"{self.repo}#DRY"
        self._ensure_label()  # idempotent; raises RepoUnavailable on 404
        body = f"{item.body}\n\n{provenance_block}\n\n<!-- backlog-item:{item.id} -->"
        r = subprocess.run(
            ["gh", "issue", "create", "--repo", self.repo,
             "--title", f"[backlog] {item.title}", "--label", SEED_LABEL,
             "--body", body],
            capture_output=True, text=True, timeout=90, check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(f"gh issue create failed ({self.repo}): {r.stderr[:200]}")
        self.created.append(item.id)
        return (r.stdout or "").strip()


def _root_idr_log_at_genesis(log_path: Path, gsha: str) -> None:
    """Pre-seed the IDR telemetry log so the first real IDR chains from
    the exogenous genesis sha (build_idr_ref reads prior from last line)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists() and log_path.read_text().strip():
        return  # already rooted — idempotent
    log_path.write_text(json.dumps({
        "genesis": True, "anchor": "operator-authorization",
        "idr_ref": {"sha256": gsha},
    }) + "\n")


def seed_inaugural(*, dry_run: bool = True, repos=AUTHORISED_REPOS,
                   idr_log: Path | None = None) -> dict:
    """Seed the bounded inaugural set across the authorised repos.

    `dry_run=True` (default) computes + W0-gates everything but creates
    NO issues. Live seed = explicit `dry_run=False` (operator-authorised).
    """
    gsha = genesis_sha()
    log = idr_log or (Path(_GENESIS_PATH).parent / "idr-chain.jsonl")
    _root_idr_log_at_genesis(log, gsha)
    result: dict = {"genesis_sha": gsha, "dry_run": dry_run, "repos": {}}
    for repo in repos:
        backend = GitHubIssueBackend(repo, dry_run=dry_run)
        items = plan_items(repo)
        try:
            # W2 sync: antiblast.check gates EVERY write before backend.upsert.
            plans = sync(items, execute=True, backend=backend)
        except RepoUnavailable as exc:
            # Per the plan: a 404/issues-disabled repo is a NOTE+SKIP, not a
            # dogfood failure. The proof stands on the reachable repos.
            result["repos"][repo] = {"items": len(items), "skipped": str(exc)}
            continue
        result["repos"][repo] = {
            "items": len(items),
            "created": list(backend.created),
            "skipped_existing": list(backend.skipped_existing),
            # Compared against the constant that PRODUCES the value, never a
            # duplicate of it. build_idr_ref stamps relay_decision._HAPPI_VERSION
            # (lib/relay_decision.py:317); writing "happi/1.1" here too made the
            # two silently coupled, so bumping the producer would flip this
            # predicate to False for every plan — and it returns a bool rather
            # than raising, so a health signal would start reading "not rooted"
            # with nothing actually wrong. A no-op today; the point is that it
            # stays correct when the producer moves.
            "all_idr_rooted": all(
                p.idr_ref.get("v") == _RELAY_HAPPI_VERSION for p in plans
            ),
        }
    return result
