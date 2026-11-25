---
name: verdict
description: "Render a GRIP hypothesis-verdict delight card — the falsification-engine moment. Celebrates a CONFIRMED and a FALSIFIED hypothesis equally (Popper: good tests kill flawed theories; we remain alive to guess again). Reads the real hypothesis ledger so `/verdict H027` works end-to-end, or takes explicit args. Use when a pre-registered hypothesis resolves at its deadline, or when V>> invokes /verdict, or to mark the moment a prediction met reality. On a SUBSTANTIVE verdict (research, audit, review, comparison) it does not stop at the terminal — it ends by OPENING THE DELIVERABLE, one persistent artifact carrying the ruling, the evidence chain and the limits (`rules/verdict-opens-the-deliverable.md`). Distinct from AGORA's PR verdict and /ultrajudge — this is the scientific-method delight surface, not a review gate."
---

# /verdict — the falsification-engine delight card

GRIP is a falsification machine: every loop is meant to exit on a hypothesis
resolving against reality (`rules/scientific-method.md`, `rules/r0-critical-thinking.md`).
That moment had no delight surface — `/aurora` marks the night's aggregate work,
`/ship-card` the ship, `/rave` a milestone. `/verdict` marks **the verdict**: the
instant a pre-registered prediction met reality and was CONFIRMED, FALSIFIED, or
left INCONCLUSIVE.

The novel part: **a falsified hypothesis is celebrated exactly as much as a
confirmed one.** A refuted prediction is not a failure — it is the experiment
*working*. Refuted renders in GOLD, never red. This is GRIP's epistemology
(falsify before believing) rendered as joy.

## Usage

```bash
# End-to-end from the real ledger (learning/hypotheses.jsonl) — the usual path:
python3 -m lib.verdict_card H027 --from-engine

# Explicit (when you already hold the claim + outcome + evidence):
python3 -m lib.verdict_card H027 "autostash kills dirty-tree pull failures" CONFIRMED \
    --metric "dirty-tree pull failures / 90d" --prediction "0" --actual "0"
```

`--from-engine` reads the record by id, maps `status` → outcome, and renders.
A still-PENDING hypothesis raises (you cannot celebrate a verdict that has not
happened — verify-canonical: `status` is the record self-reporting). Colour is
auto-enabled on an interactive tty; `--no-color` forces plain text.

## How it works (canonical delight pattern)

- `lib/verdict_card.py` — pure deterministic render core (SHA-256-seeded
  aphorism, bit-identical across runs) + `from_engine()` as the single I/O seam.
  Mirrors `lib/rave_card.py`.
- Evidence tokens (`metric` / `prediction` / `actual`) are rendered **verbatim** —
  never rounded or truncated (output-tier precision invariant). `196 PRs` stays
  `196 PRs`; a SHA stays exact.
- Outcomes accept aliases: `FALSIFIED`→REFUTED, `PASS`→CONFIRMED, etc.
- Three print-only lines never touch the outcome. A REFUTED card says "The tower fell." A why ladder that ended with no evidence earns a stamp through `--because-i-said-so`, and the requirement is then marked for deletion. A claim that cites 67 says "Somebody count the fives."
- `bin/grip-popper-board` reads the whole ledger: the towers that fell, the towers still standing, the claims lost in the fog, and the `/knife-fight` scoreboard. It reads a high self-falsification rate as health.

## When to fire it

- A `/converge` / `/auto` / RSI loop iteration resolves its hypothesis.
- `lib/hypothesis_engine.py verify` flips a record to CONFIRMED/REFUTED.
- V>> wants to see a recent verdict as a moment of delight.

## Closing step — open the deliverable (PARAMOUNT, automatic)

A **substantive** verdict does not end at the terminal card. It ends by opening one
persistent artifact the operator can open, keep and share. Authoritative rule (not
restated here): `rules/verdict-opens-the-deliverable.md`. **Mechanism (2026-08-13):
the closing step IS a full `/witness`** (`skills/witness/SKILL.md`) — it supplies
the artifact, the cite.verify tally over `[[cite:ID]]`-bound claims, the sealed
IDR leaf, and the closing witness stanza with its see-it links; the three mandated
parts below are /witness's artifact contract. Client-context verdicts route to the
local branded-PDF path only, never a cloud page (V>> ruling, 2026-08-13).

- **Fires on:** research findings, audits, reviews, comparisons, falsification
  reports — any ruling whose reasoning and evidence have standing value past the turn.
- **Does NOT fire on:** a single hypothesis flipping inside an RSI loop, a one-line
  factual ruling, a pass/fail. Those get the card and nothing more — a hosted page per
  loop iteration is exactly the ceremony the rule guards against.
- **The artifact carries three parts**, and the third is the one that gets dropped:
  the ruling at a glance, the evidence chain with tokens **verbatim** (same precision
  invariant as the card), and **the limits** — what could not be adjudicated and what
  rests on weaker evidence. A verdict artifact without its limits section is marketing.
- **Sweep before publishing** (`visual-artifact-leak-prevention.md`,
  `no-sensitive-info-in-grip.md`): client names, internal hostnames, repo paths,
  third-party names quoted from a message. Artifacts start private, but the operator
  is one click from sharing — which is the whole point.
- **Name the authors** in the artifact per `provenance-name-the-authors.md`.
- **Return the link or path** in the closing block, so the operator does not have to go
  looking for the thing just made for them.

Fallbacks where no artifact surface exists: `professional-pdf`, or a written file under
a durable path plus `bin/grip-backup`. The invariant is *openable and persistent*, never
*hosted*.

Tests: `tests/test_verdict_card.py` (33 Goodhart-safe — determinism, pool
isolation, evidence-verbatim, the PENDING guard, no-ANSI-when-plain).
