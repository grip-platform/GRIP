---
name: aurora
description: Canonical team work-update command — /aurora. Honest digest (PRs, commits, tests, hypotheses) rendered as a delightful ANSI aurora borealis in the terminal, plus plain-language tldr/summary, optional eli5 narrative, Slack Block Kit, and changelog markdown. Absorbs the catch-up/update reporting family. Data is fail-soft and verify-canonical per merged PR.
---

# /aurora — canonical team work update

One command that gives the team an honest, delightful update of recent work
over a configurable window (default 24h).

```
/aurora [duration] [--target terminal|slack|markdown|all] [--eli5] [--tldr] [--post CHANNEL]
```

## What it produces

### Terminal (default)

An **aurora borealis whose form IS the data** — a quiet night is a low green
shimmer; a big night is tall vivid multi-colour curtains. Beneath it:

```
── 3 PRs in 24h — 2 feat, 1 fix ──

Merged in the last 24h:

[feat] (2)
  • feat(aurora): add canonical report orchestrator (#196)
  • feat(relay): operator-session routing (#204)

[fix] (1)
  • fix(relay): dispatcher import guard (#200)

Commits on origin/main: 17 | Test lines: 4 | Hypotheses: 2

Open / in-flight (1):
  • feat(wip): work in progress (#210)
```

### Markdown (`--target markdown`)

Changelog-style block — `## Work — last Nh`, then `### feat` / `### fix` etc.
with `- title (#number)` lines verbatim (precision invariant: numbers never
rounded), plus an open-items section and a stats footer. Ready to paste into
a PR body or team doc.

### Slack (`--target slack`)

Block Kit blocks (header + per-category sections + stats context). Rendered to
stdout as JSON by default. Requires `--post CHANNEL` to actually send.

### All (`--target all`)

All three renderings joined by a separator — useful for review or piping.

## Options

| Flag | Meaning |
|------|---------|
| `duration` | `24h`, `12h`, or bare integer hours (default: `24`) |
| `--target` | `terminal` (default) · `slack` · `markdown` · `all` |
| `--eli5` | Plain-English HAL narrative appended (fail-soft to nothing) |
| `--tldr` | Print only the one-line headline; exit |
| `--post CHANNEL` | OPT-IN: post the Slack render to `CHANNEL` via `slack_transport` (workspace: `codetonight`). **Never auto-posts** — channel-scope rule |

## CLI

```bash
# Default: terminal aurora + summary for the last 24h
python3 lib/aurora_report.py

# Last 12 hours
python3 lib/aurora_report.py 12h

# Changelog markdown
python3 lib/aurora_report.py --target markdown

# All three renders
python3 lib/aurora_report.py --target all

# tldr only
python3 lib/aurora_report.py --tldr

# With plain-English narrative
python3 lib/aurora_report.py --eli5

# Render Slack blocks and post (explicit opt-in)
python3 lib/aurora_report.py --target slack --post C0EXAMPLE01
```

## Architecture

```
lib/aurora_report.py   — thin orchestrator + renderers (presentation layer)
lib/work_digest.py     — gather shim: PRs · commits · tests · hypotheses
lib/work_summary.py    — summarise shim: tldr · plain · eli5 · verify
lib/aurora_card.py     — pure ANSI delight renderer (unchanged; imported)
lib/comms_update.py    — Block Kit helpers (reused for Slack render)
lib/slack_transport.py — Slack post shim (used only on --post)
```

- `gather_digest` is fail-soft per source (each git/gh call independently).
- `summarise` verify-canonical checks each merged PR's state via `gh pr view`.
- `eli5` fires only on `--eli5`; always fail-soft to `None` (no LLM in the
  deterministic floor).
- Evidence tokens (PR titles, numbers) are VERBATIM throughout — never rounded.

## Absorbed playbook family

These commands are covered by `/aurora`; they coexist as independent skills
but `/aurora` is the canonical umbrella for team work-update reporting:

| Absorbed source | What it contributed |
|-----------------|---------------------|
| `/catch-me-up` | verify-canonical per claim + achievement cataloguing |
| `/whoa-update-pls` | Live / Open / Backlog triage; close-wizard coexists |
| `/slack-update` / `/slack-feature-updates` | PR categorisation + Block Kit renderer |
| `/generating-changelogs` | Conventional-commit → changelog markdown |
| `/morning` | Digest framing (calendar / personal side coexists) |

## Tests

```bash
bash bin/grip-test tests/test_aurora_report.py -q
# 31 passed
```

Tests are Goodhart-safe: verify renders contain verbatim PR numbers (e.g.
`#196` stays `#196`), `--post` is asserted NOT called on a bare `report()`
call and IS called via `_do_post`, and fail-soft seams return safe fallbacks
on simulated network failures.
