"""W5 — dogfood proof, READ-ONLY (binding-DA + exogenous-anchor-law).

Plan unified-backlog-issue-system.md W5 (VERIFY). The dogfood reduces THIS
plan + its waves into BacklogItems and runs them through the W2 sync shim
to produce IDR-notarised MutationPlans — proving the end-to-end
read→reduce→plan→notarise path WITHOUT performing any external write.

**The write-leg is deferred — by law, not by omission.** H-BL-4 requires
the IDR chain to root at an EXOGENOUS human-commit genesis anchor (an
artefact the loop cannot fabricate — exogenous-anchor-law anchor #2).
Canonically, no such anchor exists: every origin/main commit is
loop/automation-authored (same git identity). Per the law, manufacturing
one from a loop commit IS the self-fulfilling-prophecy failure the law
forbids. So `dogfood()` runs `execute=False` until an operator supplies a
genuine external human-commit SHA, and `validate_exogenous_anchor`
mechanically REJECTS any loop-generated root (H-BL-4 falsifier, by
construction — the rejection is the test, not a promise).

READ-ONLY: zero external writes. The W0 anti-blast TRIAD sits on the
would-be write path (composed, never bypassed) for when a real anchor
arrives in a future operator-rooted session.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lib.backlog.reducer import reduce
from lib.backlog.sync import MutationPlan, NoOpBackend, sync

# Repos the dogfood would seed (HITL-locked 3 — repo-allowlist is W0's job).
AUTHORISED_REPOS = ("grip-platform/HAL", "grip-platform/GRIP", "happi-lang/happi.md")

_ANCHOR_DEFERRED = "WRITE-LEG DEFERRED — exogenous human-commit anchor required"


@dataclass(frozen=True, slots=True)
class DogfoodResult:
    anchor_status: str          # _ANCHOR_DEFERRED or "rooted:<sha>"
    executed: bool              # True only with a valid exogenous anchor
    plans: tuple[MutationPlan, ...]
    repos_targeted: tuple[str, ...]


def validate_exogenous_anchor(sha: str | None, loop_shas: set[str]) -> str | None:
    """Return a valid exogenous anchor SHA, or None if it must be deferred.

    Rejects (returns None): absent SHA; a loop/automation-generated SHA
    (in `loop_shas`). This rejection IS the H-BL-4 falsifier — a chain
    rooted at a system-generated commit is forbidden, mechanically, here.
    """
    if not sha:
        return None
    if sha in loop_shas:
        return None  # self-genesis is circular — exogenous-anchor-law
    return sha


def dogfood(
    root: Path,
    *,
    anchor_sha: str | None = None,
    loop_shas: set[str] | None = None,
) -> DogfoodResult:
    """Reduce THIS substrate → IDR-notarised dry-run plans. READ-ONLY.

    `anchor_sha` defaults to None ⇒ no valid exogenous anchor ⇒ the
    write-leg is DEFERRED and `execute=False` is forced (zero external
    writes). A future operator-rooted session passes a genuine external
    human-commit SHA (not in `loop_shas`) to enable the real write-leg —
    which still flows through the W0 TRIAD inside `sync(execute=True)`.
    """
    anchor = validate_exogenous_anchor(anchor_sha, loop_shas or set())
    executable = anchor is not None
    model = reduce(Path(root))                       # W1 — pure, deterministic
    # READ-ONLY by construction: execute is gated on a VALID exogenous
    # anchor; absent it, sync runs dry (execute=False) — no writes, ever.
    plans = sync(model, execute=executable, backend=NoOpBackend())
    return DogfoodResult(
        anchor_status=(f"rooted:{anchor}" if anchor else _ANCHOR_DEFERRED),
        executed=any(p.executed for p in plans),
        plans=tuple(plans),
        repos_targeted=AUTHORISED_REPOS,
    )
