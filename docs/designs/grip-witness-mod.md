# GRIP witness mod (M1) — a recorder that watches only and never blocks

Status: DESIGN, with a working spike (`mods/grip-witness/`, `lib/witness_sealer.py`).
Owner: GRIP / CodeTonight. Supersedes nothing; pairs with [`grip-guard-mod.md`](grip-guard-mod.md) (M2).

> Read this beside the [attack list](#attack-list-publish-beside-any-claim). A
> witness claim with no attack list beside it is theatre, not evidence.

## What it is, in one paragraph

The witness mod is a Claude Code plugin that sits in the hook chain and records
what a session was served — every tool call, every finished turn — without ever
changing any of it. For each event it computes a **keyed-HMAC commitment**
before passing the event on, numbers the event in a **per-session sequence**, and
keeps a **census** of which events it was actually served. Batches of
commitments are sealed through `$.process.run` by a sealer CLI into GRIP's signed
decision chain, with the seal time recorded. The point is the GRIP moat made
operational at the harness edge: *witness it, don't trust it* — a tamper-evident,
joinable record of what the harness did, whose gaps are visible rather than
hidden. It is a **recorder**: it never denies, rewrites, reorders or blocks. A
watch-only mod that could block would be a guard (that is M2), and a guard that
claims to witness would be conflating two different trust levels.

## Why watch-only is the whole design

The witness's value is that its record is *believable*. The moment it can change
what it records, the record means less: did the tool run because the model asked,
or because the witness let it? So the mod holds itself to two mechanical rules:

1. **Always continue.** Every handler returns `next(e)` unchanged, and wears an
   explicit pass-through `.catch((_$, e, next) => next(e))`. `next` in a `.catch`
   is replay-safe, so a recorder that throws or overruns its 10 s budget resolves
   to what the chain already settled — it can never become a deny. (The engine
   already runs `next(e)` on a failed hook's behalf; the witness states the
   guarantee explicitly rather than leaning on the default. `claude plugin
   validate` reports this as `gating hook with .catch: tool.call`.)
2. **Record before continuing, but never fail the turn.** The commitment is
   computed *before* `next(e)` so the commitment order matches the order the
   chain saw; the recording is wrapped so any failure is swallowed. Observability
   never subtracts availability.

## Architecture: a portable core plus attested extras

The mod is split so the *shape* of the record survives a move to another harness,
while the *rich* fields are honestly marked as Claude-specific.

```text
                 ┌───────────────────────────── witness mod ─────────────────────────────┐
 event served ──▶│ portable core: tool.call, turn.start, turn.complete                    │
                 │   • per-session sequence number (seq)                                  │
                 │   • census: which subscribed events were served                        │
                 │   • keyed-HMAC commitment (crypto.subtle, before next)                 │──▶ in-memory batch
                 │ Claude-specific attested extras (recorded served-or-not):              │        │
                 │   • turn.step usage / model                                            │        │ on turn.complete
                 │   • session.append (transcript growth)                                 │        ▼
                 │   • prompt_id / session_id join keys                                   │   $.process.run
                 └───────────────────────────────────────────────────────────────────────┘        │
                                                                                                   ▼
                                                                                    sealer CLI ──▶ signed chain
                                                                                    (seal time recorded)
```

### Portable core

The core is what any agentic harness can offer, so a witness record means the
same thing whatever produced it:

- **`tool.call`** — one tool the model invoked. The commitment binds the tool
  name, its `tool_use_id`, and the loop it ran in (`agentId`), none of which is
  stored in plaintext.
- **`turn.start`** — a model turn began; the engine mints the `turnId` here. The
  witness records the boundary so a turn with no `turn.complete` is a visible
  gap, not a silent one.
- **`turn.complete`** — the turn ended (`reason`: `answer` / `aborted` / `refusal`
  / `error`), carrying `turnId`, `durationMs`, and `usage`. This is also the
  natural **seal point**: the batch accumulated since the last seal is handed to
  the sealer, so at most one turn's worth of commitments is ever only-in-memory.
- **Per-session sequence numbers.** A monotone `seq` per session, incremented
  across every served event. This is the spine of *missing-event detection*: a
  chain that reads `…3, 4, 7, 8…` proves events 5 and 6 were committed and lost
  (or never served), and the size of the gap is the measured loss.
- **A census of which events were served.** The mod subscribes to a known set of
  event names and records, per session, which of them actually fired. The census
  is what turns "I saw nothing" from an ambiguity (nothing happened? or the mod
  was not loaded?) into a statement: *these events were served, these were not.*

### Claude-specific attested extras, recorded served-or-not

These ride on top of the core and are marked as Claude Code's, because another
harness will not serve them. Each is recorded **served-or-not** in the census, so
their absence is a fact, not a hole:

- **`turn.step` usage / model.** `turn.step` fires per model request inside a
  turn; its result carries `usage: TurnUsage | null`, and `TurnUsage` is the four
  token counts (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`) plus the answering `model`. The witness commits
  these so a cost/model claim can be checked against what the harness actually
  reported, per step.
  - **Honesty about `serverToolUses`.** The raw Anthropic usage block carries
    `server_tool_use` (`web_search_requests`, `web_fetch_requests`), and the
    engine's own result messages carry it. **The mod's `TurnUsage` does not
    expose it** — `turn.step` gives the four token counts and the model, nothing
    more. So the witness records `serverToolUses` *only where a surface actually
    serves it* (a classic-hook or result-message seat that carries the raw usage),
    and otherwise marks it **not served** in the census. It never invents a zero:
    an absent count means "not measured", and a zero would misreport that as
    "free" (the same rule HAL's own witness applies to its usage fields).
- **`session.append` (transcript growth).** The mod API serves it: `session.append`
  "fires once per row a conversation of this session keeps (a prompt, a response
  block, a tool result, a notice), before it is stored" (Claude Code 2.1.277
  types; installed CLI 2.1.296). Its input carries the row (`message`), the `door`
  it came in by, its `origin` (the person, the model, a tool, the engine, a
  settings hook, a plugin), its `uuid` (the same id the transcript file keeps)
  and the loop (`agentId`). The eight session events are `session.start`,
  `session.receive`, `session.append`, `session.compact`, `session.attach`,
  `session.detach`, `session.measure` and `session.end`. The finished witness
  commits each row's `uuid`, `door` and `origin` together with a commitment over
  its content — never the content itself — and records `session.append`
  **served-or-not** in the census, because it is a Claude-specific extra another
  harness will not serve; the spike does not hook it yet. If a build retires the
  event, the census shows it going dark with no change to the record's shape.
  That is the whole point of the census: the record degrades loudly. (An earlier
  draft of this document said no such event existed. The types on this machine
  list it, so that claim was wrong; it is corrected here rather than silently.)

## First-party precedent: the `telemetry` mod

Claude Code's own `telemetry` mod is the closest built-in analogue of the
witness — observability shipped as a plugin — and M1 borrows two of its moves
and refuses the third:

- **Trust only what the host stamps.** Telemetry's gate serves
  `telemetry.log` / `telemetry.mark` to the built-in plugins alone and refuses
  a call from a plugin a person installed *or* an administrator listed, reading
  the caller's tier off `next.origin` — "set by the host alone… nothing a
  plugin writes reaches it". That is the same trust basis as the census-hash
  exemption below: engine-pinned attributes, never a module's own word.
- **Batched egress.** Telemetry flushes queued rows in batches (seconds after
  the first row, at a hundred waiting, at session end). The witness seals per
  turn on `turn.complete` — the same shape at a tighter cadence, because its
  loss window has to stay small *and measured*.
- **Where the witness deliberately differs: the silent drop.** A telemetry
  batch the ingest keeps refusing is *dropped, with one line in the debug
  log*. The witness ledger must never do that: a lost batch has to surface as
  a `seq` gap in the record, so the loss is a published fact with a size, not
  a debug line.

(The third reference mod, `diff`, is a UI pane, not a recorder; M1 reads it
only as an example of a mod refreshing itself off tool traffic, and takes no
mechanism from it.)

## Keyed-HMAC commitments — and why the HMAC is built by hand

**Never plaintext.** The ledger keeps only non-secret join keys (event kind,
`seq`, the registration `epoch`, `session_id`, `key_source`, `turnId`,
`prompt_id`) beside the commitment. The event's *content* — the **whole event as
served**: which tool and every one of its arguments, which file, which command,
which model, what it cost — goes **inside** the HMAC, so that changing what a
call did changes its commitment. It is committed, not disclosed: revealed later,
if ever, by disclosing the content and the key and recomputing. This is what lets
a witness record be published or handed to a third party without handing them
the session.

**Canonical bytes.** The HMAC is computed over canonical JSON — object keys
sorted at every depth, arrays in order, no whitespace, `undefined` members
dropped — with the encoding's version bound into the payload
(`v: "grip-witness-c1"`). A verifier who rebuilds the same event in another
language or another property order therefore hashes the same bytes. Full
RFC 8785 is the production target; the versioned payload is what lets the rules
change without orphaning older commitments.

**A bare hash of a short value is guessable.** `sha256("Read")` or
`sha256("claude-sonnet-4")` is not a secret: anyone can guess the value and
confirm the hash, and a short input space is brute-forced outright. So the
witness commits with a **keyed** hash — without the key, nobody can produce or
check a commitment, and the content stays hidden even when it is short and
guessable.

**`crypto.subtle` exposes `digest()` only.** A hooks module runs in a closed
environment (no Node, no DOM, no `require`, no `eval`/`new Function`, no
`WebAssembly`, no timers). Its `crypto.subtle` is `{ digest(algorithm, data) }` —
**there is no `sign()` and no `importKey()`**, so `crypto.subtle.sign("HMAC", …)`
is not available. The witness therefore builds **HMAC-SHA256 from `digest`
alone**, exactly as RFC 2104 defines it:

```text
HMAC(K, m) = H((K' ⊕ opad) ‖ H((K' ⊕ ipad) ‖ m))
K' = H(K) if len(K) > blocksize else K zero-padded to blocksize   (blocksize = 64 for SHA-256)
ipad = 0x36 repeated,  opad = 0x5c repeated
```

This is the load-bearing crypto decision, and it is why the spike's
`hooks/hmac.ts` (imported by `register.ts`) carries a hand-built
`hmacSha256(key, message)` over `crypto.subtle.digest`. It is not a shortcut; it
is the only correct keyed commitment the environment permits. (A naive
`digest(key ‖ message)` would be vulnerable to length extension on SHA-256's
Merkle–Damgård construction; the RFC 2104 double-pass is not.) Two things pin
it. `claude plugin test` asserts, in the hooks' own environment, that
`crypto.subtle.sign` and `importKey` are absent (a future build that adds them
turns that test red, which is the signal that the hand-built form can retire)
and that the construction returns RFC 4231's known answers. And a lesson from
building it: an earlier draft of this spike reached for `crypto.subtle.importKey`
and `sign`, and `claude plugin validate` passed it, because the validator reads
`$` and `on` calls and not the Web Crypto surface; at run time every commitment
would have thrown inside the recorder's own try/catch and the ledger would have
stayed empty, silently. The validator is not the check for this; the kit test
is.

**Computed before `next`, numbered before anything is awaited.** The commitment
is made before the event continues down the chain, so the sequence of
commitments is bounded by, and ordered with, the sequence of events the chain
actually served. The spike honours this: `await commit(...)` runs, then `return
next(e)`. Parallel tool calls make this subtler than it looks: a handler that
takes its sequence number only after awaiting the key or the session id can be
overtaken by a later event, so the spike takes `seq` synchronously on entry and
seals the batch sorted by `seq`, which `claude plugin test` checks with three
concurrent calls.

## Sealing into GRIP's signed decision chain

Commitments accumulate in memory and are sealed in **batches**:

- **Transport.** On `turn.complete` the mod hands the batch to the sealer CLI
  through `$.process.run([interpreter, sealer, "--ledger", ledger], { stdin })`.
  `$.process.run` is the environment's only route to compiled/external code (the
  hooks environment has no `WebAssembly` and no Node); the batch crosses as JSON
  on stdin (argv has length limits; commitments do not).
- **The sealer CLI** (`lib/witness_sealer.py`) validates the batch, refuses to
  write anywhere but the named `--ledger`, stamps the **seal time** (UTC,
  ISO-8601) on every line, and appends one self-describing JSONL line per
  commitment. The seal time is recorded because a commitment's *value* proves
  what was served and in what order, but only the seal time places the batch on a
  wall clock — and the gap between event time and seal time is itself evidence
  (a batch sealed long after its events says something about the run). Two
  properties keep the seal honest. The seal time and the batch label are the
  **sealer's word**: a commitment that carries `batch_id` or `seal_time` is
  refused, so a batch cannot relabel or backdate what it seals. And the append is
  **idempotent by commitment identity** `(session_id, epoch, seq)`: a commitment
  the ledger already holds is skipped and counted, so a sealer response lost to a
  timeout and the mod's retry cannot duplicate a row. The append is one write
  followed by `fsync` — durability, not atomicity: a crash mid-write can leave a
  torn final line, which the sealer and every reader skip, and the next append
  starts on a fresh line after it. All-or-nothing framing belongs to the signed
  chain the production sealer feeds, not to the scratch file.
- **Into the signed chain.** In the full design the sealer's output is not a
  loose file: it is fed into GRIP's signed decision record — the HMAC-SHA256
  hash-chained `logs/api-audit.jsonl` (`lib/audit_chain.py`) and the
  Merkle-rooted signed forest (`lib/idr_forest.py`), with the public,
  externally-anchorable projection in `data/idr-public.jsonl`
  (`lib/idr_public_emitter.py`). Each batch becomes an anchored leaf, so a
  tampered commitment breaks the chain from that point and the Merkle root no
  longer verifies.
- **The spike stops at a scratch ledger.** `lib/witness_sealer.py` deliberately
  writes only a scratch JSONL file named by `--ledger` and **mechanically refuses
  the real chain** — `logs/api-audit.jsonl`, `data/idr-public.jsonl`, any
  `decision-log.jsonl`, anything under `state/audit/`, any `*.chain-state.json`
  or `*.chain-lock`. The refusal (`is_real_chain_target`) is code, not a comment,
  and is tested. This keeps the spike safe to run anywhere, CI included, while
  the design's sealing-into-the-chain is a separate, deliberate step.

## The crash-loss window, measured

A witness that cannot lose data cannot be built in memory. The honest design
names the four durability points and **measures** the span between each:

```text
 A: commitment computed (in memory)
      │  window A→B: at most one turn (commitments flush on turn.complete)
 B: durable record (batch appended to the ledger)
      │  window B→C: the sealer→chain feed
 C: batch sealed into the signed chain (hash-chained leaf)
      │  window C→D: the anchor cadence
 D: anchored externally (Merkle root published / timestamped)
```

- **A→B** is the live window: a crash after a commitment is computed but before
  the batch is appended loses that batch. It is bounded by one turn, because
  `turn.complete` flushes — in a `finally`, so a downstream handler that rejects
  or overruns cannot strand the batch — but a `kill -9` raises no `session.end`
  and no `turn.complete`, so the current turn's in-memory commitments are the
  exposure.
- **B→C** and **C→D** are the sealer's and the anchor's own cadences.

The window is **measured, not asserted**, through the sequence numbers: the
chain records the `seq` range each batch sealed, so a gap in `seq` across a crash
is a measured loss of known size, and the census distinguishes "committed and
lost" (a `seq` gap) from "never served" (an event absent from the census with no
gap). No claim of completeness is made without the gap report beside it.

**A worker respawn is an epoch, not a repeat.** The mod's state lives in the
hooks worker; when the engine respawns that worker mid-session, `register()` runs
again and the sequence restarts at 1 for the same `session_id`, which would read
as duplicates or as a rewound ledger. So every registration mints an `epoch` (a
random id) that rides on each row and in each batch id, and commitment identity
is the triple `(session_id, epoch, seq)`. A respawn therefore shows as a new
epoch with a short first sequence, and whatever the old epoch had in memory and
never sealed is the measured loss of that crash. Persisting the counter across
respawns (through `$.store`, which every session shares) would hide the event the
census is meant to show; the finished mod may add it as a cross-check, never as
the only record.

## Key ownership and disclosure

- **Ownership.** The witness key is GRIP's, provisioned like the audit chain's
  signing key (never in the repo, never in the ledger). In the finished mod it is
  a configured secret (`GRIP_WITNESS_KEY`) held under the same custody as
  `state/audit/` key material.
- **The spike's ephemeral fallback.** With no configured key the spike mints a
  **per-epoch ephemeral key** (`crypto.getRandomValues`) that lives only in the
  hooks worker's memory, and stamps every ledger row `key_source: "ephemeral"` so
  no reader can mistake it for an attributable one. Be exact about what such a
  row proves: once the worker is gone the key is gone, so **nobody can recompute
  or open an ephemeral commitment afterwards** — the row's `seq`, `epoch` and
  `session_id` still order the events, but the MAC is then indistinguishable
  from any 64 hex characters. Ephemeral mode is a shape test of the pipeline,
  never evidence; evidence needs `GRIP_WITNESS_KEY`. The design never lets an
  ephemeral commitment masquerade as an attributable one, and never escrows the
  ephemeral key into the ledger, which would hand the ledger's holder the power
  to forge.
- **Disclosure.** Commitments are disclose-or-not: to open one, reveal the
  content and the key and recompute the HMAC. This is deliberate — the record can
  be published (proving something happened, in order, untampered) without
  disclosing what, and disclosed selectively later. Key disclosure is therefore
  the sensitive act, and is governed by the same rules as any GRIP signing key:
  rotating it invalidates verification of older commitments, so rotation is a
  documented, versioned event, not a silent one.

## Missing-event detection

Three independent signals, so a gap cannot hide:

1. **Sequence gaps** — a hole in `seq` means events were committed and lost
   (crash window) or served and dropped.
2. **The census** — an event the mod subscribes to that never fired is recorded
   as *not served*, distinguishing "the harness did not raise it" from "the mod
   did not see it".
3. **Turn-boundary pairing** — a `turn.start` with no `turn.complete` is an
   unfinished turn (interrupted, crashed, or still running), flagged rather than
   assumed complete.

Missing-event detection is what makes the witness falsifiable: the claim is never
"I saw everything" but "here is exactly what I was served, here are the gaps, and
here is the size of each".

## Joining to the HAL wire's signed rows by the per-prompt id

The witness record is only half the picture; HAL's gateway already signs what it
served on the wire. Joining them ties *what the harness did* to *what the provider
actually served and cost*.

- **The join key is Claude Code's per-prompt id.** `prompt_id` is a UUID on the
  classic-hook input (`BaseHookInput.prompt_id`) documented as "correlating a user
  prompt with all subsequent events until the next prompt. Same value emitted on
  OpenTelemetry events as the `prompt.id` attribute, so hook output can be joined
  to OTel events at prompt grain." It is the **same id** Claude Code sends on the
  wire as `x-claude-code-prompt-id` and writes into the transcript as `promptId`.
  One prompt, one id, three surfaces — that is the join.
- **What HAL signs today.** HAL's gateway witness (`lib/hal/gateway_witness.py`)
  writes a **signed thread row** per exchange (`lib/hal/infinite_thread.py`,
  Ed25519 + ML-DSA-65, chained per thread), keyed by **`session_id`** — Claude
  Code's `x-claude-code-session-id` header, falling back to a per-gateway-launch
  id. Its payload fields are `route, model, provider, status, latency_ms, usage,
  list_price_usd, price_version, billing, actual_spend_usd`.
- **The honest gap.** HAL's signed rows are keyed by **session**, not by prompt;
  `prompt_id` is **not** in `PAYLOAD_FIELDS`, so a *per-prompt* join is not
  possible against today's rows alone. The wire *carries* `x-claude-code-prompt-id`,
  but HAL does not yet persist it on the row. So the full per-prompt join needs a
  coordinated HAL change: add the prompt-id header to the signed row's payload
  (an additive field). Until then the witness joins at **session grain**
  (`session_id`, which both sides have) and orders within a session by its own
  `seq` and by HAL's per-row `seq`; the per-prompt grain is the designed target,
  stated as not-yet-wired rather than claimed.
- **`turnId` is not `prompt_id`, and `tool.call` carries neither.** The
  function-hook events the witness's portable core uses are not uniform: `turn.complete`
  carries `turnId`; `tool.call` carries `agentId` (the loop) and `tool_use_id` but **no
  `turnId`** — a tool call is tied to its turn by the loop it ran in and by sequence
  order, not by a turn id on the event. Neither carries `prompt_id`, which lives on the
  *classic*-hook input. On the main loop a prompt and a turn are 1:1, but they are
  different identifiers and the design does not pretend otherwise. To capture
  `prompt_id` the finished mod also takes a **classic-hook seat** (whose
  `BaseHookInput` carries `prompt_id`) and correlates it to the `turnId` its turn
  events see; the spike records `session_id` (plus `turnId` on turn events, `agentId`
  on tool calls) and leaves the `prompt_id` correlation to the full mod.

## GRIP's own mods, exempt from "unattested" by a census hash

The witness will run alongside GRIP's other mods (the M2 guard, and itself). Its
own traffic must not be flagged "unattested" (a third-party call with no witness
behind it), or the record drowns in self-noise — and a mod must not witness itself
into an infinite regress. So GRIP's own mods are **exempt by a census hash**:

- The engine pins, per loaded module, its **tier** and its scanned **`uses`**
  (`{ events, calls, env }`) — `plugin.register`'s `PluginRegisterInput`. The
  witness computes a **census hash** over the set of loaded modules' *engine-pinned*
  attributes, and a module whose (tier, uses) matches the known-good GRIP census is
  exempt from the "unattested" mark.
- **The trust argument, and its limit.** The exemption must key on what the
  **engine pins**, not on what a module **says of itself**. The types are explicit
  that a plugin's `name`, `root`, `version` and `provenance` "are its own word…
  a rule keyed on them is one a rename walks past." So the census hash is computed
  over tier + uses (host-read), which a rename does not change. **But this is a
  deployment argument, not a cryptographic identity:** it says "these are the mods
  we shipped, in the managed tier, hooking what we expect", and it is only as
  strong as the managed tier's integrity. A module admitted to the *same* managed
  tier with the *same* uses-hash is indistinguishable from a GRIP mod by this
  mechanism alone — so the exemption inherits the trust of the managed tier and
  must never be presented as unforgeable. It is a way to keep GRIP's own noise out
  of the unattested set, not a way to authenticate a stranger.

## Coverage limits, stated plainly

The witness is not omniscient, and pretending otherwise is the failure mode the
whole design guards against. These limits are published with every claim:

- **A clean-lane call loads no mods.** Some Claude Code invocation lanes run
  without loading the plugin chain at all; on those the witness is not present and
  records nothing. Its absence there is invisible to it by construction — only an
  external reconciliation (the transcript, HAL's wire rows) can show the lane ran.
- **A mod can be switched off remotely.** Managed/remote settings can disable a
  plugin. A disabled witness produces no record and raises no alarm of its own;
  the census for that session is simply empty, which is why "empty census" must
  never be read as "nothing happened".
- **Three worker crashes unload every non-built-in mod.** Mods run in a worker;
  the engine respawns it, and after repeated crashes (three) it stops loading
  non-built-in mods to keep the session available. This is a **safety** feature
  that silently disarms the witness. The types confirm the machinery (a
  `session.start` fires again on "a worker respawn"; "a step that fails unloads
  its plugin"), and the crash-unload threshold is the engine's, not the mod's.
  Consequence: a session that destabilises its worker loses its witness, and the
  loss looks like a quiet census — detectable only by the gap between the session
  existing (HAL rows, transcript) and the witness record being empty or truncated.

None of these is fixable from inside a mod. They are the reason the witness is one
source among several (transcript, HAL wire, OTel), joined by the per-prompt id, and
never the sole authority on "what happened".

## Attack list (publish beside any claim)

Any statement built on the witness record ships with this list. If a claim cannot
survive being read next to these, it is not a claim.

1. **Clean-lane bypass** — a call on a lane that loads no mods is invisible; the
   witness cannot attest to what it was never loaded for.
2. **Remote switch-off** — managed/remote settings disable the mod; no record, no
   self-alarm.
3. **Worker-crash unload** — three worker crashes drop every non-built-in mod; the
   witness is disarmed mid-session and the loss reads as a quiet census.
4. **Crash-loss window** — commitments are in memory until the batch seals; a
   `kill -9` loses the current turn's batch (measured by the `seq` gap, not
   hidden), and a worker respawn starts a new `epoch` whose unsealed predecessor
   is the loss. A crash mid-append can leave a torn final line in the scratch
   ledger; readers skip it, and the scratch file claims durability, not
   atomicity.
5. **Ephemeral key** — with no configured key, rows are `key_source:"ephemeral"`
   and attribute to nobody; once the worker is gone nobody can recompute them at
   all, so they order events and prove nothing else. A shape test, not evidence.
6. **Key disclosure** — revealing the key lets anyone forge or open commitments;
   key custody is the whole scheme's root of trust.
7. **Census-hash spoof in-tier** — a mod in the *same managed tier* with the *same*
   uses-hash is exempt as "GRIP's own"; the exemption is a deployment argument, not
   an identity.
8. **Self-reported provenance** — anything keyed on a plugin's own `name`/`root`/
   `version`/`provenance` is walked past by a rename; only engine-pinned tier/uses
   are trusted.
9. **Per-prompt join not yet wired** — HAL's signed rows are keyed by `session_id`;
   `prompt_id` is not persisted on them today, so per-prompt joining needs a HAL
   change. Until then the join is session-grain only.
10. **`serverToolUses` not attested from function hooks** — the mod's `TurnUsage`
    omits `server_tool_use`; a web-search/fetch count claimed from `turn.step`
    alone is unbacked.
11. **`session.append` is not hooked by the spike** — the event exists and fires
    once per stored transcript row, but the spike commits only `tool.call` and
    `turn.complete`; a claim about transcript rows is unbacked until the finished
    mod seats it, and the census records it served-or-not.
12. **A mod above can rewrite first** — the witness records what it was *served*,
    not ground truth; a higher-tier hook that rewrites an event changes what the
    witness commits to.
13. **Observation is not neutral** — the engine raises some event chains *only
    when some plugin hooks them*: documented for `tool.check` (sec-default's
    README: "the engine raises that event only when some loaded plugin hooks
    it", so seating a guard there puts every tool call through the chain) and
    for `prompt.compose`. Whether `turn.step` — which the witness hooks for
    usage — dispatches the same way is not stated in the reference and must be
    measured, not assumed. To the extent it does, loading the witness changes
    what is observable: its presence perturbs the very surface it measures.
14. **Watch-only means it cannot prevent** — the witness records a harmful call
    that happened; it does not stop it. Prevention is M2's job, and M2 is the
    weaker, second-best fix (see [`grip-guard-mod.md`](grip-guard-mod.md)).

## The spike (what is actually built)

- `mods/grip-witness/` — a plugin folder: `.claude-plugin/plugin.json`,
  `hooks/hooks.json` (declaring the `modules` entry `./register.ts`),
  `hooks/hmac.ts` (RFC 2104 HMAC-SHA256 over `crypto.subtle.digest`) and
  `hooks/register.ts`: a module that registers watch-only `tool.call` and
  `turn.complete` handlers, commits each event under the witness key before
  `next(e)`, numbers events per registration, and on each finished turn seals the
  batch through `$.process.run` to the sealer (`GRIP_WITNESS_PYTHON`, default
  `python3`, runs it; inert until `GRIP_WITNESS_SEALER` / `GRIP_WITNESS_LEDGER`
  are set). A batch the sealer refuses is re-queued ahead of later commitments
  rather than dropped, under a cap of 1,000 past which the oldest go and the
  sequence numbers keep advancing, so the loss is a visible gap.
  `claude plugin validate mods/grip-witness` passes and reports `gating hook
  with .catch: tool.call` and the `$.env.get`, `$.process.run`, `$.session.id`
  calls it makes.
- `mods/grip-witness/tests/witness.test.ts` — ten tests under `claude plugin
  test mods/grip-witness`, in an environment like the hooks' own: `crypto.subtle`
  offers `digest` alone; `hmacSha256` returns RFC 4231's known answers;
  `canonical` sorts keys at every depth; unconfigured, the recorder runs no
  process and leaves the chain's answers untouched; configured, one finished turn
  hands the sealer one batch whose rows carry only the join keys and the MAC (no
  tool, path, reason or usage); with no key every row says `ephemeral`; a
  commitment recomputes from the key and the whole served event in canonical
  JSON (the disclosure property); a refused batch rides again next turn with the
  new commitments behind it; three parallel tool calls are numbered in arrival
  order; a turn the chain rejects is still sealed.
- `lib/witness_sealer.py` — the sealer CLI: validates the batch (each `hmac` 64
  lowercase hex, which is all it can check without the key; no sealer-owned
  field on a commitment), appends it to a **scratch** JSONL ledger (`--ledger`)
  idempotently by `(session_id, epoch, seq)`, stamps the seal time, syncs the
  append to disk, starts on a fresh line after a torn tail, and mechanically
  refuses the real chain. Covered by `tests/test_witness_sealer.py` (46 tests,
  bound into `config/test-blocks/unbound-sweep.txt`; the last nine pin the
  module's watch-only shape statically, because CI has no Claude Code binary).
  Nineteen mutants across the sealer, `register.ts` and `hmac.ts`, nine in the
  first round and one per Codex finding in the second, were each caught by a
  named test, with every file restored byte-identical afterwards.

The spike proves the two load-bearing moves — commit-before-next with a hand-built
keyed HMAC, and seal-through-`$.process.run`-to-a-scratch-ledger — and stops short
of the real chain, the classic-hook `prompt_id` seat, and the external anchor,
which are the next steps.

## Open questions / next steps

- Wire the sealer's scratch output into `lib/audit_chain.py` / `lib/idr_forest.py`
  as an anchored leaf, and define the batch→IDR record shape.
- Add the classic-hook seat that captures `prompt_id` and correlates it to
  `turnId`; drive the HAL change that persists `x-claude-code-prompt-id` on the
  signed row so the per-prompt join closes.
- Decide the anchor cadence (window C→D) and publish it beside the gap report.
- Specify key provisioning and rotation against `state/audit/` custody.
