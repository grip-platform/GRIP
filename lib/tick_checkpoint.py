"""lib/tick_checkpoint.py: the tick's first granted act, a recovery checkpoint of a hot or stuck session.

The gate is deterministic and every clause must hold: a grant for ``checkpoint`` that verified
on this tick (``lib/tick_grant.py``), whose triggers name one of the transcript's incident kinds
(``fleet:hot`` or ``fleet:stuck``); no checkpoint of this transcript under the grants in force;
and room under the grant's storage cap, metadata included. The act copies the transcript's bytes
through its last complete record (the last newline) into a folder the tick owns, with the offset,
the byte count and the SHA-256 of the copy, keeps the signed grant that allowed it beside the
copy, and seals the act as an IDR leaf. A checkpoint whose seal cannot be written is not a
checkpoint: the folder is removed and the failure reported, because an unrecorded act is theatre
(``rules/cryptographic-causation.md``). The act reads the source and never writes to it, never
pauses or signals the session, and never presents the copy as a restorable workspace: it is
evidence kept before logs rotate or the session rewrites its own history. The reversal is to
remove the folder (``grip-tick-checkpoint remove <name>``); the tick notices the folder is gone
and the transcript becomes eligible again. One transcript that is both hot and stuck is one
checkpoint with both causes on record; a session's main transcript and its subagent transcripts
are distinct transcripts and are preserved separately; a transcript that is in trouble again later
(a new hot hour, a different repeated error) is a new incident and gets a new checkpoint.

Records. Each act is sealed on the tick's own side ledgers, ``state/grip-tick/acts.idr.jsonl``
(the decision leaf, verifiable with ``bin/grip-idr-verify-external``) and
``state/grip-tick/acts.context.jsonl`` (the paired memory-chain node that states what the tick
believed: the grant, the incident, the cap and the bytes held), threaded by their own head
pointers. A side ledger never advances the installation's main decision head, so a root leaf here
breaks no lineage there (``lib/precog/idr.py::_advance_head``). The seal is written only after the
folder's final size, index included, is known to fit the cap, so no leaf ever asserts a checkpoint
that was rolled back.

Falsifiable prediction (H843, registered with the hypothesis engine): over fourteen days of
grants, no checkpoint folder set exceeds its grant's cap; one breach refutes this implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

_LIB = Path(__file__).resolve().parent
for _p in (_LIB, _LIB.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from claude_dir import get_claude_dir  # noqa: E402
from jcs import dumps as canonical_dumps  # noqa: E402


class SealFailed(RuntimeError):
    """The act's records could not be written; the copy was removed and the act did not happen."""


class CapExceeded(RuntimeError):
    """The folder's final size would breach the grant's cap; the copy was removed before any seal."""


class CopyShort(RuntimeError):
    """The transcript shrank while it was being copied; the copy was removed and the attempt will be retried."""

TAIL = 65536
CHUNK = 1 << 20
METADATA_RESERVE = 8192  # room inside the cap for index.json and grant.json
ID_PLACEHOLDER = "x" * 40  # longer than any record id, so the final index is never larger than the one sized
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def checkpoints_dir() -> Path:
    return get_claude_dir() / "state" / "grip-tick" / "checkpoints"


def acts_ledger() -> Path:
    return get_claude_dir() / "state" / "grip-tick" / "acts.idr.jsonl"


def acts_context() -> Path:
    return get_claude_dir() / "state" / "grip-tick" / "acts.context.jsonl"


def acts_context_head() -> Path:
    return get_claude_dir() / "state" / "grip-tick" / "acts.context.head"


def last_complete_offset(path: Path, size: Optional[int] = None) -> int:
    """Bytes up to and including the last newline, so the copy ends on a whole record; 0 when there is none.

    Scans backwards in fixed chunks, so one oversized final record costs one chunk at a time, never
    the whole transcript in memory. A read that comes up short means the transcript shrank under
    the scan, and that is CopyShort, never a transcript with no complete record.
    """
    size = path.stat().st_size if size is None else size
    with path.open("rb") as fh:
        end = size
        while end > 0:
            start = max(0, end - CHUNK)
            fh.seek(start)
            chunk = fh.read(end - start)
            if len(chunk) != end - start:
                raise CopyShort(f"the transcript shrank during the scan (read {len(chunk)} of {end - start} bytes at {start})")
            cut = chunk.rfind(b"\n")
            if cut >= 0:
                return start + cut + 1
            end = start
    return 0


def folder_bytes(root: Path) -> int:
    """Every byte the tick's checkpoints hold, the number the grant's cap is checked against."""
    if not root.is_dir():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def transcript_id(path: str) -> str:
    """Six hex digits of the transcript path, so two transcripts of one session never share a name."""
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:6]


