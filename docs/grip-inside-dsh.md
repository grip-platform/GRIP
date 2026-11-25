# Running GRIP inside the DeepSeek Harness

DSH can delegate a task to Claude Code, and because that child reads GRIP's own
configuration directory, the agent it delegates to is a full GRIP session — the
rules, the hooks, the memory, all of it. This document records how that mount is
built, which of its assumptions were verified against the source, and which are
still only plausible.

The two live configuration files sit under `~/.dsh`, outside every repository, so
nothing in git would otherwise record why they exist or how to rebuild them after
a DSH reinstall. That is what this file is for.

## What was built

A dedicated profile named `grip`, created rather than editing the operator's
existing `web` or `headless` profiles. A new profile is additive and reversible;
editing a working one is neither.

```
~/.dsh/profiles/grip/package.json         bundles: dsh-base, dsh-headless
~/.dsh/profiles/grip/cordis.patch.yml     the mount (both planes)
~/.dsh/profiles/grip/pnpm-workspace.yaml  written by dsh's own profile init
~/.dsh/profiles/grip/cordis.yml           written by dsh at every boot; do not edit
```

Install, with the version pinned — this matters, see below:

```bash
dsh plugin --profile grip add @deepseek-ai/dsh-subagent-claude-code@0.1.0-rc.7
```

Run:

```bash
HAL_GATEWAY_KEY=<any-non-empty> dsh --profile grip "<task>"
```

The variable is for the DSH **parent** agent's own model route, not for the mount.
It is a doorstop rather than a credential — see "The parent's model route" below.

### Verify the plugin actually loads, every time you add one

`dsh --profile <name> --dump-config` exits 0 on a profile that cannot boot: it
composes the plugin tree without importing a single plugin. Run
`bin/grip-dsh-verify <profile>` after any `plugin add`. It boots the profile for
real and reports FAIL with the missing package and a ready-to-paste `pnpm` command.

The failure it catches is not obvious. A profile does not carry its own copy of
most `@deepseek-ai` packages — it resolves them from a symlink farm the installed
harness maintains one directory above every profile, and the profile's
`pnpm-workspace.yaml` sets `autoInstallPeers: false` deliberately so that the
harness's singletons are inherited rather than duplicated. An unsatisfied peer is
therefore usually harmless. It becomes fatal only when the plugin imports a
package the farm does not carry, and then one unloadable plugin aborts the entire
tree and dsh does not start at all. See the 2026-08-21 incident below.

### The pin is not optional

`npm view @deepseek-ai/dsh-subagent-claude-code version dist-tags` returns version
`0.0.1-rc.1` with `dist-tags { latest: '0.0.1-rc.1', next: '0.1.0-rc.7' }`, while
the installed dsh app is `0.1.0-rc.7`. A bare `add` therefore installs a package
from a different release line than the host that has to load it. Always pin.

The install prints one warning, which is correct and expected:

```
dsh: warning: @deepseek-ai/dsh-subagent-claude-code declares no dsh.bundle —
installed as a plain dependency, not a profile layer
```

The package carries no `dsh` field in its `package.json`, so it is mounted by a
patch entry rather than by the bundle stack. That is exactly how it is used here.

### Why the bundle list is written by hand

`runPlugin` initialises a missing profile with `PROFILE_TEMPLATES[profile] ??
DEFAULT_PROFILE_BUNDLES` (`apps/cli/src/plugin.ts:121-124`), and
`DEFAULT_PROFILE_BUNDLES` is `['@deepseek-ai/dsh-base']` alone
(`packages/boot/app-boot/src/profile.ts:125`). There is no template named `grip`,
so letting the installer create the profile would have produced a bundle list
missing the pieces this mount needs. Writing `package.json` first avoids that:
`initProfile` only writes the manifest `if (!existsSync(manifestPath))`
(`profile.ts:155`), so a hand-written one survives.

## The mount, and why it is two planes

```yaml
- insert:
    - id: subagent-claude-code
      name: '@deepseek-ai/dsh-subagent-claude-code'
      config:
        disposeGraceMs: 3000
        env:
          GROVE_PROJECT_NAME: ''

    - id: tool-subagent-claude-code
      name: '@deepseek-ai/dsh-tool-subagent'
      config:
        provider: claude-code
        toolName: subagent_claude_code
        backgroundMode: one-shot
        maxDepth: provider-managed
```

