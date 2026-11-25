# Connect Slack Command

Guided opt-in wizard to connect a Slack workspace to Claude Code via the official Slack MCP server.

## Usage

```text
/connect-slack
```

## Flow

### Step 1: Check Current Status

Run `claude mcp list` and check if `slack` is already configured.

- **If connected**: Use AskUserQuestion with options: "Verify connection", "Reconfigure", "Disconnect"
  - Verify: Run `claude mcp list` and report status
  - Reconfigure: Run `claude mcp remove slack` then continue to Step 2
  - Disconnect: Run `claude mcp remove slack`, confirm removal, done
- **If not connected**: Continue to Step 2

### Step 2: Bot Token Check

Use AskUserQuestion:

**Question**: "Do you have a Slack Bot Token (xoxb-...)?"

| Option | Action |
|--------|--------|
| Yes, I have one | Proceed to Step 4 |
| No, help me create one | Display Step 3 instructions |

### Step 3: Slack App Creation Guide

Display these instructions:

```text
Create a Slack App:

1. Open https://api.slack.com/apps
2. Click "Create New App" > "From scratch"
3. Name it (e.g., "Claude Code") and select your workspace
4. Go to "OAuth & Permissions" in the sidebar
5. Under "Bot Token Scopes", add:
   - channels:history (read channel messages)
   - channels:read (list channels)
   - chat:write (post messages)
   - reactions:write (add reactions)
   - users:read (list users)
   - users.profile:read (read user profiles)
6. Click "Install to Workspace" and authorize
7. Copy the "Bot User OAuth Token" (starts with xoxb-)
8. Find your Team ID: open Slack in browser, it's the T... value in the URL
   (e.g., https://app.slack.com/client/T01234ABCDE/...)
```

Then proceed to Step 4.

### Step 4: Collect Credentials

Use AskUserQuestion to collect:

1. SLACK_BOT_TOKEN (the xoxb-... token)
2. SLACK_TEAM_ID (the T... workspace ID)

**IMPORTANT**: These are secrets. Do not echo them back in plain text. Mask in output.

### Step 5: Channel Restriction

Use AskUserQuestion:

**Question**: "Restrict access to specific channels? (Recommended for production)"

| Option | Action |
|--------|--------|
| Yes, specific channels | Ask for comma-separated channel IDs (C...) |
| No, all public channels | Skip channel restriction |

### Step 6: Install

Run the appropriate command:

**Without channel restriction:**

```bash
claude mcp add --scope user --transport stdio \
  -e SLACK_BOT_TOKEN=xoxb-... \
  -e SLACK_TEAM_ID=T... \
  slack -- npx -y @modelcontextprotocol/server-slack
```

**With channel restriction:**

```bash
claude mcp add --scope user --transport stdio \
  -e SLACK_BOT_TOKEN=xoxb-... \
  -e SLACK_TEAM_ID=T... \
  -e SLACK_CHANNEL_IDS=C123,C456 \
  slack -- npx -y @modelcontextprotocol/server-slack
```

### Step 7: Verify and Inform

1. Run `claude mcp list` to confirm `slack` appears
2. Inform user: **"Restart Claude Code for the Slack MCP server to take effect."**
3. After restart, available Slack tools:
   - `slack_list_channels` - List channels
   - `slack_post_message` - Post a message
   - `slack_reply_to_thread` - Reply in a thread
   - `slack_add_reaction` - Add emoji reaction
   - `slack_get_channel_history` - Read channel history
   - `slack_get_thread_replies` - Read thread replies
   - `slack_search_messages` - Search messages
   - `slack_get_users` - List workspace users

## Disconnect

```bash
claude mcp remove slack
```

Then restart Claude Code.

## Security Notes

- Tokens are stored in `~/.claude.json` under `mcpServers` (same as GitHub token)
- Claude Code prompts for confirmation before each MCP tool execution
- Use channel restriction (`SLACK_CHANNEL_IDS`) for production workspaces
- For read-only access, omit `chat:write` and `reactions:write` scopes from the Slack app

## See Also

- `~/.claude/docs/SLACK-INTEGRATION.md` - Full setup guide
- `~/.claude/mcp-registry.json` - MCP server registry
- `/install-mcp` - General MCP server installation
