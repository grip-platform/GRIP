---
name: sneak
description: >
  Silent autonomous research and drafting mode. Spawns parallel agents that
  write ONLY to ~/.claude/drafts/<topic>/, push milestone alerts to the
  operator's phone, and produce zero public trace. Use when the operator is
  AFK or going to sleep and wants progress without commits, pushes, or
  public notifications.
status: Active
effort: high
version: 1.0.0
created: 2026-04-17
triggers:
  - /sneak
  - "run while I sleep"
  - "work quietly"
  - "draft only, no commits"
integrates:
  - lib/sneak_orchestrator.py
  - lib/grip_notify.py
  - lib/gates/yolo_mode.py
  - lib/hypothesis_engine.py
see_also:
  - rsi-optimal
  - ultrado
  - saving-state
---

# /sneak -- Silent Parallel Draft Mode

Autonomous research and drafting that leaves no public trace.
The only surface that knows work happened is the operator's phone.

## Overview

/sneak is the stealth counterpart to /rsi-optimal and /ultrado.
Where /rsi-optimal ships publicly (commits, pushes, PRs) and /ultrado
converges recursively with full ceremony, /sneak stays entirely local:
agents write drafts to disk, milestones ping the phone, nothing touches
a remote. The operator reviews the drafts in the morning and decides
what to promote.

## When to Use

- Operator going to sleep or AFK for >=2 hours
- Research, analysis, or drafting work where quality matters more than speed
- Tasks where a premature commit would be worse than no commit
- Exploring multiple approaches in parallel before committing to one

## When NOT to Use

| Situation | Use instead |
|-----------|-------------|
| Work must ship tonight | /rsi-optimal |
| Full recursive convergence required | /ultrado |
| Single focused task, operator present | /converge |
| Real-time external data needed | Manual session |

Never use /sneak when the outcome MUST go public before the operator
can review it. Drafts are drafts — they require human promotion.

---

## Protocol

### Phase 0.0: Operator Consent Check

/sneak only runs when explicitly invoked. It never self-triggers.
On invocation, confirm the goal and TTL with the operator:

```
Goal: <parsed goal>
Tracks: <2-5 proposed parallel tracks>
TTL: 6h (override with /sneak 4h "..." or /sneak --ttl 8h "...")
Drafts will land in: ~/.claude/drafts/<topic>/
Phone alerts: on
Public trace: NONE (no commits, pushes, PRs, Slack, Discord)

Proceed? (yes / adjust tracks / cancel)
```

If the operator does not explicitly confirm, HALT. /sneak does not assume consent.

### Phase 0.1: Yolo Activation

After consent, activate yolo mode so agents run without halting gates.
Quality gates (DRY/KISS/BIG-O/CC, correct-by-construction) keep firing.

```python
# In SneakSession.activate():
from lib.gates.yolo_mode import activate_yolo
activate_yolo('/sneak', ttl_seconds=ttl_hours * 3600, reason=goal)
```

Default TTL: 6 hours. Operator-overridable via `--ttl N` or `Nh` prefix.

Deactivate on completion or abort (Phase 5). If the session crashes,
the TTL auto-expires at next gate check — no stale bypass.

### Phase 1: Parse Goal, Classify Tracks

Decompose the goal into 2-5 non-overlapping parallel tracks.
Each track must:
- Have a single clear deliverable (a file, a section, an analysis)
- Be expressible as a falsifiable hypothesis
- Write exclusively to `~/.claude/drafts/<topic>/`

Track naming: `drafts/<slug>/` where slug is 2-3 words, lowercase-hyphens.

Example decomposition for "Research GRIP commercialisation options":
```
Track A: drafts/grip-pricing-models/     -- pricing structure analysis
Track B: drafts/grip-target-personas/    -- ICP and buyer journey
Track C: drafts/grip-competitive-moats/  -- differentiation analysis
```

Tracks must not overlap. If two tracks would read the same external
source, assign that source to one track and have the other read its
output file.

### Phase 2: Spawn Parallel Agents

Spawn one Agent per track using the Agent tool with `run_in_background=True`.
Each agent receives:

```
You are a SNEAK agent for track: <track_name>
Goal: <track_deliverable>
Output directory: ~/.claude/drafts/<topic>/
Hypothesis: <falsifiable claim for this track>
Constraints (MANDATORY):
  - Write ONLY to ~/.claude/drafts/<topic>/
  - NO git operations (no add, commit, push, stash)
  - NO Slack/Discord messages
  - NO PRs or GitHub operations
  - NO deploys or service restarts
  - NO recursive /sneak invocations
  - When touching code, use a worktree (git-crypt repo: grip-worktree.sh)
  - Write a DONE marker file when complete: drafts/<topic>/<track>.done
```

Agent count: match track count (2-5). Do not spawn more agents than tracks.

### Phase 3: Heartbeat via ScheduleWakeup

Schedule a wakeup every 25 minutes to check agent progress and send
a heartbeat notification if any track has crossed a milestone.

