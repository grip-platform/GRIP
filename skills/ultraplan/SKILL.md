---
name: ultraplan
description: Context-aware planning in a cloud session. Builds a self-contained prompt from this session's context and the task, then launches it with claude --cloud (the default) or as a one-time RemoteTrigger routine.
command: /ultraplan
effort: low
version: 2.0.0
triggers:
  - /ultraplan
  - ultraplan
---

# Ultraplan -- context-aware planning in the cloud

Plans in a fresh cloud session, and carries this session's context into the prompt so
the cloud planner does not start cold. `lib/ultraplan_remote.py` builds both launch
shapes and checks them. It never creates a routine and never calls the network itself.

## What the cloud offers, and what it does not (read first)

Measured 2026-10-02 against Claude Code 2.1.283 and the vendor pages under Sources.

- **There is no "session from a prompt" API.** `RemoteTrigger(action="create")` saves a
  *routine*; a session exists only when that routine fires. The body this skill used to
  document, `{"name", "prompt", "max_turns"}`, never worked: the API answers `One of
  job_config or session_request must be set`, and `prompt` and `max_turns` are not API
  fields (GRIP#6859).
- **The API cannot delete a routine** (anthropics/claude-code#83066, closed as not
  planned). So the routine path only ever builds a one-time routine (`run_once_at`),
  which fires once and then disables itself. It never uses `cron_expression`, which would
  keep firing with nothing here able to stop it.
- **A cloud clone does not have GRIP.** It loads the repository's `CLAUDE.md` and its
  `.claude/` folder (skills, agents, commands, rules, and hooks in `.claude/settings.json`),
  and nothing from your machine. GRIP keeps its skills, agents, commands and hooks at the
  repository root, because the repository *is* `~/.claude`, so none of them exist in the
  cloud: no `/refresh-context`, no context-refresh agent, no GRIP hooks, no memory. Write
  the prompt so it needs none of them.
- **git-crypt files are ciphertext there.** Every path matched by a `filter=git-crypt`
  line in `.gitattributes` (33 patterns on 2026-10-02, among them
  `rules/operational-context.md`, `drafts/**` and `facts/*.md`) clones as encrypted bytes.
  The builder refuses a prompt or tool rule that names one.
- **An API credential breaks both paths.** `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`
  takes precedence over the claude.ai login, and `claude --cloud` then fails with "Unable
  to get organization UUID". While the auth-failover marker
  `state/session/auth-failover.json` exists, `~/.zshrc` exports `ANTHROPIC_API_KEY` into
  every new shell, so the builder refuses until a remedy is chosen (operator step 4).

## Usage

```text
/ultraplan                           # plan the remaining work, with context
/ultraplan "design the auth system"  # plan one task, with context
```

## Implementation

1. **Gather context from this conversation**: the task, what was decided, what shipped
   (with PR and commit ids), and what remains. If the conversation holds little, run
   `/recall` first; it reads instance state, recent achievements, active work and the last
   plan. (`rules/session-context.md`, which an earlier version of this skill read, no longer
   exists.) Leave out client data and anything taken from an encrypted file: the prompt is
   stored on the session, and on a routine until the routine is deleted.

2. **Build the prompt** and write it to a file in the session scratchpad:

   ```text
   You are planning in a fresh cloud clone of the GRIP repository
   (github.com/grip-platform/GRIP). The repository root is GRIP's own ~/.claude
   tree, but this session has none of GRIP's skills, agents, hooks or memory --
   only the files in the clone. Files matched by a filter=git-crypt line in
   .gitattributes are ciphertext here: do not try to read them.

   ## Current state
   {decisions, what shipped with PR and commit ids, what remains}

   ## Task
   {the planning request, or "Plan the next optimal steps for the remaining work"}

   ## Instructions
   - Read the files you need yourself, starting with README.md, CLAUDE.md and the paths named above.
   - Do not edit files, commit or open pull requests. Your final message is the plan.
   - Give priorities, dependencies, effort and the first concrete step.
   - Apply KISS, DRY, YAGNI and YSH.
   ```

3. **Launch.** Run the builder under the GRIP venv. It checks the prompt and the failover
   state, then prints what to run.

   **Path 1, the default: `claude --cloud`.** One session, nothing left behind.

   ```bash
   "${CLAUDE_DIR:-$HOME/.claude}/bin/grip-py" "${CLAUDE_DIR:-$HOME/.claude}/lib/ultraplan_remote.py" \
     --prompt-file <scratchpad>/ultraplan-prompt.md
   ```

   It prints `claude --cloud '<prompt>'`. The operator runs that in their own terminal,
   from a clean checkout whose branch is pushed: the cloud VM clones the current branch
   from GitHub, not the local checkout. With `--exec` the builder starts it directly. Do
   not run it through the Bash tool; how `claude --cloud` behaves without a terminal is
   UNVERIFIED. The session then appears at claude.ai/code. Read the plan there, or pull it
   into a terminal with `claude --teleport <session-id>`.

   **Path 2: a one-time routine.** For a run that should start later, or a session that
   cannot hand a command to the operator.

   ```bash
   "${CLAUDE_DIR:-$HOME/.claude}/bin/grip-py" "${CLAUDE_DIR:-$HOME/.claude}/lib/ultraplan_remote.py" \
     --routine --prompt-file <scratchpad>/ultraplan-prompt.md \
     --environment-id <id from operational-context> --run-once-at <UTC, e.g. 2026-10-03T08:07:00Z>
   ```

   It prints the create body in the shape of the bundled `/schedule` skill's "Create body
   shape": `run_once_at`, the read-only tools `Read`, `Grep` and `Glob`, model
   `claude-opus-5-5`, no connectors, and a fresh v4 uuid. Load the tool with
   `ToolSearch select:RemoteTrigger`, then pass the body unchanged:
   `RemoteTrigger(action="create", body=<printed JSON>)`. Take `run_once_at` from
   `date -u`, a few minutes ahead and not exactly on the hour, because on-the-hour runs can
   start several minutes late. It fires once at that time; do not also call
   `RemoteTrigger(action="run")`. A routine clones the repository's default branch, so name
   a branch in the prompt if the plan depends on unmerged work.

4. **Report back.** Path 1: the session link from claude.ai/code. Path 2:
   `https://claude.ai/code/routines/<id>` from the create response. If a run looks stuck,
   `RemoteTrigger(action="list_runs", trigger_id=...)` and `get_run_log` show what
   happened; a run that hangs after "Claude Code process started" is a known upstream
   report (anthropics/claude-code#87787, closed as not planned). A fired one-time routine is
   marked Ran and stays listed: delete it at claude.ai/code/routines.

## Operator steps, recorded not guessed

None of these is recorded in GRIP yet, so the builder cannot know them. Each is the
operator's to do once.

1. **Name the login.** `claude auth status --text` shows the claude.ai account the CLI is
   signed in to, and both paths spend that account's allowance. The two logins on this
   machine swapped on 2026-10-01, so check rather than assume. `--cloud` and `/schedule`
   need a claude.ai login; an API key does not work.
2. **Pick the environment and read its id, BEFORE the first launch attempt** -- the routine
   builder refuses without an id, and `claude --cloud` silently picks one for you if you
   have not chosen. With none on the account, `/web-setup` creates
   "Default" (and connects GitHub through your `gh` token), or create one in the environment
   selector at claude.ai/code. `/schedule` prints your environments with their ids under
   "Available environments"; the web selector is not documented to show them. Use an
   environment made for GRIP work, never one set up for a client. `claude --cloud` takes its
   environment from `/remote-env`; without a pick it falls back to "the Anthropic-hosted
   environment", and which one it chooses when there are two is UNVERIFIED.
3. **Confirm GitHub access.** The GitHub identity connected to that login must be able to
   read `grip-platform/GRIP`. If the Claude GitHub App is not installed on the repository,
   `claude --cloud` does not clone: it uploads a bundle of the local repository, with the
   history of every branch and uncommitted changes to tracked files, even after
   `/web-setup`. Check the App at
   github.com/organizations/CodeTonight-SA/settings/installations, and launch from a
   worktree with no uncommitted changes. Whether a bundle would carry a locally modified
   git-crypt file as plaintext is UNVERIFIED.
4. **Decide about the failover key.** While `state/session/auth-failover.json` exists the
   builder refuses. Either clear it with `grip-auth-failover --reset`, which returns local
   sessions to the subscription as well, or pass `--unset-api-key` so the printed command
   runs under `env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN`. For a routine, the session
   that calls RemoteTrigger must itself be on the claude.ai login, so relaunch it without
   the key if it carries one.
5. **Record the id and the login** in `rules/operational-context.md`, which git-crypt
   encrypts. Never put them in a plaintext file. That includes `settings.json`:
   `/remote-env` writes `remote.defaultEnvironmentId` into user settings, which on a GRIP
   machine is the tracked `settings.json`, so do not commit that key.

## Billing

- **`claude --cloud`** spends the signed-in account's cloud-session credit (the operator's
  plan, 2026-10-01). The vendor docs add that cloud sessions share rate limits with all
  other Claude usage and carry no separate charge for the VM.
- **A routine: UNKNOWN.** The routines page says runs draw down subscription usage the same
  way interactive sessions do, and says nothing about the cloud-session credit. Third-party
  guides say the credit does not cover routines; nothing official confirms that either way.
  Treat it as unknown until a run shows up at claude.ai/settings/usage.

## Fallback

If the builder refuses and no remedy is available now, or the launch fails, plan locally
with the context already gathered.

## Local or remote

- **Local**: when the remaining context can hold both the current mental model and the
  planning output. It already holds the mental model; nothing needs transferring. (An
  earlier draft said "about 40%"; no measurement produced that number, so it is gone.)
- **Remote**: when the context is nearly spent, or the operator wants a fresh session to
  plan in parallel while work continues here.

## Sources

- Cloud sessions and `--cloud`: https://code.claude.com/docs/en/claude-code-on-the-web
- Routines: https://code.claude.com/docs/en/routines
- Cloud environments: https://code.claude.com/docs/en/cloud-environments
- The create body: the "Create body shape" section of the `/schedule` skill bundled with
  Claude Code 2.1.283, copied verbatim into
  `tests/fixtures/ultraplan_remote/schedule-create-body.json` (the `run_once_at` key is
  the one-time rule) and pinned against the installed binary by
  `tests/test_ultraplan_remote.py`
- anthropics/claude-code#83066 (no delete), anthropics/claude-code#87787 (a routine run
  that hangs), GRIP#6859 (the rejected body)
