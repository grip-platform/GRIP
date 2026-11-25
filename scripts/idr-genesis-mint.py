#!/usr/bin/env python3
"""Mint the genesis record that opens GRIP's signed era. DRY RUN unless told otherwise.

    python3 scripts/idr-genesis-mint.py --anchor human:...        # show what would happen
    python3 scripts/idr-genesis-mint.py --anchor human:... --commit   # append it

Default is a dry run because appending to an append-only ledger cannot be
undone. The dry run does everything the real path does — freezes the ledgers,
builds the record, SIGNS it, and re-verifies that signature the way an outside
auditor would — and then discards it. So a green dry run is evidence the commit
will work, not a promise that it might.

WHY THIS EXISTS. GRIP's audit HMAC secret is world-readable and committed in
plaintext. HMAC is symmetric, so anyone who can read the repository can forge
any of the 866 records it seals. Re-signing them under fresh custody is refused:
that would make forgeable records LOOK trustworthy and would destroy the one
property the chain exists to have. Instead the compromised era is frozen as it
stands and this record commits to that fingerprint. Behind it, records are
attested by anchor. Ahead of it, signed.

Exit codes:

    0  the genesis is ready (dry run) or was appended (--commit)
    1  minting refused, with the reason on stderr
    2  usage error
"""
import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.claude_dir import get_claude_dir  # noqa: E402
from lib.idr_genesis import (  # noqa: E402
    GENESIS_KIND,
    GenesisError,
    build_genesis,
    plan_genesis,
    verify_genesis,
)

DEFAULT_TENANT = "grip-production"
VERIFIED = "Verdict.VERIFIED"
EXIT_OK, EXIT_REFUSED = 0, 1


def default_ledgers() -> dict[str, Path]:
    base = get_claude_dir() / "state" / "precog"
    return {"idr": base / "idr.jsonl", "context": base / "context.jsonl"}


def existing_genesis(path: Path) -> str | None:
    """The id of a genesis already in this ledger, or None.

    Minting a second genesis would create two competing claims about where the
    signed era begins, which is worse than not minting at all.
    """
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or GENESIS_KIND not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("kind") == GENESIS_KIND:
            return str(rec.get("id"))
    return None


def refuse(message: str) -> int:
    print(f"refused: {message}", file=sys.stderr)
    return EXIT_REFUSED


def preflight(args, ledgers: dict[str, Path]) -> str | None:
    """The reason minting must not proceed, or None if it may."""
    if not args.anchor:
        return ("--anchor is required. The genesis must hang from a named "
                "exogenous anchor — a human directive, a CI run, a council "
                "seal. A genesis anchored to nothing anchors nothing.")
    already = existing_genesis(ledgers["idr"])
    if already:
        return (f"a genesis already exists in this ledger: {already}. Two "
                "genesis records would make two competing claims about where "
                "the signed era begins.")
    return None


def _ledger_lines(name: str, freeze) -> list[str]:
    return [
        f"  {name}:",
        f"      records        {freeze.records}",
        f"      merkle_root    {freeze.merkle_root}",
        f"      sha256_records {freeze.sha256_records}",
        f"      placed         {freeze.forest_nodes}"
        f"  (orphans {len(freeze.orphans)}, duplicates {len(freeze.duplicates)})",
    ]


def describe(plan, idr, verdict: str) -> str:
    audit = idr.audit or {}
    lines = [
        "=== GENESIS RECORD ===",
        f"  id            {idr.id}",
        f"  kind          {idr.kind}",
        f"  tenant        {plan.tenant_id}",
        f"  anchor        {plan.anchor}",
        f"  custody       {audit.get('custody')}",
        f"  scheme        {audit.get('scheme')}",
        f"  outside-audit {verdict}",
        "",
        "=== COMMITS TO ===",
    ]
    for name, freeze in sorted(plan.freezes.items()):
        lines += _ledger_lines(name, freeze)
    if plan.incomplete_ledgers:
        lines += [
            "",
            "  NOTE — these ledgers are frozen INCOMPLETE: "
            f"{', '.join(plan.incomplete_ledgers)}",
            "  The compromised era is frozen AS IT STANDS, damage included.",
            "  That is deliberate. What must never happen is freezing it silently.",
        ]
    return "\n".join(lines)


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true",
                    help="actually append the record (default: dry run)")
    ap.add_argument("--tenant", default=DEFAULT_TENANT,
                    help=f"signing tenant (default: {DEFAULT_TENANT})")
    ap.add_argument("--anchor", default=None,
                    help="exogenous anchor id this genesis hangs from "
                         "(must start ci: / human: / council: / hypo:)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    ledgers = default_ledgers()

    blocked = preflight(args, ledgers)
    if blocked:
        return refuse(blocked)

    try:
        plan = plan_genesis(ledgers, tenant_id=args.tenant, anchor=args.anchor)
        idr = build_genesis(plan)
        verdict = verify_genesis(idr)
    except GenesisError as exc:
        return refuse(str(exc))

    if verdict != VERIFIED:
        return refuse(f"the record did not verify under its own published keys "
                      f"({verdict}). A seal nobody can check is decoration.")

    print(describe(plan, idr, verdict))
    if not args.commit:
        print("\nDRY RUN — nothing was written. Re-run with --commit to append.")
        return EXIT_OK

    from lib.precog.idr import append_idr
    append_idr(idr)
    print(f"\nAPPENDED to {ledgers['idr']}")
    print("The signed era begins at this record.")
    return EXIT_OK


if __name__ == "__main__":
    # Dual-sign custody guard (GRIP#5628). This entrypoint has a
    # `#!/usr/bin/env python3` shebang and reaches a signed-record write, so under
    # an interpreter without `cryptography>=48` it would silently emit forgeable
    # `demo-shared-key` records. Re-exec under a capable interpreter FIRST, before
    # main() reaches any signing path.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "lib"))
    from pq_reexec import reexec_if_incapable as _reexec
    _reexec(_Path(__file__))

    sys.exit(main())
