# /ultrajudge

**Multi-model verdict council** — N models render verdicts on a question with
assigned perspective roles, weighted into a decision matrix with explicit dissents.

Part of the `ultra*` family: `/ultrathink`, `/ultraplan`, `/ultrareview`, `/ultraread`, `/ultrajudge`.

## Quick usage

```bash
/ultrajudge "Should we adopt the syscall doctrine repo-wide?"
/ultrajudge --roles devil,pragmatist,futurist "<question>"
/ultrajudge --models opus,grok-3,sonnet,llama "<question>"
/ultrajudge --weights '{"devil": 2.0, "pragmatist": 1.5}' "<question>"
```

## Roles

| Role | What it does |
|---|---|
| `devil` | Finds the strongest case against |
| `pragmatist` | Evaluates cost/benefit — does this actually matter? |
| `futurist` | Traces second-order consequences over 12-24 months |
| `integrator` | Synthesises across all other roles |
| `historian` | Checks if this has been tried before and what happened |

## What you get

A Markdown deliverable at `state/ultrajudge/<slug>-<timestamp>.md` with:

- Weighted verdict: PROCEED / RETHINK / ABANDON with confidence %
- Decision table (role, model, verdict, confidence, reasoning) — the Model
  column names the model HAL **actually served**, and any stub-sourced row is
  labelled `-stub` under a warning banner
- Dissents section (full reasoning from roles that disagreed)
- Recommendation from the integrator

Dispatch is live HAL per seat (`ULTRAJUDGE_USE_HAL=1`, the default). With HAL
wholly unreachable the run refuses (exit 2) rather than emitting canned
verdicts; `ULTRAJUDGE_USE_HAL=0` opts into an explicit, labelled stub run.

## Options

| Flag | Description |
|---|---|
| `--roles` | Comma-separated role list (default: devil,pragmatist,futurist,integrator,historian) |
| `--models` | Comma-separated model list (default: opus,sonnet,grok-3,llama-3.3-70b) |
| `--weights` | JSON weight map to amplify specific roles e.g. `'{"devil": 2.0}'` |

## Full documentation

See `skills/ultrajudge/SKILL.md` for the complete protocol, weighted-verdict
formula, HAL integration details, and falsification conditions.
