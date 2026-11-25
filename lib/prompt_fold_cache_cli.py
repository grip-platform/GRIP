"""Operator CLI for PromptFoldCache (exposed as bin/grip-cache).

Subcommands (plan: GRIP Integration):
  status              hit-rate style metrics from the record ledger
  prove KEY           print fold trace + hashes and RE-VERIFY them from disk
  clear --scope local delete local records + blobs (refuses any other scope)
  simulate            run the benchmark scenarios (passthrough to scripts/)

Every claim `prove` prints is recomputed from the record + blobs alone — a
tampered blob prints FAIL and exits 1 (replayability is the product).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from prompt_fold_cache import (  # noqa: E402
    BLOBS_NAME,
    clear_local,
    resolve_cache_dir,
    stats,
    _load_records,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cmd_status(args: argparse.Namespace) -> int:
    s = stats(cache_dir=args.cache_dir)
    print(json.dumps(s, indent=2))
    return 0


def _verify_blob(root: Path, sha: str, label: str) -> bool:
    path = root / BLOBS_NAME / f"{sha}.bin"
    if not path.exists():
        print(f"  {label}: blob missing ({sha[:16]}…)  FAIL")
        return False
    ok = _sha(path.read_bytes()) == sha
    print(f"  {label}: {sha}  {'PASS' if ok else 'FAIL (hash mismatch)'}")
    return ok


def cmd_prove(args: argparse.Namespace) -> int:
    root = resolve_cache_dir(args.cache_dir)
    matches = [r for r in _load_records(root)
               if r.get("exact_key", "").startswith(args.key)]
    if not matches:
        print(f"No record with exact_key starting {args.key!r}")
        return 1
    record = matches[-1]
    print(f"record v={record.get('v')} route={record.get('route')} "
          f"task={record.get('task_type')} risk={record.get('risk_class')} "
          f"created={record.get('created_at')}")
    print(f"exact_key: {record.get('exact_key')}")
    print("fold_trace (iter, bytes, sha256):")
    for step in record.get("fold_trace", []):
        print(f"  {step[0]:>2}  {step[1]:>7}  {step[2]}")
    print(f"idr_ref: {record.get('idr_ref')}")
    print(f"happi_envelope_sha256: {record.get('happi_envelope_sha256')}")
    if not record.get("stored_output"):
        print("privacy_scope=none record: hashes only, no blobs to verify.")
        return 0
    print("re-verifying content-addressed blobs from disk:")
    ok = _verify_blob(root, record.get("folded_sha256", ""), "folded prompt")
    ok = _verify_blob(root, record.get("output_sha256", ""), "output") and ok
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_clear(args: argparse.Namespace) -> int:
    if args.scope != "local":
        print(f"Refusing to clear scope {args.scope!r} — only 'local' is "
              "supported (plan: /cache-clear --scope local).")
        return 1
    counts = clear_local(cache_dir=args.cache_dir)
    print(f"cleared: {counts['records_deleted']} records, "
          f"{counts['blobs_deleted']} blobs")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    script = Path(__file__).resolve().parents[1] / "scripts" / \
        "benchmark_prompt_fold_cache.py"
    if not script.exists():
        print(f"Benchmark script not found at {script} — it ships alongside "
              "this CLI; reinstall or pull the repo.")
        return 1
    return subprocess.run(
        [sys.executable, str(script), "--json"], check=False).returncode


COMMANDS = {
    "status": cmd_status,
    "prove": cmd_prove,
    "clear": cmd_clear,
    "simulate": cmd_simulate,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grip-cache",
        description="PromptFoldCache operator surface (Banach fold memoisation)")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="override the cache root (default: env "
                             "GRIP_PROMPT_FOLD_CACHE_DIR or state/cache/prompt-fold)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="metrics from the record ledger")
    prove = sub.add_parser("prove", help="replay-verify one record's hashes")
    prove.add_argument("key", help="exact_key (sha256, prefix accepted)")
    clear = sub.add_parser("clear", help="delete local records + blobs")
    clear.add_argument("--scope", default=None, required=True)
    sub.add_parser("simulate", help="run the three plan benchmark scenarios")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