The first row is the **host plane**: it registers a provider named `claude-code`
on the shared `ctx.subagents` service. The second is the **agent tool plane**: it
exposes that provider to the model as a callable tool. They are separate because
the provider is a shared service and the tool is part of one agent's tool set.

Both rows are appended flat to the root entry list. That is legitimate here rather
than a shortcut: `dsh-base` contains no groups and exactly one top-level
`- insert:` at `packages/bundle/base/cordis.patch.yml:15`, and an `insert` with no
`id` does `data.push(...insert)` (`vendor/include/src/index.ts:80`). The rows land
beside `dsh-base`'s own `subagent-fork-in-process` / `tool-subagent-fork` pair
(`base/cordis.patch.yml:300-329`), which this mount is modelled on directly.

**`maxDepth: provider-managed` is mandatory, not stylistic.** A numeric cap
requires the provider's `depthLimit` capability, and this provider advertises
`NO_START_CAPABILITIES` where `depthLimit` is `false`
(`packages/subagent/subagent/src/out-of-process.ts:25-30`). Setting a number fails
the whole boot — verified deliberately, output quoted below.

**`env` and `disposeGraceMs` are the only keys this provider accepts.** Its config
schema is exactly those two fields
(`packages/subagent/subagent-claude-code/src/index.ts:33-45`). There is no model
key, no cwd key, no permission key. The child's working directory is not
configurable at all: `start()` calls `resolveChildCwd('subagent-claude-code',
undefined, parentCwd)` with the requested-cwd argument hardcoded to `undefined`
(`index.ts:73-77`), so the child always runs in the parent DSH session's directory.

**No API key is set, deliberately.** The child authenticates from the host login,
exactly as an interactive session does — see the next section.

### `GROVE_PROJECT_NAME: ''` — neutralising a third-party wrapper

`command -v claude` on this machine resolves to `~/.subspace/bin/claude`,
a 4.6 KB bash script ("Subspace wrapper v17"), not the real binary at
`/opt/homebrew/bin/claude`. When `GROVE_PROJECT_NAME` and a workspace scope are
both set, that wrapper prepends `--append-system-prompt-file <path>` before
exec'ing the real binary (line 122).

This is not hypothetical. Those variables are live in the environment DSH is
launched from (`GROVE_PROJECT_NAME=.grip`, `GROVE_WORKSPACE_NAME=grip`), they
contain none of `KEY|PASSWORD|SECRET|TOKEN` and do not start with `DSH_`, so
`scrubbedParentEnv()` forwards them untouched
(`packages/subprocess/subprocess/src/index.ts:44,60-66`). The file it injects
exists and is **23,752 bytes**. Without the override, every GRIP child spawned
through this mount would have started with 23 KB of unreviewed third-party system
prompt prepended.

Setting the variable empty takes the wrapper's `-z` branch at line 98, so it never
resolves a context file and falls through to `exec "$REAL_EXECUTABLE" "$@"` at
line 124 — a transparent exec with full argument passthrough. Verified both ways:

```
$ ~/.subspace/bin/claude --version
2.1.233 (Claude Code)                                    # silently primed

$ GROVE_PROJECT_NAME='' ~/.subspace/bin/claude --version
[SESSION_PRIME] skipping prime: GROVE_PROJECT_NAME='' scope='grip'
  — agent will launch without session-context
2.1.233 (Claude Code)
```

This removes no GRIP safety control; it steers away from a foreign one.

**A rejected alternative.** The investigation proposed filtering `~/.subspace` out
of `PATH` instead. That was not used. On POSIX, `childEnv` returns
`{...scrubbedParentEnv(), ...extra}` (`subprocess-local/src/spawn.ts:37-39`), so an
explicit `PATH` **replaces** the inherited one wholesale rather than prepending —
anything else the child needs that lives only in that directory would break. The
environment variable achieves the same isolation with a far smaller blast radius.

## The settingSources claim: CONFIRMED

The claim under test was that a DSH-spawned Claude Code child inherits GRIP's full
configuration. It holds — confirmed twice, once by reading the source chain below
and once empirically, by a live child quoting GRIP's own `CLAUDE.md` back verbatim
(see "What was actually run").

