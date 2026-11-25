# Headless GRIP -- slices 1 and 2

`gg++ -p "<task>"` runs a task through GRIP with nobody watching, instead of
opening an interactive session. Slice 1 of the plan in
`~/.grip/drafts/ULTRAPROMPT-2026-10-01-headless-grip-hal-runtime.md` is the
smallest shippable slice, and the one the plan's own falsifier names: an
outward action must end in `awaiting-approval`, never run. Slice 2 adds the
gate-equivalence check (see "Slice 2 -- gate equivalence" below): a headless run
is refused whatever an interactive session's hard gates refuse.

This spec should have gone through AGORA before the code that implements it
(the plan's task 3). GitHub Actions on this repository is billing-blocked
at the time of writing, so the spec and the code ship in the same pull
request instead, and this is said plainly here rather than left implicit.

## What slice 1 is

A thin, auditable wrapper: `gg++ -p "<task>"` execs `bin/grip-run "<task>"`,
which runs `claude -p` (Claude Code's own non-interactive mode) inside a
disposable, isolated git worktree, under a capability lock and a budget,
and writes a `receipt.json` next to the run's log recording exactly what
happened -- never what was intended, never a guess.

Implementation: `lib/headless_run.py` (the logic), `bin/grip-run` (the CLI
shim), the `-p` passthrough in `bin/gg++`.

## Inputs

| Input | Where it comes from | Default |
|---|---|---|
| the task text | positional argument | required |
| `--origin` | who asked: `operator`, `relay`, or `cron` | `operator` |
| `--tools` | the capability allowlist | `Read,Grep,Glob` |
| `--max-turns` | the turn budget | 20 |
| `--timeout` | the wall-clock budget, seconds | 900 |
| `--model` | an explicit model id | none -- the account default |

`--model` is left unset by default, deliberately. `lib/model_pin.py`
documents that a headless `claude -p` child takes the account default and
never inherits the interactive session's model pin (V>> ruled on
2026-10-11); an explicit `--model` here is the operator's own choice for
this one run, never an inherited one.

## What a run actually does

1. The task text is checked against the outward-action barrier (below)
   before anything else runs.
2. The gate-equivalence check (slice 2, below) decides whether every hard GRIP
   gate would still refuse what it guards in this run. If one would not, the
   run ends as `denied` here, before any worktree or harness exists.
3. If neither check refused it, a disposable git-crypt-safe worktree is created
   from a freshly fetched `origin/main` (`lib/grip-worktree.sh add ...
   --base origin/main`), per `rules/worktree-must-track-main.md`. The fetch
   is best-effort and never blocks the run. The worktree lives under
   `$GRIP_WORKTREE_HOME` (default `~/.grip-worktrees`), outside the
   repository, because the helper refuses a path inside it. If it cannot be
   created, the run ends as `failed` and still writes its receipt.
4. `claude -p "<task>"` runs inside that worktree, with:
   - `--tools <allowlist>` -- the capability lock (never `--allowedTools`,
     which is pre-approval, not a restriction -- slice 0 measured the `-p`
     init line listing every tool and every MCP connector as available
     regardless of `--allowedTools`).
   - `--strict-mcp-config` -- with no `--mcp-config`, Claude Code starts
     with zero MCP servers, so a send-shaped connector (Gmail, Slack, ...)
     is unreachable regardless of the native-tool allowlist.
   - `--permission-mode dontAsk` -- never `--dangerously-skip-permissions`,
     never `--bare`. Slice 0 measured that a GRIP PreToolUse deny HOLDS
     under `dontAsk`, and that `--bare` cannot even authenticate on the
     subscription path (it skips the whole config-dir credential
     resolution, not just the hooks).
   - `--max-turns <N>` and a wall-clock `timeout_s` passed into
     `subprocess.run(..., timeout=...)` -- the budget.
   - `--output-format stream-json --verbose` -- a structured transcript,
     so the receipt reports measured facts (cost, model, tools used)
     rather than a guess.
5. The worktree is removed afterwards, and so is its `sess/headless-<run>`
   branch while that branch still points at the commit it was created on.
   That comparison does not depend on Git's branch-tracking settings, which
   `git branch -d` would. With the default read-only tools nothing in the
   worktree can have changed. A run given write tools loses its uncommitted
   edits at this step, but its commits survive, because a branch that moved is
   kept.
6. A signed IDR leaf is appended (`lib/precog/idr.py`) recording the
   decision -- the task text itself is hashed into the leaf, never
   embedded raw; the raw text stays local, in `receipt.json` only.
7. `receipt.json` is written next to the run's log.

GRIP's context is assembled exactly as an interactive launch's, because it
already is: hooks and `CLAUDE.md`/`rules/` resolution are driven by Claude
Code's own config-dir resolution, not by this tool. Slice 0 measured 27
`SessionStart:startup` + 27 `SessionStart:compact` hooks firing under
`claude -p` with no special wiring -- placing the subprocess's `cwd` inside
a proper GRIP worktree is the whole of what "context assembly" means here.
Nothing here re-implements hook wiring.

## The outward-action barrier

An outward action (send, post, publish, pay, submit, email, WhatsApp,
Slack, tweet, deploy, `git push`) must end in `outcome: awaiting-approval`,
with the blocked action named in the receipt, and the harness is never
invoked for that run at all.

The check is a deterministic keyword/regex floor over the task text,
applied before anything is launched -- never an LLM judgement
(`lib/headless_run.py::detect_outward_action`). A second pass runs over any
tool-use events a run did produce (`detect_outward_tool_use`): with the
default read-only allowlist this can never fire, since none of
Read/Grep/Glob can act outward, but it is defence in depth for a future,
wider allowlist where the text-only check could miss an outward-shaped
tool call the model made anyway.

**Known, named limitation.** "post" and "submit" are common words and will
over-block some benign tasks ("post-mortem the outage", "submit a review
comment"). Narrowing the list without weakening it to an LLM judgement is
review work for a later slice, not something this spec claims to have
solved.

## Slice 2 -- gate equivalence

An interactive GRIP session is guarded by hard gates: PreToolUse hooks that
refuse a dangerous tool call before it runs (`classification: "hard"` in
`config/hooks.registry.json`). A headless run must be refused the same things.
`lib/headless_gate.py` checks that before any worktree or harness exists. For
every hard PreToolUse policy the registry binds, it gives one verdict:

| Verdict | Meaning |
|---|---|
| `locked` | no tool this run can call matches the policy, so the operation it guards cannot happen |
| `hooked` | the harness runs GRIP's hooks, and the settings the child will load bind this policy's hook under the registry's own matcher |
| `uncovered` | the run could reach the operation and nothing would refuse it |

One `uncovered` policy refuses the run as `denied`, and the receipt's `error`
names the policies. With the default read-only tools on this repository's
wiring, one policy is hooked (the unified context gate, matcher `.*`) and the
other 24 are locked. Widened to Edit, Write and Bash, 12 are hooked and 13
locked.

Which harness can be admitted, and why:

| Harness | Enforcement | Basis |
|---|---|---|
| Claude Code | GRIP's PreToolUse hooks, under `--permission-mode dontAsk` | slice 0 measured a hook deny holding there; `--strict-mcp-config` makes the `--tools` allowlist the whole tool surface |
| HAL (`HALHarness`) | none needed | a bare provider call with no tool loop, so every policy is locked |
| Codex, OpenCode, Gemini, every other kind | none | GRIP's gates are wired to Claude Code's hook events only (`bin/grip-gate`); each of these runs its own shell, so the gate refuses them |

What the check reads, all of it settings the child will load:

- the registry, from the code checkout;
- the user settings file, which is `$CLAUDE_CONFIG_DIR/settings.json` when that
  variable is set and the install directory's `settings.json` otherwise;
- the main checkout's `.claude/settings.local.json`, because Claude Code reads a
  worktree's local file "at the main checkout's root";
- `.claude/settings.json` from both the working copy and `origin/main`, because
  the child's fresh worktree is born from `origin/main`. Either copy can switch
  hooks off. Hooks bound there are not credited, which can only refuse more.

A binding counts only when it runs, and can block, on every call its matcher
selects. A binding with an `if` filter skips some calls, and an `async` binding
cannot block at all (Claude Code's hooks reference says "Async hooks can't block
or control Claude's behavior"), so neither makes a gate `hooked`.

It fails closed. An unreadable registry or settings file, a registry with no
hard PreToolUse policy, or `disableAllHooks` refuses the run. That cannot
deadlock its own repair (`rules/gate-resolution-escape-hatch.md`, Invariant 1):
it refuses headless runs only, and fixing a settings file is not one.

**The forward anchor** is `tests/test_headless_gate_equivalence.py`, bound in
`.github/workflows/forward-anchors.yml`. It runs one test per (hard policy,
tool tier) pair, refuses every harness kind that cannot run GRIP's hooks, pins
the admitted kinds as a literal, and checks that the runner asks the gate
before any worktree or harness run. Each mutation listed in GRIP#8668's
description turns it red.

**Known limit.** The check proves that each hard hook is bound and will fire.
It does not prove that each hook denies correctly; that proof is each policy's
own test. Five hard policies name no deny test of their own (GRIP#8667). The
anchor pins those five, and the list may only shrink.

## Receipt schema

Every field is a measured fact or an honest `null` -- never a guess.

| Field | Type | Meaning |
|---|---|---|
| `origin` | string | who asked: `operator`, `relay`, or `cron` |
| `cwd` | string | the worktree the run executed in, or a note that none was entered |
| `model` | string or null | the model(s) `claude -p` reported using, from `modelUsage` in its own transcript |
| `tools_allowed` | list of strings | the `--tools` allowlist this run was given |
| `tools_used` | list of strings | the distinct tool names the transcript actually shows a `tool_use` for |
| `cost` | number or null | `total_cost_usd` from the transcript's final `result` event |
| `outcome` | string | one of the six values below |
| `idr_leaf` | string or null | the id of the signed IDR leaf for this run, or null if the chain could not be written |
| `started_at` / `ended_at` | string | UTC timestamps, read from the clock, never typed |
| `tldr` | string | one plain-language sentence summarising the outcome |
| `task` | string | the task text, verbatim, as given to this run |
| `blocked_action` | string or null | the outward-action keyword/phrase that caused a refusal, if any |
| `error` | string or null | why the run did not complete: the harness's own error text on `failed` or `cancelled`, the gate-equivalence reason on a pre-launch `denied`, or `transcript present but unparseable`; a harness or gate text keeps only its first 500 characters |

### `outcome` values

| Value | Meaning |
|---|---|
| `completed` | the transcript's final `result` event reported no error |
| `awaiting-approval` | an outward action was named (in the task text, or in a tool-use event) and the step was refused before it could run |
| `denied` | a GRIP safety gate refused: a PreToolUse hook during the run, or the slice-2 gate-equivalence check before launch (its `error` then starts `gate-equivalence:`) |
| `failed` | the harness could not launch, or its final `result` event reported an error |
| `cancelled` | the run exceeded its wall-clock budget |
| `uncertain-external-outcome` | the transcript has no `result` event at all, or none could be parsed -- never guessed as `completed` |

Outcome is read from the transcript, never from Claude Code's own process
exit code: slice 0 measured a control run exit with code 1 while its one
requested tool call ran twice and succeeded, because the run also hit
`--max-turns` and Claude Code reports that as `error_max_turns`. Exit code
and tool-call success are two different facts; this schema reports the
second one.

## Exit codes

`bin/grip-run` exits 0 when `outcome` is `completed` or `awaiting-approval`
(a refusal is the system working as designed, not a failure of the tool),
and 1 for every other outcome. `argparse` usage errors (a missing task,
an unknown flag) exit with `argparse`'s own code, 2.

## Refusals

The only refusal slice 1 implements is the outward-action barrier above.
There is no separate approval-request flow yet (that is part of a later
slice): a refused run simply writes its receipt with
`outcome: awaiting-approval` and stops. The operator reads the receipt and
decides whether to re-run the step themselves.

## What headless GRIP does NOT do yet

Named explicitly, because an undocumented boundary reads as a bug:

- **Slice 2 gates `grip-run` only.** GRIP has two other headless paths, and
  neither calls the gate. `lib/relay_dispatcher.py` runs a capability-locked
  `claude -p`, or OpenCode with a permission deny-set and no GRIP hooks; the
  VPS uses OpenCode with the operator at full capability, by V>>'s design of
  2026-07-04. `lib/overnight_loop.py` is the second. Wiring the gate into
  either would change a production path, which is a separate decision.
- **No Codex, OpenCode or HAL-native rung.** The gate refuses each until it can
  run GRIP's hard hooks and the forward anchor proves it.
- **No detached, durable jobs (slice 3).** A run is synchronous: `grip-run`
  blocks until the task finishes, fails, or times out. There is no
  `--detach`, no crash-resilient resume, no `yes {id}`-style approval that
  survives a restart.
- **No HAL-native executor (slice 4).** Every run goes through
  `ClaudeCodeHarness` (a real `claude -p` subprocess). There is no
  `FallbackHarness` to a HAL-native tool loop.
- **No relay or room delivery (slice 5, task 6).** The receipt is a local
  file. Nothing here posts a `tldr` to a peer room, a WhatsApp thread, or a
  Slack channel. (The brief for this slice names that as optional and
  skippable when the relay is not trivially reachable; it is skipped here
  for the same reason the brief gives -- it adds a dependency this slice
  does not need to prove its own falsifier.)
- **No `--chain-dir` override.** The IDR leaf always writes to the shared
  chain (`lib/precog/idr.py`'s default path), the same convention most
  existing GRIP tools follow. A headless-run receipt is not inherently a
  private matter; if one ever is, that is a case for the private-chain
  override `rules/prove_it_render_records_on_the_shared_chain_by_default.md`
  already names for other tools, added when a real caller needs it.
- **No read of `lib/model_pin.py`'s pin file.** Per the operator's own
  2026-10-11 ruling, a headless child takes the account default, full
  stop; this tool does not read the pin, it simply never injects `--model`
  unless the caller passes one explicitly.

## Shape B -- a HAL-native runtime: the open question

The plan's last step (task 6) asks whether GRIP should run headless tasks on
HAL's own tool loop instead of wrapping a vendor CLI. The candidate runtime is
HAL's `send_with_tools` (`lib/hal/tool_conversation.py`), with `tool_render`
(`lib/hal/tool_render.py`) to show its tool calls to an attached operator.
Astra's novelty test (`~/.grip/drafts/headless-grip/ultrareason.md`, section
1) asks three things of each claimed benefit: the primitive that already
exists, the integration that is missing, and the measurable gain over wrapping
a harness. "Provider-neutral" alone is not a reason. Each row was read on HAL's
`origin/main` at `9833245b`.

| Claimed benefit | Existing primitive | Missing integration | Measurable gain over `claude -p` |
|---|---|---|---|
| Gate equivalence on the native path (a precondition, not a benefit) | `ToolDispatcher` runs a secrets guard, HAL's `GuardEngine` and a `HookDispatcher`; HAL#621 made that dispatcher read GRIP's deny shapes | the dispatcher loads hooks from `$HAL_HOME/hooks/`, empty by default, so none of GRIP's 25 hard gates run there | a `hal-native` row in `lib/headless_gate.py` that the forward anchor admits |
| No new spend | `make_openai_compat_chat_fn` can point at the gateway on `:4010` (Pool B); subscription routes to Claude exist elsewhere in HAL (CCH, `lib/hal/utils/cch.py`, and the gateway forwarding a subscription bearer on `claude-*` ids) | `make_anthropic_chat_fn` sends an `x-api-key`, the metered key, and the tool loop is wired to neither subscription route. UNVERIFIED: whether the gateway route carries a tool loop end to end | a native run that costs $0, against `claude -p`, which already runs on the subscription seat |
| Cost-tiered routing across the Pool-B ladder | `Conversation.send`'s ROUTE stage | `send_with_tools` bypasses that whole seven-stage pipeline on purpose (its own module docstring) | cost per completed task on a fixed task set, measured against slice 1 |
| `sysmsg` steering in the middle of a run | GRIP's `lib/hal/sysmsg.py`, native to Anthropic only (ultrareason, section 6) | no hook from `run_tool_loop` into `sysmsg` | a run redirected without a restart. UNVERIFIED: whether `claude -p` with streamed JSON input already allows this |
| Exogenous verification inside the loop | AGORA and the sovereign council, which judge PRs, not tool steps | no verifier stage in `run_tool_loop` | the rate of steps a non-Anthropic checker refutes before they take effect |
| Replay of the whole workflow | `lib/ultracode`: a repeat run makes zero provider calls | replay says nothing about external effects (Astra); there is no at-most-once token for an effect | a replay that reproduces the receipt with zero provider calls and zero effects |
| Watching a detached run | `tool_render`'s one line per tool call | it renders HAL's own loop only; `claude -p` already streams its tool events as JSON | none on its own |

**Where this leaves Shape B.** It has not passed the novelty test. Its
precondition, gate equivalence, is unmet: none of GRIP's hard gates run on the
native path. Every other row names an integration nobody has built. If Shape B
is wanted, the first step is the precondition: run the registry's hard
PreToolUse hooks through HAL's `HookDispatcher` with Claude Code's matcher
semantics, and admit `hal-native` through the same forward anchor. Only then is
the cost row worth measuring against slice 1. Until that, a `FallbackHarness`
to a HAL-native rung stays off (ultrareason, section 5). Whether to build it is
V>>'s decision.

## Siblings

`lib/headless_gate.py` -- slice 2's gate-equivalence layer, and
`tests/test_headless_gate_equivalence.py`, its forward anchor.
`lib/relay_dispatcher.py::build_argv` -- the one production headless path
this slice generalises the capability-lock pattern from.
`lib/harness/cli.py::ClaudeCodeHarness` -- the harness this slice extends,
never forks. `rules/untrusted-content-capability-lock.md` -- the doctrine
behind `--tools` + `--strict-mcp-config` together. `rules/no-stubs.md`,
`rules/declared-input-must-reach-the-actor.md` -- why every field and flag
this spec names is wired to something real, or not named at all.