def checkpoint_name(owner: str, now: float, path: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    return f"{owner}-{stamp}-{transcript_id(path)}"


def _copy(src: Path, dst: Path, offset: int) -> Tuple[int, str]:
    digest, copied = hashlib.sha256(), 0
    with src.open("rb") as fh, dst.open("wb") as out:
        while copied < offset:
            chunk = fh.read(min(CHUNK, offset - copied))
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            copied += len(chunk)
    return copied, digest.hexdigest()


def seal(index: dict, idr_path: Path, context_path: Path, context_head: Path) -> Tuple[str, str]:
    """The act as a decision leaf on the tick's ledger and a paired memory node; raises SealFailed on any failure."""
    try:
        from dataclasses import asdict
        from lib import context_chain
        from lib.precog.idr import append_idr, build_idr, content_addr
        idr = build_idr(prompt="grip-tick checkpoint act", fingerprint="tick-checkpoint",
                        decision={"act": "checkpoint", "incident_keys": index["incident_keys"], "folder": index["folder"],
                                  "sha256": index["sha256"], "bytes": index["bytes"]},
                        predecessor_idr=None, depth=0, kind="tick-act-checkpoint",
                        inputs={"grant_sha256": index["grant_sha256"], "source": index["source"], "offset": index["offset"],
                                "kinds": index["kinds"], "cap_bytes": index["cap_bytes"], "held_bytes": index["held_bytes"]})
        append_idr(idr, path=idr_path)
        node = context_chain.checkpoint(
            next_step=None,
            summary=(f"tick checkpoint {index['folder']}: {index['bytes']} bytes of {index['source']} under grant "
                     f"{index['grant_sha256'][:12]}, cap {index['cap_bytes']} bytes, {index['held_bytes']} held before")[:600],
            title="tick checkpoint act", tier="feedback", records_idr=content_addr(asdict(idr)),
            path=context_path, head_pointer=context_head)
        return idr.id, node.id
    except Exception as exc:  # noqa: BLE001  one answer: the act is not recorded, so it did not happen
        raise SealFailed(f"{type(exc).__name__}: {exc}"[:200]) from exc


def write_checkpoint(root: Path, incident: dict, grant: dict, now: float, cap_bytes: int, held_bytes: int,
                     idr_path: Optional[Path] = None, context_path: Optional[Path] = None,
                     context_head: Optional[Path] = None) -> dict:
    """Copy the transcript through its last complete record into a new folder, keep the signed grant beside it, seal the act.

    ``incident`` is one transcript's ``{"path", "owner", "kinds", "keys"}``; ``grant`` is the signed grant in
    force; ``cap_bytes`` and ``held_bytes`` are the grant's cap and what the checkpoints already hold. The
    folder's final size is checked against the cap before anything is sealed. On any failure the folder is
    removed and the error raised: CapExceeded, CopyShort, SealFailed, or the OSError itself.
    """
    src = Path(incident["path"])
    offset = last_complete_offset(src)
    if offset == 0:
        raise CopyShort("no complete record to copy: the transcript is empty or was rewritten under the scan")
    name = checkpoint_name(incident["owner"], now, incident["path"])
    folder = root / name
    folder.mkdir(parents=True, exist_ok=False)
    try:
        copied, sha = _copy(src, folder / "transcript.jsonl", offset)
        if copied != offset:
            raise CopyShort(f"the transcript shrank during the copy ({copied} of {offset} bytes)")
        (folder / "grant.json").write_text(json.dumps(grant, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        index = {"folder": name, "source": str(src), "owner": incident["owner"], "incident_keys": list(incident["keys"]),
                 "kinds": list(incident["kinds"]), "offset": offset, "bytes": copied, "sha256": sha,
                 "grant_sha256": hashlib.sha256(canonical_dumps(grant)).hexdigest(), "cap_bytes": int(cap_bytes),
                 "held_bytes": int(held_bytes), "created": time.strftime(TIME_FORMAT, time.gmtime(now)),
                 "idr_leaf": ID_PLACEHOLDER, "memory_node": ID_PLACEHOLDER}
        final = folder_bytes(folder) + len(json.dumps(index, indent=1, sort_keys=True)) + 1
        if held_bytes + final > cap_bytes:
            raise CapExceeded(f"{held_bytes} held + {final} for this folder > cap {cap_bytes}")
        index["idr_leaf"], index["memory_node"] = seal(index, idr_path or acts_ledger(), context_path or acts_context(),
                                                       context_head or acts_context_head())
        (folder / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return index


def list_checkpoints(root: Path) -> List[dict]:
    out = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*/index.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            out.append({"folder": path.parent.name, "error": "index unreadable"})
    return out


def remove_checkpoint(root: Path, name: str) -> bool:
    """The reversal: delete one checkpoint folder, and only a folder directly under the root."""
    if not name or name != Path(name).name:
        return False
    folder = (root / name).resolve()
    if folder.parent != root.resolve() or not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="List or remove the always-on tick's recovery checkpoints.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="every checkpoint folder with its source, size, hash and seal")
    sub.add_parser("remove", help="delete one checkpoint folder (the act's reversal)").add_argument("name")
    args = ap.parse_args(argv)
    root = checkpoints_dir()
    if args.cmd == "remove":
        ok = remove_checkpoint(root, args.name)
        print(f"grip-tick-checkpoint: {'removed' if ok else 'no such checkpoint'} {args.name}")
        return 0 if ok else 2
    rows = list_checkpoints(root)
    print(f"checkpoints under {root}: {len(rows)}, {folder_bytes(root) / 1048576:.1f} MB")
    for r in rows:
        print(f"  {r.get('folder')}  {r.get('owner', '?')}  {r.get('bytes', 0) / 1048576:.1f} MB  sha256 {str(r.get('sha256', ''))[:12]}"
              f"  leaf {r.get('idr_leaf') or 'none'}  {r.get('created', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
