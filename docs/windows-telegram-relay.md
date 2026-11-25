# GRIP Telegram Relay on Windows — query GRIP from your phone, anywhere

Turn an always-on Windows machine into your personal GRIP host. You message
a private Telegram bot from your phone; the Windows machine answers as GRIP.
This is the Windows/Telegram sibling of the VPS WhatsApp/Slack relay
(`lib/relay_dispatcher.py`) — same architecture, same security model, far
fewer moving parts.

## Why Telegram first (and not WhatsApp)

| | Telegram | WhatsApp |
|---|---|---|
| API | Official Bot API — supported, stable | No official personal API; relays use Baileys (an unofficial WhatsApp Web client) |
| Account risk | None — bots are a first-class feature | Small but real ban risk on the linked number (unofficial client) |
| Pairing | Paste one token string | QR-code device pairing; re-pairing when sessions expire |
| Runtime | One Python file, stdlib only | Node daemon + Baileys + session state |
| Network | Outbound HTTPS long-poll — works behind any NAT/firewall, no open ports | Outbound WebSocket — similar, but more fragile |

WhatsApp remains possible as a phase 2 (port the VPS Baileys bridge to the
Windows box); Telegram gets you phone→GRIP with the least that can break.

## Architecture

```
[Your phone: Telegram app]
      | message to @YourPrivateBot
      v
Telegram Bot API (cloud)
      ^ long-poll getUpdates — OUTBOUND https only,
      | no inbound port, works on any home network
[Windows machine: lib/telegram_relay.py  (Scheduled Task, auto-restart)]
      | 1. sender allowlist: only YOUR enrolled Telegram user id
      | 2. spawn per message:
      |      claude -p --output-format json
      |             --dangerously-skip-permissions --strict-mcp-config
      |             --tools=Read,Grep,Glob        <- capability lock
      |      cwd = %USERPROFILE%\.grip            <- GRIP-grounded answers
      v
reply -> sendMessage back to your chat (chunked to Telegram's 4096 limit)
```

Session continuity: each chat keeps a per-chat Claude session (`--resume`),
so follow-up questions have context. `/fresh <question>` starts clean. A
transcript that grows past 2 MB is dropped in favour of a fresh session
(resuming huge transcripts is slow and failure-prone — same cap as the VPS
relay).

## Security model (three independent layers)

1. **Only your bot.** The bot token is secret, stored only at
   `%USERPROFILE%\.grip\state\telegram-relay\config.json` — a gitignored
   path (`state/**/*.json`), so it can never be committed.
2. **Only you.** The relay starts with an empty allowlist plus a one-time
   6-digit enroll code printed by the setup script. The first message that
   exactly matches the code enrolls that Telegram account; the code is then
   cleared. Everyone else is ignored silently (and logged). With neither an
   allowlist nor a code, the relay refuses to start at all (fail-closed).
3. **Only read-only capabilities.** Message content is never trusted
   (`rules/untrusted-content-capability-lock.md`): the spawned Claude gets
   `--tools=Read,Grep,Glob` — no Bash, no Write/Edit, no agents, no web.
   Forwarded or malicious text physically cannot run commands or modify the
   machine. `--dangerously-skip-permissions` only suppresses interactive
   prompts (there is no TTY); the tool allowlist is the orthogonal,
   load-bearing control. Widening it is a deliberate config edit (see
   below), never something a message can cause.

## What you need

- A Windows 10/11 machine that stays powered on and logged in (the relay
  runs as a Scheduled Task in your user session).
- A Claude subscription login (the relay bills your account; default model
  is Haiku for cheap phone turns — change `model` in config if you want
  heavier answers).
- Access to the GRIP GitHub repository (the setup clones it).
- Telegram on your phone.

## Setup (about 10 minutes, one paste + two phone messages)

1. **Phone:** in Telegram, message **@BotFather** → `/newbot` → pick a
   name/username → copy the token.
