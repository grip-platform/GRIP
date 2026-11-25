---
name: tunnel
description: Expose localhost to a public URL via cloudflared (primary) or localtunnel (fallback) with auto-caffeinate. Use when the user wants to share a local server, demo a running app, or create a temporary public URL for localhost.
status: Active
effort: medium
version: 2.0.0
triggers:
  - "/tunnel"
  - "expose localhost"
  - "public url"
  - "share my local server"
  - "ngrok"
  - "localtunnel"
  - "cloudflared"
  - "mcp tunnel"
  - "grip-tunnel"
integrates:
  - caffeinating-sessions
---

# /tunnel -- Expose Localhost to Public URL

Two backends: **cloudflared** (primary, stable) and **localtunnel** (fallback).

## Usage

```
/tunnel                          # Default: cloudflared on port 3000
/tunnel 8080                     # Custom port
/tunnel --lt 3000 my-demo        # Force localtunnel with subdomain
/tunnel stop                     # Kill active tunnel + stop caffeinate
/tunnel status                   # Show active tunnel info
/tunnel mesh                     # Start grip-server + cloudflare tunnel
/tunnel mesh stop                # Stop mesh tunnel + server
```

## Protocol

### 1. Pre-flight

- Verify target port is listening: `lsof -i :PORT`
- Check for existing tunnel: `pgrep -f cloudflared` or `pgrep -f localtunnel`
- If tunnel already running on same port, show URL and exit

### 2a. Start Tunnel — cloudflared (PRIMARY)

cloudflared generates a random `*.trycloudflare.com` URL, stable for hours.
No account required. No splash page. Binary at `/opt/homebrew/bin/cloudflared`.

```bash
PORT="${1:-3000}"

# Kill any existing cloudflared tunnel
pkill -f "cloudflared.*tunnel" 2>/dev/null

# Start as fully detached daemon
nohup cloudflared tunnel --url http://localhost:${PORT} > /tmp/cloudflared-grip.log 2>&1 & disown

echo "Log: /tmp/cloudflared-grip.log"

# Wait for URL to appear in log
sleep 5
rg -o 'https://[^ ]*\.trycloudflare\.com' /tmp/cloudflared-grip.log | tail -1
```

### 2b. Start Tunnel — localtunnel (FALLBACK)

Use when cloudflared is unavailable or `--lt` flag is passed.

```bash
PORT="${1:-3000}"
SUBDOMAIN="${2:-grip-live}"

pkill -f "localtunnel.*--port $PORT" 2>/dev/null

nohup bash -c '
  while true; do
    npx --yes localtunnel --port '"$PORT"' --subdomain '"$SUBDOMAIN"' 2>&1
    echo "[tunnel] Disconnected. Reconnecting in 3s..."
    sleep 3
  done
' > /tmp/tunnel-${PORT}.log 2>&1 & disown

echo "Log: /tmp/tunnel-${PORT}.log"
sleep 5
rg -o 'https://[^ ]*' /tmp/tunnel-${PORT}.log | tail -1
```

### 2c. Start Tunnel — Mesh Mode

Mesh mode starts `grip-server` on port 3099 and exposes it via cloudflared. Used to share the GRIP mesh with remote peers.

```bash
# 1. Pre-flight: verify grip-server exists
if [ ! -f "$HOME/CodeTonight/grip-server/server.js" ]; then
  echo "Error: grip-server not found at ~/CodeTonight/grip-server/"
  exit 1
fi

# 2. Start grip-server if not already running on 3099
if ! lsof -i :3099 > /dev/null 2>&1; then
  cd "$HOME/CodeTonight/grip-server"
  PORT=3099 node server.js > /tmp/grip-server.log 2>&1 &
  disown
fi

# 3. Wait for ready (max 5s)
for i in 1 2 3 4 5; do
  sleep 1
  if curl -s http://localhost:3099/api/mesh/health > /dev/null 2>&1; then
    break
  fi
done

# 4. Start cloudflared tunnel on 3099
pkill -f "cloudflared.*3099" 2>/dev/null
nohup cloudflared tunnel --url http://localhost:3099 > /tmp/tunnel-mesh.log 2>&1 & disown

echo "Log: /tmp/tunnel-mesh.log"

# 5. Extract and print URL (wait for cloudflared to register)
sleep 5
MESH_URL=$(rg -o 'https://[^ ]*\.trycloudflare\.com' /tmp/tunnel-mesh.log | tail -1)

# 6-7. Print connection info
echo "Mesh exposed at: ${MESH_URL}"
echo "Peers can connect via: ws://${MESH_URL#https://}/grip-mesh"
```

