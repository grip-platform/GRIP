# GRIP Recall Substrate — Architecture

Moat-backed, cross-harness, memory-independent recall. This document is the
architecture reference for the recall substrate (task #500: moat-backed recall
plus an auto-firing preassert gate). The pinned cross-track contract is
`lib/recall/schema.py`; the full build plan (including the locked decision
matrix) is `plans/grip-recall-substrate.ultraprompt.md`.

## 1. Why this exists — the 2026-07-16 incident

After a `/clear`, a GRIP agent asserted "no rendered AOD exists on disk,
Confident" and proposed rebuilding the artefact. The claim was false. The
render sat in `file-history/e07fba57…/70aa5bc430503777@v2`; the signed IDR
forest (`state/precog/idr.jsonl`) and the context memory-chain
(`state/precog/context.jsonl`) had both recorded it; and a GRASP receipt
(`aod-arnold.receipt.json`: 6 verified, 0 not-found, grounding 1.0) sat on
disk — listed in the very first `find` and walked past.

The moat held the truth and went unqueried. A memory file the agent must
remember to grep is theatre; the recall substrate makes querying the moat
deterministic and auto-firing, so a confident false negative-existence claim
becomes structurally impossible on mediated surfaces, not merely discouraged.

## 2. The three legs of proof-carrying recall

1. **LOOKUP** — subject, alias, and entity search over the index produces
   candidate artefacts. `AOD`, `acknowledgement of debt`, `Arnold`, `K>>`,
   and filenames normalise through an alias table plus full-text search.
2. **VERIFY** — recompute the receipt hash, the IDR signature, and the
   content hash; confirm the file is present at its recorded pointer.
3. **FRESHNESS** — prove the latest subject event is still ACTIVE (not
   superseded or invalidated). Latest-event-wins; supersession and
   invalidation are first-class index events.

The index is a rebuildable materialised view over the existing authorities
(IDR forest, context chain, GRASP receipts, file-history) — never a new
source of truth. Recall is a lookup problem; the inclusion-proof leg already
exists as the signed IDR forest, and the MMR adds an audit commitment over
the append-only event log.

## 3. Locked design decisions (F1–F6)

Locked 2026-07-17 via the HITL decision matrix (V>> in session 38532),
recorded in the plan's "Decisions Locked" section and mirrored here.

| Fork | Locked outcome | Class |
|------|----------------|-------|
| F1 primitive | **Both in v1**: content-addressed subject index (primary lookup) + MMR audit commitment over the append-only event log | override |
| F2 gate on DENY | **Error state + forced-surface-to-operator (HITL) + signed RED-chain forensic node.** Autonomous/relay contexts: hard-block + record + surface in report, never silent. A contradicted confident claim = broken chain of trust, recorded intent-free (the contradiction, never the motive) | reframe |
| F3 coverage | **Mediated-only, honest.** Mechanism harness-agnostic in `lib/recall` as one syscall; thin adapters (CC hooks, HAL/HAPPI filter); coverage = adapters shipped × canaries proving each. The coverage↔test-coverage SMT mapping holds exactly over the enumerable/instrumented universe — which is why the claim stops at the mediated boundary | reframe |
| F4 freshness | Incremental event-append + verify-hash-on-read; MMR root per append batch; forensic events appended at gate-fire | default |
| F5 negatives | Semantic/fuzzy search NEVER alone authorises a negative claim (hard). A negative claim needs explicit scope + verified freshness + exact/alias/FTS basis | default |
| F6 delight | **All four headline delights**, built as 3 parallel ULTRACODE tracks: T1 gate core · T2 catch/HITL card + déjà-vu + wizard · T3 shareable provenance card + cross-harness demo. Gate isolated on its own track (directly answers the F6 falsifier) | reframe |

## 4. Component map

| Component | Responsibility |
|-----------|----------------|
| `lib/recall/schema.py` | The pinned cross-track contract: dataclasses (`Artefact`, `IndexEvent`, `Freshness`, `QueryResult`, `PreassertResult`, `ForensicNode`), verdict and status constants, canonical hashing, `forensic_uuid` HMAC identity |
| `lib/recall/aliases.py` | Subject and alias normalisation feeding LOOKUP (alias table + alias_add events) |
| `lib/recall/mmr.py` | Merkle Mountain Range audit commitment over the event log: `append_leaf` / `root` / `prove` / `verify` |
| `lib/recall/index.py` | The subject index — the rebuildable materialised view: `query_subject` / `verify_artefact` / `append_event` / build |
| `lib/recall/preassert.py` | Negative-existence verdict engine: ALLOW / DENY / UNVERIFIABLE under the F5 scope + freshness + basis discipline |
| `lib/recall/forensic.py` | RED-chain emitter: seals a `ForensicNode` only after its falsification attempt survives (the libel guard) |
| `lib/recall/halfilter.py` | HAL/HAPPI syscall wrapper routing GRIP-mediated output through preassert |
| `lib/recall/cards.py` | Block-as-delight "GRIP already made this" catch card shown when the gate fires |
| `lib/recall/dejavu.py` | Proactive recall-before-create: surfaces a near-duplicate before the agent rebuilds it |
| `lib/recall/wizard.py` | Operator onboarding and configuration wizard (stores, gate action, coverage policy, aliases, freshness model) |
| `lib/recall/provenance_card.py` | Screen-shareable provenance card: `build_card` / `render` / `reverify` (live re-proof of a recorded result) |
| `lib/recall/crossharness_demo.py` | Same-brain proof comparing `result_hash` across in-process, CLI, module-subprocess, and optional opencode legs |
| `bin/grip-recall` | The one deterministic syscall shim (syscall doctrine) fronting query / preassert / build / verify / dejavu / wizard |
| Output-gate hook | PreToolUse/Stop adapter: scans outbound assistant text for negative-existence patterns, calls preassert, applies F2 on DENY |
| Tool-result-gate hook | PostToolUse adapter: after an empty or failed `find` / `rg` / `ls`, injects recall evidence before the model concludes absence |

