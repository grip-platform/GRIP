---
name: bg-dispatch
description: Launches background work and background experts while the main session stays free. Each launch makes two choices -- what the expert knows (a fresh brief, or the inherited conversation, plus optional notes the operator types) and where the thinking runs (Claude agents, or outside models seated as experts in a Council or Roundtable). Use when an autonomous loop must run without bloating the main session, or when the operator wants experts working in the background.
status: Active
version: 2.0.0
created: 2026-04-27
updated: 2026-11-01
argument-hint: "<task>, with --fresh, --inherit or --seats council|roundtable, then optional -- notes"
triggers:
  - /bg-dispatch
  - /broly-auto
  - /ultrado /go
  - /rsi-optimal background mode
  - autonomous-rsi-sprint
  - any RSI loop where main session is the orchestrator
chains_to: autonomous-orchestration
see_also:
  - autonomous-orchestration
  - dispatch
  - dfi-fib
  - feedback_rsi_background_pipelines.md
---

# BG-DISPATCH -- Background Dispatch Protocol

Start work in the background and keep the main session free. `rules/dispatch-economics.md` decides whether
to dispatch at all. This skill decides how: what each expert knows, and where its thinking runs. The evidence
for this version is in `docs/measurements/2026-11-01-skills-vs-subagents.md`.

## Usage

```text
/bg-dispatch <task>                     Pick for me, and say the pick in one line.
/bg-dispatch --fresh <task>             The expert knows only the brief.
/bg-dispatch --inherit <task>           The expert knows the whole conversation.
/bg-dispatch --seats council <task>     Outside models answer; another vendor judges.
/bg-dispatch --seats roundtable <task>  An outside advisor frames the question first.
/bg-dispatch ... --expert <seat>=<role> Give one seat an expert role. Repeatable.
/bg-dispatch ... -- <notes>             Add your own context. Optional.
/bg-dispatch                            Ask me what to run and what it should know.
```

Notes after `--` go into the brief word for word, under the heading `Operator notes`. Leave them out and
nothing asks for them. A bare call asks one `AskUserQuestion` with the choices below. An autonomous loop
always passes a task, because nobody is there to answer.

## Choice 1 -- What the expert knows

| Choice | The expert starts with | Its result | Start it with |
|---|---|---|---|
| **Fresh** | Its own definition and your brief | Comes back here | `Agent` with a named `subagent_type` |
| **Inherit** | The whole conversation, with the same system prompt, tools and model | Comes back here | `Agent` with `subagent_type: "fork"`; the operator can type `/subtask <task>` |
| **Copy** | The whole conversation, in a separate session | Stays in that session | The operator starts it by typing `/fork [prompt]`; this skill cannot. The vendor's docs say a copy made from a linked worktree starts in the main working tree, here the shared tree production reads, so its prompt should name `lib/grip-worktree.sh` |
| **Seats** | Your brief, though a Codex seat can also read local files | A sealed record | Shape D below; outside models cannot inherit |

With no flag, the first line that fits decides:

1. The job checks, reviews or judges work from this session and needs tools: fresh. Never inherit: a fork shares
   the author's reasoning, and the verifier is never the author (`rules/exogenous-anchor-law.md`).
2. The job is thinking only, including a review of text the brief can carry: seats.
3. The task says "this" or "what we decided", or its brief would run long: inherit.
4. Anything else: fresh.

Three facts change the choice:

- **Cost.** A fork reads the whole window again on every request, so a long job from a large session can cost
  more than a fresh agent. Weigh that against what a fresh agent must re-read to catch up.
- **Model.** A fork runs on the session's model and effort, and ignores a `model` override. A fresh agent takes its
  model from its definition.
- **Files.** A fork edits the session's checkout. Native `isolation: "worktree"` does not work on this git-crypt
  repository, so tell an editing fork to use `lib/grip-worktree.sh`.

`context: fork` in a skill's frontmatter is not a conversation fork. That skill starts fresh and sees no history.

## Choice 2 -- Where the thinking runs

- **Shape A (fan-out)** -- N independent tasks, no shared mutable state, latency matters more than cost. N agents.
- **Shape B (chain)** -- coupled tasks, or the default for work that clears the gate. One coordinator agent.
- **Shape C (inline)** -- cheaper here than an agent's start-up cost. No agent. Long waits go to background Bash.
- **Shape D (seats)** -- thinking only. Outside models, no Claude agent.

```text
For each wave's tasks:
  Can this session do it for less than an agent's start-up cost?
    YES -> Shape C
  Is the job thinking only: a council, review, deliberation or bulk judgement?
    YES -> Shape D
  Do the tasks share mutable state?
    YES -> Shape B
  More than 1 task, independent, and latency matters more than cost?
    YES -> Shape A
    NO  -> Shape B
```

## The Four Rules

**Rule 1 -- Keep the main session free, and dispatch only what earns it.**
A tool call that would hold the main session for more than ~30 seconds leaves the foreground: a process that only
needs waiting for goes to background Bash, and work that needs reasoning goes to an expert. Anything below the gate
in `rules/dispatch-economics.md` stays here, where the start-up cost is already paid.

