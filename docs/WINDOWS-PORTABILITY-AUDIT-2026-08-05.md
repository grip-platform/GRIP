# Windows portability audit — 2026-08-05

**Machine:** A>> (Andre), Windows 11, Git Bash, GRIP repo at `~/.grip`.
**Driven by:** V>> via TeamViewer.
**Why this document exists:** GRIP is being prepared for an open-source AGPL
copyleft release. Every defect below was found on Windows, but most are *not*
Windows bugs — they are portability assumptions that happen to be invisible on
macOS. They must be fixed before release, because the first Windows or Linux
user to clone the repo will hit them on day one.

A note on how to read this: most of these defects share **one shape**. Something
reaches for a Python interpreter by a POSIX name or path — `venv/bin/python`, or
a bare `python3` — Windows either lays it out differently
(`venv/Scripts/python.exe`) or does not provide it at all, and the fallback then
lands on something that *looks* like Python and is not. That single shape accounts
for seven of the fifteen findings, and one of them (D14) alone disables 55 of
GRIP's command-line entry points.

If only one thing is carried into the release work, let it be this: **on Windows,
`python3` is not a Python.**

---

## Summary

| # | Defect | Scope | Severity | Status |
|---|--------|-------|----------|--------|
| D1 | `python3` resolves to a non-functional Microsoft Store stub | Windows | High | Fixed |
| D2 | `grip-doctor` cannot start on Windows | Windows | High | Fixed |
| D3 | `gg++` depends on a shell function that does not exist in most contexts | Windows | High | Fixed |
| D4 | MCP package detector is blind to the Windows venv layout | Windows | High | Fixed |
| D5 | Node diagnosis recommends a fix that cannot apply | All platforms | Low | Fixed |
| D6 | MCP health probe can never run on Windows | Windows | **Critical** | Fixed |
| D7 | `mcp` pip package is unpinned; v2 removed the API GRIP uses | **All platforms** | **Critical** | Fixed |
| D8 | Two server launchers hardcode the POSIX venv path | Windows | High | Fixed |
| D9 | `grip-session-mesh` submodule not checked out | All platforms | Medium | Fixed locally |
| D10 | `grip doctor` reports a false positive on `settings.json` | All platforms | Low | Open |
| D11 | Plaintext API keys for 14 providers in `~/.bashrc` | This machine | **Critical** | Open — needs operator |
| D12 | `~/.claude` and `~/.grip` are unlinked, with stale state in `~/.claude` | Windows | Medium | Open — needs decision |
| D13 | Durable reminders are macOS-only by construction | Windows | Medium | Worked around |
| D14 | **55 `bin/` commands dead — `python3` shebang** | Windows | **Critical** | Fixed |
| D15 | Health probe times out and silently reports everything offline | **All platforms** | High | Fixed |

---

## D1 — `python3` resolves to a Microsoft Store stub

**Symptom.** Nearly every GRIP command run from Git Bash failed with:

```
Python was not found; run without arguments to install from the Microsoft Store,
or disable this shortcut from Settings > Apps > Advanced app settings > App
execution aliases.
```

**Root cause.** Windows ships "App Execution Aliases" in
`AppData/Local/Microsoft/WindowsApps/`. These are symlinks to
`AppInstallerPythonRedirector.exe`. On this machine `python3` resolved *only* to
that stub — there was no real `python3` on the Git Bash PATH at all. The stub is
executable and `command -v python3` finds it happily, but every invocation prints
the message above and exits with code 49.

This is worse than a missing interpreter. A missing one fails loudly at the point
of use; the stub satisfies every existence check and then fails later, reporting a
*Python* problem for what the user typed as a *GRIP* command.

**Fix.** `scripts/lib/venv-utils.sh` — added `_python_answers()`, which verifies a
candidate interpreter actually runs (`"$py" -c ''`) before returning it. The
system-fallback branch now walks candidates and returns the first that answers.

Deliberately a *behavioural* check, not a path check. Rejecting anything under
`WindowsApps/` would also reject a genuine Microsoft-Store Python, which does
work. This asserts the property that makes an interpreter worth returning — that
it runs — rather than the spelling of its location.

