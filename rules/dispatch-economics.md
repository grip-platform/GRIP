# Dispatch Economics (PARAMOUNT)

Origin: 2026-05-15. Five `general-purpose` background agents were fanned out
to prune a stale session task list — each cost ~84k input tokens (~420k
total) to discover that `TaskUpdate` is not in the subagent tool surface.
The work was undelegatable session-state mutation, fanned out at maximum
depth with zero capability check. V>> RCA directive: codify the economic
gate so this cannot recur.

This rule is the **economic precondition** for any Agent/Task dispatch.
`rules/agent-dispatch-protocol.md` is the **structural** precondition
(atomic deliverable, anti-pause prompt). `rules/efficiency-rules.md`
Rule 21 is the **retrieval twin** (don't convene a council if the answer
already exists). This rule is its economic twin: don't convene N agents if
the useful work per agent is smaller than the ambient floor.

## The ambient floor (the core fact)

Every spawned subagent cold-boots an **ambient context** independent of task
size — but it is **NOT uniform across agent types** (corrected 2026-06-24,
verify-canonical from the official Claude Code docs + live subagent-transcript
inspection; the original "fixed ~80–85k for everyone" model was wrong):

- **`general-purpose` / custom agents** carry the heavy floor: `CLAUDE.md`
  (~8.7k) + the **`rules/*.md` memory stack (~73k — the DOMINANT component,
  loaded natively + unconditionally by the harness, no `@`-import) including
  `operational-context.md` (~10k)** + the skills-catalogue system-reminder
  (~16.5k, all 293 descriptions) + tool schemas ≈ **~80–98k** (consistent with
  the original ~80–85k measure: 2026-05-15, five agents, 81.5–86.3k each).
- **`Explore` / `Plan` agents already SKIP the entire ~73k rules/memory
  stack** by harness design — they are floor-lean (≈ catalogue + task), with
  no config knob to change that (and none needed).
