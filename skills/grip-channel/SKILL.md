---
name: grip-channel
description: Bridge WhatsApp and Slack messages into Claude Code sessions via the CC Channels API. Self-messaging pattern for remote GRIP control.
effort: low
status: Active
version: 1.0.0
triggers:
  - "/channel"
  - "channel status"
  - "whatsapp bridge"
  - "slack bridge"
integrates:
  - grip-channel MCP server
  - permission-relay
see_also:
  - asking-users
  - creating-wizards
  - hitl
  - pair-mode
---

# GRIP Channel

Bridge external messaging platforms (WhatsApp, Slack) into active Claude Code sessions using the CC Channels API (v2.1.80+).

## Launch

```bash
# Default (GRIP_CHANNELS_FLAG=off) — HITL tools available, no inbound relay
grip fresh latest 2000

# Opt-in (inbound relay enabled, HITL tools unavailable — see warning below)
GRIP_CHANNELS_FLAG=force grip fresh latest 2000
```

**Trade-off (anthropics/claude-code#39342):** Claude Code ≥ 2.1.75
globally suppresses `AskUserQuestion`, `ExitPlanMode`, and
`EnterPlanMode` whenever `--dangerously-load-development-channels`
is active. GRIP defaults the flag off to preserve HITL. See
`rules/operational-context.md` → **GRIP_CHANNELS_FLAG** for the full
trade-off and upstream tracking.

The server is registered in `~/.claude/.mcp.json` as `grip-channel`
(streamable-http transport). Outbound tools (`channel_reply`,
`channel_status`, etc.) work in both modes because the MCP server
remains registered regardless of the flag — only the inbound relay
pathway is gated.

## Architecture

```
WhatsApp/Slack --> Bridge --> Router --> MCP notification
                                    --> <channel source="grip-channel" ...> in session
```

- **MCP Server**: `mcp-servers/grip-channel/` (raw Server class for notification support)
- **WhatsApp bridge**: Baileys main branch, self-messaging (`fromMe` filter)
- **Slack bridge**: @slack/web-api WebClient, conversations.history polling
- **Permission relay**: Forward tool approval prompts to phone (y/n {id})
- **Sender allowlist**: `config/channel-allowlist.json`

## Commands (via WhatsApp/Slack)

| Command | Permission | Action |
|---------|-----------|--------|
| /status | COMMANDS | Show GRIP status |
| /coffee | COMMANDS | Coffee diagnostic |
| /recall | COMMANDS | KONO memory recall |
| /gitgo | COMMANDS | Git health check |
| /sprint-status | COMMANDS | Sprint dashboard |
| /grip-doctor | COMMANDS | System diagnostics |
| /mode | COMMANDS | Mode switcher |
| /save | ADMIN | Persist session state |
| /create-pr | ADMIN | Create pull request |
| /shipit | ADMIN | Batch commit-push-PR |
| /rsi | ADMIN | Start RSI loop |
| /converge | ADMIN | Convergence engine |
| /evolve | ADMIN | Evolution pipeline |
| /broly | ADMIN | Meta-agent |
| Plain text | COMMANDS | Context injection |

## MCP Tools

| Tool | Description |
|------|-------------|
| `channel_reply` | Send message to WhatsApp JID or Slack channel |
| `channel_status` | Show bridge connection status for all platforms |

## Permission Relay

When Claude needs tool approval, the relay:
1. Sends formatted prompt to WhatsApp with 5-letter request ID
2. User replies `yes {id}` or `no {id}` (also accepts `y`/`n`)
3. Verdict forwarded to CC, whichever answer (terminal or remote) arrives first wins

Requires `claude/channel/permission` capability (declared in server).

## Security

- Pre-approved senders only (allowlist)
- Rate limiting: sliding 60-second window per sender
- Permission levels: read_only, commands, permission, admin
- Self-messaging pattern: WhatsApp only processes `fromMe` messages

## Environment Variables (Slack)

| Variable | Description |
|----------|-------------|
| `GRIP_SLACK_BOT_TOKEN` | Slack bot OAuth token |
| `GRIP_SLACK_CHANNEL_ID` | Channel to poll for inbound messages |

## Test Suite

```bash
cd ~/.claude/mcp-servers/grip-channel && npm run test
```

36 tests across 5 files covering allowlist, router, permission-relay, WhatsApp bridge, and Slack bridge.