**Verification.** `bash tests/test_venv_resolution.sh` → 8 passed, 0 failed. The
existing "falls back to system python" case now resolves to
`/c/Python/Python313/python` instead of the stub.

---

## D2 — `grip-doctor` cannot start on Windows

**Symptom.** `grip-doctor` and `grip-doctor --check` both printed the D1 message
and did nothing.

**Root cause.** `bin/grip-doctor` hardcoded `PY="$CLAUDE_DIR/venv/bin/python"`.
That path does not exist on Windows, so it fell through to `command -v python3` —
the stub.

The consequence was circular. GRIP's activation banner names `grip-doctor --fix`
as the remedy for an unhealthy install, and that command could not run on Windows
at all. A user following the instructions would loop forever.

**Fix.** `bin/grip-doctor` now sources `scripts/lib/venv-utils.sh` and calls
`resolve_venv_python`, which already handles both layouts and is covered by tests.
The historical two-step is retained as a fallback for a partial install, so a
half-updated tree still resolves on POSIX rather than hard-failing.

---

## D3 — `gg++` depends on a shell function

**Symptom.** `gg++ codex` exited 2 with the D1 message. Bare `gg++` worked.

**Root cause.** Two layers.

`bin/gg++` calls `python3 lib/grip_launch_args.py` to classify its arguments. Bare
`gg++` never reaches that call — the script has a deliberate backward-compatibility
shortcut for the no-argument case — so only `gg++ <harness>` exposed the bug.

The existing workaround lived in `~/.bashrc` as a shell *function* that prepends
the venv to PATH before calling through. A function only exists in an interactive
shell that sourced that file. It is therefore absent in exactly the contexts the
script's own header calls out: a script, a Makefile, cron, `bash -c`, and an IDE
terminal that does not spawn a login shell. That is why the failure appeared in
Antigravity but not in a normal terminal.

**Fix.** `bin/gg++` now resolves its own interpreter via `resolve_venv_python` at
the top and uses it for the classifier call. The script is self-sufficient on
every platform and no longer depends on shell state it cannot guarantee.

**Verification.** With the venv *not* on PATH:

- `gg++ codex --dry-run` → exit 0, `harness=codex`
- `gg++ --list` → `claude`, `codex`, `opencode`, `agy`
- `gg++ opencode --qwen max --dry-run` → `harness=opencode model=qwen3.7-max`

---

## D4 — MCP package detector blind to the Windows venv layout

**Symptom.** `grip-doctor` reported *"the 'mcp' python package is missing from the
GRIP venv"* for four servers, immediately after `pip show mcp` confirmed it was
installed.

**Root cause.** `lib/grip_activation_health.py::_venv_has_mcp` globbed
`venv/lib/python*/site-packages/mcp`. POSIX nests site-packages under a version
directory; Windows does not — it is `venv/Lib/site-packages/`, with no `python*`
level for the glob to match. The check could never return true on Windows,
regardless of what was installed.

**Fix.** Both layouts are now checked.

---

## D5 — Node diagnosis recommends an impossible fix

**Symptom.** `grip-run` was reported as *"node dependencies not installed (needs
npm ci)"*. The directory contains no `package.json`, so `npm ci` cannot run.

**Root cause.** The diagnosis assumed every Node server has dependencies.
`grip-run/server.js` requires only Node built-ins (`fs`, `os`, `path`, `readline`,
`child_process`) and ships no manifest.

**Fix.** A missing `package.json` now yields *"the server failed to boot (no node
dependencies required)"*. A missing `node_modules` is only reported when a
manifest actually declares dependencies.

---

## D6 — MCP health probe can never run on Windows (critical)

**Symptom.** `grip-doctor` reported **0/5 servers live** — permanently, on a
machine where at least two were demonstrably connected and serving tools.

**Root cause.** `lib/mcp_health.py::_default_runner` calls
`subprocess.run(["claude", "mcp", "list"])`. On Windows, npm installs the launcher
as `claude.cmd` and `claude.ps1` with **no extensionless executable**.
`subprocess.run` does not go through a shell, and `CreateProcess` does not apply
`PATHEXT`, so the call raises `FileNotFoundError [WinError 2]`.

