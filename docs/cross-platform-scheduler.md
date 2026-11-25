# Cross-Platform Scheduler (#23)

**Status:** macOS path is live; the Windows renderer (`scripts/install.ps1`) is now scaffolded correct-by-construction; Linux path is designed here. A>> validate `install.ps1` execution on a real Windows host (an always-on Windows server is the target exit-node) and add the remaining job templates. Windows behaviour cannot be validated on macOS/CI, so this doc + the renderer + the reference template are the hand-off.

## What this is (plain English)

GRIP runs recurring background jobs (the learning heartbeat, the overnight loop, memory metabolism, the daily FF update, …). On macOS those are **launchd agents**. This makes the *same* jobs run on **Windows** (Task Scheduler) and **Linux** (cron/systemd) from **one source of truth**, with **zero hardcoded operator paths** — every path comes from a `__GRIP_HOME__` placeholder resolved at install time (pairs with #24, which drove operator-path hardcoding to zero).

## The architecture: one job → three renderers

A scheduled job is defined once (program, arguments, interval, limits) and rendered per-OS:

| OS | Artifact | Placeholder source | Rendered by | Activated by |
|----|----------|--------------------|-------------|--------------|
| macOS | `config/**/*.plist.template` | `__GRIP_HOME__`, `__CLAUDE_DIR__`, `__CLAUDE_BIN__` | `scripts/install.sh` (`sed` substitution → `~/Library/LaunchAgents/<label>.plist`) | `launchctl load` |
| Windows | `config/windows/*.task.xml.template` (NEW — see reference below) | same `__GRIP_HOME__` / `__GRIP_PYTHON__` | `scripts/install.ps1` (substitute → `%TEMP%\<task>.xml`) | `schtasks /create /tn <name> /xml <file>` |
| Linux | crontab line / systemd `.timer` (template TBD) | `__GRIP_HOME__` | `scripts/install.sh` (Linux branch) | `crontab`/`systemctl --user` |

The macOS renderer already exists (`install.sh`, see its `__GRIP_HOME__` `sed` block). The Windows renderer now exists too — `scripts/install.ps1` (option 1 below), authored correct-by-construction on macOS; A>> validate its execution on a real Windows host and generate the remaining job templates.

## Windows path — two options

1. **XML template + `schtasks` (this task's approach — `scripts/install.ps1` implements it).** A `*.task.xml.template` parallel to each `*.plist.template`; `install.ps1` resolves `__GRIP_HOME__` → `$env:CLAUDE_DIR` (fallback `$env:USERPROFILE\.claude`) and `__GRIP_PYTHON__` → `(Get-Command python).Source` (fallback `python3`), renders every `config/windows/*.task.xml.template`, and runs `schtasks /create /tn <stem> /xml <rendered> /f` (idempotent — `/f` overwrites, no duplicates). `-DryRun` renders to `%TEMP%` for inspection without registering. The registered task name is the template file stem (`grip-heartbeat.task.xml.template` → `grip-heartbeat`). One template ships today (`config/windows/grip-heartbeat.task.xml.template`); A>> add one `*.task.xml.template` per launchd job and validate `schtasks /query`.
2. **PowerShell cmdlets (already proven).** `config/heartbeat/Install-GripHeartbeat.ps1` registers a task via `New-ScheduledTaskAction`/`-Trigger`/`Register-ScheduledTask`, computing `$ClaudeDir = Join-Path $env:USERPROFILE ".claude"`. Portable, no XML. Use this as the fallback/cross-check.

Both are portable by construction (no operator name/path baked in). A>>: pick one as canonical, generate the rest of the jobs from it, validate `schtasks /query` on the Windows server.

## Setup-wizard gap (#23 part 2)

The complete GRIP setup wizard must collect **every** operator-specific value cross-platform — home/`CLAUDE_DIR`, identity, email, harness×model, HAL gateway key, launchd/Task-Scheduler paths, channel config — with **zero hardcoded paths** on macOS, Windows, and Linux. The harness-agnostic capability (#3648/#3649 `grip-harness`) already exists; the wizard must surface it. Coordinate with A>> (AndreTheart).

## Portability invariant (shared with #24)

A shipped scheduler artifact must be portable to **every** target OS: no `/Users/<name>/`, no `C:\Users\<name>\`, no operator username. Placeholders only; resolved from `$CLAUDE_DIR` / `$env:CLAUDE_DIR` (falling back to `$HOME/.claude` / `$env:USERPROFILE\.claude`) at install. Enforced by `tests/test_no_hardcoded_operator_paths.py`.
