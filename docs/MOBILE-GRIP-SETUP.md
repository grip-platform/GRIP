# Mobile GRIP Access Setup

Access GRIP from your phone via terminal (SSH/Mosh) or the Claude mobile app (MCP).

## Part 1: Terminal Access (SSH + Mosh)

### Mac Prerequisites (One-Time)

#### 1. Enable Remote Login

System Settings > General > Sharing > Remote Login > Toggle ON

Set "Allow access for" to your user account or "All users".

**Gotcha**: The toggle can show ON but sshd may not actually be running. Force-start it:

```bash
sudo launchctl load -w /System/Library/LaunchDaemons/ssh.plist
```

Verify: `lsof -nP -iTCP:22 -sTCP:LISTEN` should show `sshd`.

#### 2. Install Mosh

```bash
brew install mosh
```

Mosh keeps sessions alive through WiFi drops, network switches, and phone sleep/wake cycles. SSH alone disconnects on any interruption.

#### 3. Configure Firewall for Mosh

macOS firewall blocks mosh-server by default. Add it explicitly:

```bash
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /opt/homebrew/bin/mosh-server
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp /opt/homebrew/bin/mosh-server
```

#### 4. Install Tailscale

```bash
brew install --cask tailscale
```

Requires sudo - run in a real terminal, not Claude Code.

Open the Tailscale app and sign in. Get your stable IP:

```bash
tailscale ip -4
# Example: 100.x.y.z
```

This IP stays the same across any network. Without Tailscale, you need your Mac's current local IP and must be on the same network.

**Enable HTTPS certs** (required for MCP connector):

1. Go to https://login.tailscale.com/admin/dns
2. Enable **MagicDNS** (if not already on)
3. Enable **HTTPS Certificates**

**Install Tailscale on your phone too** - both devices must be on the same tailnet.

#### 5. Verify tmux

```bash
tmux -V
# Expected: tmux 3.5a
```

tmux lets you detach from sessions and reattach later. Your Claude Code session keeps running even when you disconnect.

### Phone App Setup (iOS)

**Blink Shell & Code** - App Store (free tier available, Blink+ $19.99/yr)

Features:
- Native Mosh + SSH support built in
- Multiple tabs, external keyboard support
- SFTP file browsing

### SSH Key Setup

In Blink Shell:

```bash
# 1. Generate key (in Blink's Config > Keys)
#    Choose Ed25519, name it "mac-grip"

# 2. Copy the public key from Blink's key config screen

# 3. On your Mac, add the public key:
echo "ssh-ed25519 AAAA...your-key..." >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# 4. Test connection from Blink:
ssh <username>@<tailscale-ip>
```

### Mosh Connection

```bash
mosh --server=/opt/homebrew/bin/mosh-server <username>@<tailscale-ip>
```

The `--server` flag is required because Homebrew installs to `/opt/homebrew/bin` which isn't in the default SSH PATH.

### Persistent Sessions with tmux

**Important**: Blink Shell uses its own shell, not your Mac's zsh. Commands like `source`, `tmux`, and aliases don't work directly. Use full paths:

```bash
# First connection - create or attach to session
/opt/homebrew/bin/tmux new -A -s grip

# Subsequent connections - one-liner via mosh
mosh --server=/opt/homebrew/bin/mosh-server <username>@<tailscale-ip> -- /opt/homebrew/bin/tmux new -A -s grip
```

Once inside tmux, your Mac's zsh takes over and all aliases work normally.

### Running Claude Code

Once connected via Mosh + tmux:

```bash
# Quick fresh session
grip fresh latest 2000

# Resume last session
grip resume latest

# Aliases (from .zshrc, available inside tmux)
gg      # quick session
ggr     # resume last
```

### Workflow Diagram

```
[Phone] Blink Shell
  +-- mosh --server=/opt/homebrew/bin/mosh-server <user>@<tailscale-ip>
       +-- /opt/homebrew/bin/tmux new -A -s grip
            +-- Claude Code (gg, ggr, grip fresh/resume)
```

### Blink Shell Gotchas

- Blink uses its own shell, NOT your Mac's zsh. Commands like `source`, `tmux`, `gg` aren't available directly.
- Always use full paths for Homebrew binaries: `/opt/homebrew/bin/tmux`, `/opt/homebrew/bin/mosh-server`
- Once inside a tmux session, your Mac's zsh takes over and all aliases work normally.

## Part 2: Claude App + GRIP MCP

