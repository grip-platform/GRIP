"""Continuously anchor GRIP's moat onto Bitcoin via OpenTimestamps.

The moat is the set of tamper-evident records GRIP's whole value rests on: the
IDR forest (what was decided), the memory/context chain (what was believed
when), the hypothesis ledger (beliefs and their falsification outcomes), the
learning substrate, and the sealed council verdicts. This module snapshots
their cryptographic fingerprints into ONE manifest file and stamps that file's
sha256 onto Bitcoin with OpenTimestamps — so a third party can later prove
"these records existed in exactly this state at or before block N" without
trusting GRIP or any server.

Design invariants (each carries a test in tests/test_moat_anchor.py):

- **Anchor-on-change.** A new manifest is stamped only when the combined moat
  fingerprint changed since the last manifest. Re-running on an unchanged moat
  is a no-op, so the launchd agent can fire as often as it likes without
  emitting spurious anchors.
- **Manifests are immutable once written.** ``ots stamp`` commits to the
  file's bytes at stamp time; rewriting the manifest afterwards would
  invalidate its own receipt. Stamp/upgrade status therefore lives in an
  append-only sidecar log (``anchor-log.jsonl``), never inside the manifest.
- **Fail-open on network.** If the OTS calendars are unreachable the manifest
  is still written and logged with ``stamp="deferred"``; the next run retries
  every deferred manifest before considering new work.
- **Absence is recorded, never silent.** A configured source that does not
  exist on this machine appears in the manifest with ``state="absent"`` — the
  manifest says exactly what it covers, and a missing chain is visible instead
  of quietly narrowing the claim.
- **Tiered verification, trust always named.** Without a local Bitcoin node,
  ``upgrade`` parses the receipt's attestations (``ots info``) and records a
  Bitcoin block height at tier "attestation-present" — explicitly NOT full
  node verification, mirroring GRASP's tiered honesty.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

# (label, path relative to the GRIP root, kind). ``dir`` sources are
# fingerprinted as a sorted name:hash list so ordering never matters.
# der-receipts and prove-it have no continuous store yet — they stay listed so
# the manifest records their absence truthfully until those stores exist.
MOAT_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("idr-forest", "state/precog/idr.jsonl", "file"),
    ("memory-chain", "state/precog/context.jsonl", "file"),
    ("memory-chain-archive", "state/precog/context-archive.jsonl", "file"),
    ("memory-chain-head", "state/precog/context-HEAD.txt", "file"),
    ("hypotheses", "learning/hypotheses.jsonl", "file"),
    ("hypothesis-verifications", "learning/hypothesis_verifications.jsonl", "file"),
    ("insights", "learning/insights.jsonl", "file"),
    ("council-seals", "state/sovereign-council", "dir"),
    ("der-receipts", "state/der", "dir"),
    ("prove-it", "state/prove-it", "dir"),
)

ANCHOR_SUBDIR = "state/anchors"
LOG_NAME = "anchor-log.jsonl"
_CHUNK = 1 << 20


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _fingerprint_dir(path: Path) -> tuple[str, int]:
    """Order-independent fingerprint: sha256 over sorted "name:hash" lines."""
    entries = sorted(
        f"{p.relative_to(path)}:{_sha256_file(p)}"
        for p in path.rglob("*") if p.is_file()
    )
    joined = "\n".join(entries).encode()
    return hashlib.sha256(joined).hexdigest(), len(entries)


def _snapshot_source(root: Path, label: str, rel: str, kind: str) -> dict:
    p = root / rel
    if not p.exists():
        return {"label": label, "path": rel, "state": "absent"}
    if kind == "dir":
        digest, count = _fingerprint_dir(p)
        return {"label": label, "path": rel, "state": "present",
                "sha256": digest, "files": count}
    return {"label": label, "path": rel, "state": "present",
            "sha256": _sha256_file(p), "bytes": p.stat().st_size}


def build_manifest(root: Path) -> dict:
    """Snapshot every moat source under ``root`` into a manifest dict."""
    sources = [_snapshot_source(root, label, rel, kind)
               for label, rel, kind in MOAT_SOURCES]
    canonical = json.dumps(sources, sort_keys=True, separators=(",", ":"))
    return {
        "kind": "grip-moat-manifest",
        "version": 1,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "moat_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "sources": sources,
    }


def _anchor_dir(root: Path) -> Path:
    d = root / ANCHOR_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


# How much of the tail of the log to read to recover the last few lines. Sized
# far above any plausible run of long entries, and still a constant.
_TAIL_WINDOW = 64 * 1024


def _tail_lines(path: Path, count: int, window: int = _TAIL_WINDOW) -> list[str]:
    """Last ``count`` lines of ``path``, reading only the final ``window`` bytes.

    The log is append-only and never rotated, and it does not grow linearly:
    ``upgrade_receipts`` writes one line per receipt per run, so the rate rises
    with the number of receipts. Measured on this machine 2026-08-10, 4.7 days
    after the agent was installed: 1,235 lines / 286 KB, of which 1,120 were
    per-run upgrade entries re-recording the same 27 receipts.

    ``status`` wants five lines out of that, so reading the file whole to slice
    the end is work proportional to the entire history. Seeking instead makes
    the read constant regardless of how long the agent has been anchoring.

    A window that lands mid-line drops that partial first line rather than
    reporting a truncated record — the log is JSON per line and half a line is
    not a record. Deliberately does NOT truncate or rotate the log itself:
    these entries are the audit trail of a tamper-evidence system, and
    discarding them is a policy decision for the operator, not a side effect of
    reading a status summary.
    """
    if count <= 0:
        return []
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            start = max(0, size - window)
            fh.seek(start)
            chunk = fh.read()
        lines = chunk.decode("utf-8", errors="replace").splitlines()
        # Only a window that began mid-file can have clipped its first line.
        if start > 0 and lines:
            lines = lines[1:]
        return lines[-count:]
    except OSError:
        return []


def _log(anchor_dir: Path, **fields) -> None:
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}
    with open(anchor_dir / LOG_NAME, "a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def latest_manifest(anchor_dir: Path) -> dict | None:
    manifests = sorted(anchor_dir.glob("moat-*.json"))
    if not manifests:
        return None
    try:
        return json.loads(manifests[-1].read_text())
    except (json.JSONDecodeError, OSError) as exc:
        # A corrupt latest (partial write, disk error) must NOT crash the
        # daemon forever — log, return None, and the next cycle anchors a
        # fresh manifest over it (self-healing).
        _log(anchor_dir, event="corrupt-manifest", manifest=manifests[-1].name,
             detail=f"{type(exc).__name__}: {exc}"[:200])
        return None


def should_anchor(new: dict, last: dict | None) -> bool:
    return last is None or new["moat_sha256"] != last["moat_sha256"]


def _stamp(path: Path, ots_bin: str) -> tuple[bool, str]:
    """Run ``ots stamp`` on ``path``. Returns (ok, detail)."""
    try:
        r = subprocess.run([ots_bin, "stamp", str(path)],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if r.returncode == 0 and path.with_suffix(path.suffix + ".ots").exists():
        return True, "stamped"
    return False, (r.stderr or r.stdout).strip()[:300]


def write_and_stamp(manifest: dict, anchor_dir: Path, ots_bin: str = "ots") -> Path:
    """Write the manifest (immutable from here on) and try to stamp it."""
    name = f"moat-{manifest['created_utc'].replace(':', '').replace('-', '')}.json"
    path = anchor_dir / name
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    ok, detail = _stamp(path, ots_bin)
    _log(anchor_dir, event="stamp", manifest=name,
         moat_sha256=manifest["moat_sha256"],
         stamp="ok" if ok else "deferred", detail=detail)
    return path


def stamp_pending(anchor_dir: Path, ots_bin: str = "ots") -> int:
    """Retry every manifest that has no .ots receipt yet. Returns retries done."""
    retried = 0
    for m in sorted(anchor_dir.glob("moat-*.json")):
        if m.with_suffix(".json.ots").exists():
            continue
        ok, detail = _stamp(m, ots_bin)
        _log(anchor_dir, event="stamp-retry", manifest=m.name,
             stamp="ok" if ok else "deferred", detail=detail)
        retried += 1
    return retried


_ATTESTATION_RE = re.compile(r"BitcoinBlockHeaderAttestation\D*(\d+)")


def _parse_block_height(info_text: str) -> int | None:
    """Extract the Bitcoin block height from ``ots info`` output, if attested.

    Anchored capture, not digit-scraping: the FIRST number after the
    attestation token is the height, so a line carrying other digits
    (timestamps, counts) can never concatenate into a false height."""
    m = _ATTESTATION_RE.search(info_text)
    return int(m.group(1)) if m else None


def upgrade_receipts(anchor_dir: Path, ots_bin: str = "ots") -> list[dict]:
    """``ots upgrade`` each receipt, then parse attestation state from
    ``ots info``. Tier is named explicitly: attestation-present is NOT full
    Bitcoin-node verification."""
    results = []
    for ots in sorted(anchor_dir.glob("moat-*.json.ots")):
        subprocess.run([ots_bin, "upgrade", str(ots)],
                       capture_output=True, text=True, timeout=120)
        info = subprocess.run([ots_bin, "info", str(ots)],
                              capture_output=True, text=True, timeout=60)
        height = _parse_block_height(info.stdout)
        entry = {"receipt": ots.name,
                 "state": "bitcoin-attested" if height is not None
                          else "pending-calendar",
                 "block_height": height,
                 "tier": "attestation-present (parse of ots info; full "
                         "verification needs a Bitcoin node)"}
        results.append(entry)
        _log(anchor_dir, event="upgrade", **entry)
    return results


def _backup(paths: list[Path]) -> None:
    """Receipts are not re-derivable — copy them out per paramount-backups.
    Non-fatal on any failure (the anchor itself already succeeded)."""
    grip_backup = get_claude_dir() / "bin" / "grip-backup"
    if not grip_backup.exists():
        return
    try:
        subprocess.run([str(grip_backup), *map(str, paths),
                        "--label", "moat-anchor"],
                       capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run(root: Path | None = None, ots_bin: str = "ots") -> dict:
    """One anchor cycle: retry deferred, build, stamp if changed, upgrade."""
    root = root or get_claude_dir()
    anchor_dir = _anchor_dir(root)
    retried = stamp_pending(anchor_dir, ots_bin)
    manifest = build_manifest(root)
    last = latest_manifest(anchor_dir)
    outcome = {"retried_deferred": retried, "anchored": False,
               "moat_sha256": manifest["moat_sha256"]}
    if should_anchor(manifest, last):
        path = write_and_stamp(manifest, anchor_dir, ots_bin)
        receipt = path.with_suffix(".json.ots")
        _backup([path] + ([receipt] if receipt.exists() else []))
        # "anchored" means STAMPED, not merely written: a deferred stamp
        # (calendars unreachable) must not report success upstream.
        outcome["anchored"] = receipt.exists()
        outcome["manifest"] = path.name
        outcome["stamp"] = "ok" if receipt.exists() else "deferred"
    else:
        _log(anchor_dir, event="unchanged", moat_sha256=manifest["moat_sha256"])
    outcome["upgrades"] = upgrade_receipts(anchor_dir, ots_bin)
    return outcome


def status(root: Path | None = None) -> dict:
    root = root or get_claude_dir()
    anchor_dir = root / ANCHOR_SUBDIR
    manifests = sorted(anchor_dir.glob("moat-*.json"))
    receipts = sorted(anchor_dir.glob("moat-*.json.ots"))
    log_path = anchor_dir / LOG_NAME
    tail = _tail_lines(log_path, 5) if log_path.exists() else []
    return {"manifests": len(manifests), "receipts": len(receipts),
            "unstamped": len(manifests) - len(receipts),
            "latest": manifests[-1].name if manifests else None,
            "log_tail": tail}


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "run":
        print(json.dumps(run(), indent=2))
        return 0
    if cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if cmd == "upgrade":
        print(json.dumps(upgrade_receipts(_anchor_dir(get_claude_dir())), indent=2))
        return 0
    print("usage: moat_anchor.py [run|status|upgrade]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
