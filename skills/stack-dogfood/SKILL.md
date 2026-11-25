---
name: stack-dogfood
description: Dogfood the CodeTonight AI suite onto itself -- install GRASP, HAPPI, HAL and GRIP into every harness and use them, inside those harnesses, to recursively improve all four. Backed by a deterministic census, an idempotent-fold install proof on lib/dogfood_kernel.py, grip-tick reporting, and a gated overnight loop that opens draft PRs only on the public products (GRASP, HAPPI) and never merges. Use for /stack-dogfood, /dogfood-grasp, /dogfood-happi, /dogfood-hal, /dogfood-grip, or when asked to install or converge the stack across harnesses.
status: Active
effort: medium
version: 1.0.0
triggers:
  - "/stack-dogfood"
  - "/dogfood-grasp"
  - "/dogfood-happi"
  - "/dogfood-hal"
  - "/dogfood-grip"
---

# /stack-dogfood

Dogfood the whole stack onto itself. The four products -- GRASP, HAPPI, HAL and
GRIP -- are installed into every harness and then used, inside those harnesses,
to improve all four. Backing framework: `lib/stack_dogfood/`.

## The CLI

```
bin/grip-stack-dogfood status   [--product X] [--require-installed] [--json]
bin/grip-stack-dogfood report   [--product X]
bin/grip-stack-dogfood install  --product X [--harness H] [--dry-run]
bin/grip-stack-dogfood prove    [--product X]
bin/grip-stack-dogfood loop     [--config PATH] [--force]
bin/grip-stack-dogfood escalate [--config PATH] [--refresh] [--live]
```

`status` probes and prints the census and health checks. `report` persists each
report to `state/stack-dogfood/<product>.json`, which the always-on tick reads.
`install` runs the installers for a product's missing cells. `prove` shows the
idempotent-fold witness. `loop` runs one check-phase tick (reads state, writes
reports; opens no PR). `escalate --config config/stack-dogfood.json` ideates a
fix and opens a DRAFT PR per public product -- dry-run by default (`--live` arms
it; `--refresh` runs the check phase first). It never merges, and HAL and GRIP
can never reach draft-pr.

## Each product declares its own install surface

* **GRASP** registers as the `grasp-mcp` MCP server in each harness: an `mcp add`
  CLI for codex, gemini, opencode, hermes, amp, grok and qwen; a plugin for
  Claude Code; a JSON entry for Cursor and the Claude desktop app.
* **HAPPI** is a runtime on PATH (`bash ~/.hal/HAPPI.md install`); every harness
  shells out to it. Probe-only -- the install is that one external step.
* **HAL** is the gateway (port 4010) plus `grip-harness` wiring. Probe-only: HAL
  wiring needs a dry run shown to the operator first.
* **GRIP** is the harness itself (`~/.claude`). Probe-only: GRIP installs itself.

## The install is an idempotent fold

Installing the stack into every harness is a fixed-point fold:
`model.InstallDomain` plugs into `lib/dogfood_kernel.py`, so `prove` runs
`generic_proof` over the census. At the fixed point every cell is REGISTERED, the
residual is zero, and a re-install is a provable no-op (`fold(fold(x)) == fold(x)`).

## The overnight loop -- gated, and it never merges

`lib/stack_dogfood/loop.py` decides whether the loop may run and in what mode per
product. It does not spawn agents or open PRs itself; that is a human-run runner
reusing the existing overnight wave. Three safety properties:

* Every operator halt wins over the schedule: the weekly-spend throttle, the
  global outbound-stop brake, and a dedicated `state/stack-dogfood/STOP` marker
  all short-circuit the fire gate.
* `wave_mode` can only ever return `draft-pr` for a PUBLIC product (GRASP,
  HAPPI). HAL and GRIP are forced to `brief` regardless of the config, so a
  config typo can never arm an autonomous PR on a bootstrap-authority repo.
* The runner opens a **draft** PR only; a human merges under the normal review
  rules (AGORA, Codex). The overnight agent has no GitHub credentials.

Config: `config/stack-dogfood.json`. Runtime state: `state/stack-dogfood/loop.json`.
Schedule: launchd `com.grip.stack-dogfood` (write it per *Arming the overnight
loop* below).

## The escalation config block

`escalate --config config/stack-dogfood.json` reads a per-product `escalation`
block from the file named by `--config`. The block is loaded ONLY when `--config`
names the file: a bare `escalate` leaves the escalation map empty, so a live run
then blocks on the missing `workdir`. The launchd job passes `--config`; a manual
run must too. Each entry names where a prepared fix is gated and pushed --
copy-paste and edit:

```json
{
  "escalation": {
    "grasp": {
      "repo": "CodeTonight-SA/grasp",
      "workdir": "~/CodeTonight/grasp",
      "test_cmd": ".venv/bin/python -m pytest -q tests"
    },
    "happi": {
      "repo": "CodeTonight-SA/HAPPI",
      "workdir": "~/CodeTonight/HAPPI",
      "gate_profile": "docs"
    }
  }
}
```

Each field, per product:

* `repo` -- the `owner/name` the draft PR is opened against. Required; a missing
  repo fails the push gate closed.
* `workdir` -- the local clone the fix agent prepares the commit in (`~` is
  expanded). Required for a live run.
* `test_cmd` -- the product's own test command, run as the push gate for a
  **code** repo. Required unless `gate_profile` is `docs`; a missing command
  fails closed.
