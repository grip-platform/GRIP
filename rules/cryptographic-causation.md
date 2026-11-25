# Cryptographic Causation — IDR + Memory-Chain Always-On, Every Claim Provable (PARAMOUNT)

Origin: 2026-06-24, V>>--<<V ratified. GRIP's defensible moat is not the model —
it is **cryptographic causation**: tamper-evident, replayable, externally-anchorable
records of *what* an AI decided AND *what it believed when it decided*, with *every
outward claim* carrying deterministic provenance. V>>'s words: "tamper-proof,
Merkle-anchored, irrefutable records of AI decisions — the why, the how, the when,
the what — COMBINED with what the Agent believed when ... real anchored Bitcoin
blockchain entries — real evidence. REAL." This rule makes that a binding doctrine,
not an aspiration.

## The three legs (one moat)

1. **IDR — the decision record.** Every consequential decision emits a signed
   Intent Decision Record into the forest (`lib/idr_forest.py`, `lib/precog/idr.py`):
   the what / why / how / when, carrying a `predecessor_idr` link, replayable,
   externally timestamp/Bitcoin-anchorable. The chain is the evidence an AI *did*
   something. **Read "Measured state" below before citing this leg outward — the
   linking and the signing are both weaker in the live chain than this paragraph
   once claimed.**

2. **Memory chain — the belief record.** Every reasoning state / mental-model
   snapshot is recorded into the context chain (`lib/context_chain.py` +
   `context_chain_classify`/`context_head`/`context_snapshot`/`context_gc`/
   `context_warmstart`), so the record carries *what the agent believed at decision
   time*, not just the output. The chain is the evidence of *why*. An IDR without
   its belief context is a verdict with no reasoning; the two are recorded together.

3. **Prove-it — the claim record.** Every outward, team-facing, or client-facing
   CLAIM is backed by deterministic, verbatim-quote provenance (`lib/prove_it.py`
   L1 floor + `lib/prove_it_council.py` L2 external MoE + judge), signed into the
   IDR forest + memory chain as an audit leaf. A claim GRIP cannot prove, GRIP
   does not send.

## Addendum 2026-09-17 — the anchoring leg now anchors, and the word "moat" is withdrawn

Two corrections, and they point in opposite directions.

