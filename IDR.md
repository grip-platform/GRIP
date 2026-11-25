# IDR.md — Intent Decision Record

> *"A decision you can replay is a decision you can trust."* — V>>--<<V
>
> The tamper-evident record of a single decision. Sibling specs
> (HAPPI lives in the **HAL** repo, which is private — the link resolves
> for anyone with access, not for a stranger; it pointed at the GRIP repo
> root until 2026-09-20, where the file has never existed, so the protocol
> layer of the cascade read as missing rather than as elsewhere):
> [`HAPPI.md`](https://github.com/grip-platform/HAL/blob/main/happi.md) (the protocol — `~/.hal/happi.md`),
> [`MEMORY_CONTEXT.md`](MEMORY_CONTEXT.md) (the belief chain an IDR pairs with),
> and the doctrine [`rules/cryptographic-causation.md`](rules/cryptographic-causation.md).

**Status**: live in production. Every IDR GRIP writes is HMAC-SHA256-signed and
chain-linked; a tampered record is detectable by re-running the verifier — the
math, not GRIP's word, is the proof. Reference implementation:
[`lib/precog/idr.py`](lib/precog/idr.py) (build / append / read) +
[`lib/idr_forest.py`](lib/idr_forest.py) (signed forest, Merkle root, replay).

---

## WHAT — the one-paragraph version (plain English)

An **IDR (Intent Decision Record)** is a small signed JSON object that captures a
single decision: *what was decided*, *the inputs it saw*, *which models were
consulted*, and — when it was a real judgement call rather than a deterministic
step — *why, how confident, and what would prove it wrong*. Each record carries a
cryptographic signature, and each record names its predecessor, so the records
form a chain. Because the signature covers the record's content, **you cannot
change a recorded decision after the fact without the change being detectable.**
Anyone — including someone who does not trust GRIP — can re-run the verifier and
get `VERIFIED` or `BROKEN`. That is the moat: provability, not promises.

ELI5: it's a receipt for a decision, sealed with a wax stamp. If anyone scratches
out a word on the receipt, the stamp no longer matches and everyone can see it was
tampered with.

---

## WHY — the load-bearing reason

An AI that *says* "I decided X because Y" is asking you to take its word. An AI
that hands you a **signed, replayable record** of "I decided X, here were the
inputs, here was my confidence and my stated falsifier, here is the signature" is
giving you something you can check **without trusting the AI at all**. The
verifier is deterministic arithmetic; the external party (a human, a court, an
auditor, a counterparty) is the judge. This is the exact shape GRIP's
[Exogenous-Anchor Law](rules/exogenous-anchor-law.md) demands: the thing that
ratifies the record is *outside* the model that produced it.

This matters most for products whose entire value is an integrity guarantee
(legal work, financial records): a fabricated or silently-edited decision
is the catastrophic failure mode, and the IDR makes it **structurally
impossible to do silently**.

---

## HOW — the record format (verify-canonical against `lib/precog/idr.py`)

An IDR is a flat JSON object (dataclass `PrecogIDR`). Fields, in the order
`asdict()` emits them:

| Field | Type | Meaning |
|-------|------|---------|
| `happi` | string | Protocol version of the envelope — currently `"1.6"` (`HAPPI_VERSION` in `lib/precog/idr.py`; GRASP's `grasp/idr.py` pins the same, converged in grasp#18). Nothing mechanically checks that those two agree, nor that either matches the canonical polyglot runtime's own declaration — which is how all three drifted across two releases with every suite green. The `idr` event type entered HAPPI at v1.1, so older records legitimately carry `"1.1"`, `"1.3"`, `"1.4"` or `"1.5"` — a record states the version it was signed under and is never rewritten. |
| `kind` | string | Record class — e.g. `precog-decision` (an orchestration decision), `context-delta` / `context-snapshot` (a memory-chain node, see [`MEMORY_CONTEXT.md`](MEMORY_CONTEXT.md)). |
| `id` | string | Unique id `precog-<unix_seconds>-<8 hex>`. A 4-byte random suffix avoids same-second collisions across concurrent waves. |
| `predecessor_idr` | string \| null | The `id` of the record this one follows — the backward chain link. `null` only at a genuine root; otherwise an exogenous anchor (e.g. `council:context-chain-genesis`) or a prior record's id. |
| `depth` | int | Distance from the root along the predecessor chain (0 at genesis). |
| `fingerprint` | string | Caller-supplied routing/identity fingerprint for the decision (e.g. a hash of the prompt + kernel + routing choice). |
| `ts` | string | Wall-clock instant, ISO-8601 UTC `YYYY-MM-DDThh:mm:ssZ`. **Metadata, not addressed** (see content address below). |
| `decision` | object | The decision payload — free-form, caller-defined (e.g. `{"action": "failover", "provider": "gemini", "reason": "anthropic 429"}`). |
| `inputs` | object | The inputs the decision saw. Convention: `inputs.model_versions` is the list of models consulted (the audit chain). |
| `audit` | object | The signature block (see below). **Derived from the body; not part of the addressed content.** |
| `decision_anatomy` | object \| null | OPTIONAL "mind" of a delegated decision — `why` / `confidence` / `uncertainties` / `assumptions` / `falsification_criteria`, validated through the Decision schema's SMT + honesty gates (`lib/decision_record.py`). `null` on the deterministic/minimal tier — and a record with `null` here addresses and signs **byte-identically** to a pre-anatomy record (strict back-compat). |

### The `audit` block (signature)

Produced by `_sign_real` over the canonical body (every field **except** `audit`):

```json
{
  "scheme": "hmac-sha256",
  "key_fingerprint": "9eaa61e2",
  "signature": "hmac-sha256:8ca184fc7ee96098372b6ba49fa22f732b239368e9c5713bcc49cd88d1a4164e"
}
```

- `scheme` — `hmac-sha256` for all new records. (`sha256-placeholder` exists only
  to *read* legacy records and is reported `DEGRADED` by the verifier — it is **not**
  tamper-evident.)
- `key_fingerprint` — first 8 hex of `sha256(signing_key)`; identifies which key
  signed without revealing it.
- `signature` — `hmac-sha256:` followed by the HMAC of the entry hash. The signing
  key is provisioned by `lib.audit_chain._ensure_signing_key` and never appears in
  the record.

### Content address vs signature — two different hashes

`content_addr(body)` returns `"sha256:<hex>"` over the canonical JSON of the body
**excluding the volatile `id`, `ts`, and `audit` fields**. Two records with
byte-identical *semantic* content but different `id`+`ts` (and therefore different
`audit`) share **one** content address — the dedup / Merkle coordinate. This
mirrors git's tree-hash, which excludes the committer-date so identical trees
share a hash. (When `decision_anatomy` is `null` it is dropped from the addressed
body, so an un-annotated record addresses identically to a pre-anatomy one.)

The signature, by contrast, covers `id`+`ts` too — so the *exact* record, timing
included, is what is signed.

### HMAC chaining — why tampering is detectable

`_sign_real` signs `compute_entry_hash(body)` (from `lib.audit_chain`). Flip any
byte of any signed field and the recomputed HMAC no longer matches the stored
`signature` → the verifier returns `BROKEN`. Predecessor linkage is carried *in
the signed body* (`predecessor_idr`), so re-pointing a record at a different
parent also breaks its signature. This is HMAC-SHA256 hash-chaining: each record
is independently signed, and the chain is the graph of `predecessor_idr` edges.

### Merkle root over a forest

`lib/idr_forest.py` organises records into an immutable **Forest** rooted at
[exogenous anchors only](rules/exogenous-anchor-law.md) (`ci:` / `human:` /
`council:` / `hypo:` id prefixes). `forest_merkle_root` builds an RFC-6962 Merkle
root over the sorted **content addresses** of every node, so any node change moves
the root. `forest_inclusion_proof` then proves **one** decision is committed by
that single root **without revealing the others** (`O(log N)` proof) — selective
disclosure: show a counterparty that one decision is in the committed set, reveal
nothing else.

---

## WHEN — emission and the `idr` terminator event

- **In GRIP**: `build_idr(...)` constructs and signs the record; `append_idr(...)`
  atomically appends it as one JSONL line to the chain at
  `state/precog/idr.jsonl` (POSIX `fcntl.flock` for concurrent-session safety,
  `fsync` on write). `read_idr_chain(predecessor_id=...)` walks the chain backward
  from a named record (cycle-safe), or returns all records in file order.
- **In HAPPI** (`~/.hal/happi.md`): an `idr` is the **audit terminator** — when a
  dispatch sets `flags.audit=true`, the runtime emits one `idr` event *after*
  `completed`/`error`, carrying the content hash of (envelope + event stream). It
  is emitted at most once and is always the terminator. The GRIP record above is
  the richer, persisted, signed form of that same idea.

---

## Replay, audit, verify

Replay is **re-derivation from the signed record, never LLM re-execution** — a
stochastic model could never reproduce bytes, and putting an LLM on the audit path
is forbidden by the import-isolation lint. Because the decision *is* the data,
replaying it is a deterministic fold:

- `verify_chain_integrity(forest)` → tri-state `Verdict`: `VERIFIED` (every node's
  HMAC matches), `BROKEN` (any node tampered), `DEGRADED` (a recognised but
  not-tamper-evident scheme, e.g. legacy placeholder).
- `replay_chain(forest, node_id)` → the reconstructed root→node trail, the
  integrity verdict, and a `replay_digest` that is **byte-identical across
  independent runs** of the same forest. A differing digest means the chain itself
  changed — that is the determinism anchor.
- `replay_deterministic(forest, node_id)` **refuses** to replay a `BROKEN` forest,
  so replay can never silently launder a tampered record.

---

## External / Bitcoin anchoring

The `idr_ref` in HAPPI carries an optional `block_anchor` field, and the forest's
Merkle root is exactly the kind of single commitment that anchors to an external
append-only ledger (a Bitcoin transaction via OpenTimestamps) so that even GRIP's
own storage cannot rewrite history undetectably. **Anchoring is live and confirmed:**
the production decision-chain root (content address `0bba0792…`, 501 decisions) is
**confirmed in Bitcoin block 956992** — verify: `ots info state/crypto-anchor/decision-chain-501-2026-07-06.json.ots`.
The residual gap is *continuous automatic per-IDR-write* anchoring, tracked as H889
(the FF pilot wires its own records). Today's guarantee: HMAC tamper-evidence + the
signed Merkle root + the confirmed decision-chain Bitcoin anchor; universal per-IDR anchoring is the next rung.

---

## A real record (verbatim shape from `lib/precog/idr.py`)

This is the actual JSONL line `append_idr` writes for the decision *"should the
relay fail over to Gemini on a 429?"* — produced by running `build_idr` (keys are
canonically sorted in the serialised line; `decision_anatomy` is present-as-`null`
on this minimal-tier record and is excluded from the content address). This is a
real historical record from 2026-06-24 and is preserved verbatim — its
`"happi":"1.1"` reflects the protocol version at the time it was signed; records
built today carry `"1.6"`:

```json
{"audit":{"key_fingerprint":"9eaa61e2","scheme":"hmac-sha256","signature":"hmac-sha256:8ca184fc7ee96098372b6ba49fa22f732b239368e9c5713bcc49cd88d1a4164e"},"decision":{"action":"failover","provider":"gemini","reason":"anthropic 429"},"decision_anatomy":null,"depth":0,"fingerprint":"a1b2c3d4e5f6","happi":"1.1","id":"precog-1782329796-6ac139a1","inputs":{"model_versions":["claude-opus-4-8","gemini-3-pro"]},"kind":"precog-decision","predecessor_idr":"council:context-chain-genesis","ts":"2026-06-24T19:36:36Z"}
```

For that record: `content_addr` =
`sha256:c541b35611c7eaa69f0e8b04b909aca1be68790871913e851410b1c6660aa717`,
`verify_chain_integrity` = `VERIFIED`, and `replay_chain` reproduces it with a
stable digest. The test [`tests/test_idr_memory_specs.py`](tests/test_idr_memory_specs.py)
parses the fenced example **from this file** and asserts it round-trips and
verifies against the real `lib/precog/idr.py` — so if this spec ever drifts from
the code, the test goes red.

---

## Falsification (this spec is wrong if…)

- a record whose any signed field is altered without re-signing ever verifies
  `VERIFIED` (HMAC chaining broken);
- two bodies identical but for `id`/`ts`/`audit` produce **different**
  `content_addr` (content-address broken);
- `replay_chain` produces a **different** `replay_digest` on a second run of the
  same forest (determinism broken);
- the fenced example above fails to round-trip against `lib/precog/idr.py` (spec
  drifted from code).

---

*Cross-references: [`HAPPI.md`](https://github.com/grip-platform/HAL/blob/main/happi.md) ·
[`MEMORY_CONTEXT.md`](MEMORY_CONTEXT.md) ·
[`rules/cryptographic-causation.md`](rules/cryptographic-causation.md) ·
[`rules/exogenous-anchor-law.md`](rules/exogenous-anchor-law.md). Canonical
implementation: `lib/precog/idr.py` + `lib/idr_forest.py`. Watermark: V>>--<<V*
