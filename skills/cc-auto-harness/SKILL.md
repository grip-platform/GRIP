---
name: cc-auto-harness
description: Meta-RSI skill that detects new Claude Code features and converges them into GRIP capabilities. Use when invoking /cc-auto-harness, after a Claude Code upgrade, or to check which substrate features GRIP has not yet absorbed.
---

# CC Auto-Harness — Substrate Feature Convergence

Meta-RSI skill that detects new Claude Code features and auto-converges them
into GRIP capabilities. The substrate improves; the layer above improves with it.

## Usage

`/cc-auto-harness` is a procedure the session runs (Steps 1-4 below). The commands it
uses are these, and only these, exist:

```bash
bin/grip-py lib/cc_auto_harness.py refresh        # fetch new releases from GitHub; exit 1 with the cause on failure
bin/grip-py lib/cc_feature_scanner.py unharnessed # undecided features scoring >= 20 (HIGH_VALUE_CUTOFF)
bin/grip-py lib/cc_feature_scanner.py report      # harnessed / deferred / undecided, with totals
bin/grip-py lib/cc_feature_scanner.py scan        # every feature on disk with its score (no network)
bin/grip-py lib/cc_feature_scanner.py mark  <version> <title> <how>     # record a feature as harnessed
bin/grip-py lib/cc_feature_scanner.py defer <version> <title> <reason>  # record a decision NOT to harness it
bin/grip-py lib/cc_feature_scanner.py reconcile [--apply]               # re-point ledger keys that match no feature
bin/grip-py lib/cc_auto_harness.py dashboard      # tiers, top actionable features, coverage
```

There is no `harness <feature>` or `auto` command: harnessing one feature (Step 3) and
looping over all of them (Step 4) are judgement work the session does, recorded with
`mark` or `defer` when each feature is decided.

## Architecture

```
converge(CC_AUTO_HARNESS, grip_stays_ahead, fractal_improve, max_depth=6)
  |
  +-- OUTER LOOP: Scan CC releases for unharnessed features
  |     criterion: "all high-value features (score >= 20) harnessed"
  |     improve: escalate from detection -> scoring -> integration
  |
  +-- INNER LOOPS (per feature):
        converge(HARNESS_{feature}, integrated_and_tested, implement, max_depth=4)
          +-- Depth 0: Assess — what does this CC feature do?
          +-- Depth 1: Design — how should GRIP leverage it?
          +-- Depth 2: Implement — build the integration
          +-- Depth 3: Verify — test and mark as harnessed
```

## Implementation

### Step 1: Refresh Release Data

```bash
bin/grip-py lib/cc_auto_harness.py refresh
```

Fetches every Claude Code release via `gh api repos/anthropics/claude-code/releases
--paginate`, parses the release-note bullets into features, and writes one file per
release **not already on disk** to `~/.claude/data/cc-releases/v{version}.json`.
Releases already present, live or in `archive/`, are left alone, so the session pruner's
archive is never undone.

It fails loudly. A missing `gh`, a non-zero `gh` exit (the message names the proxy in use,
because the old HAL wire on port 4011 answers GitHub with `Forbidden`), a timeout, bad JSON,
an empty feed, or a feed whose newest release is older than one on disk all print
`refresh failed: <cause>` to stderr and exit 1. A feed that is simply up to date is a
success ("wrote 0 new (up to date)"). `gh` is found on `PATH`, then at
`/opt/homebrew/bin/gh` or `/usr/local/bin/gh`, because launchd's default `PATH` has no
Homebrew directory: until 2026-10-09 that made the heartbeat's refresh print "Refreshed 0
releases" and exit 0 without ever reaching GitHub.

The fetch lives in `cc_auto_harness.py::refresh_releases`, **not** in
`cc_feature_scanner.py`. The scanner never touches the network: its `scan` command only
reads and scores the JSON already on disk, so running it alone scores whatever corpus is
there. Always refresh first, then scan.

The corpus is local runtime data: `/data/` is gitignored, and only a subset of release
files was ever force-tracked. The ledger, `data/cc-releases/harness-state.json`, is
tracked. Releases the session pruner moved into `cc-releases/archive/` are still scored;
`_release_files()` reads both directories and prefers the live copy.

### Step 2: Score and Prioritise

```bash
bin/grip-py lib/cc_feature_scanner.py unharnessed
```

