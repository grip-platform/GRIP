# Design #464 — Shrink the Subagent Preload Below the 200K Ceiling

Status: DESIGN DRAFT for V>> + AGORA ratification (guardrail-class shared
config — `rules/dispatch-economics.md` names this the one large reducible
lever; never a silent edit). Implementation task: #464. Evidence base:
the design-reviewer investigation (task #463, PR #4030, 2026-07-14).

## 1. Problem (measured, not theorised)

- A GRIP custom-agent spawn answered a zero-tool one-liner at a reported
  **207,920 tokens** (design-reviewer, 2026-07-14 20:2x). A context
  introspection probe confirmed the composition: **RULES yes, TASKLIST no**.
- Consequence at a 200K window: **0-tool spawns live; any multi-file read
  workload dies** with "Prompt is too long" after the first Read batch.
  Five falsified dispatches proved NO model pin escapes it (sonnet,
  inherit, haiku all died on a ~9k-token 6-file audit).
- The preload MOVES: HOLISTIC.md / MEMORY.md / session-context.md
  regenerate mid-session, so the same agent flips between working and
  dying — intermittent-by-construction.

## 2. What the preload is made of (chars, live tree 2026-07-14)

`~/.grip/rules/` total: **393,906 chars ≈ ~98k tokens**, loaded natively
and unconditionally into every general-purpose/custom spawn (per
`rules/dispatch-economics.md`, verified 2026-06-24). Top items:

| File | Chars | Class |
|---|---|---|
| operational-context.md | 39,971 | operational data (already git-crypt) |
| efficiency-rules.md | 29,536 | doctrine |
| HOLISTIC.md | 27,739 | generated memory kernel |
| session-context.md | 26,317 | generated warm-start (EMBEDS a full HOLISTIC copy) |
| learned-insights.md | 16,481 | generated |
| canonical-output-style.md | 13,030 | doctrine |
| agent-dispatch-protocol.md | 12,826 | doctrine |
| CLAUDE.md (rules index) | 11,535 | index |
| syscall-doctrine.md | 10,616 | doctrine |
| ci-review-driven-convergence.md | 9,781 | doctrine |

Plus per-agent mandated skills (design-principles ~9.7k + 3 more) and
tool schemas.

## 3. Quick wins (mechanical, reversible, NOT gated — ship first)

- **QW1 — de-duplicate the kernel.** `lib/kono_preload.py` writes the FULL
  HOLISTIC kernel INTO `rules/session-context.md` while the harness also
  loads `rules/HOLISTIC.md` itself. Every spawn pays ~27k chars (~7k
  tokens) twice. Fix: session-context REFERENCES the kernel
  ("Holistic kernel: rules/HOLISTIC.md — loaded separately") instead of
  inlining it. One generator edit + test.
- **QW2 — cap generated members.** `learned-insights.md` (16.5k) and the
  session-context recall block get hard char caps in their generators
  (the HOLISTIC synthesiser already has one: ≤30k).

Estimated QW effect: ~45-55k chars (~11-14k tokens) off every spawn.

## 4. The structural options

### O1 — directory split (move non-safety rules out of `rules/`)
Move doctrine bodies to `docs/rules-full/`; `rules/` keeps only a safety
core. Main sessions regain depth via CLAUDE.md `@`-imports.
**Risk**: whether subagent loads follow `@`-imports is UNVERIFIED — if
they do, the split saves nothing for spawns; if imports are main-only,
this is the cleanest cut. Requires the E1 experiment below first.

### O2 — digest split (HOLISTIC pattern applied to the rules themselves) — RECOMMENDED
Every NON-safety rule file is rewritten in place as a **≤12-line digest**:
the law, the trigger, the falsifier pointer, and a `Full body:
docs/rules-full/<name>.md` link (full text moved there verbatim, git
history preserves everything). Safety-core rules keep FULL bodies.
Works regardless of import semantics (the auto-loaded directory simply
shrinks); main sessions Read the full body on demand (Rule 0 tier 2);
KONO recall unaffected.

Safety core (full-body, non-negotiable — the gate/irreversibility class):
`no-guardrail-evasion`, `untrusted-content-capability-lock`,
`sync-mirror-safety`, `bash-safety`, `no-sensitive-info-in-grip`,
`commit-standards` (git safety section), `exogenous-anchor-law`,
`cryptographic-causation`, `visual-artifact-leak-prevention`,
`operational-context` (operational truth, not compressible without loss).
Everything else (process/doctrine/loop rules) digests.

### O3 — per-agent rules opt-out (frontmatter `rules: core`)
Rejected: requires harness support that does not exist today.

## 5. Verification protocol (exogenous, cheap)

- **E1 (pre-work probe)**: create `rules/_probe_import_target.md` reachable
  only via `@`-import; spawn the introspection agent ("is PROBE_MARKER in
  your context?") → decides O1 viability. One dispatch, ~30s.
- **E2 (before/after)**: the 0-tool introspection probe (token accounting
  + RULES/TASKLIST answers) run before, after QW1-2, and after each
  digest batch. Success criterion: zero-tool spawn ≤ **150k** tokens →
  ≥50k headroom for real workloads at 200K.
- **E3 (workload anchor)**: the exact 6-file lib/harness audit that died
  5/5 on 2026-07-14 becomes the regression anchor — it must complete.

## 6. Staging

1. QW1 + QW2 (mechanical PR, tests on the generators). Re-measure (E2).
2. E1 probe → lock O1-vs-O2 (recommendation: O2 regardless; O1 only if
   imports are proven main-only AND V>> prefers relocation to digestion).
3. Digest batch 1: the 10 largest non-safety rules (~150k chars ≈ 37k
   tokens gross). Re-measure. AGORA reviews the digests for lost teeth —
   each digest MUST retain the rule's trigger + law + falsifier.
4. Iterate until E2 ≤150k or the non-safety pool is exhausted.

## 7. Rollback + falsifiers

- Everything is file edits under git — single revert per batch.
- **Falsified if**: (a) after reaching ≤150k the E3 workload anchor still
  dies (the preload model is incomplete — investigate schemas/skills
  share); (b) operator corrections traced to a DIGESTED rule's lost
  nuance exceed 2 in 30 days (restore that rule full-body — digests are
  per-file reversible); (c) QW1 breaks warm-start continuity (session
  resume degrades — revert the generator edit).

## 8. Governance

QW1/QW2: normal PR + AGORA (generator behaviour, not rule content).
O2 digest batches: **V>> ratification + AGORA per batch** — rule bodies
are the behavioural substrate of every session; compressing them is a
guardrail-class change by definition. This document is the ratification
artifact: V>> approves the safety-core list (§4) + the staging (§6).