`check_health` catches that and fails open to UNKNOWN. That design is correct — it
must never produce a false green — but the consequence on Windows was that the
probe had *never executed*, so every server read OFFLINE forever and the
activation banner could never be cleared.

This was the highest-value finding. It made every other server diagnosis
meaningless, because the health signal underneath them was fabricated from a
subprocess that never ran.

**Fix.** `shutil.which("claude")` applies `PATHEXT` and returns the runnable
`claude.CMD`. On POSIX it resolves to exactly what the bare name would have, so
that platform is unchanged. `or "claude"` preserves the original argv — and
therefore the expected `OSError` — when the launcher is genuinely absent.

**Verification.** `grip-doctor --check` went from `0/5 live` to `2/5 live`
immediately, with no change to any server.

---

## D7 — `mcp` pip package unpinned; v2 removed the API GRIP uses (critical, all platforms)

**This is the most serious finding for the open-source release, and it is not a
Windows bug.**

**Symptom.** After installing `mcp`, `code-index` and `grip-hear` still failed with
*"No Python with 'mcp' package found"* — while `pip show mcp` reported it present.

**Root cause.** `mcp` 2.0.0 **removed the `mcp.server.fastmcp` module**. Every GRIP
FastMCP server imports `from mcp.server.fastmcp import FastMCP`. Nothing in the
repository pins the version — `scripts/grip-deps-install.sh` ran
`pip install -q mcp`, and there is no `mcp` entry in `requirements.txt` at all.

So any fresh install performed today, on **any** operating system, resolves to
2.x and breaks both servers.

It is also **non-converging**. The installer's own guard tests
`import mcp.server.fastmcp` — the very module 2.x removed. So the check reports
"missing", the installer reinstalls 2.x, and the check reports "missing" again.
The fix keeps reapplying the cause. A user could run `grip-doctor --fix` forever.

**Fix.** `scripts/grip-deps-install.sh` now installs `"mcp<2"`, with the reasoning
recorded inline so nobody silently unpins it later.

**Verification.** `mcp` downgraded to 1.29.0; `from mcp.server.fastmcp import
FastMCP` succeeds; both servers complete an MCP `initialize` handshake.

**Release action.** Add `mcp<2` to `requirements.txt` as the declared dependency
rather than leaving it implicit in a shell script. Migrating to the 2.x API is a
separate piece of work with its own tests.

---

## D8 — Server launchers hardcode the POSIX venv path

**Symptom.** As D7's symptom, and masking it.

**Root cause.** `mcp-servers/code-index/start.sh` and
`mcp-servers/grip-hear/start.sh` both set `VENV_PY="$CLAUDE_DIR/venv/bin/python"`.
On Windows that path is absent, so the launcher skipped the venv entirely and fell
through to a PATH cascade beginning with `python3` — the D1 stub. Both files carry
a header claiming *"Cross-platform: macOS (Homebrew), Linux (system/pyenv),
Windows (Git Bash)"*, which was not true of either.

`grip-apps/start.sh` was unaffected because it is a Node server. `linkedin-dma`
and `grip-presence` were already clean.

**Fix.** Both now resolve through `resolve_venv_python`, retaining the old path as
a fallback.

**Verification.** Both servers return valid MCP `initialize` responses.

---

## D9 — `grip-session-mesh` submodule not checked out

**Symptom.** `grip-presence` offline: *"grip-session-mesh submodule is not checked
out"*.

**Fix applied locally.** `git submodule update --init mcp-servers/grip-session-mesh`,
then `npm ci && npm run build` in `presence/`.

**Release action.** The install instructions must use
`git clone --recurse-submodules`, or the installer must run
`git submodule update --init` itself. A plain clone leaves `grip-presence`
permanently broken, and the error only surfaces when the server is first probed.

---

## D10 — `grip doctor` false positive on `settings.json` (open)

`grip doctor` warns *"settings.json contains ~/ paths which may not work on
Windows"*. It does not. The only `~/` occurrences are inside a documentation
string (`_claudeMdExcludes_doc`), where they are prose — "removing ~80K from the
~208K preload" — not paths. No configuration value uses `~/`.

