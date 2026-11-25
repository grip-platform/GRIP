---
name: realise
description: Realise — make GRIP's recursive self-improvement REAL and WITNESSABLE. Renders the live RSI signals (hypotheses confirmed/refuted, the decision chain) into a verifiable, Banach-idempotent witness — each signal backed by deterministic provenance so a skeptic verifies rather than believes. The entry verb of the Precognition area. Use for "/realise", "witness the RSI", "prove GRIP is self-improving", "show me the self-improvement", or to render/preview the public RSI-witness dashboard.
---

# /realise — witness the self-improvement

**"Don't trust that the AI is recursively self-improving. WITNESS it."**

`/realise` makes GRIP's live self-improvement *real* and *witnessable* — in both
senses of the word: *understand it profoundly* and *actualise it into a
verifiable artifact*. It is the entry verb of the **Precognition** area (the
cousin of the public [decision-chain](https://codetonight-sa.github.io/grip-decision-chain/)),
built on `lib/rsi_witness_publish.py`.

## What it does

Renders the live RSI signals into a minimal witness page where **every signal
carries its own provenance** — the command or source a skeptic runs to verify it
independently. A fabricated signal has no resolvable proof; the render is a pure
function of the signals (Banach fixed point), so the page changes only when the
real substrate does.

Signals (W1): **hypotheses** confirmed/refuted (the strongest — a high refutation
count proves GRIP *falsifies* its own claims, exogenously verified) · the
**decision chain** pointer. There is no genome card: a pre-registered test on
2026-11-22 found that no production code acts on the genome.

## How to run

```bash
# Dry-run: verdict + manifest + idempotence proof (no files published)
python lib/rsi_witness_publish.py dry-run

# Render a local artifact you can open in a browser
python lib/rsi_witness_publish.py render ./rsi-witness-site   # then open index.html

# Prove the Banach fixed point (render(render(S)) == render(S))
python lib/rsi_witness_publish.py selftest
```

Then surface a plain-language digest: what changed, and the one-line proof for
each signal.

## Boundaries (honest, per the doctrines)

- **Never fakes a signal.** A source that cannot load is OMITTED, not invented
  (`load_signals` fail-soft). The witness never shows what it cannot back.
- **Never auto-publishes.** W1 is local render + dry-run only; the live gh-pages
  publish is the operator's gated step (`exogenous-anchor-law.md` bootstrap
  carve-out). Publishing sends content externally — V>>'s call.
- **External-value gated.** This is a witness for someone OUTSIDE the building
  (a skeptic, a regulator, an investor) — success is measured by external
  *verification* events, not page views (`loop-priority-external-over-theatre.md`).

## Grounding

`lib/rsi_witness_publish.py` · `lib/hypothesis_engine.py::status` ·
sibling `lib/decision_chain_publish.py` ·
issue: Precognition public RSI-witness dashboard. Rules: `cryptographic-causation.md`,
`exogenous-anchor-law.md`, `loop-priority-external-over-theatre.md`.
