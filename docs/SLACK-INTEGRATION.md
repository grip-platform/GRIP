# Slack Integration Guide

Connect Claude Code to your Slack workspace using the official Slack MCP server.

## Overview

This integration uses `@modelcontextprotocol/server-slack` via Claude Code's native MCP support. Once connected, Claude can read channel history, post messages, search conversations, and interact with your Slack workspace directly from a session.

### Available Tools

| Tool | Description |
|------|-------------|
| `slack_list_channels` | List public channels in the workspace |
| `slack_post_message` | Post a message to a channel |
| `slack_reply_to_thread` | Reply to a specific thread |
| `slack_add_reaction` | Add an emoji reaction to a message |
| `slack_get_channel_history` | Read recent messages from a channel |
| `slack_get_thread_replies` | Read all replies in a thread |
| `slack_search_messages` | Search messages across channels |
| `slack_get_users` | List users in the workspace |

## Prerequisites

- Claude Code installed and working
- Slack workspace admin access (to create an app and install it)
- Node.js / npx available (for the MCP server)

## Step 1: Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** > **From scratch**
3. Enter an app name (e.g., "Claude Code") and select your workspace
4. Click **Create App**

## Step 2: Configure Bot Token Scopes

1. In the app settings sidebar, go to **OAuth & Permissions**
2. Scroll to **Bot Token Scopes**
3. Add the following scopes:

| Scope | Purpose | Required |
|-------|---------|----------|
| `channels:history` | Read messages from public channels | Yes |
| `channels:read` | List and get info about public channels | Yes |
| `chat:write` | Post messages to channels | Yes (omit for read-only) |
| `reactions:write` | Add emoji reactions to messages | No |
| `users:read` | List workspace users | Yes |
| `users.profile:read` | Read user profile information | No |

**For read-only access**: Only add `channels:history`, `channels:read`, `users:read`.

## Step 3: Install App to Workspace

1. In the sidebar, go to **Install App**
2. Click **Install to Workspace**
3. Review the permissions and click **Allow**
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

## Step 4: Find Your Team ID

Your Team ID (starts with `T`) identifies your Slack workspace.

**Method 1 - Browser URL**: Open Slack in a browser. The URL contains the Team ID:
```text
https://app.slack.com/client/T01234ABCDE/C0EXAMPLE01
                               ^^^^^^^^^^^
                               This is your Team ID
```

**Method 2 - Workspace settings**: Open Slack desktop > Settings > About this workspace

## Step 5: Connect via GRIP

### Option A: Use the wizard

```text
/connect-slack
```

Follow the interactive prompts.

### Option B: Manual installation

**Basic (all public channels):**

```bash
claude mcp add --scope user --transport stdio \
  -e SLACK_BOT_TOKEN=xoxb-your-token-here \
  -e SLACK_TEAM_ID=T01234ABCDE \
  slack -- npx -y @modelcontextprotocol/server-slack
```

**Restricted to specific channels (recommended for production):**

```bash
claude mcp add --scope user --transport stdio \
  -e SLACK_BOT_TOKEN=xoxb-your-token-here \
  -e SLACK_TEAM_ID=T01234ABCDE \
  -e SLACK_CHANNEL_IDS=C0EXAMPLE01,C56789FGHIJ \
  slack -- npx -y @modelcontextprotocol/server-slack
```

To find channel IDs: right-click a channel in Slack > **View channel details** > scroll to the bottom.

## Step 6: Verify and Restart

```bash
# Verify the server is registered
claude mcp list

# Expected output includes:
# slack: npx -y @modelcontextprotocol/server-slack
```

**Restart Claude Code** for the MCP server to connect.

## Usage Examples

Once connected and restarted, you can ask Claude:

- "List the channels in our Slack workspace"
- "Show me the last 20 messages from #engineering"
- "Post 'Deploy complete for v2.1.0' to #releases"
- "Search Slack for messages about the auth bug"
- "Who posted in #general today?"
- "Reply to that thread with a summary"

Claude Code will prompt for confirmation before executing each Slack action (posting, reacting, etc.) via its default permission model.

## Security Considerations

1. **Token storage**: Tokens are stored in `~/.claude.json` under `mcpServers`, the same location as other MCP credentials (e.g., GitHub token)
2. **Channel restriction**: Use `SLACK_CHANNEL_IDS` to limit which channels Claude can access. Recommended for production workspaces
3. **Minimum scopes**: Only add the scopes you need. Omit `chat:write` and `reactions:write` for read-only access
4. **Permission model**: Claude Code prompts for user confirmation before each MCP tool execution. No messages are sent without your approval
5. **Token rotation**: To rotate the token, disconnect and reconnect with a new token

## Troubleshooting

### Server not appearing after install

1. Ensure you ran the `claude mcp add` command (not npm install)
2. Restart Claude Code completely
3. Run `claude mcp list` to verify
4. Check `~/.claude.json` for the `mcpServers.slack` entry

### "not_authed" or "invalid_auth" errors

1. Verify the bot token starts with `xoxb-`
2. Ensure the app is installed to the correct workspace
3. Check that the Team ID matches the workspace where the app is installed
4. Disconnect and reconnect: `claude mcp remove slack` then re-add

### Bot cannot read a channel

1. The bot must be a member of the channel, or the channel must be public
2. For private channels, invite the bot: `/invite @Claude Code` in Slack
3. Private channel access requires the `groups:history` and `groups:read` scopes (not included by default)

### Bot cannot post to a channel

1. Ensure `chat:write` scope is configured
2. For channels where the bot is not a member, add the `chat:write.public` scope
3. Or invite the bot to the channel: `/invite @Claude Code`

## Disconnect

```bash
claude mcp remove slack
```

Then restart Claude Code. This removes the server configuration from `~/.claude.json`.

## See Also

- `/connect-slack` - Interactive setup wizard
- `/install-mcp` - General MCP server installation
- [Slack MCP Server](https://github.com/modelcontextprotocol/servers/tree/main/src/slack) - Official repository
- [Slack API](https://api.slack.com/) - Slack developer portal