The detector should inspect JSON *values*, not raw file text. Low severity, but it
is a false alarm in the exact tool users will run first, and false alarms train
people to ignore real ones.

---

## D11 — Plaintext API keys in `~/.bashrc` (open, needs operator)

`~/.bashrc` exports plaintext credentials for **14 providers**, including
Anthropic (`HAL_GATEWAY_FALLBACK_KEY`), OpenAI, xAI, Groq, Gemini, DeepSeek,
Moonshot, Zhipu, Mistral, OpenRouter, DashScope, SoruxGPT, Vulavula and Arcee.

The file is outside the repository, so nothing is committed and there is no leak
into git history — that was checked. But the keys sit unencrypted on disk, they
are readable by any process running as this user, and they were exposed to an
assistant session in the course of this work.

**Recommended:** rotate the Anthropic and OpenAI keys, then migrate all of them
behind `bin/grip-keyring` so the shell profile carries no secrets. Not actioned —
this is the operator's call.

---

## D12 — `~/.claude` and `~/.grip` are unlinked (open, needs decision)

On V>>'s Mac, `~/.claude` is a symlink to the shared working tree `~/.grip`, and
GRIP's rules assume that arrangement.

On this machine they are two separate real directories:

- `~/.grip` — the git repository, 145 entries, live (written today).
- `~/.claude` — a plain directory, 21 entries, **stale since 2026-07-15**, holding
  an older Claude Code state tree (`projects/`, `sessions/`, `venv/`).

Nothing is currently broken, because `CLAUDE_DIR` and `CLAUDE_CONFIG_DIR` are both
set to `~/.grip` in `~/.bashrc` and Claude Code honours them. The env vars are
standing in for the missing symlink.

The residual risk is that any tool which ignores those variables and resolves
`$HOME/.claude` directly will silently read three-week-old state. `hooks/session-end.sh`
already does exactly this (`GRIP_PYTHON="${HOME}/.claude/venv/bin/python3"`).

**Options, for the operator to choose:**

1. Replace `~/.claude` with a directory symlink to `~/.grip` (matches the Mac, and
   fixes hardcoded `$HOME/.claude` consumers). Requires removing or archiving the
   stale directory first — destructive, so not done unattended.
2. Keep the env-var override and fix hardcoded consumers to use `CLAUDE_DIR`.
   Non-destructive, but the class of bug recurs whenever someone writes
   `$HOME/.claude` again.

Recommendation is option 1 with the stale directory archived rather than deleted,
but this needs an explicit go-ahead.

---

## D13 — Durable reminders are macOS-only by construction

**Symptom.** `/remind-me` could not arm anything on Windows.

**Root cause.** `bin/grip-remind` is macOS-only in three independent ways:
`AGENTS_DIR = ~/Library/LaunchAgents`, `plistlib`, and
`launchctl bootstrap gui/{os.getuid()}` — and `os.getuid()` does not exist on
Windows at all. There is no `schtasks` path. `launchctl` is absent here;
`schtasks` is present.

This matters more than it looks. The whole point of `/remind-me` is that a
reminder outlives the session. On Windows it could not be armed at all, so the
guarantee was not degraded — it was absent.

**Worked around, not fixed.** A durable reminder was armed directly through
Windows Task Scheduler (`Register-ScheduledTask`, job `GRIP-Remind-VA-Chat`,
`-StartWhenAvailable` so a missed fire is delivered on wake), with a local
desktop dialog and an audit row in `logs/reminders.jsonl`.

Note `schtasks /TR` mangles a command string containing a quoted path with
spaces — `C:\Users\Andre Theart\...` fails. The `ScheduledTasks` cmdlets take the
executable and its arguments structurally and are the reliable route.

**Release action.** Give `grip-remind` a Windows backend, selected on
`sys.platform`, using `Register-ScheduledTask`. Keep the payload in a file rather
than an inline argument string: the quoting only bites at fire time, which is the
one moment a reminder must not fail.

