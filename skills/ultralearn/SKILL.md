---
name: ultralearn
description: 'The lean RSI-witness kernel — ONE idempotent cycle (introspect → exogenous grip-sovereign-council deliberation → harvest the sha256+IDR seal → publish to the witness ledger). Banach f∘f=f, a re-run on unchanged RSI state makes ZERO new council calls. Use for "/ultralearn", "run a sealed introspection cycle", "witness the self-improvement with a fresh exogenous deliberation". NOT a mega-command — a cross-provider sovereign council explicitly de-scoped it (verdict BUILD-MINIMAL, seal 530ddaa79e8086d4) to its single external-value anchor: a third-party-auditable, cryptographically-sealed proof that GRIP deliberated over its own RSI state. Composes lib/ultracode (idempotent kernel) + grip-sovereign-council (Pool-B, $0) + the internal strat-ops witness page.'
---

# /ultralearn — the lean RSI-witness kernel

`/ultralearn` runs one idempotent cycle over GRIP's *existing* primitives and produces a
single artifact a skeptic **outside the building** can independently verify: a
cryptographically-sealed record that GRIP ran an exogenous, cross-provider deliberation
over its own recursive-self-improvement state.

It is deliberately **not** a be-all-and-end-all mega-command. A grounded sovereign council
(4 non-Anthropic speakers + a non-Anthropic judge, Pool-B, $0) voted **unanimous
BUILD-MINIMAL** and rejected the 20-skill-pipe / delight++ framing as internal theatre.
`/ultralearn` is the 20% that survived that cut. Provenance of the de-scope decision:
`grip-sovereign-council --verify 530ddaa79e8086d4`.

## The cycle

| Phase | What runs | Substrate |
|-------|-----------|-----------|
| **refresh** | re-run Claude Code's `/insights` headlessly so the facets introspect reads are current — skipped while the report is under 24 h old | `lib/cc_insights_refresh.py` (`bin/grip-insights`), Pool A, fail-soft |
| **introspect** | a cheap, deterministic snapshot of GRIP's RSI state (genome generation + fitness, insight count, memory generation, and the `/insights` digest: facet count in tens + the most frequent friction types) | file reads; honest `None` on any missing source |
| **deliberate** | `grip-sovereign-council` on a *grounded* question about that snapshot — N non-Anthropic speakers + a non-Anthropic judge | `bin/grip-sovereign-council` (Pool-B, exogenous — no model grades its own homework) |
| **prove** | **harvest** the council's sha256 provenance seal + signed IDR leaf — we do not re-mint provenance a real subsystem already produces | `lib/prove_it.py` / `lib/idr_forest.py` (via the council) |
| **publish** | append the sealed run-record to the witness ledger the internal strat-ops page renders | `state/ultralearn/witness-ledger.jsonl` |

## The Banach guarantee (f∘f = f)

The cycle is content-addressed on the introspection snapshot through
`lib/ultracode/kernel.py::run` (sha256 of `[question, state.root]`). Re-running on an
**unchanged** snapshot makes **zero** new council calls — the seal is served from the
persistent content-addressed cache (`state/ultralearn/kernel-cache.json`), and the ledger
is not grown. The RSI state genuinely changing (a new genome generation, new insights) is
what earns a fresh deliberation. This is the idempotency the council named as the sole
external-value anchor. Enforced by `tests/test_ultralearn.py` — the second-cycle
`council_calls == 0` assertion is the mutation canary.

## Honesty rails

- **The council is DIP-injected** (`adapter=`) so tests exercise the whole cycle without
  spending Pool-B or depending on non-deterministic LLM output.
- **A council failure fails soft and honest** — a network/provider/timeout returns a record
  with `verdict="UNAVAILABLE"` and **no seal**, that record is **not** witnessed, and the
  failure is **not** cached (the next run retries — fail-open, `rate-limit-never-blocks`).
  `/ultralearn` never fabricates a seal (`no-stubs`).

## The refresh phase — Claude Code's /insights, run headless

`main()` calls `refresh_insights()` before the cycle. It runs
`claude -p "/insights" --model claude-sonnet-5` through `lib/cc_insights_refresh.py` and judges
the result by the facet files and report it leaves behind, never by the exit code. Typing
`/insights` inside a GRIP session does not work, for reasons measured on 2026-10-02 with
Claude Code 2.1.283:

- the pinned `default` model alias (GRIP#6630, which keeps sessions on the 1M window) reaches
  the API unresolved, and every `/insights` call is refused with a 404;
- Opus 5.5 and Fable 5.1 cannot switch thinking off, so `/insights`' 4,096 and 8,192-token
  answers come back empty; Sonnet 5 answers within them;
- a session whose analysis failed is never retried until its cached stats are released,
  which the runner does before each run.

`/insights` spends the Claude weekly limit (Pool A, `rules/pool-a-offload-routing.md`), so the
refresh runs at most once per `GRIP_INSIGHTS_MAX_AGE_HOURS` (default 24). Inside that window it
is a no-op, which keeps the cycle's f∘f=f promise. `GRIP_INSIGHTS_REFRESH=0` switches it off. A
hollow or failed refresh is printed and the cycle carries on with whatever facets exist.

The digest reaches the council through `introspect()`'s `cc_insights` field, coarse enough
that the cache still hits on unchanged state. Adding the field changed every snapshot once, so
the first run after it convenes one fresh council. The same refresh runs from any harness that
has the `claude` CLI signed in: `grip-insights run` (`status` and `digest` spend nothing).

## Run it

```bash
# generic RSI introspection (no subject)
PYTHONPATH="$HOME/.claude:$HOME/.claude/lib" "$HOME/.claude/venv/bin/python" \
  "$HOME/.claude/lib/ultralearn.py"

# deliberate on a SPECIFIC subject - it is appended to the grounded question, so the
# council actually sees it. A new subject earns a fresh council; the same subject
# re-serves from cache (f.f=f). Omitting it leaves the prompt byte-identical to every
# prompt ever sealed, so existing seals stay valid.
PYTHONPATH="$HOME/.claude:$HOME/.claude/lib" "$HOME/.claude/venv/bin/python" \
  "$HOME/.claude/lib/ultralearn.py" "the subject to deliberate on"
```

Prints `SEALED · calls=N cache_hit=… witnessed=…` plus the seal and the
`grip-sovereign-council --verify <seal>` re-check command. The sealed record lands in
`state/ultralearn/witness-ledger.jsonl`; the internal strat-ops page renders it (encrypted,
`V>>--<<V`-gated).

## Falsification

`/ultralearn` is wrong / theatre if, over its next ~10 runs: no sealed record is ever
independently re-verified by anyone outside the building (the witness is unread → it is
theatre, delete it); OR the idempotence never holds in practice (a re-run on unchanged state
still convenes the council → the Banach claim is false, fix the kernel); OR the council's
"one highest-leverage improvement" output is never once acted on (the deliberation is
ceremony → cut the deliberate phase, keep only the seal). Sibling: `rules/loop-engineering.md`
(the ONE converge kernel), `rules/exogenous-anchor-law.md` (the judge stays exogenous),
`rules/loop-priority-external-over-theatre.md` (this whole skill lives or dies by external use).