## 5. Coverage — the honest statement

Recall enforcement is **guaranteed only on GRIP/HAL/HAPPI-mediated output**.
The mechanism is harness-agnostic (one syscall in `lib/recall`), and coverage
equals the set of thin adapters actually shipped, each proven by its own
canary test. Everywhere else — a harness that emits directly without GRIP
mediation — enforcement is best-effort and must be labelled as such.

The reason this boundary is load-bearing is the **coverage illusion**, the
worst-ranked failure mode in the plan: claiming universal protection while an
unmediated path quietly bypasses the gate is strictly worse than an honest
smaller claim, because it converts a known gap into false confidence. The
cross-harness demo applies the same honesty at surface level: an uninstalled
opencode leg reports `skipped: grip-opencode not installed` and is excluded
from the identity verdict rather than silently counted.

## 6. The RED-chain forensic forest (v1)

The F2 reframe creates the RED chain: a Merkle-anchored, signed, append-only
forest of proven-incorrect AI assertions — the evidential counterpart of the
green IDR forest. v1 is fed exclusively by this gate's DENY events.

Node anatomy (`ForensicNode` in `lib/recall/schema.py`):

- **`forensic_uuid`** — HMAC-SHA256 over the identity tuple (session, prompt,
  user, model, provider, node, harness, envelope, ip, serial — whatever
  subset is known). The raw tuple never enters the chain; only the HMAC plus
  a field-presence mask, so the chain stays publishable evidence without
  leaking identity.
- **Claim + evidence** — the verbatim claim, its source, the contradicting
  artefacts, the belief head (context-chain record id at assertion time),
  and digests of the tool results that preceded the claim.
- **`input_commitments`** — sha256 commitments of the raw inputs (prompt,
  context). Reveal-and-verify in a dispute without storing raw content
  (council seal `8acc9c14`).
- **`anchor_ref`** — external temporal-anchor pointer (OpenTimestamps batch
  id), set when the RED root joins the green root's anchoring cadence.
- **Chain linkage** — `prev_hash` hash-chaining plus an HMAC `sig` over the
  canonical node, and a `remediation_edge` back to the green-chain IDR of
  the fix — which is what makes the RED chain useful for RSI, not shameful.

**The libel guard is load-bearing**: a node seals ONLY after its own
falsification attempt survives (`falsifier_survived` — the evidence is
re-verified and index freshness re-checked at emit time). Without it the
forensic chain would accuse falsely with cryptographic confidence. Severity
(`gated_decision` / `pre_output` / `reached_user`) and `where_caught` record
how far the false claim travelled and which gate stopped it.

## 7. CLI usage

All recall operations front through the one syscall shim:

```bash
# LOOKUP + VERIFY + FRESHNESS for a subject
bin/grip-recall query --subject "AOD Arnold" --type render --json

# Verdict on a claim before it ships (the gate calls this automatically)
bin/grip-recall preassert --claim "no rendered AOD exists on disk" --scope file-history --json

# (Re)build the materialised view from the authorities
bin/grip-recall build --store state/recall

# Re-verify a subject's artefacts against their recorded hashes
bin/grip-recall verify --subject "AOD Arnold" --json

# Recall-before-create: is there a near-duplicate already?
bin/grip-recall dejavu --subject "AOD render" --json

# Operator configuration wizard
bin/grip-recall wizard
```

The T3 delight surfaces run as modules:

```bash
# Screen-shareable provenance card (add --json for the dict form)
python3 -m lib.recall.provenance_card --subject "AOD Arnold" --store state/recall

# Cross-harness same-brain proof table
python3 -m lib.recall.crossharness_demo --subject "AOD Arnold" --store state/recall
```

## 8. The Goodhart canary

The done-predicate carries a mutation-sensitive canary: a test makes the
output path emit exactly "no rendered AOD exists on disk" while
`aod-arnold.receipt.json` exists, and requires the gate to **DENY** it. The
test fails if the claim passes — so a regression that silences the gate, or
a rewrite that games the detector without preserving the block, turns CI
red. The canary replays the real 2026-07-16 incident; green means the
original failure is now mechanically impossible on that surface.

## 9. Cross-links

- [IDR.md](../IDR.md) — the signed decision record; the IDR forest is the
  inclusion-proof leg recall builds on.
- [MEMORY_CONTEXT.md](../MEMORY_CONTEXT.md) — the signed belief chain; the
  `belief_head` in a forensic node points into it.
- HAPPI.md (`~/.hal/happi.md`) — the polyglot protocol whose envelope the
  HAL filter mediates; `cite.verify` is the claim-provenance sibling.
- [rules/prove-it-claims-canonical.md](../rules/prove-it-claims-canonical.md)
  — the behavioural law this substrate mechanises: a negative/existence
  claim is only as strong as the exhaustiveness of the search behind it.
- [rules/cryptographic-causation.md](../rules/cryptographic-causation.md) —
  the moat doctrine: recorded always, falsifiable by construction.