**Stop mesh mode:**

```bash
pkill -f "cloudflared.*3099" 2>/dev/null
pkill -f "grip-server" 2>/dev/null
rm -f /tmp/tunnel-mesh.log /tmp/grip-server.log
```

### 3. Auto-Caffeinate

After starting the tunnel, automatically prevent macOS sleep:

```bash
source "$HOME/.claude/lib/intelligent-caffeinate.sh"
caffeinate_start_for_rsi
```

This ensures the machine stays awake for the tunnel's lifetime.
When `/tunnel stop` is called, caffeinate is also stopped.

### 4. Verify

```bash
# cloudflared
URL=$(rg -o 'https://[^ ]*\.trycloudflare\.com' /tmp/cloudflared-grip.log | tail -1)
# localtunnel
# URL=$(rg -o 'https://[^ ]*' /tmp/tunnel-${PORT}.log | tail -1)
curl -s -o /dev/null -w "%{http_code}" "$URL"
```

### 5. Stop

```bash
# Stop whichever is running
pkill -f "cloudflared.*tunnel" 2>/dev/null
pkill -f "localtunnel.*--port ${PORT:-3000}" 2>/dev/null
rm -f /tmp/cloudflared-grip.log /tmp/tunnel-${PORT:-3000}.log

source "$HOME/.claude/lib/intelligent-caffeinate.sh"
caffeinate_stop_if_safe
```

### 6. Status

```bash
if pgrep -f cloudflared > /dev/null; then
  echo "Tunnel: cloudflared (running)"
  rg -o 'https://[^ ]*\.trycloudflare\.com' /tmp/cloudflared-grip.log 2>/dev/null | tail -1
elif pgrep -f localtunnel > /dev/null; then
  echo "Tunnel: localtunnel (running)"
  rg -o 'https://[^ ]*' /tmp/tunnel-*.log 2>/dev/null | tail -1
else
  echo "Tunnel: stopped"
fi
```

## Notes

- **cloudflared**: free, no account needed, no splash page, stable for hours. URL is random (share via Slack).
- **localtunnel**: free, custom subdomains (best-effort), shows splash page on first visit.
- Auto-caffeinate prevents macOS sleep (pairs with `lib/intelligent-caffeinate.sh`)
- Daemon pattern (`nohup + disown`) survives Claude Code session end

## MCP tunnels -- connecting cloud Claude to a private MCP server

A **different kind of tunnel** from everything above: `/tunnel` above exposes
*your own app* to the public internet via cloudflared's free quick-tunnel
service. An **MCP tunnel** is Anthropic's own research-preview product
(`platform.claude.com/settings/workspaces/default/mcp-tunnels`) that lets
**cloud Claude** -- a Managed Agent session or the Messages API, never the
daily Claude Code session -- reach an MCP server running in your private
network, with no inbound port opened and no service exposed on the public
internet. It needs a Console tunnel, a Docker Compose deployment, and
certificates you control; cloudflared is one of its two containers, but
there is no `/tunnel`-style quick-URL here.

Use `bin/grip-tunnel` (backed by `lib/mcp_tunnel.py`) for this:

```bash
bin/grip-tunnel scaffold --dir ~/mcp-tunnel --domain <tunnel-domain>
bin/grip-tunnel certs --dir ~/mcp-tunnel
bin/grip-tunnel doctor --dir ~/mcp-tunnel     # offline checks, exit 0/1/3
bin/grip-tunnel up --dir ~/mcp-tunnel
bin/grip-tunnel status --dir ~/mcp-tunnel
bin/grip-tunnel nonce new --dir ~/mcp-tunnel  # prove first contact
bin/grip-tunnel nonce verify --dir ~/mcp-tunnel --reply-file reply.json
bin/grip-tunnel weather --dir ~/mcp-tunnel    # one line: tunnel, certificate, seals
bin/grip-tunnel stop --dir ~/mcp-tunnel       # the brake
```

The default (and, out of the box, only) route this kit wires is a
data-free nonce probe -- routing a real MCP server through the tunnel is
a deliberate, hand-edited operator decision, never this kit's default
(`rules/untrusted-content-capability-lock.md`: tunnels carry no
zero-data-retention cover while in research preview). The tunnel token
and both private keys live only in `data/`, never in a tracked file and
never on the command line.

Full walkthrough, the `doctor` checks (with their count and rule-id
table), the certificate-renewal and `vendor-check` watch, and the
security notes: `docs/mcp-tunnel-first-contact.md`.