* `gate_profile` -- `code` (the default) runs `test_cmd`; `docs` instead
  requires that every changed file is documentation, for a spec repo with no
  test suite (e.g. HAPPI). An unknown value falls back to the stricter `code`.

The block is consulted only for a product that reaches `draft-pr` mode, so HAL
and GRIP are never escalated regardless of what it contains. The public-profile
leak sweep always runs ahead of the test/docs gate.

## Arming the overnight loop

The schedule is a user launchd agent. The repo ships the template
`config/launchd/com.grip.stack-dogfood.plist.template`, on the check-only `loop`
by design -- arming the live escalation is a separate, operator-gated step.
Install it the same way as every GRIP launchd agent: make the log directory
first (launchd does not create it, and the job fails silently without it),
substitute `__HOME__`, copy, load, and confirm it is loaded:

```bash
GH="${CLAUDE_DIR:-$HOME/.claude}"
mkdir -p "$GH/logs"
sed "s|__HOME__|$HOME|g" \
  "$GH/config/launchd/com.grip.stack-dogfood.plist.template" \
  > ~/Library/LaunchAgents/com.grip.stack-dogfood.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.grip.stack-dogfood.plist
launchctl print "gui/$(id -u)/com.grip.stack-dogfood" | grep -E 'state|program'
```

To arm the live ideate + draft-PR escalation, change the second
`ProgramArguments` entry from `loop` to the three entries `escalate`, `--live`
and `--refresh`, after a hand dry run (`grip-stack-dogfood escalate --config
"$GH/config/stack-dogfood.json"`, which opens nothing), then re-arm with the
bootout-wait sequence below. Keep `--refresh`: it runs the check phase first, so
a late wake outside the hour still refreshes the reports before the guard
declines the off-schedule live run; without it, an off-schedule wake refreshes
nothing.

The plist's `StartCalendarInterval` and `schedule_hour`/`schedule_minute` in
`config/stack-dogfood.json` are two sources of truth that MUST agree (both ship
at 02:15). The guard compares the wake time to `schedule_hour`, so a plist that
fires at a different hour would be declined on every run. To move the schedule,
edit BOTH; the repo test `test_plist_template_schedule_matches_the_config_hour`
pins the shipped pair.

Three traps make this job fail SILENTLY -- it runs, opens nothing, and prints no
error you would see. Each was paid for once:

* **PATH.** launchd starts a job with a minimal PATH that omits Homebrew and
  `~/.local`, where `claude`, `gh` and the harness CLIs live. The template sets
  an explicit `EnvironmentVariables` PATH for this reason; without it the job
  finds no CLI and fails closed. Prove the PATH resolves the tools through a
  shell, because `command` is a shell builtin rather than an executable that
  `env` can run:
  `env -i PATH="<the plist PATH>" /bin/sh -c 'command -v grip-stack-dogfood claude gh'`.
* **No `~` expansion.** A plist string is literal, so `~`, `$HOME` and `$VAR`
  are not expanded. The template's `__HOME__` placeholder, replaced by the `sed`
  above, keeps every path absolute.
* **Asynchronous bootout.** To replace the plist, bootout first and wait for the
  label to disappear before bootstrap; a `bootstrap` issued immediately after
  `bootout` fails with `Input/output error` (errno 5). Bound the wait so a
  failed bootout reports its cause instead of looping forever:

  ```bash
  launchctl bootout "gui/$(id -u)/com.grip.stack-dogfood" 2>/dev/null
  for _ in $(seq 1 30); do
    launchctl print "gui/$(id -u)/com.grip.stack-dogfood" >/dev/null 2>&1 || break
    sleep 1
  done
  if launchctl print "gui/$(id -u)/com.grip.stack-dogfood" >/dev/null 2>&1; then
    echo "bootout did not release the label -- check the domain and permissions" >&2
  else
    launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.grip.stack-dogfood.plist
  fi
  ```

`RunAtLoad` is `false`, so arming does not spend a live run at once. One
scheduling caveat for live mode: `StartCalendarInterval` does not skip a missed
run -- if the Mac is asleep at 02:15, launchd fires the job on the next wake,
which can fall in working hours. The loop guards against this in-process
(GRIP#9275): a live run outside the configured hour (`schedule_hour`, read in
local time to match launchd) is declined, and the `--refresh` check phase stands
as the fallback -- the reports refresh, no agent spawns, and no draft PR opens.
To run `--live` on purpose outside that hour, pass `--ignore-schedule`; the
launchd job never does, so a late wake always falls back to check-only.

Disarm: `launchctl bootout "gui/$(id -u)/com.grip.stack-dogfood"`. Back to
check-only: change the `escalate`, `--live`, `--refresh` entries back to a
single `loop`, then re-arm with the bootout-wait sequence above.

## Astra in the mix

The default council speakers are `deepseek`, `xai`, `astra`, `groq` -- Astra
(gpt-6-astra) sits alongside the others for the dogfood ideation, which runs
through `/ultraideate` (no Anthropic weekly-limit draw).

## Honest limits

* `status` reports what it can probe now; a harness CLI that is absent reads as
  UNKNOWN, not MISSING -- the framework never claims to install into a harness
  that is not there.
* A seal or a green check proves integrity, not correctness. A draft PR the loop
  prepares is a proposal for human review, never a merge.