Use the Claude iOS/Android app with GRIP's MCP server for a native mobile experience.

### How It Works

```
Claude App (phone)
  +-- Remote MCP connector
       +-- HTTPS tunnel (ngrok/cloudflared)
            +-- grip-mcp HTTP on localhost:3001
                 +-- Full GRIP: bootstrap, skills, modes, UI components
```

### Deployment Options

#### Option A: Railway (Production - Recommended)

grip-mcp is deployed to Railway with automatic HTTPS, GRIP content cloned from GitHub, and secret URL path auth.

**Production URL**: `https://<your-app>.up.railway.app`

**Auth**: Secret URL path (claude.ai connectors don't support bearer tokens or OAuth for custom servers). The MCP endpoint is at `/mcp/<secret>` instead of `/mcp`. Set via `GRIP_MCP_AUTH` env var on Railway.

**Railway env vars**:

| Variable | Value | Purpose |
|----------|-------|---------|
| `HTTP_PORT` | `${{PORT}}` | Railway's dynamic port |
| `CLAUDE_DIR` | `/app/grip-content` | Cloned GRIP repo |
| `GRIP_MCP_AUTH` | `<hex-token>` | Secret URL path segment |
| `GH_PAT` | GitHub PAT with `repo` scope | Clone private GRIP repo during build |

**Redeploying**:

```bash
cd ~/.claude/mcp-servers/grip-mcp
railway up --detach --service <SERVICE_ID>
```

The Dockerfile clones the GRIP repo during build so all skills, agents, and CLAUDE.md are available on the server.

#### Option B: Local + Tunnel (Development)

For local development/testing, run grip-mcp via launchd and expose with a tunnel.

**Mac Setup - Persistent MCP HTTP Server**:

```bash
# Ensure log directory exists
mkdir -p ~/.claude/tmp

# Load the agent (starts immediately and on every boot)
launchctl load ~/Library/LaunchAgents/com.grip.mcp-http.plist

# Verify it's running
launchctl list | rg grip.mcp

# Check logs if needed
tail -20 ~/.claude/tmp/grip-mcp-http.stderr.log
```

**Tunnel options**:

**cloudflared (Recommended)**:
```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:3001
```
Free, no session limits, no bandwidth limits, no account required.

**ngrok (Quick Start)**:
```bash
brew install ngrok
ngrok config add-authtoken YOUR_TOKEN
ngrok http 3001
```
Free tier: random URL per restart, 2hr session limit, 1GB bandwidth.

### Why You Need a Public URL

Claude.ai servers connect from the **public internet**. They cannot reach:
- Private Tailscale hostnames (e.g., `your-machine.tailnet-name.ts.net`)
- Local IP addresses (e.g., `192.168.1.x`)

**Tailscale Serve** only exposes to your private tailnet. **Tailscale Funnel** provides public exposure but requires a paid Tailscale plan.

### Claude App Configuration

1. Open **claude.ai** in a desktop browser (not the mobile app)
2. Go to **Settings > Connectors > Add Custom Connector**
3. Configure:
   - **Name**: GRIP
   - **URL**: `https://<your-app>.up.railway.app/mcp/<secret>` (production) or `https://<tunnel-url>/mcp` (local)
4. Save - it syncs automatically to the iOS/Android app
5. Requires Claude **Pro, Max, Team, or Enterprise** plan

### Known Limitation: Claude iOS/Android App

**Status**: Claude iOS/Android app has a bug where custom connectors always trigger an OAuth flow, even for authless servers. This causes "Failed to generate authorization URL" error.

- **claude.ai web**: Works
- **Claude Desktop**: Works
- **Claude iOS/Android**: Blocked by client-side OAuth bug
- Tracked at: https://github.com/anthropics/claude-ai-mcp/issues/5

**Workaround**: Use terminal access (Mosh + tmux + Claude Code) from your phone instead.

### Server Implementation Notes

The grip-mcp server includes:
- GET/HEAD handlers returning `MCP-Protocol-Version` headers (required by claude.ai connector protocol)
- Reconnection bug fix (`Already connected to transport`) via try/catch/close pattern
- `/health` endpoint for Railway healthchecks (unauthenticated)
- Secret URL path auth via `GRIP_MCP_AUTH` env var (bearer tokens don't work with claude.ai connectors)

### Available MCP Tools on Mobile

| Tool | Description |
|------|-------------|
| `grip_bootstrap` | Load full GRIP context into conversation |
| `grip_load_skill` | Load any GRIP skill by name |
| `grip_list_skills` | Browse all available skills |
| `grip_options_picker` | Interactive option picker (Emergent UI) |
| `grip_confirmation` | Confirmation dialogs |
| `grip_mode_switcher` | Switch between GRIP modes |
| `grip_skill_browser` | Visual skill catalog browser |
| `grip_dashboard` | System dashboard overview |

### Usage Example

In the Claude app:

> "Bootstrap GRIP and switch to strategy mode"

Claude calls `grip_bootstrap` to load context, then `grip_mode_switcher` to switch modes.

## Part 3: Pair-Mode Steering from Phone

Steer an active Claude Code session running on your Mac from your phone.

### Option A: Terminal Steering (via SSH/Mosh)

On your Mac, start Claude Code with monitoring enabled:

```bash
export GRIP_MONITORING_ENABLED=true
export GRIP_STEERING_MODE=soft
claude
```

From your phone (via Mosh), open a second tmux pane or window:

```bash
# List active sessions
python3 ~/.claude/lib/session_monitor.py list

# Connect and steer
python3 ~/.claude/lib/session_monitor.py connect <SESSION_ID> --steer
```

### Option B: Claude App + grip-pair MCP

If grip-pair is configured as a remote MCP connector (same tunnel setup as grip-mcp), use the Claude app:

| Tool | Description |
|------|-------------|
| `pair_connect` | Connect to an active session |
| `pair_status` | Check connection state |
| `pair_steer` | Inject context, suggest tools, flag concerns |
| `pair_disconnect` | Close the monitoring connection |

Example: "Connect to my active GRIP session and inject context: focus on the auth module"

### Environment Variables Reference

| Variable | Values | Description |
|----------|--------|-------------|
| `GRIP_MONITORING_ENABLED` | `true`/`false` | Enable session monitoring |
| `GRIP_STEERING_MODE` | `off`/`soft`/`hard` | Steering permission level |
| `GRIP_PAIR_AUTH` | any string | Shared secret for remote connections |

## Part 4: Multi-Frontend MCP Guide

GRIP MCP works with any client that supports remote HTTP MCP transport.

### Client Configuration

| Client | Setup |
|--------|-------|
| **Claude.ai (web)** | Settings > Connectors > Add Custom Connector > paste tunnel URL + `/mcp` |
| **Claude iOS/Android** | Auto-syncs from claude.ai settings (cannot configure directly on mobile) |
| **Claude Desktop** | Supports custom connectors (same as web setup) |
| **Claude Code CLI** | `claude mcp add grip-remote --transport http https://<tunnel-url>/mcp` |
| **Cursor** | Settings > MCP > Add remote HTTP server |
| **Windsurf** | Supports remote HTTP MCP transport |

### Requirements

- Claude **Pro, Max, Team, or Enterprise** plan (for claude.ai connector)
- grip-mcp running in HTTP mode on the Mac
- Public HTTPS tunnel active (ngrok or cloudflared)

## Part 5: Cross-Platform Notes

| Platform | Terminal App | MCP Path |
|----------|-------------|----------|
| iOS | Blink Shell (native Mosh) | Claude iOS app (auto-sync from claude.ai) |
| Android | Termux (`pkg install mosh`) | Claude Android app (auto-sync from claude.ai) |
| Windows | WSL 2 + mosh (no native Windows mosh binary) | Claude Desktop / claude.ai web |
| Linux | Native mosh/tmux | Claude Desktop / Claude Code CLI |
| macOS | Native Terminal / Blink (iPad) | Claude Desktop / Claude Code CLI |

### Windows Notes

- Mosh has no native Windows binary. Use WSL 2.
- WSL 2 may have UDP issues that break mosh reconnection. WSL 1 is more reliable for UDP.
- Alternative: use SSH only (accepts network drops but reconnects cleanly).

### Android Notes

- Termux provides a full Linux environment: `pkg install mosh openssh`
- Termux keyboard has Ctrl key for tmux bindings
- Use `mosh --server=/opt/homebrew/bin/mosh-server <user>@<tailscale-ip>` same as iOS

## Part 6: Quick Reference Card

### Connection Commands

```bash
# SSH (basic, drops on network change)
ssh <username>@<tailscale-ip>

# Mosh (survives network changes)
mosh --server=/opt/homebrew/bin/mosh-server <username>@<tailscale-ip>

# Mosh + tmux (persistent session)
mosh --server=/opt/homebrew/bin/mosh-server <username>@<tailscale-ip> -- /opt/homebrew/bin/tmux new -A -s grip
```

### GRIP Aliases (inside tmux)

```bash
gg          # Fresh Claude Code session
ggr         # Resume last session
ggl         # List sessions
grip fresh latest 2000    # Fresh with context
grip resume latest        # Resume last
```

### Production (Railway)

```bash
# Redeploy
cd ~/.claude/mcp-servers/grip-mcp && railway up --detach --service <SERVICE_ID>

# Check status
curl -s https://<your-app>.up.railway.app/health

# View logs
railway logs --service <SERVICE_ID>

# Set env vars
railway variables --service <SERVICE_ID> --set "KEY=value"
```

### Local Tunnels

```bash
# cloudflared (recommended)
cloudflared tunnel --url http://localhost:3001

# ngrok
ngrok http 3001
curl -s http://127.0.0.1:4040/api/tunnels | jq -r '.tunnels[0].public_url'
```

### Launchd (grip-mcp HTTP - local)

```bash
# Start
launchctl load ~/Library/LaunchAgents/com.grip.mcp-http.plist

# Stop
launchctl unload ~/Library/LaunchAgents/com.grip.mcp-http.plist

# Check status
launchctl list | rg grip.mcp

# View logs
tail -20 ~/.claude/tmp/grip-mcp-http.stderr.log
```

### Pair-Mode Commands

```bash
# Enable monitoring (before starting Claude Code)
export GRIP_MONITORING_ENABLED=true
export GRIP_STEERING_MODE=soft

# List sessions (from second terminal)
python3 ~/.claude/lib/session_monitor.py list

# Connect and steer
python3 ~/.claude/lib/session_monitor.py connect <ID> --steer
```

### QR Code for Tunnel URL

With `qrencode` installed (`brew install qrencode`), generate a scannable QR code for the MCP connector URL:

```bash
# For ngrok
qrencode -t ANSIUTF8 "$(curl -s http://127.0.0.1:4040/api/tunnels | jq -r '.tunnels[0].public_url')/mcp"

# For any URL
qrencode -t ANSIUTF8 "https://<tunnel-url>/mcp"
```

Scan with your phone camera to copy the URL, then paste into claude.ai Settings > Connectors.

### One-Command Pairing

```bash
# Start mobile pairing (tunnel + connection info + QR code)
~/.claude/scripts/grip-mobile-pair.sh

# Check status
~/.claude/scripts/grip-mobile-pair.sh --status

# Stop
~/.claude/scripts/grip-mobile-pair.sh --stop
```

The pairing script auto-generates a QR code if `qrencode` is installed.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| sshd not listening despite toggle ON | `sudo launchctl load -w /System/Library/LaunchDaemons/ssh.plist` |
| `mosh-server not found` on remote | Use `--server=/opt/homebrew/bin/mosh-server` flag |
| `tmux: command not found` in Blink | Use full path: `/opt/homebrew/bin/tmux` (Blink doesn't load zshrc) |
| Tailscale phone can't connect | Install Tailscale app on phone, sign in with same account |
| claude.ai "URL must start with https" | Use ngrok or cloudflared for public HTTPS URL |
| claude.ai "error connecting to MCP" with Tailscale URL | Tailscale Serve is private-only; claude.ai needs public tunnel |
| grip-mcp "Already connected to transport" | Fixed in server code (try/catch/close pattern) |
| ngrok "agent version too old" | `ngrok update` |
| SSH connection refused | System Settings > General > Sharing > Remote Login: ON |
| Mosh timeout/firewall | `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /opt/homebrew/bin/mosh-server` |
| grip-mcp not responding | `launchctl list | rg grip.mcp` - check exit code, review stderr log |
| tmux session gone | `tmux ls` - if no sessions, create new: `tmux new -s grip` |
| "No MCP tools" in Claude app | Ensure connector URL ends in `/mcp` (or `/mcp/<secret>`), check server is running |
| ngrok URL changed | Update connector in claude.ai Settings > Connectors |
| Railway healthcheck failed | Check `HTTP_PORT` is set to `${{PORT}}` (Railway's dynamic port) |
| Railway build fails on git clone | Ensure `GH_PAT` env var is set with valid GitHub token |
| "Failed to generate authorization URL" on iOS | Known Claude iOS bug - use terminal access instead |
| claude.ai "error connecting" with bearer token | claude.ai connectors don't support bearer tokens; use secret URL path |