2. **Windows:** copy `scripts/windows-telegram-relay-setup.ps1` onto the
   machine (TeamViewer file transfer works) and in PowerShell run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\windows-telegram-relay-setup.ps1
   ```

   The script installs git/Python/Claude Code, walks you through the
   one-time Claude login and GitHub sign-in, asks for the bot token,
   writes the config with a fresh enroll code, and registers + starts the
   Scheduled Task. Pass `-GripBranch <branch>` if the relay code has not
   merged to `main` yet.
3. **Phone:** send the 6-digit enroll code the script printed to your bot.
   Expect “Enrolled.” Then `/ping`, then ask anything.

## Config reference (`state\telegram-relay\config.json`)

| Key | Default | Meaning |
|---|---|---|
| `bot_token` | — | From @BotFather. Secret. |
| `allowed_user_ids` | `[]` | Filled by enrollment. Add ids manually to allow more people. |
| `enroll_code` | set by setup | One-time code; cleared after first successful enrollment. |
| `grip_dir` | `~/.grip` | Spawn cwd — what makes answers GRIP-grounded. |
| `model` | `claude-haiku-4-5-20251001` | Per-message model. Set e.g. `claude-sonnet-5` for heavier answers, `off` for your account default. |
| `tools` | empty → `Read,Grep,Glob` | Capability allowlist. `Read,Grep,Glob,Write,Edit` allows document work; `full` removes the lock entirely — only sane because the bot answers you alone, and still your explicit decision. |
| `spawn_timeout_s` | `300` | Per-message ceiling; timeouts return an honest message. |
| `system_prompt` | built-in | Override the phone-style answer instructions. |
| `claude_bin` | auto (`PATH`) | Explicit path to the claude CLI if needed. |

Config is re-read per message — edits (including widening `tools`) take
effect without restarting the task.

## Operations

```powershell
python "$env:USERPROFILE\.grip\lib\telegram_relay.py" --status   # config, enrolment, offset, recent events
Get-Content "$env:USERPROFILE\.grip\state\telegram-relay\relay-log.jsonl" -Tail 20
Start-ScheduledTask -TaskName "GRIP Telegram Relay"              # (re)start
Stop-ScheduledTask  -TaskName "GRIP Telegram Relay"              # stop
git -C "$env:USERPROFILE\.grip" pull --rebase                    # update GRIP + relay
```

The loop never exits on transient failures: network errors and Telegram
429s back off (capped 60 s) and continue, per
`rules/rate-limit-never-blocks.md`. Telemetry (never the token — it is
redacted) appends to `relay-log.jsonl`, rotated at 5 MB.

## Optional: let GRIP drive the Windows box remotely

TeamViewer is a GUI tool, so a GRIP session cannot operate it. If you want
GRIP (from another machine) to maintain this box hands-on, enable OpenSSH
Server once, in an **elevated** PowerShell:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service sshd -StartupType Automatic
Start-Service sshd
```

Pair it with Tailscale (`winget install -e --id tailscale.tailscale`) on
both machines and GRIP can `ssh <user>@<tailscale-ip>` from anywhere —
no port forwarding, no public exposure.

## WhatsApp (phase 2)

The repo already carries the WhatsApp stack (`lib/whatsapp_transport.py`,
`lib/channel/adapters/whatsapp.py`) used on
the Mac/VPS. Porting it to the Windows host means: Node LTS, the Baileys
client as a second Scheduled Task, one interactive QR pairing against your
WhatsApp account, and pointing its outbound at the same capability-locked
spawn this relay uses. Ask GRIP for “WhatsApp phase 2 on the Windows relay”
when you want it.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Bot never replies, log shows `poll_error ... 409` | Two relay instances are polling the same bot. Stop the extra one (`Stop-ScheduledTask`), or find a stray `pythonw.exe` in Task Manager. |
| `claude`/`python` not found right after install | winget updated PATH for new shells only. Open a new PowerShell and re-run the script. |
| Replies say the CLI could not start | Claude login missing on this machine — run `claude` once and `/login`. |
| First message from a friend gets no answer | Correct — they are not enrolled. Add their Telegram id to `allowed_user_ids` if you want that. |
| Machine rebooted, no replies until login | The task runs at user logon. Enable Windows auto-logon for this user (or convert the task to run whether logged in or not) if the box is headless. |