`claudeQueryOptions` builds the SDK options from exactly seven keys —
`abortController`, `cwd`, `pathToClaudeCodeExecutable`, `env`, `persistSession`,
`disallowedTools`, `spawnClaudeCodeProcess`
(`packages/subagent/subagent-claude-code/src/run.ts:177-195`). `settingSources` is
not among them, and its absence is pinned by a test that can fail
(`tests/subagent-claude-code.spec.ts:567-576`).

What that omission *means* is version-specific, so it was read from the pinned SDK
(`@anthropic-ai/claude-agent-sdk@0.3.220`) rather than recalled:

> `sdk.d.ts:1906` — "When omitted, all sources are loaded (matches CLI defaults).
> Pass `[]` to disable filesystem settings (SDK isolation mode). Must include
> `'project'` to load CLAUDE.md files."

The doc comment matches the implementation: `sdk.mjs:136` reads
`(e.settingSources ?? QMe)` with `QMe = ["user","project","local"]`, and
`sdk.d.ts:6626` defines `SettingSource = 'user' | 'project' | 'local'`.

**One link in the original reasoning was wrong, in a way that helps.** The chain
was assumed to depend on the `~/.claude` → `~/.grip` symlink. It does not.
`export CLAUDE_CONFIG_DIR=~/.grip` is set at `~/.zshrc:4` and confirmed
live; it survives the credential scrub by the same test as the GROVE variables, and
the SDK honours it directly (`sdk.mjs:136`, `CLAUDE_CONFIG_DIR ?? …/".claude"`).
The symlink is also real (`.claude -> ~/.grip`), so both routes land on
GRIP and the mount survives either being removed.

What the child therefore inherits, counted from the live file rather than assumed:

| | |
|---|---|
| `~/.grip/settings.json` | 48,074 bytes |
| hook events | 17 |
| PreToolUse matcher entries | 22 |
| `permissions.defaultMode` | `auto` |
| `permissions.allow` / `deny` / `ask` | 4 / 0 / 0 |
| `~/.grip/CLAUDE.md` | 27,003 bytes |

## The tool allowlist: there is none, and one cannot be written

The brief asked which tool allowlist this mount should use. The honest answer is
that **no allowlist is expressible on this provider**, and inventing a plausible
one would have been worse than saying so.

`tool-subagent` does support a `toolFilter`, but using it requires the provider's
`toolFilter` capability, and this provider freezes that to `false` along with
everything else (`out-of-process.ts:25-30`). The comment there is explicit that the
shared service "rejects a request needing any of them before `start` runs — never
accepted-then-ignored". The provider's own config schema offers no tool key either.

Three real levers exist instead, in the order they should be applied:

1. **Profile scoping.** The tool row lives in the `grip` profile alone. The
   operator's `web` and `headless` profiles are untouched and cannot reach it.
2. **GRIP's own 22 PreToolUse deny gates**, inherited through the user settings
   source. This is a stronger and already-ratified mechanical control than a flag
   would be, and it is the substantive answer to
   `rules/untrusted-content-capability-lock.md`, whose mandate is that the surface
   be restricted *by mechanism* — not that the restriction be spelled as an
   allowlist.
3. **The `env` overlay**, used here for the wrapper neutralisation.

Anything stronger needs an upstream change: the SDK accepts an `Options.settings`
field (`sdk.mjs:136`) that this provider never passes.

**The residual exposure should be a conscious operator decision.** The child runs
at `defaultMode: auto` with no deny list and no ask list, in the parent session's
working directory, spawned through `dsh-subprocess-local`. DSH's own
`workspace-write` sandbox governs DSH's fs and bash tools, not
`ctx.subprocess.spawn`, so it does not constrain this child. Its restraints are
GRIP's hooks and Claude Code's own permission layer. If that is too wide, the fix
is a `permissions.deny` list in GRIP's `settings.json` — which would also apply to
every interactive session, and is therefore an operator decision rather than a
mount decision.

## What was actually run, and what happened

**It works end to end.** A real delegation through the mount:

```
$ HAL_GATEWAY_KEY=<any> dsh --profile grip \
    "Use the subagent_claude_code tool to ask a child agent to reply with
     exactly the word PONG and nothing else. Then tell me the child's reply."

[SESSION_PRIME] skipping prime: GROVE_PROJECT_NAME='' scope='grip'
  — agent will launch without session-context
The child's reply was:

**PONG**
```

That `[SESSION_PRIME]` line is worth pausing on: it proves the wrapper override
reached the *real* spawn path, not just a hand-run test.