- **Custom agents preload a MANDATED 4-skill frontmatter** (`agents/CLAUDE.md`
  fractal-gate: `design-principles` ~9.7k + `dependency-guardian` +
  `recall` + `concise-communication`) as FULL bodies — a fixed,
  non-prunable per-custom-agent cost. `skills:` declared beyond those 4 add
  more (e.g. a meta-agent's deliberation kit), but those are usually that
  agent's function, not waste.

Consequences:

- **Prefer a floor-lean agent type when the task allows it.** A read-only
  search via `Explore` skips the ~73k rules stack — far cheaper than the same
  search via `general-purpose`. Pick the leanest agent type that can do the job.
- **Fan-out multiplies the floor by N.** 5 `general-purpose` agents = ~400k+
  tokens *before* any useful work. Fan-out is only economical when each
  agent's useful work is large relative to the floor.
- **A chain loads the floor once.** One sequential coordinator agent
  amortises the floor across all its work.
- **The main thread has the floor already loaded** — marginal cost of
  doing work there is ~0. Delegating *to escape the main thread* is not
  free; it re-pays the floor.
- **Background ≠ free.** `run_in_background=true` hides *latency*, not
  *cost*. Token spend is identical foreground or background.

**The law: the most token-efficient agent is no agent — and when you must
dispatch, the leanest agent type that fits.**

The only large *reducible* lever on the heavy floor is **path-scoping the
non-safety `rules/*.md`** (keep a small safety core unconditional) — a
guardrail-class shared-config change that must be V>>+AGORA-ratified, never a
silent per-callsite edit. Trimming `maxSkillDescriptionChars` (default 1536) is
**NOT** a usable lever: measured 2026-06-24, all 253 GRIP skill descriptions are
already ≤749 chars (avg 209), so lowering the cap to 800 shaves **0 tokens** — the
cap only truncates descriptions *longer* than itself. Only a cap below the ~209
avg (e.g. 200) would shave the ~13k catalogue, and that silently drops trigger
keywords from the longer descriptions, breaking skill-matching
(`anthropics/claude-code#56710`). The mandated 4-skill frontmatter and the
harness-native rules load are NOT per-agent-prunable. (Floor-reduction
investigation: 2026-06-24; the earlier "~7k shave" claim was falsified by direct
measurement.)

## The pre-dispatch gate (run BEFORE any Agent/Task fan-out)

### Gate 0 — Cheap-test-first (premise verification)

A dispatch, council, or investigation premised on a **failure claim** ("tool
X corrupts output", "the build broke", "Y regressed") runs the cheapest
controlled reproduce-test FIRST — a <30s command with a known input and a
predictable expected output that makes the claimed failure appear on demand.
**No repro → no expensive dispatch.** Every token downstream of an unverified
premise elaborates a possible confabulation; suspect your OWN command/flags
before an external tool (origin: 2026-07-14 — an `rg -rn` typo, `-r` being
`--replace` not "recursive", silently rewrote matches, was mis-blamed on RTK,
and spawned a false two-session investigation whose council confirmed the
false premise; H798). Gate 0 is wrong if, over ~20 failure-premised
dispatches, the cheap test never once kills a false premise AND measurably
adds friction — then narrow it to tool-blame claims, don't drop it.

### Gate 1 — Capability (binary, non-negotiable)

Can a subagent physically do this? Subagents act on the **world**; they
cannot mutate the **spawning session's private state**.

| Crosses the boundary — delegate freely | Main-session-only — NEVER delegate |
|---|---|
| `Bash`, `Read`, `Write`, `Edit`, `Grep`, `Glob` | `TaskCreate/List/Get/Update` |
| `gh`, `git`, `WebFetch`, `WebSearch` | `AskUserQuestion`, `ExitPlanMode`, `EnterPlanMode` |
| `Agent` (recursive), `TaskStop` (kills *processes*) | `ScheduleWakeup`; the conversation/task list itself |

If the deliverable requires a main-session-only tool → **do not dispatch.**
Do it on the main thread or mechanically. No exceptions.

### Gate 2 — Economics (the ~3:1 rule)

Estimate `useful_work_tokens_per_agent` (the tool calls + reasoning the
agent must actually perform to produce its deliverable, excluding the
ambient floor).

- `useful_work_per_agent ≥ 3 × ambient_floor` → dispatch is economical.
- Below ~3:1 → **do it on the main thread** (floor already loaded) **or
  mechanically** (a script / deterministic loop — no LLM at all).

A 47-entry list edit is ~2k of useful work against an ~84k floor (1:42).
A multi-file refactor + tests + PR is ~150k+ of useful work (clears the
floor comfortably). The first must never be delegated; the second should.

### Gate 3 — Shape (chain vs fan-out)

Default to **chain** (one agent, floor paid once, sequential). Choose
**fan-out** only when BOTH: (a) the subtasks are genuinely independent AND
(b) wall-clock latency matters more than token cost AND (c) each agent's
useful work clears Gate 2 on its own. Fan-out for "it feels powerful" is
the anti-pattern this rule exists to stop.

### Gate 4 — Depth ladder (dfi-fib / Coordinator + Converge)

dfi-fib is a depth ladder. The **Coordinator** holds it; **Converge**
exits at the lowest depth that satisfies the criterion.

| Depth | Mechanism | Use when |
|---|---|---|
| 0 | Direct on main thread / mechanical script | Default. Always try first. |
| 1 | One sub-agent (chain) | Depth 0 criterion failed; world-work clears Gate 2 |
| 2 | Short sequential chain | One deliverable too large for a single agent envelope |
| 3+ | Fan-out / mesh / council | Genuinely parallel, latency-bound, each clears Gate 2 |

**Start at depth 0. Escalate one rung only when the converge criterion
fails at the current depth, with a documented reason.** Jumping straight
to depth 3 (as the originating incident did) is a Gate-4 violation.

### Harness caps, and why "depth 3" means two different things

Claude Code has its own dispatch ceilings, and since 2.1.219 one of them
shares a number with the ladder above. Read them as separate axes or you will
misjudge both.

**GRIP-depth** (the table above) is an *escalation tier* the Coordinator
holds — how much machinery you have allowed yourself for this deliverable.
**Claude Code spawn-depth** measures something else entirely: how many agents
deep a *spawn chain* runs before the harness refuses to nest further.

They diverge exactly where it matters. A fan-out launched from the main session
is **GRIP-depth 3** — the top rung of the ladder — but **spawn-depth 1**: the
main session spawned one layer of agents, and none of those agents spawned
anything. Both reading "3" is a coincidence of scale, not a correspondence. A
session that conflates them will believe it is near a harness ceiling when it
has not begun to approach one.

The current ceilings, and the environment variable that changes each:

| Ceiling | Default | Override |
|---|---|---|
| Nested spawn depth | 3 | `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` |
| Concurrently-running subagents | 20 | `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` |
| Subagent spawns per session | 200, reset by `/clear` | `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` |

Nesting is the one that has moved recently: 2.1.217 turned it off by default,
and 2.1.219 set the ceiling to 3. (GRIP's own harness ledger carries an entry
reading "subagents 5-deep" against 2.1.172. That release note says no such
thing — the entry describes GRIP's ultrawin/ultrado fleet ladder, not a harness
default. Treat any "5-deep" claim about the harness as unsourced.)

**None of the three is the binding constraint on GRIP, and none is set.**
Gate 3 above binds far tighter, on cost grounds. GRIP's widest *declared*
fan-out anywhere is four agents (`skills/ultrawin` FLEET; `skills/research-council`
spawns 2-3; `lib/inception_wake.py` defaults `fanout=3`) against a cap of 20.
The per-session ceiling sits a similar distance away: `skills/ultrado` caps its
wave loop at six Fibonacci waves, roughly 24 spawns at four agents a wave,
against 200. An override would change nothing today and would add one more
number to keep in sync.

Two limits on what these caps cover, both of which matter when reasoning about
a wide fan-out. They count **Claude Code subagents only** — agents spawned
through the Task/Agent tool. GRIP's genuinely wide fan-outs are the Pool-B
sovereign councils, and those do not use that tool at all:
`lib/hal/agent_spawn.py::AgentSpawner.spawn` dispatches through
`self._registry.chat(...)`, a HAL provider call, so no Claude Code cap counts
them. And these numbers are read from the Claude Code release notes (2.1.212,
2.1.217, 2.1.219), not observed firing: nobody has spawned a 21st concurrent
agent to watch a cap refuse. If a cap turns out to bind differently in
practice, trust the observation over this table.

There is a **third** depth number, which is the strongest reason to keep these
axes apart. `AgentSpawner.spawn` takes `max_depth: int = 21` and recurses on
its own count, independent of both the ladder above and the harness ceiling.
So "depth" in GRIP can mean an escalation tier (0-3+), a Claude Code spawn
chain (ceiling 3), or a HAL recursion budget (default 21). Three scales, one
word. Always say which one you mean. If a cap turns out to bind differently in practice, trust
the observation over this table.

## Falsification

This rule is wrong if, over four weeks of adoption:

- Measured `tokens / useful-deliverable` does **not** improve versus the
  pre-rule baseline (the gate adds friction without saving cost).
- The ~3:1 threshold blocks genuinely valuable parallel work more often
  than it prevents waste (threshold mis-tuned — raise or lower it, don't
  discard the rule).
- The ambient floor turns out to be **task-variable, not fixed** — then
  agent-type/tool-surface selection, not the work/floor ratio, is the
  real lever and Gate 2 must be rewritten around it.

Track via dispatch telemetry: input tokens per dispatch ÷ useful
deliverables produced, aggregated weekly, fed to `/evo`. Goodhart-safe:
"deliverable" is defined by outcome (PR merged, file shipped, question
answered), never by tool-call or agent count.

## Scope

Applies to every `Agent`/`Task` dispatch, every broly mesh, every AGORA
council, every `/ultra*` fan-out, every `/bg-dispatch`. Council patterns
are dispatch at scale — a 6-agent mesh on a question answerable by reading
one canonical file is this rule's violation at council scale (and Rule 21's
too). A council/review prompt MUST carry a ground-truth citation for its
PREMISE (command+output, `file:line`, or a verify-canonical probe), not only
its question — an exogenous verdict on an unverified premise launders the
corruption (the 2026-07-14 RTK council confirmed a false premise because it
was asked about the conclusion; see `rules/prove-it-claims-canonical.md`).
Outside scope: depth-0 main-thread work (this rule is what *keeps*
work at depth 0 when it belongs there).
