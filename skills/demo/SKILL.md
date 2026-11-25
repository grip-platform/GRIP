---
name: demo
description: Live demonstration protocols for GRIP system showcasing. Use when presenting to prospects, teams, or recording tutorials. Leads with the provability moat (WITNESS / prove-it), any-model-via-HAL, and the one-command install.
status: Active
effort: medium
version: 2.0.0
triggers:
  - /demo
  - /mode demo
  - capability demo
  - live walkthrough
  - show me what GRIP can do
integrates:
  - prove-it
  - trust-review
  - concise-communication
  - asking-users
  - creating-wizards
  - hitl
  - professional-pdf
  - mode
---

# Demonstrating Capabilities

Live demonstration mode for showcasing GRIP **as it actually is now**. Optimise
for visual clarity, audience engagement, and honest live proof — never a faked
green.

## What GRIP is, in one breath (the three names)

- **GRIP** — the self-improving reasoning + decision system that runs the show.
- **HAL** — the any-model gateway: one local port (`:4010`) any AI provider plugs
  into. Swap models like changing a setting, never a rewrite.
- **HAPPI** — the open shared language all of it speaks.

> **The headline / the moat:** GRIP makes AI decisions **provable** — witnessed,
> cryptographically signed into a tamper-evident chain, and **re-verifiable live
> and offline**. Change one byte and the chain refuses to agree. Everything else
> is in service of that. Lead the demo with this.

## The moat, demonstrated live (lead with these two)

These need no slides — they prove themselves on camera.

| Demo | Command | What the audience sees |
|------|---------|------------------------|
| **Provable answers** | `/prove-it` | Every claim becomes a clickable, verbatim citation. A **hallucinated citation renders RED — "not found"**. A fabricated claim *cannot* pass; the deterministic check is un-fakeable. |
| **The witnessed node** | `grip-happiverse` (door → `ask` → `status` → `verify` → `cutover`) | One real question is **witnessed + signed** into a chain; `verify` re-checks the signatures independently, in front of you. The probe is **honest** — gateway down shows `NOT-LIVE` with the reason, never a fake green. That honesty *is* the pitch. |

The 5-command witnessed-node sequence (canonical, from the blackbook §6):

```bash
grip-happiverse                       # the node's surfaces — honest live/not-live probe
grip-happiverse ask "Witness this."   # one real question -> a witnessed + signed row
grip-happiverse status                # ports, the model actually served, chain tamper PASS/FAIL
grip-happiverse verify                # the signed chain re-verifies independently — the moat, live
grip-happiverse cutover               # route this harness through the witnessed gateway (every call signed)
```

`cutover` only *prints* the line; `cutover --apply` makes it permanent. Safe to
run cold.

## Mechanical safety + efficiency (also proves itself — no setup)

The enforcement hooks demonstrate themselves the instant you misuse a tool:

| Try this live | What happens | The point |
|---------------|--------------|-----------|
| type `grep pattern` | Blocked → suggests `rg` | Tool + token discipline (this fires every session) |
| `cat file` | Blocked → suggests Read | Token efficiency |
| `find . -name` | Blocked → suggests `fd` | Modern tooling |
| Read `node_modules/` | Blocked | Dependency guardian (saves 50k+ tokens) |
| Edit a file you didn't Read | Blocked | Approach-verification gate |

These are **deterministic DENY gates**, not suggestions — trust through
mechanism, not vibes. `/aegis` renders the standing roster of guardians.

## Any model, any harness (the HAL story)

- Type `grip` → it asks **which harness to open** (Claude Code, or Codex / aider /
  opencode / Hermes if installed) and defaults to a true **1,000,000-token**
  session. Press Enter for the Claude Code default.
- Every harness routes through HAL `:4010`, so you **swap the underlying model
  like a setting** — with multi-provider failover underneath (a transient
  provider 429 fails over, never blocks).

## One command sets up a whole machine (the blackbook)