**The child is a genuine GRIP session.** Asked whether its instructions contain
GRIP's own sections, the child answered:

> YES. The instructions contain a section headed "Cryptographic Causation — The
> Moat (PARAMOUNT)", and the "What GRIP Is" section states verbatim: "GRIP
> (General Reasoning & Intelligence Platform)" — note the actual text uses an
> ampersand, not the word "and". My current working directory is `/private/tmp`.

This is stronger than a yes. It quotes `~/.grip/CLAUDE.md` verbatim and *corrects
the question's* wording, which a model agreeing agreeably could not do. It also
confirms the predicted cwd behaviour: `/private/tmp` was the parent DSH session's
directory, exactly as `resolveChildCwd(..., undefined, parentCwd)` requires.

**Cross-plane resolution is proven by a deliberate red probe.** This was the one
genuinely unverified structural question: does a tool row naming
`provider: claude-code` resolve a provider registered on the host plane?
`tool-subagent` registers its tool inside `mount(provider)`, which only runs once
the provider is live, and it fails there if a numeric `maxDepth` meets a provider
without `depthLimit` (`packages/subagent/tool-subagent/src/index.ts:283-289`).
Temporarily setting `maxDepth: 3` produced:

```
dsh: plugin tree failed to load: failed to apply loader entry include
(cordis:include): failed to apply loader entry subagent-claude-code
(@deepseek-ai/dsh-subagent-claude-code): tool-subagent: provider "claude-code"
cannot enforce maxDepth (no depthLimit capability) — set maxDepth:
'provider-managed' to leave the recursion budget to the provider
    at new apply (…/profiles/grip/node_modules/.pnpm/
    @deepseek-ai+dsh-subagent-claude-code@0.1.0-rc.7_…/lib/index.js:366:16)
```

That message can only come from code that resolved the provider by name and read
its real capabilities. It also proves the provider loaded from the profile-local
install at the pinned version. Restoring `provider-managed` cleared it, so the
check demonstrably fails and passes on the right inputs.

**No duplicate service identity.** A flagged risk was that pnpm might materialise
a second copy of `dsh-subagent` inside the profile, giving the provider a
different `SubagentProvider` identity than the host service.
`ls ~/.dsh/profiles/grip/node_modules/@deepseek-ai/` lists exactly one entry.
Refuted.

### The parent's model route

The mount needs nothing, but the DSH **parent** agent needs its own model. Two
failures preceded the working run, and both were upstream of the mount:

```
dsh: MISSING_CREDENTIAL: llm-deepseek: no API key for provider route
"deepseek-official" …
```
```
dsh: MISSING_CREDENTIAL: llm-pi-ai: no credential for provider route "hal";
its profile resolves HAL_GATEWAY_KEY, which is not set …
```

`~/.dsh/settings.yaml` routes the parent through the local HAL gateway
(`llm-pi-ai`, `baseURL: http://127.0.0.1:4010/v1`, `agent-default-model:
{provider: hal, model: openai/gpt-4o-mini}`). `HAL_GATEWAY_KEY` gates that route,
and it is a **doorstop rather than a credential** — the gateway resolves real
upstream keys itself, so any non-empty value clears it. Confirmed directly: a POST
to `/v1/chat/completions` bearing the literal string `doorstop-probe` returned a
normal completion.

One trap worth recording, because it cost a wrong conclusion: probing `/v1/models`
returns `invalid x-api-key` for *any* value including a valid one, so that route
is useless for testing whether the gateway is reachable. Probe
`/v1/chat/completions` instead.

### A real limitation, with a partly-corrected explanation

Reproducible at the time of testing, 3 attempts out of 3: any delegation where the
**child uses a tool** ended in

```
dsh: CONTEXT_WINDOW_EXCEEDED: 400 status code (no body)
```

while every delegation where the child only answers succeeded (2 of 2). Prompt
length was not the variable — the shortest tool-using prompt failed just as the
longest did.

**The first explanation written here was too confident and is corrected.** It said
the child's tool transcript dwarfs its answer, because GRIP injects gate context on
every tool call. That may still contribute, but a later run surfaced a figure that
changes the picture:

```
dsh: RATE_LIMIT: 413: Request too large for model `openai/gpt-oss-120b` …
tokens per minute (TPM): Limit 8000, Requested 53608
```