Scoring heuristic, exactly as `lib/cc_feature_scanner.py` computes it. A feature takes the
score of the highest keyword group it matches; a keyword matches only where a word starts
(so "build" does not count as "ui", and "prevent" does not count as "event"), and stems such
as "orchestrat" match as prefixes.

| Keyword group | Score | Keywords |
|---------------|-------|----------|
| Hook/lifecycle | 30 | hook, lifecycle, pretooluse, posttooluse, prehook, posthook, event, callback, interceptor |
| Agent/subagent | 25 | agent, subagent, task, orchestrat, spawn, worker, isolation, worktree |
| Memory/persistence | 25 | memory, persist, session, state, serializ, resurrect, continuity, checkpoint |
| MCP/tool | 20 | mcp, tool, server, protocol, provider, transport |
| Context/model | 15 | context, model, token, window, extend, compact, 1m, 200k |
| UI/display | 10 | ui, display, render, markdown, terminal, output, format |

Then, in order: a bug fix (category `fix`, or a title starting "Fixed ") scores a flat 5 and
stops; a title starting "Added " gets +5 (capped at 35); one starting "Changed " gets -5
(floor 5); a `breaking` category adds +5; the last validation result adds +10 (pass) or
-15 (fail); a feature naming a lib module with no tests adds +10. Harnessed and deferred
features score 0.

**High value means 20 or more** (`HIGH_VALUE_CUTOFF`), in the scanner, the runner and here.

### Step 3: Converge on Each Feature

For each undecided feature scoring 20 or more, run an inner convergence:

1. **Assess**: Read the CC release note. What capability does it expose?
2. **Design**: How can GRIP use this? New hook? New skill? Enhanced existing?
3. **Implement**: Build the integration (skill, hook, config, or lib code)
4. **Verify**: Test it works, then record the decision:
   `mark <version> <title> <how>` when it is harnessed, or
   `defer <version> <title> <reason>` when GRIP decides not to harness it. A deferral needs a
   reason, keeps the feature off the undecided list, and shows in `report` with that reason.
   Ledger entries written before `defer` existed carried the decision as a `how` beginning
   "SKIP:"; they are read as deferred.

### Step 4: The Whole Loop (a session procedure, no CLI)

The criterion is: every feature scoring 20 or more is harnessed, or deferred with a reason.
Each iteration:
1. `refresh` for new releases (stop and fix the cause if it exits 1)
2. `unharnessed` to list what is still undecided
3. Pick the highest-scoring undecided feature
4. Run Step 3 on it
5. Record it with `mark` or `defer`, and loop

## Key Principle: Stay Ahead

The criterion is NOT "use all CC features". It is:

> GRIP must offer capabilities that CC alone does not, by leveraging CC's
> own features as building blocks for higher-order automation.

When Anthropic ships a feature that replicates something GRIP already does
(e.g. auto-memory mirrors GRIP's KONO), GRIP must:
1. Adopt the native feature as infrastructure
2. Build something MORE on top of it
3. The delta between CC and GRIP must always grow, never shrink

## Data Locations

| Item | Path |
|------|------|
| Release JSONs | `~/.claude/data/cc-releases/v{version}.json` |
| Harness state | `~/.claude/data/cc-releases/harness-state.json` |
| Scanner lib | `~/.claude/lib/cc_feature_scanner.py` |
| This skill | `~/.claude/skills/cc-auto-harness/SKILL.md` |

## Integration Points

- **Heartbeat**: runs `cc_auto_harness.py refresh` as its `cc:refresh` step
  (`scripts/grip-heartbeat.py`); a failed fetch is a failed step whose `error` field carries
  the cause
- **RSI loops**: High-scoring unharnessed features feed RSI sprint backlogs
- **Genome**: Harnessing success rate is a fitness dimension
- **/release-notes**: Existing command feeds raw data to this system
- **/insights**: Detected patterns from harnessing inform future scoring

## GRIP-First Retrieval

Before harnessing any feature, check:
1. Does GRIP already have this capability? (KONO recall)
2. Is there an existing skill that could be extended? (skill search)
3. Would a new skill or a hook modification be more appropriate?

## When NOT to Auto-Harness

- Feature is CC-internal (rendering, terminal handling) with no GRIP surface
- Feature duplicates existing GRIP capability with no improvement delta
- Feature is experimental/unstable (check for "preview" or "experimental" tags)
- Feature requires breaking changes to GRIP's stable interfaces
