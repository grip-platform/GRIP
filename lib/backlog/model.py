"""W1 — the normalised BacklogItem shape (DRY single model).

The one canonical shape every substrate source reduces into (plans/drafts
frontmatter, pending hypotheses, and — as honest documented extension
points — the whoa aggregator and TaskList store). Mirrors the
syscall-doctrine normalised-return idiom: callers never see source-specific
shapes, only BacklogItem.

W1 is READ-ONLY (model + pure reducer). No write legs — those stay deferred
behind the W0 anti-blast TRIAD per the plan's binding-DA invariant.

Determinism contract (H-BL-2): BacklogItem is a frozen dataclass with a
total, stable ordering key, so an identical substrate reduces to a
byte-identical list (idempotence is provable, not asserted).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

# Priority/status are bounded label sets (not free strings) so the reduced
# board is comparable and the idempotence test is meaningful.
PRIORITIES: tuple[str, ...] = ("P0", "P1", "P2", "P3", "unset")
STATUSES: tuple[str, ...] = ("open", "in_progress", "blocked", "done", "unknown")


@dataclass(frozen=True, slots=True)
class BacklogItem:
    """One normalised unit of backlog work.

    Fields are exactly the plan's L0 shape:
    {id, source, title, body, repo, priority, status, idr_ref, provenance}.
    `provenance` is the source-specific locator (file path, hypothesis id,
    task id) — never human prose, so a substrate write can later be fenced
    to it (W2) without clobbering human-edited fields.
    """

    id: str
    source: str            # "plan" | "draft" | "hypothesis" | "whoa" | "tasklist"
    title: str
    body: str
    repo: str              # canonical OWNER/REPO; "" when source is repo-agnostic
    priority: str          # one of PRIORITIES (coerced on construction)
    status: str            # one of STATUSES (coerced on construction)
    idr_ref: str = ""      # HAPPI IDR ref once notarised (W2); "" until then
    provenance: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Coerce out-of-set labels to the safe sentinel rather than raise:
        # a reducer must never crash on a malformed substrate entry (it must
        # degrade-to-silence per the plan's taste rule). object.__setattr__
        # because the dataclass is frozen.
        if self.priority not in PRIORITIES:
            object.__setattr__(self, "priority", "unset")
        if self.status not in STATUSES:
            object.__setattr__(self, "status", "unknown")

    @property
    def sort_key(self) -> tuple:
        """Total, stable ordering — the basis of byte-identical idempotence.

        Priority rank, then source, then id: every field is deterministic
        and total, so `sorted(items, key=...)` is a pure function of the
        substrate (no dict/set iteration order leaks in).
        """
        return (PRIORITIES.index(self.priority), self.source, self.id)