**53,608 tokens for a trivial no-tool delegation.** The DSH parent's baseline
request is already very large before any child output is added. So the honest
statement is: the parent request is big to begin with, and a child tool turn adds
to it; how the total divides between the two has not been measured. Do not repeat
the tidy version of this story without measuring it.

A second confound, which matters for anyone reproducing these numbers:
`~/.dsh/settings.yaml` was being edited by a concurrent session throughout this
work. Its `agent-default-model` moved from `openai/gpt-4o-mini` to
`groq/openai/gpt-oss-120b` to `deepseek/deepseek-reasoner` within roughly ten
minutes, and the file's mtime was two seconds old when last checked. **The
successful delegations ran under `gpt-4o-mini`; the failures did not all run under
the same model.** Treat the 3/3 and 2/2 counts as observations against a moving
target, not as a controlled comparison.

None of this affects the mount, which is what this document is about. It does bound
what the mount is currently useful for, and it means the next person should pin the
model before drawing conclusions.

## Still unproven

Honest ignorance, not a to-do list someone else has already done.

- **Whether GRIP's hooks actually fire in the child.** The settings are loaded —
  proven twice over, by the SDK default and by the child quoting `CLAUDE.md` back
  verbatim. Nobody has yet watched a PreToolUse gate *deny* something inside an
  SDK-spawned child. Per `rules/tests-must-be-able-to-fail.md` the check that
  counts is the one that can go red: delegate a task that trips a known deny gate
  and confirm it is denied. Every attempt hit the context ceiling described above,
  so it is blocked on a larger-context parent model rather than on anything about
  the mount. **Until it passes, do not call this mount hook-locked** — say it
  inherits GRIP's settings, which is what has been shown.
- **SDK and CLI version skew.** The provider pins
  `@anthropic-ai/claude-agent-sdk@0.3.220`, whose README states its keyless test
  uses SDK-distributed Claude Code 2.1.220 as a fixture and does not claim
  compatibility with every independently installed version. The host CLI is
  2.1.233. Thirteen patch versions of control-protocol drift, untested — though
  the working delegations above are real evidence against a serious break.
- **The child's system prompt.** `sdk.mjs:136` shows an omitted `systemPrompt`
  becoming `""` rather than being absent. Whether the CLI reads that as "use the
  default preset" or "use an empty system prompt" is not determinable from the
  minified bundle. It does not touch settings, hooks, `CLAUDE.md` or skills, which
  arrive by other routes — only how Claude-Code-like the child's preamble is.
- **Project-scope inheritance.** Because the default sources include `project` and
  `local`, the child also loads the `.claude/settings.json`,
  `.claude/settings.local.json` and `CLAUDE.md` of whatever directory the parent
  DSH session is in. GRIP's PARAMOUNT tier always arrives via the user source, but
  a delegation launched from a client working tree inherits that tree's project
  settings too. Worth knowing before delegating from client work.

## Making this a web-surface profile

The `grip` profile is deliberately `dsh-base` + `dsh-headless`, because that is
what made an end-to-end test possible at all without an interactive UI.

If it should instead mirror the `web` profile that `gg++ dsh` launches, the tool
row moves. On the web surface an agent's tools come from an **agent preset**, which
mounts its own composition subtree (`packages/preset/agent-presets/src/mount.ts`),
so a root-level tool row would never reach the agent. All four shipped presets
already carry a `tool-subagent-claude-code` row pre-wired and `disabled: true`
(for example `apps/cli/config/agent-presets/standard/agent.cordis.yml:212-219`),
which is the intended opt-in seam. The steps would be:

1. Change the profile's bundles to `dsh-base` + `dsh-web-app`, which is where the
   `agent-presets` row lives (`packages/bundle/web-app/cordis.patch.yml:421-424`)
   and nowhere else.
2. Copy `standard/` to `~/.dsh/.agent-presets/grip/` and delete the single
   `disabled: true` line. Use a new preset id, never a copy of `standard` — an
   earlier configured root wins a duplicate id
   (`agent-presets/src/index.ts:99-104,133-135`).
3. Keep the host-plane provider row exactly as it is; only the tool row relocates.
4. Optionally set the profile's default preset by patching
   `- id: agent-presets` with `config: {default: grip}`. That survives the CLI's
   later overlay, which spreads the existing config before adding the shipped root
   (`apps/cli/src/profile-boot.ts:159-167`).