**The external anchor now exists, which completes the origin spec rather than
revising it.** V>>'s ratified words above already called for *"real anchored Bitcoin
blockchain entries — real evidence. REAL."* Leg 1 said
"externally timestamp/Bitcoin-anchorable" — *capable* of anchoring, with nothing
anchored. Measured this day: a commitment leaf's `ts` was written by the same party
the record constrains, and the audit block carried `custody`, `ed25519`, `mldsa65`,
`scheme`, `tenant_id` and **no anchor of any kind**. Signatures prove WHO, never
WHEN. `lib/rfc3161_anchor.py` (GRIP#6190) closes it against a real authority —
DigiCert returned `gen_time 2026-09-17T17:22:23+00:00`, a time signed with a key we
do not hold. Opt-in via `GRIP_TSA_URL`, and the three statuses `disabled` /
`anchored` / `unavailable` stay distinguishable so a blocked network yields an
unanchored record that SAYS so.

**The word "moat" is withdrawn from the CLAUDE.md summary of this rule.** Not the
engineering claim — the COMMERCIAL one. Four non-Anthropic councils (zero Anthropic
calls; seals `7dc01f0b739be243`, `9fb9b0fc0f2d4309`) unanimously called the
integrated system "a bundle of commodities with a nice story", and a survey found
Asqav shipping MIT-licensed under the same ML-DSA-65 **plus** RFC 3161 and RFC 8785
that we lacked, with an explicit non-assertions list where we printed a single
`PROVEN`. The tamper-evidence also had a live hole: a rewritten verdict with a
recomputed hash verified clean (fixed, GRIP#6176).

The discipline survives all of that and is worth keeping — it found its own defect
in an hour. What is not established is defensibility, and this rule should not be
cited outward as though it were. Evidence:
`docs/measurements/2026-09-17-buyer-question-empirical.md`,
`docs/measurements/2026-09-17-incumbent-capability-survey.md`,
`docs/measurements/2026-09-17-adversarial-boundary-injections.md`.

## Measured state — what is true of the live chain, 2026-09-13

This section exists because the three legs above were written as an aspiration and
then read as a description. Everything here is re-derivable: the commands are in
`docs/measurements/2026-09-13-cryptographic-causation-audit.md`, and a reader who
disagrees should re-run them rather than trust this paragraph.

**The decision chain is essentially unchained, and the earlier word "hash-chained"
was false.** Of 910 records in `state/precog/idr.jsonl`, **895 carry
`predecessor_idr: null`** — they are not broken links, they are records written
with no link at all. 12 resolve to a record in the file and 3 dangle. Walking
`predecessor_idr` back from the newest record reaches **1 of 910**. The longest
run anywhere in the file is 8. "Hash-chained" also named the wrong mechanism:
HMAC-SHA256 is the SIGNATURE on each record, not a link between records, and
nothing in the IDR body commits to a predecessor's content hash.

**The root cause is a missing head pointer, not a broken one.** The belief chain
has `lib/context_head.py`, and `append_context` reads the head, links to it, and
swaps it atomically. The decision chain has no equivalent: `append_idr` writes a
line and maintains no pointer, so every caller must supply `predecessor_idr`
itself and almost none can — a fresh process has nothing to read. The asymmetry
shows in the data: the belief chain is **20,231 of 20,283 (99.7%) pairwise
linked**, the decision chain 12 of 910.

**The belief chain is far better linked than it is walkable.** Despite that 99.7%,
walking from `context-HEAD.txt` reaches only **21 of 20,283**, because 52 dangling
links cut the chain into segments and HEAD sits in a short one. "99.7% linked" and
"21 reachable" are both true; quote both or neither.

**Almost every record is signed with one shared secret on the operator's disk, so
a stranger cannot verify it.** 909 of 910 IDR records and all 20,283 belief records
are HMAC-SHA256 under a single key, fingerprint `9eaa61e2`. `lib/precog/idr.py`
says so in its own words — *"The DEMO seal — HMAC-SHA256 under one shared key.
Forgeable by design."* Anyone holding that key can forge or alter any of those
records undetectably, and only the key-holder can check them. **These records are
tamper-evident to the operator and to nobody else.** Exactly **1** record carries
the dual `ed25519+ml-dsa-65` seal that an outsider could verify — and the public
half is not published, so today even that one is unverifiable externally.

**The Bitcoin anchor is real, is the strongest leg, and proves less than it is
often asked to.** `state/anchors/` holds 302 OpenTimestamps receipts; **301 carry a
`BitcoinBlockHeaderAttestation`** across roughly 400 distinct heights from 961224
to 966596, one still pending. The live `idr.jsonl` digest
(`6c0fc3eb…5fdebde`) matches its anchored manifest entry exactly. An outside
auditor re-derived `moat_sha256` unaided and it matched.

What that proves is **existence by a given block, and nothing else.** It does not
establish that the anchored bytes are honest, that the decisions were good, that
the chain improved, or that a record was not forged before anchoring — a forged
record anchors exactly as well as a true one. Anchoring binds content to *time*;
it is silent about *provenance*. Note also that `context.jsonl` has since **drifted
from its anchor** (live `8fcfb509…`, anchored `ad6efe82…`), which is expected for
an append-only file between anchor runs and is why an anchor is cited with its
timestamp or not at all.

**What may be claimed outward today**, until the repairs below land: that GRIP
records decisions and beliefs in append-only, per-record-signed logs; that their
contents provably existed by a specific Bitcoin block; and that the operator can
detect tampering. **What may NOT be claimed**: that the decision records form a
chain, that a third party can verify them, or that the Bitcoin anchor attests to
anything beyond existence-by-a-block.

## The law (PARAMOUNT, prod-binding)

> **In production, IDR recording and memory-chain recording are MANDATORY, not
> optional. An un-recorded decision is theatre, not evidence. Every team-/client-
> facing claim carries /prove-it provenance. The records are falsifiable by
> construction — replayable, independently verifiable, shadow-metric'd,
> anti-Goodhart.**

"Mandatory" means a prod decision path that *can* skip recording is a bug to close,
not a config to leave off. "Falsifiable" means each record can be replayed and
verified against its inputs by a process that does not trust the record's own
self-report (verify-canonical; exogenous anchor).

## Why falsifiability is the whole point (the load-bearing why)

A record that only ever confirms itself is theatre. The moat's value is that a
SKEPTIC — a regulator, opposing counsel, a future auditor — can independently
REFUTE or CONFIRM it: re-run the replay, re-verify the Merkle path, re-check the
quote provenance against the cited source, compare against the external anchor.
Self-scoring is forbidden (`exogenous-anchor-law.md`): the verifier is the math
(signatures, Merkle roots, content hashes) + an external party, never the agent
that wrote the record. Shadow metrics may only lower confidence, never raise it
(`scientific-method.md`). This is Popper applied to AI provenance: the records
earn trust by being refutable and surviving refutation — not by assertion.

## Mandate

- New prod decision surface → emits an IDR **and** a memory-chain node; never ship
  a decision path that records neither.
- New outward claim surface (/update-team, /eod, channel relays, client docs) →
  /prove-it-backed; the provenance signs into the IDR forest + memory chain.
- Recording is **verify-canonical**: assert the record actually landed (chain head
  advanced, Merkle root recomputes, prove-it offsets resolve) — never a
  `recorded: true` status flag (`verify-canonical-not-claimed.md`).
- Specs are canonical, polyglot, and sibling to `HAPPI.md`: **`IDR.md`** (decision-
  record spec) and **`MEMORY_CONTEXT.md`** (belief-record spec) — referenced from
  CLAUDE.md and AGENTS.md in every codebase.

## Enforcement tier (knowledge-maturation-functor)

Rule-now (V>>--<<V PARAMOUNT; the moat's integrity is severe). The mechanical floor
is the EXISTING substrate (`idr_forest`, `precog/idr`, `context_chain`, `prove_it`)
+ the always-record hooks (`hooks/precog-orchestrate-hook.py`,
`hooks/auto-memory-hook.py`, `hooks/context-checkpoint-hook.py`).

**Three gaps are open, and they are ranked by how badly each falsifies an outward
claim, not by effort.** This list replaces the earlier single "coverage" gap, which
named the least damaging of the three and so read as though the other two did not
exist.

1. **Custody — the one that makes the records unverifiable by anyone else.**
   909 of 910 decision records and every belief record are sealed with a shared
   local secret. A dual keypair for tenant `grip-production` IS provisioned at
   `state/audit/keys/grip-production/` (Ed25519 + ML-DSA-65, both limbs present),
   and `build_idr` already resolves a tenant through `lib/idr_tenant.py` — so the
   product path is reachable and one record has taken it. It stays at one because
   the tenant is configured ONLY as `$GRIP_IDR_TENANT` in `~/.zshrc`, which an
   interactive shell inherits and a launchd agent or a hook spawned from one does
   not. The declarative rung (`config/grip.json` → `idr.tenant_id`) was empty.
   Closed by populating that rung, so every process signs the same way regardless
   of how it was started. **Historical HMAC records stay HMAC and are labelled as
   such** — backfill is impossible and re-signing them under the strong key would
   assert a custody they never had.

2. **Linking — the one that makes "chain" the wrong noun.** `append_idr`
   maintains no head pointer, so 895 of 910 records have no predecessor.
   Closed by giving the decision chain the head primitive the belief chain has
   had all along (`lib/context_head.py`). History cannot be repaired: a
   predecessor is inside the signed body, so linking the existing 895 would mean
   re-signing them, which is exactly the forgery the seal exists to detect. The
   fix is forward-only and the gap in the record is permanent and disclosed.

3. **Coverage** — verifying recording fires on *every* prod decision/claim, not
   only on orchestration. Tracked as slice 4. A coverage gate / shim is deferred
   until ≥2 distinct uncovered surfaces are mapped (per the functor's N=2 rule).

The dogfood Banach proof (claim → IDR leaf → memory-chain node, deterministically
replayable) is the composition's self-test.

## Falsification

Wrong if, over the next ≥10 prod decisions/claims it governs: (a) mandatory
recording provably prevents no dispute it would otherwise lose AND measurably slows
prod (pure overhead → relax to advisory for low-stakes paths); (b) "falsifiable"
records are never once independently re-verified (the verify path is dead → wire
verification into a consumer or drop the claim); (c) prove-it gating blocks more
legitimate claims than false ones (mis-tuned → widen, don't drop); (d) recording
cost (latency/storage) exceeds the moat's realised value over the window. Track:
prod decisions with a landed IDR+memory node (target 100% of consequential paths);
outward claims prove-it-backed (target 100%); records independently re-verified vs
total (the refutability signal); disputes where the chain was the deciding
evidence. Goodhart-safe: success = disputes-won / claims-proven, never IDR-count.

## Scope

Every prod decision + outward claim surface in GRIP and HAL. Sits beside
`exogenous-anchor-law.md` (the verifier is exogenous, never the agent),
`verify-canonical-not-claimed.md` (assert the record landed, don't trust its
status), `scientific-method.md` (shadow metrics, falsifiable),
`loop-priority-external-over-theatre.md` (the moat is external value — a record
nobody can use to win a dispute is theatre), and `no-stubs.md` (a recording path
that names a capability it does not perform is the worst false-completeness signal).
Specs: `IDR.md`, `MEMORY_CONTEXT.md` (siblings to `HAPPI.md`). Mirror into
`~/.hal/rules/`.
