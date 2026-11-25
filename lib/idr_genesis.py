"""Mint the genesis record that opens the post-compromise era.

GRIP's audit HMAC secret is world-readable and committed, so every record it
sealed is forgeable by anyone with repository read access. Re-signing those
records under fresh custody is REFUSED: it would produce records that look
trustworthy and are not, and it would destroy the one property the chain exists
to have — that reasoning provably predates outcomes.

The genesis record is the honest alternative. It is the first record signed
under real custody, and its body commits to a fingerprint of the compromised
era exactly as that era stands. From it forward, records are *signed*. Behind
it, records are *attested by anchor* — a weaker and TRUE claim, instead of one
uniform green tick that is false for all 866.

WHAT THE BODY COMMITS TO, and why both halves are required.

`merkle_root` is RFC6962 over each record's `content_addr`, which hashes the
semantic body while excluding `id`, `ts` and `audit`. It therefore moves when a
DECISION changes and does not move when a record is renamed or re-dated. That
is correct for linkage stability and insufficient alone: a holder of the
compromised secret could reissue the same decision under a new id and timestamp
without moving it.

`sha256_records` is over the ledger bytes, where identity and time do appear.

So the genesis commits to both. Neither is sufficient; together they bind
content, identity and order. `freeze_commitment` is deliberately NOT used — it
digests the freeze REPORT, so it moves when the report's schema changes even
though no record did (observed: adding one field moved it from 9447949f to
67449568 with every record byte-identical).

MINTING IS REFUSED, LOUDLY, unless real custody is reachable. A genesis record
carrying the demo seal would be a record announcing the end of the demo era
while itself belonging to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.idr_freeze import LedgerFreeze, freeze_ledger

GENESIS_KIND = "custody-genesis"
SUPERSEDED_ERA = "hmac-demo-seal"

GENESIS_REASON = (
    "the audit HMAC secret was world-readable and committed in plaintext; "
    "HMAC is symmetric, so every record it sealed is forgeable by anyone with "
    "repository read access"
)
PRE_GENESIS_CLAIM = "attested by anchor, NOT signed by trusted custody"


class GenesisError(RuntimeError):
    """Minting cannot proceed. Always carries why, never a bare failure."""


@dataclass(frozen=True)
class GenesisPlan:
    """Everything the genesis record will say, computed before anything is written."""

    anchor: str
    tenant_id: str
    freezes: dict[str, LedgerFreeze]

    @property
    def incomplete_ledgers(self) -> tuple[str, ...]:
        """Ledgers whose freeze could not place every record.

        Reported rather than blocking. A genesis that refuses to open until the
        past is tidy would never be minted, and the compromised era is exactly
        the thing being frozen AS IT STANDS — including its damage. What must
        never happen is freezing damage SILENTLY.
        """
        return tuple(sorted(n for n, f in self.freezes.items() if not f.complete))


def _frozen_summary(freeze: LedgerFreeze) -> dict[str, Any]:
    """The per-ledger commitment written into the genesis body."""
    return {
        "records": freeze.records,
        "merkle_root": freeze.merkle_root,
        "sha256_records": freeze.sha256_records,
        "placed_in_forest": freeze.forest_nodes,
        "true_roots": freeze.true_roots,
        "fragment_roots": freeze.fragment_roots,
        "orphans": [list(o) for o in freeze.orphans],
        "duplicates": list(freeze.duplicates),
        "complete": freeze.complete,
    }


def plan_genesis(
    ledgers: dict[str, Path],
    *,
    tenant_id: str,
    anchor: str,
) -> GenesisPlan:
    """Compute the genesis plan. Reads ledgers, writes nothing, signs nothing."""
    if not ledgers:
        raise GenesisError("no ledgers given — a genesis must commit to something")
    missing = sorted(str(p) for p in ledgers.values() if not Path(p).exists())
    if missing:
        raise GenesisError(f"ledger(s) not found: {', '.join(missing)}")
    return GenesisPlan(
        anchor=anchor,
        tenant_id=tenant_id,
        freezes={name: freeze_ledger(path) for name, path in ledgers.items()},
    )


def genesis_decision(plan: GenesisPlan) -> dict[str, Any]:
    """The signed body of the genesis record."""
    return {
        "verdict": "GENESIS",
        "supersedes_era": SUPERSEDED_ERA,
        "reason": GENESIS_REASON,
        "pre_genesis_records_are": PRE_GENESIS_CLAIM,
        "frozen": {n: _frozen_summary(f) for n, f in plan.freezes.items()},
        "incomplete_ledgers": list(plan.incomplete_ledgers),
    }


def assert_custody_reachable(tenant_id: str) -> None:
    """Raise unless this tenant can actually produce a product seal.

    Checked BEFORE building, so the failure names the missing piece instead of
    surfacing as a silently downgraded record. The three conditions are the same
    ones `lib.precog.idr._custody_gap` asks, and they are kept separate because
    each has a completely different fix.
    """
    from lib.aide.key_provisioning import pq_backend_available, tenant_pq_keys_exist

    if not tenant_id:
        raise GenesisError("no tenant named — cannot mint a genesis with no custody")
    if not tenant_pq_keys_exist(tenant_id):
        raise GenesisError(
            f"tenant {tenant_id!r} has no ML-DSA-65 key material. Provision it "
            f"first: key_provisioning.provision_tenant_pq_keys({tenant_id!r})"
        )
    if not pq_backend_available():
        raise GenesisError(
            "this interpreter cannot perform ML-DSA-65 signing (the post-quantum "
            "backend is unavailable), so any record built here would silently "
            "take the demo seal"
        )


def build_genesis(plan: GenesisPlan):
    """Build and SIGN the genesis record. Appends nothing.

    Refuses if the signed result did not reach real custody. A genesis carrying
    the demo seal would announce the end of the demo era while belonging to it,
    so a downgrade here is an error rather than a degraded success.
    """
    from lib.precog.idr import build_idr

    assert_custody_reachable(plan.tenant_id)
    idr = build_idr(
        prompt="custody genesis — opening the signed era",
        fingerprint=f"custody-genesis:{plan.tenant_id}",
        decision=genesis_decision(plan),
        predecessor_idr=plan.anchor,
        depth=0,
        kind=GENESIS_KIND,
        tenant_id=plan.tenant_id,
    )
    downgrade = (idr.audit or {}).get("downgrade")
    if downgrade:
        raise GenesisError(
            f"the genesis record took the DEMO seal ({downgrade}). Refusing to "
            "mint: a record announcing the end of the demo era cannot itself "
            "belong to it."
        )
    return idr


def verify_genesis(idr, *, key_dir: Path | None = None) -> str:
    """Re-verify the freshly built record the way an OUTSIDE auditor would.

    Uses only the published public keys, through the same external path a third
    party runs. Signing and verifying are different claims, and only the second
    is worth anything — a seal nobody can check is decoration.

    The key directory resolves through `pq_dual_sign._key_dir`, which is the
    SINGLE source of truth for the key root and is read at call time. An earlier
    version rebuilt that path from `get_claude_dir()` instead, duplicating a
    resolution the codebase deliberately centralises — and it broke the moment a
    caller redirected the install directory, which the test suite does for every
    test. Signing kept working (it used the canonical resolver) while verifying
    looked in an empty directory, so a perfectly good record read as BROKEN.

    `key_dir` overrides the lookup entirely, for an auditor holding keys that
    live nowhere near a GRIP install.
    """
    from lib.idr_external_read import verify_idr_external
    from lib.pq_dual_sign import _key_dir, public_key_raw_from_pem

    tenant = (idr.audit or {}).get("tenant_id")
    kd = Path(key_dir) if key_dir is not None else _key_dir(tenant)
    ed = public_key_raw_from_pem((kd / "verifying.pub").read_bytes())
    pq = public_key_raw_from_pem((kd / "verifying-mldsa65.pub").read_bytes())
    return str(verify_idr_external(idr, ed, pq))