A preset was **not** shipped in this pass on purpose. `~/.dsh/.agent-presets` is a
shared user root read by every profile that has the `agent-presets` row, including
the operator's `web`. A preset placed there would offer a `subagent_claude_code`
tool in `web`, where the host-plane provider is not mounted — a capability named
but not provided, which `rules/no-stubs.md` forbids.

## Shipped: the web plane (2026-08-19, AFK overnight run)

The recipe above was executed — with one deviation: the preset is a copy of
`code` (the run_code presentation this operator's web sessions already use),
not `standard`, and it is named `grip-code`, matching the preset an earlier
session had authored in the sandbox instance home.

Live state, both homes (`~/.dsh`, the durable DSH home, and the sandbox
instance home the running web server boots from):

- `profiles/web/cordis.patch.yml` mounts the host-plane provider rows
  `subagent-claude-code` **and** `subagent-codex` (both pinned `0.1.0-rc.7`
  in `package.json`), plus `agent-presets.config.default: grip-code`.
- `.agent-presets/grip-code/` is `code` with the `tool-subagent-claude-code`
  and `tool-subagent-codex` rows enabled. Both providers exist on the host
  plane, so neither row is a stub. The codex provider is one-shot over
  `codex app-server --stdio` (`subagent-codex/src/index.ts`).
- Both provider rows carry `GROVE_PROJECT_NAME: ''`; the codex wrapper in
  `~/.subspace/bin` has the same prime-skip branch as the claude one
  (lines 305/315).
- The sandbox instance's `settings.yaml` agent default moved from
  `deepseek-v4-pro` / `reasoningEffort: max` to `deepseek-v4-flash` /
  `reasoningEffort: low`: every `web_search` on the old route was a full
  max-effort reasoning turn and exceeded the 60 s tool cap (observed 3/3).
- Backups of all changed files: `PARAMOUNT_BACKUPS/2026-08-19/`.

Validation: run `bin/grip-dsh-verify web`. It performs a bounded web-profile boot
that proves the profile's plugin tree loads and the web plane reaches its banner;
it sends SIGKILL to the probe's process group afterwards, waits briefly for the
group to disappear, and warns on stderr if it remains alive. `dsh --profile web
--dump-config` still checks composition, but it does not load plugins.

The acceptance test also requires a fresh web session after a server restart
whose agent catalogues both `subagent_claude_code` and `subagent_codex`. Check
that manually: the boot verifier does not inspect the agent's tool catalogue and
does not replace this step.

Incident, 2026-08-21: the web profile failed at boot because
`dsh-subagent-codex@0.1.0-rc.7` imports `dsh-sdk-protocol`, while the installed
harness's 195-package symlink farm at the parent of every profile contained
`dsh-llm` and `dsh-invariants` but not `dsh-sdk-protocol`. Node's upward
bare-specifier resolution therefore found the former peers and not the latter.
The earlier `--dump-config` validation stayed green because it composes the
tree without importing it; the boot verifier closes that exact gap.

Follow-up in the same run: a local `dsh-subagent-opencode` plugin now hosts
OpenCode too (provider `opencode`, tool `subagent_opencode`, one-shot
`opencode run -m deepseek/deepseek-chat`, keys resolved from macOS Keychain
like `grip-opencode`, wired into both homes and proven end-to-end with a
PONG delegation through a throwaway headless profile). The web-plane mount
is also packaged at `~/.dsh-plugins/dsh-profile-grip/` (`npm pack --dry-run`
clean; publishing needs operator credentials and a scope name). Still open:
the 53k-token parent-request baseline, which the model change reduces in
cost and latency but not in size.

## Maintenance

Both live files are outside any repository and are destroyed by a DSH reinstall.
Back them up when changed (`rules/paramount-backups.md`):

```bash
bin/grip-backup ~/.dsh/profiles/grip/cordis.patch.yml ~/.dsh/profiles/grip/package.json
```

Do not add a test asserting on `~/.dsh` contents. It would pass on this machine and
be meaningless anywhere else — the shape `rules/tests-must-be-able-to-fail.md`
warns about.

The profile patch file is watched live (`profile-boot.ts:285-294`), so a running
session picks up an edit and a rejected parse leaves the last good tree running.
That is forgiving, but it also means a broken edit can read as inert rather than
loud. Check stderr after editing.

---

V>> (Lourens Cornelius "Laurie" Scheepers)
___
With assistance of
  Claude Opus 5 (1M context) — `claude-opus-5[1m]`
