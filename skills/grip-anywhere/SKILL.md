---
name: grip-anywhere
description: Point GRIP at any GitHub repository — analyse repo health, surface P0/P1 issues, generate an RSI improvement plan WITHOUT touching upstream. Stealth-by-default. Pairs with /sneak, /stealth, /140-85 --mission, /cross-repo-topology, /absorb-graph.
status: Active
effort: medium
version: 0.2.0
triggers:
  - "/grip-anywhere"
  - "analyse this repo"
  - "RSI report on github.com/owner/repo"
  - "run grip on someone else's repo"
integrates:
  - lib.grip_anywhere.runner
  - lib.grip_anywhere.analyzer
see_also:
  - sneak
  - stealth
  - 140-85
  - cross-repo-topology
  - absorb-graph
  - deploying-grip-to-projects
---

# /grip-anywhere — RSI for any GitHub repository

`grip-anywhere` points GRIP's `converge()` engine at any GitHub repository
and produces an improvement plan. **Stealth by default** — it never opens
issues, never comments, never posts PRs, never stars. Read-only analysis,
local report, operator decides what (if anything) to do with it.

## Plain English

Most repository-quality tools tell you a number and stop. This one tells
you what to fix next, in priority order, with reasoning attached. If the
reasoning is wrong, you can argue with it.

## CLI

```bash
# Default — stealth, dry-run, prints summary
python3 -m lib.grip_anywhere owner/repo

# Full markdown report to stdout
python3 -m lib.grip_anywhere owner/repo --report

# Reserved for follow-up release (currently rejected by argparse)
python3 -m lib.grip_anywhere owner/repo --mirror-clone

# Non-stealth: requires explicit upstream-maintainer consent token
python3 -m lib.grip_anywhere owner/repo --non-stealth --owner-consent TOKEN
```

## Why stealth defaults exist

OSS maintainers report "AI bot fatigue" — unsolicited PRs, AI-generated
issues, comment floods. A tool that points GRIP at a stranger's repo and
opens 12 PRs against it is the failure mode the tool itself created. The
fix is structural: never write upstream by default. The `--owner-consent`
token (manual, per-repo, scoped) is the only way to flip that switch.

## Skill weaving

`/grip-anywhere` composes with five other skills. They cover orthogonal
parts of the stealth-RSI pipeline; reach for each when its specific
concern dominates.

| Skill | What it adds |
|-------|--------------|
| `/sneak` | Spawns parallel research/drafting agents with zero-footprint output. Use when a deep analysis must not surface anywhere upstream. |
| `/stealth` | Zero-footprint operational mode for delicate fixes — all work via isolated agents. Use when even the act of having opinions about the repo should be invisible. |
| `/140-85 --mission` | The "secret-mission" delivery flavour: improvements arrive on V>>'s phone via WhatsApp, not on the repo. Use when V>> wants the analysis but doesn't want the trace. |
| `/cross-repo-topology` | Cross-repo dependency mapping. Use to scan an entire org's repo set in one pass before picking which to analyse. |
| `/absorb-graph` | Knowledge absorption engine. Use to ingest a target repo's README + docs + recent issues before running `--report`, so the report can cite the project's own framing. |

## Falsification

This skill is wrong if any of the following hold after three months:

- Maintainers report unsolicited bot activity from grip-anywhere → the
  stealth defaults leaked.
- Reports cite project framing incorrectly → the `/absorb-graph` integration
  is reading the wrong source files.
- The `--owner-consent` token is bypassable → security regression.

## Internal references

- `lib/grip_anywhere/runner.py` — orchestrator, CLI entry point
- `lib/grip_anywhere/analyzer.py` — repo health analysis
- `lib/mirror/__init__.py` — deprecation shim for the legacy import path

The product was renamed from `lib.mirror` (PR #2496) to avoid collision
with another GRIP MIRROR. The deprecation shim warns and re-exports
until 2026-08-01.