Delivery is also worth revisiting — tier 1 hard-wires WhatsApp, which is
unavailable on any host where the WhatsApp bridge is disabled (this one). The
skill's own falsification clause already anticipates routing through
`lib/grip_notify.py`'s multi-backend facade instead.

---

## D14 — 55 `bin/` commands are dead on Windows (critical)

**The single largest defect by blast radius.**

**Symptom.** `./bin/grip-backup --where` → *"Python was not found..."*. Same for
`grip-remind`, and 53 others.

**Root cause.** 55 files under `bin/` begin with `#!/usr/bin/env python3`. Per D1,
Git Bash on Windows has no real `python3` — only the Store stub. So `env` finds
the stub, the stub refuses to be Python, and the command dies. That is roughly
half of GRIP's command-line surface, including `grip-backup`, `grip-remind`,
`grip-mode`, `grip-yolo`, `grip-council`, `grip-cache` and `grip-llm-usage`.

This defect *masked itself*. Each failure reports a Python problem, so it reads as
a broken Python installation rather than as a GRIP portability bug — and the
machine does have working Python (`python`, and the venv), which makes the report
look wrong rather than informative.

**Fix.** A machine-local shim at `~/.local/bin/python3`, prepended to `PATH` in
`~/.bashrc` ahead of `WindowsApps/`. It execs the GRIP venv interpreter, falling
back to a system install that it verifies actually runs.

Deliberately **not** committed to the repository. On macOS and Linux `python3` is
real; a committed shim would shadow it and point at a `venv/Scripts/` that does
not exist there. It belongs to the machine.

Prepending `venv/Scripts` to `PATH` would also have worked, in one line — but it
activates the entire virtualenv for every Git Bash shell, shadowing `pip` and the
rest for unrelated Python work. The shim changes exactly one name.

**Verification.** `python3 -V` → 3.13.3 via the shim; `./bin/grip-backup --where`
and `./bin/grip-remind --list` both run correctly via their shebangs.

**Release action.** The installer should create this shim on Windows, and the
install documentation should state the requirement plainly. The alternative —
telling every user to disable App Execution Aliases in Windows Settings — is a
manual step that silently returns after some Windows updates.

---

## D15 — The health probe times out and reports everything offline (all platforms)

**Found last, while writing up the session — and it invalidated the session's own
headline result until fixed.**

**Symptom.** A final verification run reported **0/5 live** again, hours after the
same command had reported 5/5.

**Root cause.** Not a regression. A direct probe returned `Connected` for all five
servers, `rc=0`. The difference was the timeout.

`claude mcp list` opens a fresh connection to **every configured server**, so its
cost scales with how many exist — not with how many the caller asked about. This
machine has 15 configured, most of them remote HTTP (Slack, Gmail, Drive, Zoom,
Notion). Measured: **44.1 seconds**. `check_health` allowed **20**.

So `TimeoutExpired` was raised, caught by the same fail-open handler as D6, and
the probe returned `{}` — rendering as "every server is OFFLINE".

Worse, each UNKNOWN server was then passed to `diagnose()`, which **invented a
reason**: five healthy, connected servers were each reported as *"python deps
present but the server failed to boot"*. That is a fabricated diagnosis of a
non-existent fault.

**This is D6's lesson recurring inside the same session**, which is the strongest
possible argument for taking it seriously. D6 was "the probe could never run"; D15
is "the probe ran out of time". Both rendered identically: a confident, specific,
wrong claim that everything was down.

The earlier 5/5 result in this session was genuine but fragile — the probe
happened to finish inside 20 seconds at that moment. It would not have reproduced
reliably on any machine with a similar number of servers.

**Fix, in two parts** — the second matters more than the first:

1. `check_health` default timeout raised 20s to 120s, with the scaling reasoning
   recorded inline so nobody trims it back to a number that looks tidier.
2. `run_activation` no longer calls `diagnose()` on an UNKNOWN server. It reports
   *"could not verify — the MCP health probe did not return a status"*. **Unknown
   and offline are now distinguishable in the output**, which is what would have
   made both D6 and D15 visible in minutes rather than hours.

**Verification.** `check_health` at the 20s default returns 0 servers; at 120s it
returns all five as `connected`. `grip-doctor --check` now exits 0 with 5/5,
repeatably.

