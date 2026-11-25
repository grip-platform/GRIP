"""The setup record's ledger: a stdlib hash chain of install steps, sealed and checked
with GRIP's own IDR signing (GRIP Suite, W3).

Appending a step never needs more than the standard library, because the earliest steps
run before GRIP's own venv -- and therefore ``cryptography`` -- exists: step 1 checks the
computer, step 2 clones GRIP and makes its venv. Each step becomes one JSON line in the
setup folder's step file, chained to the line written before it by that line's own
SHA-256, with a fixed all-zero hash standing in for "the line before the first line".
A single process writes this file during one run of the installer, so no file locking is
needed here -- unlike the shared, multi-session IDR chain in ``lib/precog/idr.py``.

Sealing and checking run under GRIP's venv, once step 3 has made the user's own signing
key. A seal is one ``lib/precog/idr.py`` record -- GRIP's own signing, not a second
signing stack -- committing to the step chain's head hash and line count, written to a
ledger file inside the setup folder. It refuses outright, rather than writing a weaker
record, when the strongest reachable seal is not the caller's own dual key: see ``seal``.

Checking recomputes the chain from its raw lines and verifies every sealed record with
IDR's existing verifier (``lib/idr_forest.py``'s ``_verify_node``). It proves two things
only: this key signed these lines, and nothing has changed since. It does NOT prove that
the steps it records ran truthfully, or whose key signed them -- a fabricated chain,
correctly signed, checks PASS just the same (the W0 limit this design accepted rather
than hid; see ``test_a_fabricated_but_correctly_signed_chain_checks_pass``).

Two narrower limits follow from the same honesty, both tested rather than hidden. First,
the chain hashes each line's text without its line ending and skips blank lines, so any
change to a line is detected, whitespace included; only a rewritten line ending or an
inserted blank line is not (see ``recompute_chain`` and
``test_a_blank_line_inserted_after_sealing_checks_pass_anyway``).
Second, a party who can write both files can truncate the step file
and the ledger back to an earlier point together, so an earlier, honestly sealed state
re-appears as the current one -- PASS then reports the steps that remain, truthfully, with
no sign that later steps and their own seal ever existed (see
``test_truncating_both_files_to_an_earlier_seal_checks_pass_anyway``). Detecting a deleted
tail needs an anchor outside these two files -- a third place a later, independent reader
could compare against -- and this ledger deliberately keeps none: each seal is written to
``ledger_file`` with its own ``path``, which per ``lib/precog/idr.py``'s
``_advance_head`` never moves the installation's shared chain head, the same choice
``lib/tick_seal.py`` makes for its own side ledger. For the same reason the seals are not
chained to one another: each is a depth-0 record with no predecessor, so an earlier seal
removed from ``ledger_file`` is not detected either, and PASS rests on the newest seal.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.precog.idr import PrecogIDR

GENESIS_HASH = "0" * 64
# The only seal check() accepts: a tenant's own dual key, the seal seal() writes. The
# demo seal (hmac-sha256 under one shared key, lib/precog/idr.py's _demo_audit) also
# verifies, and it carries no tenant binding, so check() must refuse it as seal() does.
DUAL_SCHEME = "ed25519+ml-dsa-65"
PROVES = "this key signed these lines, and nothing has changed since"
DOES_NOT_PROVE = "that the steps it records ran truthfully, or whose key it is"


class ChainBroken(RuntimeError):
    """The step chain does not verify, so there is nothing a seal could truthfully
    commit to."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json(record: dict) -> str:
    """Deterministic JSON for hashing and storage -- sorted keys, no whitespace, every
    non-ASCII character escaped. ``ensure_ascii=True`` is load-bearing, not cosmetic:
    ``str.splitlines()`` (used by ``_lines`` to read the file back) also splits on
    U+2028, U+2029 and U+0085, so a step name or command holding one of them would be
    written as a real character and read back as two broken fragments -- a legitimate
    record failing as "not valid JSON". Escaped, those characters are an ordinary
    six-character ASCII sequence and round-trip through ``json.loads`` unchanged."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _line_hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _lines(step_file: Path) -> list[str]:
    """Every non-blank raw line in the step file, in file order; empty when the file
    does not exist yet."""
    if not step_file.exists():
        return []
    return [line for line in step_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_step(step_file: Path, step: str, command: str, exit_code: int, output: str) -> dict:
    """Append one step record, standard library only. Records the step's name, the
    command that was run, its exit code, the SHA-256 of its output, the time, and the
    SHA-256 of the line written before it (``GENESIS_HASH`` for the first). Returns the
    record written. This function does not run ``command`` itself -- it only records a
    result the caller already has.

    Flushes and ``fsync``s before returning, the same durability step
    ``lib/precog/idr.py``'s own ``append_idr`` takes for its ledger -- an installer can
    crash at any step, and a line only Python has buffered, never handed to the OS, is a
    line a crash loses silently."""
    lines = _lines(step_file)
    record = {
        "step": step,
        "command": command,
        "exit_code": exit_code,
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "ts": _utc_now(),
        "prev_hash": _line_hash(lines[-1]) if lines else GENESIS_HASH,
    }
    line = _canonical_json(record)
    step_file.parent.mkdir(parents=True, exist_ok=True)
    with step_file.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return record


@dataclass(frozen=True)
class ChainState:
    """Whether the step chain holds, in plain words why not, the head hash (what a seal
    commits to -- the last line's own hash), and how many steps are recorded."""

    ok: bool
    detail: str
    head_hash: str
    line_count: int


def recompute_chain(step_file: Path) -> ChainState:
    """Recompute the step chain from its raw lines, standard library only: every
    record's ``prev_hash`` must equal the SHA-256 of the line written before it, with
    ``GENESIS_HASH`` for the first. A changed line anywhere breaks the link from that
    line on -- this is what makes the chain tamper-evident on its own, before any
    signature is involved. The chain hashes each line's text without its line ending and
    skips blank lines, so any change to a line is detected, whitespace included; only a
    rewritten line ending or an inserted blank line is not caught here -- see the module
    docstring's limits paragraph."""
    lines = _lines(step_file)
    if not lines:
        return ChainState(False, "no steps are recorded yet", GENESIS_HASH, 0)
    expected = GENESIS_HASH
    for index, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return ChainState(
                False, f"step {index} is not valid JSON -- the file has been damaged",
                expected, index - 1,
            )
        if not isinstance(record, dict):
            return ChainState(
                False, f"step {index} is not a JSON object -- the file has been damaged",
                expected, index - 1,
            )
        if record.get("prev_hash") != expected:
            return ChainState(
                False,
                f"step {index} ({record.get('step', '?')!r}) does not chain to the step "
                "written before it",
                expected,
                index - 1,
            )
        expected = _line_hash(line)
    return ChainState(True, f"{len(lines)} step(s) chain correctly", expected, len(lines))


@contextmanager
def _refuse_demo_seal():
    """While active, ``lib.precog.idr`` raises instead of writing a demo-sealed record,
    for every downgrade reason -- no tenant, an unprovisioned tenant, or no crypto
    backend -- naming the reason and the fix. Restores whatever ``GRIP_IDR_REQUIRE_DUAL``
    held before, so a caller's own setting of it is never clobbered."""
    from lib.precog.idr import REQUIRE_DUAL_ENV

    previous = os.environ.get(REQUIRE_DUAL_ENV)
    os.environ[REQUIRE_DUAL_ENV] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(REQUIRE_DUAL_ENV, None)
        else:
            os.environ[REQUIRE_DUAL_ENV] = previous


def seal(step_file: Path, ledger_file: Path, *, tenant_id: str | None = None) -> PrecogIDR:
    """One IDR record committing to the step chain's current head hash and line count,
    signed under ``tenant_id`` and appended to ``ledger_file``.

    Raises ``ChainBroken`` when the step chain itself does not hold -- there would be
    nothing truthful to commit to. Raises the ``RuntimeError`` ``lib.precog.idr`` itself
    gives, naming the reason and the fix, when the strongest reachable seal is not the
    caller's own dual key (no tenant, an unprovisioned tenant, or no crypto backend). It
    never falls back to the shared demo key: a stranger's setup record is sealed with
    their own key, or it is not sealed at all.

    Records ``step_file.name``, never its full path, in the signed ``inputs`` -- the
    full path would carry the OS account name into a record that may later be shown or
    shared, and the file name is all a reader needs to know which chain a seal is over."""
    from lib.precog.idr import append_idr, build_idr

    state = recompute_chain(step_file)
    if not state.ok:
        raise ChainBroken(state.detail)
    with _refuse_demo_seal():
        idr = build_idr(
            prompt="grip-suite setup record",
            fingerprint="suite-install-setup",
            decision={"head_hash": state.head_hash, "line_count": state.line_count},
            predecessor_idr=None,
            depth=0,
            kind="suite-setup-record",
            inputs={"step_file": step_file.name},
            tenant_id=tenant_id,
        )
    append_idr(idr, path=ledger_file)
    return idr


@dataclass(frozen=True)
class CheckResult:
    """A plain PASS or FAIL, with a stable ``reason`` code and a human ``detail`` --
    only on PASS, ``detail`` also says what the record proves and what it does not.

    ``reason`` is one of ``pass``, ``chain-broken``, ``no-seal``,
    ``unreadable-ledger-line``, ``wrong-kind``, ``weak-seal``, ``unverified``,
    ``head-mismatch`` or ``tenant-mismatch``. Tests assert on ``reason``, never on ``detail``'s wording --
    the wording may change; the code is the stable contract."""

    passed: bool
    detail: str
    reason: str


def _nonblank_ledger_lines(ledger_file: Path) -> int:
    """How many non-blank lines ``ledger_file`` holds, counted the way
    ``read_idr_chain`` reads it -- iterating the open file, never
    ``str.splitlines()``. ``read_idr_chain`` SKIPS any line it cannot parse into a
    record rather than raising (``lib/precog/idr.py:592``), so comparing this count
    with how many records it actually returned is what makes a damaged line visible to
    ``check`` instead of silently dropped."""
    if not ledger_file.exists():
        return 0
    with ledger_file.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def _chain_and_ledger_gate(step_file: Path, ledger_file: Path):
    """The recomputed chain state and the sealed records, once both files are intact
    enough to compare against each other -- else the FAIL ``CheckResult`` that explains
    which one is not."""
    from lib.precog.idr import read_idr_chain

    state = recompute_chain(step_file)
    if not state.ok:
        return CheckResult(
            False, f"the step chain does not hold: {state.detail}", reason="chain-broken"
        )

    records = read_idr_chain(path=ledger_file)
    if _nonblank_ledger_lines(ledger_file) != len(records):
        return CheckResult(
            False,
            "the ledger file holds a line that could not be read as a record -- it may "
            "have been damaged or tampered with",
            reason="unreadable-ledger-line",
        )
    if not records:
        return CheckResult(False, "no sealed record exists for this setup yet", reason="no-seal")
    return state, records


def _first_invalid_record(records: list[PrecogIDR]):
    """The first record that is not a ``suite-setup-record``, is not sealed with a
    tenant's own dual key, or does not verify, as a FAIL ``CheckResult`` -- or ``None``
    once every record is clean."""
    from lib.audit_chain import Verdict
    from lib.idr_forest import _verify_node

    for idr in records:
        if idr.kind != "suite-setup-record":
            return CheckResult(
                False,
                f"record {idr.id} is a {idr.kind!r} record, not a suite-setup-record",
                reason="wrong-kind",
            )
        scheme = idr.audit.get("scheme") if isinstance(idr.audit, dict) else None
        if scheme != DUAL_SCHEME:
            return CheckResult(
                False,
                f"record {idr.id} is sealed with {scheme!r}, not a tenant's own dual key",
                reason="weak-seal",
            )
        verdict = _verify_node(idr)
        if verdict != Verdict.VERIFIED:
            return CheckResult(
                False, f"record {idr.id} did not verify ({verdict.value})", reason="unverified"
            )
    return None


def _match_newest(newest_idr: PrecogIDR, state: ChainState, expected_tenant_id: str) -> CheckResult:
    """The final two PASS gates: the newest record still commits to the chain's current
    head hash and line count, and it names ``expected_tenant_id``. Assumes ``records``
    (and so ``newest_idr``) is non-empty -- ``check`` only calls this after
    ``_chain_and_ledger_gate`` has already returned a non-empty ``records``."""
    newest = newest_idr.decision
    if newest.get("head_hash") != state.head_hash or newest.get("line_count") != state.line_count:
        return CheckResult(
            False,
            "the newest sealed record no longer matches the step chain -- something "
            "changed after it was sealed",
            reason="head-mismatch",
        )
    found_tenant = newest_idr.audit.get("tenant_id")
    if found_tenant != expected_tenant_id:
        return CheckResult(
            False,
            f"the newest sealed record was signed by tenant {found_tenant!r}, not the "
            f"expected tenant {expected_tenant_id!r}",
            reason="tenant-mismatch",
        )
    return CheckResult(
        True,
        f"{state.line_count} step(s), sealed and verified. This proves {PROVES}. "
        f"It does not prove {DOES_NOT_PROVE}.",
        reason="pass",
    )


def check(step_file: Path, ledger_file: Path, *, expected_tenant_id: str) -> CheckResult:
    """Recompute the step chain and verify every sealed record in ``ledger_file`` with
    IDR's own verifier. PASS only when: the chain holds; every non-blank ledger line is
    a readable record; at least one sealed record exists; every record is a
    ``suite-setup-record``, sealed with a tenant's own dual key (``DUAL_SCHEME``), that
    verifies; the newest commits to the chain's current
    head hash and line count; and the newest names ``expected_tenant_id``. See the
    module docstring's limits paragraph for what PASS does not rule out.

    ``expected_tenant_id`` is REQUIRED, with no default. ``_verify_node`` checks a
    record's signature against the keys of the tenant NAMED IN ITS OWN
    ``audit.tenant_id`` (``pq_dual_sign.dual_verify`` loads that tenant's pinned keys,
    ``lib/pq_dual_sign.py:202``, and binds the record's declared fingerprints to them),
    so VERIFIED proves the record was signed with THAT tenant's own keys.
    ``tenant_id`` sits in the unsigned ``audit`` block, but a record relabelled to
    another tenant is checked against THAT tenant's keys and fingerprints, so it fails
    to verify before this comparison is even reached -- it cannot be forged by editing
    the label alone. Without comparing the verified tenant to ``expected_tenant_id``,
    though, a record validly signed by a DIFFERENT, equally legitimate tenant on this
    same machine would still pass unremarked (AGORA, Qwen4-Max). No caller outside
    this module's own tests calls ``check`` anywhere in this repository today -- the
    installer step that will (GRIP#9921's "step 4") has not been built yet -- so
    there is nothing this signature change breaks now; it closes the gap before that
    caller exists, never after (GRIP#9926 AGORA, Qwen4-Max/Gemini: tested against a
    repo-wide search, this does not hold as a regression)."""
    gate = _chain_and_ledger_gate(step_file, ledger_file)
    if isinstance(gate, CheckResult):
        return gate
    state, records = gate

    invalid = _first_invalid_record(records)
    if invalid is not None:
        return invalid

    return _match_newest(records[-1], state, expected_tenant_id)
