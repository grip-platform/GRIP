# DER Attestation Boundary — what a Decision Evidence Receipt DOES and does NOT attest

The single most important honest limit of the Decision Evidence Receipt (DER), and
the sovereign council's #1 gap on the wedge plan: **a receipt that over-claims is
worse than no receipt.** This spec draws the line precisely so no sales copy, memo,
or downstream session can blur it. It is enforced by `tests/test_der_boundary.py`
(shipped with this spec — a boundary that isn't tested is a boundary that drifts).

## What the DER ATTESTS (the four legs)

For the exact record in the receipt, a third party verifies — with zero trust in
GRIP, using `cryptography` + the `ots` CLI + stdlib:

1. **Authenticity** — the record was signed by the holder of the private key for
   the receipt's embedded Ed25519 public key. (Asymmetric — no shared secret.)
2. **Fixity** — the decision content has not changed since signing; one flipped
   byte breaks `payload_sha256`.
3. **Time** — the record's hash was committed to Bitcoin at or before a block
   (OpenTimestamps: `pending` until confirmed, then a specific height).
4. **Belief-binding (joint-causation)** — *what the AI believed when it decided*
   (which models, the memory head) is inside the signed payload; swapping it fails
   verification **even with a valid timestamp**. This is the differentiating leg.

## What the DER does NOT attest (the boundary — never claim these)

| Not attested | Why it's out of scope | Where the risk actually lives |
|---|---|---|
| **The decision's correctness** | The receipt fixes *what was decided and believed*, not whether it was *right*. A wrong decision produces a perfectly valid receipt. | The model + the org's process — a separate assurance problem. |
| **Pre-signing input integrity** | If inputs were poisoned (prompt-injection, model-poisoning, output tampering) *before* the record was signed, the DER faithfully attests the compromised record. | Upstream input controls — must be a distinct attested boundary if needed. |
| **Signer identity** | The signature proves control of *a* key, not *whose* key. "Signed by GRIP" requires binding the public key to a named signer out-of-band. | PKI: publish/anchor the key, or challenge-sign (Wave 3). |
| **Admissibility** | Admissibility is the court's call on the facts; the DER improves the *authenticity/integrity* footing, it does not pre-decide it. | The court + the legal-mapping memo (attorney-verify). |
| **Continuous / lifecycle logging** | The DER is an *atomic* receipt for one decision, not a lifecycle log. It is explicitly **not** an "EU AI Act Article 12 compliance certificate" (council R3 falsified that framing). | A different product; do not conflate. |

## The one-line contract

> A DER proves **that this exact decision record, with this exact belief-state, is
> authentic, unaltered, and timestamped** — and *nothing more*. It is
> **evidence-of-record**, not a guarantee of correctness, compliance, identity, or
> admissibility.

## Enforcement

- `receipt["honest_limit"]` carries this disclaimer on every receipt (machine +
  human readable); `tests/test_der_boundary.py` asserts it is present and names the
  four legs while disclaiming correctness.
- `verify_receipt` gates `ok` on **only** fixity + authenticity + joint-causation
  (+ reports timestamp) — structurally it *cannot* assert correctness, and the test
  proves a semantically-absurd-but-well-formed decision still verifies `ok=True`
  (attests structure, not truth).

Origin: task #551 (DER moat-wedge hardening, Wave 2), V>> overnight /ultrawin
2026-07-19. Sibling of `docs/der-receipt-demo.md`.
