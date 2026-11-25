# Decision Evidence Receipt (DER) — the smallest proof of the moat

A **Decision Evidence Receipt** turns one real AI decision into a record a
skeptic — a General Counsel, opposing counsel, a regulator — can verify against
Bitcoin with **zero vendor trust**, using only `cryptography` + the `ots` CLI +
Python stdlib. No GRIP runtime, no network to us, nothing to take on faith.

This is the "smallest proof" of GRIP's cryptographic-causation moat: not a slide,
a **working artifact** you can hand someone and dare them to break.

## Four independent legs (each falsifiable on its own)

| Leg | Checks | Breaks if… |
|-----|--------|-----------|
| **Fixity** | recompute `sha256` of the canonical decision → equals the receipt's | one byte of the decision is edited |
| **Authenticity** | **Ed25519** signature verifies under the receipt's embedded **public** key | signature forged, or a different key used (zero-trust: no shared secret, unlike an HMAC seal) |
| **Joint-causation** | the belief-state (which models decided, the memory head) is *inside* the signed payload | someone swaps *what the AI believed* — **even with a valid timestamp** |
| **Timestamp** | OpenTimestamps proof commits the hash to Bitcoin | reported `confirmed <block>` / `pending`; a pending anchor is a real commitment awaiting a block |

## Try it (two commands)

```bash
# build a receipt from a real sealed decision (a sovereign-council record)
grip-der-receipt state/sovereign-council/<seal>.json --out receipt.json

# a skeptic re-verifies it — cryptography + ots + stdlib only
grip-der-verify receipt.json
```

Real run on this repo's sealed council decision `26806b2a27006eaf`
(payload `592e6776…3a5873a7`):

```
  ✓ FIXITY (decision hash): True
  ✓ AUTHENTICITY (ed25519, zero-trust): True
  ✓ JOINT-CAUSATION (belief binding): True
  … TIMESTAMP (bitcoin / opentimestamps): pending
PASS ✓ — authentic, fixed, belief-bound, anchor pending confirmation
```

## The moat, demonstrated (not asserted)

Swap *what the AI believed* — change which models made the decision — and leave
the hash, signature and Bitcoin anchor untouched:

```
  ✗ FIXITY (decision hash): False
  ✓ AUTHENTICITY (ed25519, zero-trust): True   ← sig still valid for the OLD hash
  ✗ JOINT-CAUSATION (belief binding): False
  … TIMESTAMP (bitcoin / opentimestamps): pending   ← anchor still "valid"
FAIL ✗ — receipt does NOT verify
```

A commodity OpenTimestamps anchor attests *a timestamp* and would happily pass a
swapped decision. GRIP binds *what the AI believed when it decided* into the
signed record, so the swap fails **even though the timestamp is fine**. That
binding — not the anchor — is the defensible part.

## Honest limits (never dropped)

- **Attests**: this record's authenticity, fixity, time, and belief-binding.
- **Does NOT attest**: the upstream decision's *correctness*, nor the integrity
  of inputs fed to the model *before* the record was signed (prompt-injection /
  model-poisoning upstream is out of scope — spec the attested boundary per the
  build).
- **Identity is out-of-band**: the receipt proves it was signed by whoever holds
  the private key for its embedded public key — it does **not** prove *who* that
  is. Binding the public key to a named signer (publish it, or anchor it) is a
  separate PKI step.
- It is **evidence-of-record**, not a compliance guarantee and not a guarantee of
  admissibility (admissibility is the court's call — see the legal-mapping memo,
  attorney-verify before external use).

## What ships

| File | Role |
|------|------|
| `lib/der_receipt.py` | build + zero-trust verify (import-light: stdlib + cryptography + ots) |
| `bin/grip-der-receipt` | builder CLI |
| `bin/grip-der-verify` | standalone verifier a skeptic runs |
| `tests/test_der_receipt.py` | 8 Goodhart-proof tests — every tamper vector fails the right leg |

Origin: task #546, V>> 2026-07-18. The confirmed-block anchor (`956992`) from the
earlier decision-chain lives on the laurie@ machine; this demo generates a fresh,
real OTS commitment (pending → confirms in ~hours) so it is self-contained and
honest, not a copied claim.