**Rule 2 -- Choose the shape per wave.**
Mixed shapes across waves are fine. Shape A with Shape B in one wave is not: parallel writes from the Shape A agents
race the coordinator's substrate sequence. Shape A with Shape C in one wave is allowed when the inline work touches
different files (`/sprint` W2, PR #2387, ran two agents and one direct edit with zero race). Shape D writes only its
sealed record and an IDR leaf, so it can run beside any shape.

**Rule 3 -- One coordinator owns shared-state chains.**
Tasks that share a mutable substrate (`learning/insights.jsonl`, genome state, KONO embeddings) run in a single
Shape B coordinator. Parallel dispatch on shared state races on writes (W9-bg, 2026-04-27).

**Rule 4 -- Never force-push from a dispatched agent.**
Yolo mode relaxes normal gates but never force-push. A force-push needs the operator's explicit approval in the
main session.

## Launch, watch, merge

1. **Write the brief.** A fresh expert knows only what the brief says: one deliverable, the stop condition and the
   evidence (`rules/agent-dispatch-protocol.md`). `lib/bg_dispatch.py build --goal ... --ref <path>` inlines files.
   A fork needs only the deliverable and the stop condition. A brief for a check carries the artefact and the
   acceptance test, never the author's reasoning.
2. **Start it.** `Agent` runs in the background by default, so never pass `run_in_background: true`. In a terminal,
   the desktop app or VS Code, Claude Code does not offer that parameter at all. `Bash` does take it.
3. **Watch it.** While anything you started runs, run `bin/grip-fleet-watch` at least every ten minutes and stop what
   you started if it says ACT (`rules/workflows-never-stuck-never-burning.md`). End the turn only while genuinely
   waiting (`rules/rsi-loop-no-yield.md`).
4. **Merge.** A report is a claim. Test what it says against the real files before acting on it
   (`rules/verify-canonical-not-claimed.md`).

## Shape D -- Outside models as experts

A seat is an outside model answering one brief from a background shell. On the default panel no Claude model runs, so
the thinking stays off the weekly limit (`rules/pool-a-offload-routing.md`, `rules/fan-out-spends-the-weekly-limit.md`). It is a shell
command, so it works in any harness. An HTTP seat gets only the brief, so the brief must carry the evidence for its own
premise (`rules/dispatch-economics.md`). A Codex seat (`astra`, `codex`, `openai`) can also read local files that
`may_send` never saw (GRIP#8920), so never seat one on a brief carrying third-party text.

Read `references/seats.md` in this skill's folder and run its script with the Bash tool's `run_in_background: true`.
`$OUT` ends with one seal. Read the sitting with `bin/grip-sovereign-council --show <sha>`, check it with `--verify <sha>`, and
test each surviving concern against the real text.

## Which agent, and what a stall costs

Use the leanest agent type that can do the work (`skills/dispatch/SKILL.md`). `Explore` searches.
`direct-implementation` edits, tests and commits. `broly` is the long-reasoning tier for work that edits as well as
reasons. A broly that needs a council runs Shape D itself instead of starting more Claude agents.

A stall timer stops an agent that stays silent too long inside one generation (`rules/agent-dispatch-protocol.md`,
Stall-Kill). So an agent commits and pushes after every logical change, and splits a long write-up into stages.

## The learning pipeline

`/insights -> /evo -> /learn` runs as two dispatches, never one chain: a Shape B chain for `/insights -> /learn`, and
`/evo` as its own Shape A unit. The reason and the evidence are in `skills/evo/SKILL.md`, under Dispatch Protocol.

## Examples

```text
/bg-dispatch --inherit draft unit tests for the parser changes so far
/bg-dispatch --fresh fix the flaky test in tests/test_x.py and open a PR -- Do not touch lib/y.py.
/bg-dispatch --seats council Is this retry policy safe? -- The retry code is in lib/retry.py.
/bg-dispatch --seats roundtable --expert deepseek=failure-modes --expert xai=premise-attack Which queue design fits?
```

## Falsification

This version is wrong if, over its next 20 launches: the flags go unused and the automatic pick is never overridden
(delete the flags); or Shape D verdicts are overturned by evidence more often than Claude-agent verdicts on the same
class of question (keep the Claude voice for that class); or a fork from a window larger than the fresh agent's own
prompt costs less than that fresh agent in 4 of 5 paired runs of a long job (remove the cost line above). Track: launches per choice, and weekly
points per launch.

Primary Author: V>>. Version 2.0.0 (2026-11-01) added the context choice, Shape D and the launch steps, and withdrew
the 2026-04-27 note that `broly` has a tighter watchdog. Git history holds the earlier text.

## Lessons Learned

- 2026-04-30: four read-only agents run in parallel cut a reconnaissance phase from an estimated 25 minutes to about 5,
  and each returned `file:line` citations. Shape A at its intended use.