The SneakSession.heartbeat_due() method returns True when 25+ minutes
have elapsed since the last heartbeat. On each wakeup:
1. Check for `.done` marker files
2. Call `session.track_done()` for any newly completed tracks
3. Send a heartbeat notification if any track completed since last check
4. If all tracks done, move to Phase 5

Default heartbeat interval: 25 minutes. Adjustable via `--heartbeat N`.

### Phase 4: Per-Milestone Phone Notification

On each milestone (track completion, unexpected external response,
context gate approaching), push to the operator's phone via
`SneakSession.milestone()`:

```python
from lib.sneak_orchestrator import SneakSession, Milestone

session.milestone(Milestone(
    title="[SNEAK] Track A done",
    body="Pricing model draft: ~/.claude/drafts/grip-pricing-models/analysis.md",
    subtitle="3 of 4 tracks complete",
))
```

Under the hood this calls `default_notifier().send(notif)` which fans
out to iMessage + macOS Notification Center + Slack per the operator's
`docs/restricted/notify-targets.json` config.

Priority:
- Track completion: normal
- All tracks done: high
- Error / context gate: high

Do NOT send notifications for every file write — only on track
completion or significant milestones. Phone fatigue defeats the purpose.

### Phase 5: Completion Summary + Deactivate Yolo

When all tracks are done (all `.done` markers present):

1. Collect each track's output files into a summary
2. Call `session.complete(summary)` which:
   - Pushes a final phone notification with the summary
   - Deactivates yolo mode
   - Writes a session record to `drafts/.sneak-sessions.jsonl`
3. Output "What's Up Next" for the operator's morning review:

```
Just completed: /sneak "<goal>" — N tracks, Xh elapsed
Drafts ready for review:
  - ~/.claude/drafts/<topic-A>/  (<N files>)
  - ~/.claude/drafts/<topic-B>/  (<N files>)
Next action: Review drafts, promote what's worth keeping via /rsi-optimal or manual commit
```

---

## Anti-Patterns

| Anti-Pattern | Why | Consequence |
|-------------|-----|-------------|
| `git commit` inside a sneak agent | Public trace | Violates core contract |
| `gh pr create` inside a sneak agent | Public trace | Violates core contract |
| Posting to Slack/Discord | Public notification | Defeats operator-only surface |
| Recursive /sneak call | Loop explosion | Hard-blocked by agent prompt |
| AskUserQuestion mid-sneak | Breaks async | Phone-notify instead, decide in morning |
| Spawning >5 agents | Context thrash | Cap at 5 tracks maximum |
| Sharing draft paths across tracks | Coupling | Each track owns its own subdirectory |
| /sneak with no goal | Undefined | HALT and ask before proceeding |

---

## Hypothesis Registration

Every /sneak invocation registers a falsifiable hypothesis before spawning agents:

```bash
$HOME/.claude/venv/bin/python $HOME/.claude/lib/hypothesis_engine.py register \
  --claim "/sneak for '<goal>' produces drafts accepted by operator at >=80% rate" \
  --metric "files promoted / files produced" \
  --prediction ">=0.8 acceptance rate" \
  --deadline "<invocation_date + 7 days>"
```

Per-track hypotheses are registered by each agent independently.

---

## Falsification

/sneak's value claim: "Unsupervised parallel drafting produces output the
operator is willing to promote at >=80% acceptance rate."

Falsified by:
- Operator discards >50% of drafts across 5+ invocations
- Parallel agents produce conflicting outputs that require more work to
  reconcile than starting fresh would have taken
- Phone notification volume exceeds 6 per session (fatigue threshold)
- A sneak agent triggers a public side-effect despite prompt constraints
- Context thrash from parallel agents causes crashes before completion

If any falsification condition is observed, deactivate /sneak and escalate
findings via the hypothesis engine (`mark_falsified` + KONO memory).

---

## Relationship to Other Commands

```
/sneak
|-- Phase 0.0: Operator consent (MANDATORY — never auto-run)
|-- Phase 0.1: Yolo activation (6h TTL default)
|-- Phase 1: Goal decomposition (2-5 tracks)
|-- Phase 2: Parallel agents (one per track, run_in_background=True)
|-- Phase 3: Heartbeat (ScheduleWakeup, 25-min interval)
|-- Phase 4: Phone milestones (grip_notify, operator-only surface)
+-- Phase 5: Completion summary + yolo deactivation

/rsi-optimal: ships publicly, Fibonacci waves, hypothesis-driven, operator present
/ultrado:     recursive convergence, full ceremony, operator present
/sneak:       drafts only, parallel, async, phone notifications, operator AFK
```

`/sneak` running inside `/ultrado` or `/rsi-optimal` is an anti-pattern.
Each autonomous command is a fixed point — nesting them causes loop explosion.

---

## Token Budget

| Component | Approximate tokens |
|-----------|-------------------|
| SKILL.md load | ~1,200 |
| Per-agent spawn overhead | ~300 |
| Per-notification call | ~50 |
| Session complete summary | ~200 |
| Hypothesis registration | ~100 |
| Total per invocation (3 tracks) | ~2,650 |
