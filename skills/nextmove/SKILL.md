---
name: nextmove
description: Ranks the candidate next moves before asking anything, derives how each should be run (inline, background, fleet, Pool B/C, council, or you-only), and puts one batched two-question choice to the operator. Use when a piece of work has just finished and the question "what now?" is about to be asked, or when the operator types /nextmove.
status: Active
version: 1.0.0
triggers:
  - /nextmove
  - "what should I do next"
  - "what's the best next move"
see_also:
  - hitl-decision-matrix
  - asking-users
  - prompt-routing
  - next
---

# /nextmove

Rank first. Ask second. Never hand over an unsorted menu.

## Why this exists

When work finishes, the honest question is not "what now?" — it is "of everything
outstanding, what matters most, and how should it be run?" Those are different
questions, and only the second one needs a human.

The scarce resource is ranking, not candidates. Measured on this machine on
2026-09-01: **98 open issues, ~125 hypotheses past their verification deadline,
3 open PRs** — roughly 226 live candidates. Asking an operator to choose from
that is not consultation, it is abdication. And presenting three of them without
saying they came from 226 is worse, because it reads as complete.

## What it does NOT do

It invents no asking machinery. `/hitl`, `/hitl-decision-matrix`, `/asking-users`
and `/creating-wizards` already exist; the ranking and gating already exist in
`lib/holistic_backlog.py` and `lib/holistic_backlog_consumer.py`; the execution
pattern already comes from `lib/prompt_routing.py`. This is the join. The
consumer's DEFER branch has said `"hold for human triage"` since it was written —
this is the triage surface that was never built.

## Protocol

**1. Build the slate.**

```bash
PYTHONPATH=$CLAUDE_DIR/lib python3 -c "import next_move as n; print(n.render(n.slate(k=3)))"
```

`slate()` gathers this session's loose ends first, then the global backlog;
round-robins by source; breaks ties on days-overdue; attaches a derived manner
and a predicate-shaped falsifier to each candidate.

**2. If AFK or auto/RSI is engaged, do not ask.** `slate.interactive` is False.
Take rank 1, say which and why in one line, and proceed. The ranking still runs —
muting it entirely would violate `rules/rsi-loop-no-yield.md`. Only the asking stops.

**3. Render the slate as prose, with its scope.** Top three, each with its
evidence, its derived manner, and the pool that manner spends. **The scope line
is mandatory** — `slate.scope_line()` produces it. A slate that cannot say what
it excluded must not be shown.

**4. Ask once, two questions in one call.** `build_questions(slate)` returns the
payload. Validate it first:

```python
import hitl_quality_gates as g
assert g.collect_violations(qs) == []
```

A live PreToolUse deny hook enforces ≤4 options, ≤4 questions, ≤12-char headers,
a terminal `?`, and no capital-O "Other". Validating first means the operator
never eats a denied turn. **This ceiling is why the slate is three**: a ranked
list longer than four cannot be a single `AskUserQuestion`.

**5. Record the choice.** Emit a `lib/decision_record.Decision` with `what`, `why`
(the evidence), `confidence < 1.0`, and the candidate's falsifier as
`falsification_criteria`. That format *rejects* a vibe-shaped falsifier at
construction, which is the point: a ranked candidate with no falsifier is a
preference wearing a number.

## The manner, and what it costs

Derived, not asked — a three-line fix never gets offered a council. The operator
overrides only when he disagrees.

| Manner | Pool | Note |
|---|---|---|
| `inline` | A | depth 0, the default |
| `background` | A | hides latency, **not cost** |
| `fleet` | A | Workflow tool — no pool switch exists on it |
| `pool-b` | B | HAL grip_infer, close to free |
| `pool-c` | C | `bin/grip-codex`, ChatGPT subscription |
| `pool-d` | D | Kimi Code subscription — **unarmed** until the keychain entry `grip-kimi-coding` exists; never offered before then |
| `council` | B | synchronous, exogenous non-Anthropic judge |
| `overnight` | B | the only mechanism surviving session exit |
| `fresh` | — | `gg++` **blocks a terminal** — hand over the command, never run it |
| `you-only` | — | credentials, payment, or the operator's judgement |

**Always name the pool.** Pool A is the weekly `/usage` percentage, so
recommending a fleet is recommending weekly-limited spend. A manner
recommendation that hides its pool is hiding the only number that matters.

## Anti-patterns

| Anti-pattern | Why wrong |
|---|---|
| Showing the slate without its scope line | Three of 226 reads as complete. The exact failure in `feedback_a_page_length_reported_as_a_total.md` |
| Asking before ranking | The whole point. An unranked menu moves the work to the operator |
| Two separate `AskUserQuestion` calls | Action and manner are 2 questions in ONE call (2 ≤ 4) |
| Dispatching a `delegable=False` candidate | It needs credentials or judgement; no pool can do it |
| Ranking by age | Measured weak (median 90, max 109 days, none past 180). Tie-break only |
| Muting the whole skill under AFK | Rank anyway; take rank 1. Only the *asking* stops |

## Falsification

Wrong if, over ~20 invocations: the operator picks rank 1 every time (ranking adds
nothing — demote to a one-line suggestion); OR he picks outside the top three more
than about one time in four (the signals are wrong — fix them, do not widen the
slate, which the hook forbids anyway); OR he overrides the derived manner more
often than he accepts it (routing is mis-modelled — feed corrections back into
`prompt_routing`, do not fork it); OR it is never invoked (delete it).

Track: rank of the chosen candidate; manner accept-vs-override ratio; and slates
where the operator's real next move **was not on the slate at all** — target 0,
and the most dangerous outcome, because that one reads as complete.
**Never track invocation count as success.**

Implementation: `lib/next_move.py`. Tests: `tests/test_next_move.py` (18,
mutation-checked — diversity, the terminal `?`, and the unpushed scope were each
observed red against the defect they guard).
