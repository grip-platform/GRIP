# Output Tier — Layer Rule (per-user, opt-in) (PARAMOUNT)

Origin: 2026-05-16, GRIP #2776. Design LOCKED by the grounded MESH + real
/agora deliberation in `drafts/default-plain-output-mesh-2026-05-16.md`
(Rule 21 — implement the verdict, do not re-deliberate). This rule
**LAYERs strictly subordinate to `rules/caveman-default.md`** and does
**not** restate or modify it (DRY; that file stays the unchanged
PARAMOUNT default).

## The default does not change

The session default remains exactly `caveman-default.md`'s
`/caveman full` + Rule 17 plain-language rails. This rule only adds a
**per-user opt-in knob** on top. A user who sets nothing observes
byte-identical behaviour to before #2776.

## Tiers (resolved by `lib/output_tier.py`)

| Tier | Meaning |
|------|---------|
| `caveman_plain` (DEFAULT) | The unchanged caveman-default + Rule 17. No change. |
| `simple` (opt-in) | ≡ caveman + plain-language — the safe escalation; substance-identical, lighter prose. |
| `eli5` (opt-in) | `/eli5` *register and analogy* rules, but eli5's **fact-rounding rule is SUPPRESSED** (a deliberate, documented GRIP divergence from the stock skill — GRIP output is *executed*, not read as an essay). |

Resolution precedence (mirrors the channels-wizard contract): env
`GRIP_OUTPUT_TIER` → `state/output-tier-config.json` → default
`caveman_plain`. CLI: `grip output-tier <tier|status>` (no wizard — KISS).

## Precision-Preservation Invariant (the binding constraint — every tier)

> **At ANY tier, the EVIDENCE TOKENS of a claim — file paths, line
> numbers, SHAs, exact counts, error strings, commands, version numbers —
> are NEVER rounded, glossed, or dropped. Only the EXPLANATORY PROSE
> around them changes register.**

A claim's evidence is load-bearing at every tier; only its pedagogy is
tier-variable. `196 PRs` stays `196 PRs`, never `196 little pieces`;
`f9b5a4cf` stays `f9b5a4cf`, never "a recent change". The R0
falsification clause is prose — it may shift register ("this is wrong
if X" → "this breaks if X") but **must never vanish**. Mechanically this
is the existing caveman Auto-Clarity Override rail
(`caveman-default.md` §"two non-negotiable rails") extended with one
trigger: the evidence + falsification clause of any factual claim
survives at every tier. No new hook — rule-loaded, matching the caveman
precedent.

**Scope of 'evidence tokens'**: file paths the user will Read/Edit, line
numbers in active discussion, SHAs the user must verify, exact counts
in falsification arguments, error strings being debugged, version
numbers gating an upgrade. NOT decorative reference IDs (PR/issue
numbers in narrative recap — see `canonical-output-style.md` Layer-2
substrate clause).

## Binding Devil's-Advocate dissent (recorded, load-bearing)

The invariant is prose discipline, not a mechanical gate. eli5's failure
mode is *invisibly* wrong (a plausibly-worded rounded number reads as a
correct answer — no syntactic tell). Therefore `eli5` is **opt-in only**:
the blast radius is one user who explicitly accepted the contract, and
the default (`caveman_plain`) has no fact-rounding rule to violate. **If
`eli5` ever becomes a default, or its fact-rounding suppression is relied
on at scale without a real hook, this dissent becomes a CONFIRMED
falsifier.** The opt-in boundary is load-bearing safety, not a UX nicety.

## Falsification

Wrong if, over ~30 sessions: (a) no user ever selects an opt-in tier AND
resolver+rule+CLI cost more context than they save → delete this rule +
`lib/output_tier.py`, keep only `caveman-default.md`; (b) ≥1 incident of
a rounded fact reaching an `eli5`-tier user despite the invariant → the
invariant is not enforceable as prose discipline; harden into a hook or
abandon the `eli5` tier; (c) any user reports the **default** got less
precise (it must not — default unchanged) → caveman-scoping regression,
revert per caveman-default's revert clause; (d) the "wants fuller
explanation" detection pushes `misunderstood_request` above the 18%
corpus baseline → drop inference, keep explicit phrase only.

## Scope

Layered atop `caveman-default.md` (unchanged) + Rule 17 + the
`plain-language` / `eli5` / `caveman` skills. Resolver-only mechanism;
prose discipline enforced by review + the existing caveman rail, not a
new gate.