A single polyglot file (`scripts/blackbook`, valid as **both** bash and python3)
bootstraps the entire GRIP world into `~/.grip` — the reasoning system, the HAL
gateway, the HAPPI language, local LLMs (RAM-tiered, concurrent pulls), and the
AI CLI harnesses — in **14 idempotent phases**. Great "watch me stand up a fresh
Mac" segment; re-runnable any time (every phase skips what's already done).

## The substrate (describe — never hardcode the counts)

Self-improving reasoning system: skills · agents · commands · rules · hooks ·
modes, plus adversarial review councils (**HAL Council** + **AGORA** — the
cross-provider PR reviewers), RSI loops (`/converge`, `/finish`, `/auto`),
cryptographic decision notarisation (IDR / `lib/idr_forest.py`), and semantic
memory + the **memory-chain** (signed, tier-classified continuity).

> **Counts are generated and stale-by-construction — do NOT hardcode them in the
> demo.** Show live numbers with `/grip-doctor`, or read the generated
> `rules/system-capabilities.md` (the `<!-- grip-stats -->` line). Quoting a
> frozen "95 skills" is exactly the staleness this skill used to ship.

## Demo flows

### Quick (5 min) — lead with the moat

1. `/prove-it` → click a green citation, then the **RED** fabricated one ("a fake
   claim can't pass").
2. `grip-happiverse verify` → the signed chain re-verifies live.
3. One live enforcement block (type `grep`).
4. Close on the one-liner: *GRIP reasons, HAL serves any model, HAPPI is the
   language — and every decision is provable.*

### Standard (15 min)

1. The moat (prove-it + witnessed node, as above).
2. Mechanical safety: trigger 2–3 live gate blocks.
3. Any-model: open `grip`, show the harness picker + 1M default; swap a model.
4. The blackbook one-command install story.
5. Substrate tour (live counts via `/grip-doctor`) + Q&A.

### Deep dive (30+ min)

1. Full witnessed-node sequence incl. `cutover --apply`.
2. An RSI loop end-to-end: `/converge` or `/finish` → CI + **AGORA** review →
   verdict-gated merge.
3. Architecture walkthrough (GRIP ↔ HAL ↔ HAPPI).
4. Live skill creation (`/create-skill`).
5. Interactive audience exploration.

## Reveal discipline (a demo IS a reveal surface — PARAMOUNT)

A live demo leaks if you let it. For any **external / prospect** audience:

- Show the **property** (provable · signed · tamper-evident · offline-verifiable)
  and the **honest probe** — never the **recipe** (no Ed25519 / ML-DSA / Merkle /
  anchoring internals; "cryptographic, offline-verifiable notarisation" is as far
  as you go).
- Use **obvious fakes** in any rendered asset (`you@your-mac`, `example.com`,
  `Jane Example`) — never real client / host / internal data
  (`rules/visual-artifact-leak-prevention.md`). Inspect rendered frames before
  publishing.
- Never name a real client, fundraising status, or roadmap on a prospect call.

## Output style

- Short, punchy sentences. "What you just saw: X" after each beat.
- Honesty is the brand: the `NOT-LIVE` probe is a *feature*, never apologise for
  it. A faked green would undercut the entire moat.
- Impact statements (tokens saved, claim caught, chain verified) over adjectives.

## AskUserQuestion triggers

Mandatory `AskUserQuestion` when: transitioning between demo sections, the
audience wants to dive into a specific feature, or before generating takeaway
materials (`/generate-pdf`).

## Changelog

- **v2.0.0** — Rewrote to current reality: leads with the provability moat
  (WITNESS / `/prove-it` / `grip-happiverse` witnessed node), adds any-model-via-HAL
  (`:4010`, harness picker, 1M default), the one-command blackbook install,
  memory-chain, and reveal-discipline. Removed hardcoded stale counts
  (95 skills / 16 agents / 25 modes → pointer to the generated source) and the
  dead "Ralph PR review" reference (now HAL Council + AGORA).
- v1.0.0: Initial creation with demo protocols, showcase sequence, live proof points.
