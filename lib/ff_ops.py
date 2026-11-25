"""FF FinancialDataRegistry control kernel — Alchemy CLI thin wrapper.

V>>-criterion "EASILY": one bash line per FF op. This module is the Python
kernel; `bin/ff-ops` is the CLI shim; `skills/ff-ops/SKILL.md` is the GRIP
skill entry point.

Composes:
  - Alchemy CLI (`/opt/homebrew/bin/alchemy --json --no-interactive`)
  - lib/precog/orchestrator.py (deferred — IDR audit chain per tx)
  - lib/kono.py MemoryBlock (deferred — event capture via ff-watch)
  - lib/scrollback_lru.py (deferred — getRecord response cache)
  - lib/fold_compress.py (deferred — event-log Banach fold before stash)

Plan: plans/ff-grip-alchemy-control-2026-05-24.md
Hypotheses: H-FF-OPS-1..5 (deadlines 2026-06-08 → 2026-07-01)

CC budget: ≤4 per function. LOC ≤120 (current ~115).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from Crypto.Hash import keccak  # pycryptodome — already a venv dep; real Ethereum keccak-256

# Portable Alchemy CLI resolution: ALCHEMY_BIN env override -> PATH lookup ->
# macOS Homebrew default. Avoids hardcoded /opt/homebrew which breaks on
# Linux/CI/Windows (AGORA depth-1 finding, R0-verified — the hardcoded literal
# WAS present pre-fix and would FileNotFoundError on any non-Mac host).
ALCHEMY = os.environ.get("ALCHEMY_BIN") or shutil.which("alchemy") or "/opt/homebrew/bin/alchemy"
DEFAULT_NETWORK = "sepolia"
STATE_DIR = Path.home() / ".claude" / "state" / "ff-ops"


def _ensure_state_dir() -> Path:
    """Lazy-create state dir. CC=1."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR


@dataclass(frozen=True)
class FFResult:
    """Single op envelope. Immutable. Returned by every call/read."""

    ok: bool
    method: str
    args: tuple
    summary: str
    tx_hash: Optional[str] = None
    json_path: Optional[Path] = None
    raw: Optional[dict] = None


def record_id(label: str) -> str:
    """Deterministic bytes32 id from a record label. CC=1.

    keccak-256 (Ethereum/Solidity standard) so the id MATCHES the on-chain
    recordId the FinancialDataRegistry derives via Solidity ``keccak256(bytes(label))``.
    NOT NIST SHA3-256 — they differ by the padding byte (keccak 0x01 vs SHA3 0x06)
    and produce a DIFFERENT digest for every input, ASCII included. Uses
    pycryptodome's ``Crypto.Hash.keccak`` (already a venv dep — no new dependency).
    """
    h = keccak.new(digest_bits=256)
    h.update(label.encode("utf-8"))
    return "0x" + h.hexdigest()


def _alchemy(args: list[str], network: str = DEFAULT_NETWORK) -> dict[str, Any]:
    """Single Alchemy CLI invocation. CC=2. Always --json --no-interactive.

    Fail-loud: non-zero rc raises RuntimeError with stderr; never silent.
    """
    cmd = [ALCHEMY, "--json", "--no-interactive"] + args + ["-n", network]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            f"alchemy CLI failed (rc={proc.returncode}): {proc.stderr[:500]}"
        )
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def call(contract: str, method: str, *args: str) -> FFResult:
    """State-changing contract call (publishRecord, updateRecord). CC=3."""
    cli_args = ["evm", "contract", "call", contract, method]
    if args:
        cli_args.extend(["--args"] + list(args))
    result = _alchemy(cli_args)
    tx_hash = result.get("transactionHash") or result.get("hash")
    json_path = None
    if tx_hash:
        json_path = _ensure_state_dir() / f"{tx_hash}.json"
        json_path.write_text(json.dumps(result, indent=2))
    return FFResult(
        ok=bool(tx_hash),
        method=method,
        args=tuple(args),
        summary=f"{method}({','.join(args[:2])}...) → tx={tx_hash[:10] if tx_hash else 'NONE'}",
        tx_hash=tx_hash,
        json_path=json_path,
        raw=result,
    )


def read(contract: str, method: str, *args: str) -> FFResult:
    """Read-only view call. No state mutation, no file write. CC=2."""
    cli_args = ["evm", "contract", "read", contract, method]
    if args:
        cli_args.extend(["--args"] + list(args))
    result = _alchemy(cli_args)
    return FFResult(
        ok=True,
        method=method,
        args=tuple(args),
        summary=f"{method}({','.join(args[:2])}) → {str(result)[:120]}",
        raw=result,
    )


def watch(contract: str, from_block: int = 0) -> Iterator[dict[str, Any]]:
    """Yield event logs from given block. Generator. CC=3."""
    cli_args = [
        "evm", "logs",
        "--address", contract,
        "--from-block", str(from_block),
    ]
    result = _alchemy(cli_args)
    logs = result.get("logs", []) if isinstance(result, dict) else []
    for log in logs:
        yield log


def status(contract: str) -> dict[str, Any]:
    """Composite snapshot: address + record count + state-dir. CC=2."""
    ids_result = read(contract, "getAllRecordIds")
    return {
        "contract": contract,
        "network": DEFAULT_NETWORK,
        "record_count_summary": ids_result.summary,
        "state_dir": str(_ensure_state_dir()),
    }
