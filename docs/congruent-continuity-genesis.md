# Congruent Continuity — Genesis Plan (the fourth leg of Cryptographic Causation)

Status: HITL-refined genesis (2026-08-30). Four design decisions locked by V>>
via the /hitl Route-D decision matrix (all four recommended defaults accepted).
This is the W0 artefact — the basis of the idea, refined; the spec and code
follow in W1+.

V>> (Lourens Cornelius "Laurie" Scheepers)
___
With assistance of
  Claude Fable 5 (claude-fable-5) — reasoning, design, this document
  lib/gentner_enforcer.py — mechanical SMT validation of the Libet mapping

---

## WHAT (plain English)

Congruent Continuity is a new signed chain recording the reasoning path BETWEEN
what GRIP believes and what it decides. Today the moat records causation at the
endpoints: the IDR forest records decisions; the context chain records belief
checkpoints; /prove-it backs outward claims. The causal path between them — the
load-bearing premises adopted mid-flight — is unrecorded and unchecked. One
wrong premise there and every downstream thought and action is coherently wrong.

The primitive: before acting on a load-bearing premise, GRIP pre-registers a
**falsifiable commitment** — {claim, dependency refs, falsification condition,
intended action, severity} — signed and hash-linked BEFORE the action executes.
Exogenous checkers (non-Anthropic, Pool-B, $0) then test **Congruence** on two
axes: premise ↔ canonical reality, and trajectory ↔ operator goal. Verdicts are
signed back into the chain; a refuted premise yields a computable contamination
set (everything downstream that inherited the falsehood) and a divergence-point
record for RSI.

ELI5: today GRIP keeps sealed receipts for its decisions and sealed diary pages
for its beliefs. This adds sealed **bets**: "I am about to act because I believe
X; here is what would prove X wrong." An outside referee checks the bet — ideally
before the action lands, always after — and the sealed bet cannot be rewritten
to match the outcome.

## The two-axis model: coherence vs congruence

The failure mode of a next-token predictor on the wrong track is NOT
incoherence. A hallucinating model is *locally coherent with a false premise* —
the reasoning machinery works fine on bad input, which is why the wrong track
feels confident from inside. Therefore:

- **Coherence** (internal consistency of the trajectory): necessary, cheap,
  and nearly worthless as a wrongness detector.
- **Congruence** (external alignment): the load-bearing measurement.
  - **Reality-congruence**: is the load-bearing premise true against the
    canonical source? (verify-canonical probes: command+output, origin/main,
    live endpoint.)
  - **Intent-congruence**: does the current direction still serve the
    operator's stated goal? (Canonical instance: the 2026-08-17
    anchor-on-the-artefact incident — a perfectly coherent audio-decoding
    trajectory, incongruent with what a two-symbol string afforded.)

Terminology: **Congruent Continuity** names the chain (V>> rename mid-session 2026-08-30, from the working title "Coherent Continuity" — the name now carries the load-bearing axis); **Congruence** names the
quantity computed over it. (The abbreviation "CC" is already taken by
cyclomatic complexity in GRIP — the spec file will be CONGRUENCE.md.)

## The massive win (V>> mid-session directive, 2026-08-30)

> "if we can accurately predict that the AI is on 'the wrong track' and either
> course-correct if the error is obvious or HALT and then ask the user for
> clarity, it will be a MASSIVE win."

Made precise: GRIP's current HALT trigger (Rule 3 / Rule 7 confidence guard) is
SELF-assessed confidence — the exact signal the prove-it doctrine's binding
council (seal b32af619) classes as most confabulation-prone. An agent on the
wrong track is confident BY CONSTRUCTION. Congruence moves the HALT trigger to
an **exogenous measurement** — the first guard in the stack whose firing
condition does not trust the agent's own self-report.

## Locked decisions (HITL Route-D matrix, 2026-08-30)

| # | Decision | Locked choice | Rationale | Falsifier |
|---|---|---|---|---|
| D1 | Record unit | **Falsifiable commitments** — never narrative CoT | Self-narrated thoughts are the self-report/confabulation class; commitments are externally checkable. Signed temporal precedence = anti-HARKing (reasoning provably cannot be retro-fitted to the outcome) | Post-hoc RCAs repeatedly need narrative we did not record |
| D2 | Watch policy | **Tiered brake** | Obvious error + obvious fix → steer (inject correction, continue). Ambiguous → HALT + AskUserQuestion. Hard sync block ONLY for the irreversible class (delete/push/send/pay). Full-power-stop-override shape; escape-hatch audited | Steer-injections are ignored or wrong more often than they correct |
| D3 | Build order | **Post-hoc first** | Settlement loop has no latency constraint and builds a MEASURED checker-precision corpus before any live interrupt is armed. Schurger lesson: early-trajectory prediction is noisy; an unmeasured brake teaches the operator to ignore it | Post-hoc corpus shows precision so high that delaying pre-hoc cost real prevented-wrong-actions |
| D4 | Spec home | **Root polyglot sibling** — CONGRUENCE.md beside IDR.md + MEMORY_CONTEXT.md, plus rules/ doctrine | Follows the cryptographic-causation mandate pattern; specs are canonical + polyglot | — |

## Record schema (draft — reuses PrecogIDR wholesale, zero new cryptography)

Two new `kind` values on the existing dataclass, same signing, same forest,
same Merkle root, same anchoring:

```
kind: "continuity-commit"          # pre-registered, BEFORE the action
decision: {
  claim:            "the branch is stale relative to origin/main",
  falsifier:        "git rev-list --count origin/main..HEAD returns 0",
  deps:             ["sha256:<belief/context addrs this claim consumed>"],
  intended_action:  {tool: "Bash", shape: "git rebase ..."},
  severity:         "reversible" | "irreversible",
  goal_ref:         "<hash of the operator goal/instruction in force>"
}

kind: "continuity-verdict"         # settlement, pre- or post-hoc
decision: {
  commit_addr:  "sha256:<the commit being settled>",
  verdict:      "CONGRUENT" | "INCONGRUENT" | "UNVERIFIABLE",
  axis:         "reality" | "intent",
  evidence:     {cmd, output_digest} | prove-it receipt,   # deterministic floor
  verifier:     "deterministic" | "<non-Anthropic model id>",
  action_taken: "none" | "steer" | "halt-ask" | "block"
}
```

Design invariants:
- The commit is appended (flock + fsync) BEFORE the intended action executes —
  provable temporal precedence via the signed chain.
- The verdict's evidence is a deterministic probe wherever one exists
  (llm-augmentation-pattern: deterministic floor, LLM triage on top).
- Checkers are exogenous per exogenous-anchor-law: non-Anthropic Pool-B
  ($0 — groq/gemini/deepseek via HAL); capability-locked read-only per
  untrusted-content-capability-lock (trajectory content includes untrusted data).
- A refuted premise marks its dependency-closure downstream commits SUSPECT
  (contamination set) — append-only, forward-masked, never rewritten.

## Prior art (structural anchors, each a proven system)

1. **Scientific pre-registration** — commit hypothesis + test before the data;
   kills HARKing. Our chain proves the ordering cryptographically.
2. **Truth-maintenance systems** (Doyle JTMS / de Kleer ATMS) — beliefs carry
   justifications; retract a justification, retract its dependents. Our
   contamination set is exactly this, signed.
3. **CPU speculative execution** — run ahead of the unresolved branch; on
   misprediction, squash back to the branch point and replay. Our divergence
   point + replay is the same shape.

The Libet readiness-potential analogy passed the mechanical Gentner gate
**100/100** (5/5 causal relations, 62% mapping ratio, 0 surface relations) with
`rp_correctness`, `conscious_experience`, and the Schurger stochastic-
accumulation debate declared orthogonal. Licensed use: the **veto window**
only — an observer with access to the antecedent state can predict the
commitment before the agent registers it, and the gap is the intervention
window. Correctness is explicitly OUTSIDE the mapping: imminence detection and
verdict are separate mechanisms. Transferred caveat (Schurger): prediction
precision rises sharply near the commit point — intervention thresholds scale
with proximity; do not fire on early-trajectory noise.

## Pre-registered hypotheses (register in hypothesis_engine at the W1 PR)

- **H-CC-1**: within 30 days of the pre-hoc watcher arming, >=1 interrupt
  prevents an action that post-hoc settlement verifies would have been wrong.
  Refuted → the pre-hoc leg is overhead; demote to post-hoc only.
- **H-CC-2**: post-hoc settlement localises >=50% of sampled session failures
  to a specific divergence commit. Refuted → the dependency/contamination
  model is mis-specified.
- **H-CC-3**: recording + checking overhead stays <10% of session token budget
  (Pool-B checkers, severity-sampled commits). Refuted → sample harder or
  narrow to irreversible-class actions only.

## Falsification of the primitive itself

Wrong if: watcher precision proves no better than the self-assessed-confidence
baseline over ~20 sessions (the exogenous trigger adds nothing — drop the
watcher, keep the chain as audit record); OR commit volume is so high the chain
is noise (granularity mis-set — commit only decision-gating claims, R0 scope);
OR no contamination set is ever replayed/consumed (the analysis surface is
dead — the chain must feed RSI or it is theatre).

## Wave plan

- **W0 (this document)**: genesis plan, HITL-locked. DONE at this commit.
- **W1**: CONGRUENCE.md polyglot spec (root, sibling to IDR.md) +
  rules/congruent-continuity.md doctrine; PR through AGORA; H-CC-1..3 registered.
- **W2**: recorder — lib/continuity_chain.py (build/append/read/settle on the
  two kinds), CLI, tests that can fail (prove settle(REFUTED) flips downstream
  state; prove a tampered commit breaks the chain).
- **W3**: post-hoc settlement worker — falsify-self POPPER shape per unsettled
  commit; deterministic probe floor + Pool-B triage; contamination computation;
  checker-precision corpus.
- **W4**: pre-hoc PRECOG watcher — PreToolUse tap on decision-gating actions;
  tiered brake (steer / halt-ask / block-irreversible); escape-hatch audit per
  gate-resolution-escape-hatch (the watcher must never block its own
  resolution). Armed only after W3 measures precision.
- **W5**: integration — IDR leaves reference their continuity segment;
  /prove-it receipts settle commits; divergence metrics feed /evo.

## Relationship to the existing moat (harmonious, not duplicative)

| Piece | Records | Congruent Continuity adds |
|---|---|---|
| IDR forest | the decision node | the derivation path INTO the node |
| Context chain | belief at checkpoints | per-claim deps between checkpoints |
| decision_anatomy | assumptions AT a decision | assumptions BEFORE it, pre-registered + checked |
| /prove-it | evidence for outward claims | the claim BEFORE its evidence, with its falsifier |
| hypothesis_engine | long-horizon predictions (days) | intra-session, per-action horizon (seconds-minutes) |
| AGORA councils | exogenous review at PR granularity | exogenous review at STEP granularity — streaming AGORA |

The stack becomes: BELIEF (context chain) → REASONING (Congruent Continuity,
new) → DECISION (IDR) → CLAIM (prove-it) — every layer signed, cross-linked,
and exogenously checkable. The missing middle layer closes.