---

## Cross-platform lessons for the AGPL release

1. **Never hardcode a venv interpreter path.** Route every one through
   `resolve_venv_python`. 25 files under `bin/` still hardcode
   `venv/bin/python`; the hot paths are fixed, the rest remain.
2. **Never invoke `claude`, `npm`, `node` or any npm-installed launcher by bare
   name from `subprocess`.** Resolve with `shutil.which` first, or Windows raises
   `FileNotFoundError` because `CreateProcess` does not apply `PATHEXT`.
3. **Pin dependencies whose API you import.** D7 would have been impossible with a
   declared, pinned requirement.
4. **A fail-open health probe must be able to report that it never ran.** D6 was
   invisible for as long as it existed because "probe failed" and "everything is
   offline" rendered identically. Distinguish *unknown* from *down* in the output,
   not just internally.

   **This lesson recurred within the same session** (D15) — the probe that could
   not run was fixed, then the probe that ran out of time produced the identical
   wrong answer. Failing open is right; failing open *silently* is not. And never
   run a diagnostic explainer over an UNKNOWN result: it will invent a specific,
   confident reason for a fault that does not exist, which is worse than saying
   nothing.

6. **A timeout is a scaling assumption in disguise.** 20 seconds was a reasonable
   default when GRIP had few MCP servers. It became wrong at fifteen, because the
   probe's cost scales with configuration the caller does not control. Any fixed
   timeout over a variable-sized workload will eventually be wrong, and it will
   fail on the biggest, busiest installation — the one you least want it to fail
   on.
5. **Existence checks are not capability checks.** The Store stub exists, is
   executable, and is not Python. Assert that a thing answers, not that it is
   present.

---

## Verified state

```
grip-doctor --check
  ✔ code-index    ONLINE
  ✔ grip-hear     ONLINE
  ✔ grip-apps     ONLINE
  ✔ grip-run      ONLINE
  ✔ grip-presence ONLINE
  ████████████████████  5/5 live
```

From a permanent, structurally-impossible 0/5 to 5/5.

- `gg++` works for `claude`, `codex`, `opencode` and `agy`, including model and
  effort selection, with the venv off `PATH` and no shell function present.
- All 55 `python3`-shebang commands under `bin/` run.
- `tests/test_venv_resolution.sh` — 8 passed, 0 failed.

### Provider pools

Pool B and Pool C are live on Windows once D14 is fixed — they were blocked by it,
not by anything provider-side. `grip-sovereign-council` convenes four
non-Anthropic speakers plus a non-Anthropic judge and returns real answers
(deepseek-v4-pro responded in 6.3 s). HAL's CLI runs. Pool A is the Opus session
itself.

One rough edge: `grip-sovereign-council --help` is treated as a *question* and
sent to the council rather than printing usage. Harmless but wasteful, and it is
the same shape as the `/ultrareason` bug where the literal word "council" was
passed as the prompt.

### Terminal

Windows Terminal was already installed but had **no Git Bash profile** — only
PowerShell, Command Prompt and the VS developer shells. That is the likely reason
GRIP was being run inside Antigravity's embedded terminal, which is where the
`gg++` failures were first seen.

A `GRIP (Git Bash)` profile has been added, starting in `~/.grip` and invoking
`bash -i -l`. The `-l` is load-bearing: Git Bash only reads `~/.bash_profile`, and
through it `~/.bashrc`, for a **login** shell — and that is where `PATH`,
`CLAUDE_DIR` and the D14 shim live. A terminal that spawns `bash` without `-l`
gets none of it, which is precisely the Antigravity failure mode.

The default profile was left unchanged.

Warp is the strongest candidate if a richer terminal is wanted: it now ships a
native, stable Windows build (Rust, GPU-rendered) and drives PowerShell, Git Bash
and WSL. It is the closest Windows analogue to Otty, which V>> uses on macOS and
which is macOS-only with Windows on a waitlist. It is not installed — it requires
account creation and its built-in AI overlaps with Claude Code, so that is a
choice for the operator rather than an unattended install.
