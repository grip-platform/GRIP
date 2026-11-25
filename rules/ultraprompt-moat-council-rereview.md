# Ultraprompts Get a Moat-Council Re-Review (PARAMOUNT)

Origin: 2026-07-18, V>>: *"New rule — all ultraprompts have to be re-reviewed and
re-analysed via /converge loops with /rsi /ultrareason where the Criterion is:
gap-analysis, and critical review using our MOAT — a sovereign council of different
LLMs acting as different experts like /broly agents."* Rule-at-N=1 (operator-declared
PARAMOUNT). Dogfooded at birth: the very run that created this rule executed
`/ultrareason` on the first such ultraprompt and its own falsifier caught a **live
shipped defect** — the `/ultrareason` → council escalation was passing the literal
word `"council"` as the question (`lib/ultrareason.py` argv), so the council answered
*"what is a council?"* and never saw the plan. Running the skill's own falsifier
caught the bug (the same pattern V>> praised on #530). Fixed + the real sealed
council then ran (seal `26806b2a27006eaf`).

## The Law

> **A `/ultraprompt` (next-session continuation brief) is not "done" when written —
> it is done when it has survived a `/converge` re-review whose CRITERION is
> (1) gap-analysis and (2) critical review through the MOAT: a sovereign council of
> different LLMs acting as different experts (`/broly`-style lenses), judged by an
> exogenous non-Anthropic judge. Surviving concerns are folded back into the
> ultraprompt (the `improve` step); the brief is not handed to the next session
> until they are.**

An ultraprompt transfers *working state* into a fresh context — a flawed one
propagates its gap into the next session's whole plan. The re-review is the cheap
exogenous check that catches over-reach, under-specification, and false-premise
BEFORE it drives a session.

## The loop (kernel-shaped, `loop-engineering.md`)

`converge(state = the ultraprompt, criterion = gap-analysis clean AND the moat
council returns no unaddressed blocker, improve = fold each surviving concern into
the brief, exogenous-exit per exogenous-anchor-law.md)`:

1. **Ground + reason (`/ultrareason`, `/rsi`)** — moat-grounded cheap pass first
   (Pool-B, `pool-a-offload-routing.md`); it must actually engage the plan (verify
   `moat_facts > 0` / `grounded_on` non-empty — an empty grounding is the bug tell).
2. **Moat council (`grip-sovereign-council`, `/broly` lenses)** — N distinct
   non-Anthropic expert speakers + an **exogenous non-Anthropic judge**, sealed
   (sha256 + IDR leaf). Distinct lenses, not redundant voices (e.g. domain-expert +
   competitor-strategist + R0-falsifier). Ground each per Rule 21-bis.
3. **Fold + re-check** — surviving concerns → into the ultraprompt; re-run until the
   criterion holds or a bounded depth cap, then hand off with the verdict + seal
   recorded IN the brief (a §"self-review verdict" block).

## Non-negotiables (inherited, not restated)

- **Exogenous judge, non-Anthropic** (`exogenous-anchor-law.md`) — the council is
  the exit anchor; a self-graded review is theatre.
- **Pool-B routing** (`pool-a-offload-routing.md`) — council/ideation off the weekly
  limit; never the Agent-tool `broly` (Pool A) for the cognition.
- **Grounded premise** (Rule 21-bis, `prove-it-claims-canonical.md`) — a council
  verdict on an unverified premise launders corruption; cite the moat/state, not a
  copied claim. Verify the reasoner actually grounded (the `moat_facts=0` / literal-
  `"council"` failure modes are the canary).
- **Sealed record** (`cryptographic-causation.md`) — the verdict signs an IDR leaf;
  the seal goes in the brief so the next session can `--verify` it.

## Falsification

Wrong if, over the next ≥10 ultraprompts it governs: the moat-council re-review never
once surfaces a gap the author would have shipped (pure ceremony → demote to advisory
for low-stakes briefs); OR the re-review cost exceeds the cost of the gaps it prevents
downstream (mis-tuned → scope to high-stakes / build-driving briefs only); OR a full
sealed council is unreachable often enough that the cheap-tier pass becomes the de
facto "council" (then the multi-LLM requirement is not being met — fix routing, don't
drop the rule). Track: gaps caught per ultraprompt re-review (target ≥1 on
build-driving briefs); ultraprompts handed off WITHOUT a recorded verdict+seal
(target 0). Goodhart-safe: measured by gaps-prevented-downstream, never by council
invocation count.

## Scope

Every `/ultraprompt` / next-session continuation brief that drives non-trivial work
(a build, a strategy, a migration, a multi-step plan). A trivial one-line handoff is
exempt (no plan to gap-analyse). Sits beside `loop-engineering.md` (the converge
kernel), `exogenous-anchor-law.md` (the exogenous exit), `pool-a-offload-routing.md`
(Pool-B council), `prove-it-claims-canonical.md` + Rule 21-bis (grounded premise),
`cryptographic-causation.md` (the sealed verdict), and the `ultraprompt` /
`ultrareason` / `grip-sovereign-council` skills it composes. Mirror into
`~/.hal/rules/` if HAL gains an ultraprompt surface.
