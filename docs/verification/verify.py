#!/usr/bin/env python3
"""Verify a published GRIP decision record. No GRIP code. Only `cryptography`.

    python3 verify.py <record.idr.jsonl> <grip-verify-keys.json>
"""
import hashlib, json, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric import mldsa

EXCLUDED = {"entry_hash", "chain_hash", "sequence", "signature", "key_fingerprint", "scheme"}

def entry_hash(record):
    body = {k: v for k, v in sorted(record.items())
            if k != "audit" and k not in EXCLUDED}
    return "sha256:" + hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

record = json.loads(open(sys.argv[1], encoding="utf-8").read().strip().splitlines()[0])
keys   = json.loads(open(sys.argv[2], encoding="utf-8").read())
audit  = record["audit"]
msg    = entry_hash(record).encode()
ok     = True

for limb, loader in (("ed25519", Ed25519PublicKey.from_public_bytes),
                     ("mldsa65", mldsa.MLDSA65PublicKey.from_public_bytes)):
    fp = audit[limb]["key_fingerprint"]
    if fp not in keys:
        print(f"  {limb:<9} NO PUBLISHED KEY for {fp}"); ok = False; continue
    raw = bytes.fromhex(keys[fp])
    if hashlib.sha256(raw).hexdigest()[:8] != fp:
        print(f"  {limb:<9} KEY DOES NOT BIND to {fp}"); ok = False; continue
    try:
        loader(raw).verify(bytes.fromhex(audit[limb]["signature"]), msg)
        print(f"  {limb:<9} VALID   key {fp} ({len(raw)}B)")
    except Exception as e:
        print(f"  {limb:<9} INVALID key {fp}: {type(e).__name__}"); ok = False

print(f"\n  message signed : {msg.decode()}")
print(f"  record id      : {record.get('id')}")
print(f"\n  {'VERIFIED — both signatures check against the published keys' if ok else 'FAILED'}")
sys.exit(0 if ok else 1)
