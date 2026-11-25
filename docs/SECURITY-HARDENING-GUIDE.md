# GRIP Security Hardening Guide

**Lock down and isolate. Children are driving.**

This guide covers security hardening for GRIP installations. All settings default to secure - you must explicitly opt-in to insecure configurations.

---

## Quick Start (5 minutes)

```bash
# 1. Verify your installation
grip doctor

# 2. Generate a gateway token
python3 ~/.claude/features/voice-gateway/gateway_auth.py --generate -d "My Device"
# SAVE THE TOKEN - it's only shown once

# 3. Run security tests
cd ~/.claude && python3 -m pytest tests/test_gates.py tests/test_gateway_security.py -v
```

---

## 1. Voice Gateway Security

### Default Configuration (SECURE)

The voice gateway now defaults to:
- **Localhost only** (`127.0.0.1`) - not accessible from network
- **Authentication required** - Bearer token mandatory
- **Rate limiting** - 10 connections/minute per IP

### Generate Access Tokens

```bash
# Generate a new token (save it - shown only once!)
python3 ~/.claude/features/voice-gateway/gateway_auth.py --generate -d "Living Room iPad"

# List existing tokens (shows hash prefix only)
python3 ~/.claude/features/voice-gateway/gateway_auth.py --list

# Revoke a token
python3 ~/.claude/features/voice-gateway/gateway_auth.py --revoke <hash-prefix>
```

### Client Connection

Clients must include the token in the WebSocket connection:

```javascript
// JavaScript client example
const ws = new WebSocket('ws://localhost:8765', {
  headers: {
    'Authorization': 'Bearer YOUR_TOKEN_HERE'
  }
});
```

```python
# Python client example
import websockets

async with websockets.connect(
    'ws://localhost:8765',
    extra_headers={'Authorization': 'Bearer YOUR_TOKEN_HERE'}
) as ws:
    # ...
```

### IP Allowlist (Optional)

Restrict connections to specific IPs:

```bash
# Add trusted IPs
python3 ~/.claude/features/voice-gateway/gateway_auth.py --allow-ip 192.168.1.100
python3 ~/.claude/features/voice-gateway/gateway_auth.py --allow-ip 192.168.1.101

# List allowed IPs
python3 ~/.claude/features/voice-gateway/gateway_auth.py --list-ips

# Remove IP
python3 ~/.claude/features/voice-gateway/gateway_auth.py --remove-ip 192.168.1.100
```

### Enable TLS (Recommended for Production)

```bash
# Generate self-signed cert (for testing)
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes

# Start with TLS
python3 ~/.claude/features/voice-gateway/voice-gateway.py \
  --ssl-cert cert.pem \
  --ssl-key key.pem
```

Clients connect via `wss://` instead of `ws://`.

---

## 2. External Access (CloudFlare Tunnel)

**DO NOT bind to 0.0.0.0.** Use CloudFlare Tunnel for external access.

### Setup CloudFlare Tunnel

```bash
# Install cloudflared
brew install cloudflare/cloudflare/cloudflared

# Login to CloudFlare
cloudflared tunnel login

# Create tunnel
cloudflared tunnel create grip-gateway

# Configure tunnel (create ~/.cloudflared/config.yml)
cat > ~/.cloudflared/config.yml << 'EOF'
tunnel: grip-gateway
credentials-file: ~/.cloudflared/TUNNEL_ID.json

ingress:
  - hostname: grip.yourdomain.com
    service: ws://localhost:8765
  - service: http_status:404
EOF

# Run tunnel
cloudflared tunnel run grip-gateway
```

Now external devices connect to `wss://grip.yourdomain.com` with CloudFlare handling TLS and DDoS protection.

---

## 3. Safety Gates

### Verify Gates Are Active

```bash
# Test dependency guardian
echo '{"tool_name": "Read", "tool_input": {"file_path": "node_modules/foo"}}' | \
  python3 ~/.claude/hooks/context-gate-hook.py
# Should output: BLOCKED

# Test secrets detection
echo '{"tool_name": "Bash", "tool_input": {"command": "git add .env"}}' | \
  python3 ~/.claude/hooks/context-gate-hook.py
# Should output: BLOCKED

# Test deploy gate
echo '{"tool_name": "Bash", "tool_input": {"command": "vercel deploy"}}' | \
  python3 ~/.claude/hooks/context-gate-hook.py
# Should output: BLOCKED (requires approval)
```

### Gate Summary

| Gate | Blocks | Bypass |
|------|--------|--------|
| Dependency Guardian | node_modules, venv, dist | None |
| Destructive Git | force push, hard reset | AskUserQuestion approval |
| Secrets Detection | .env, credentials.json, keys | None |
| Deploy Gate | vercel/firebase/npm deploy | AskUserQuestion approval |
| Tool Substitution | grep (use rg), find (use fd) | None |
| Context Gate | Operations at 80% context | User choice |

---

## 4. Network Isolation

### Firewall Rules (macOS)

```bash
# Block outbound except allowlist
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setblockall on

# Allow specific apps
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /usr/local/bin/claude
```

### Firewall Rules (Linux)

```bash
# Allow only essential egress
sudo iptables -A OUTPUT -d api.anthropic.com -j ACCEPT
sudo iptables -A OUTPUT -d github.com -j ACCEPT
sudo iptables -A OUTPUT -d pypi.org -j ACCEPT
sudo iptables -A OUTPUT -d registry.npmjs.org -j ACCEPT
sudo iptables -A OUTPUT -j DROP
```

---

## 5. Docker Sandbox (Maximum Isolation)

For maximum security, run GRIP in a Docker container:

```dockerfile
# Dockerfile.grip-sandbox
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y \
    python3 python3-pip nodejs npm git curl

# Create non-root user
RUN useradd -m grip
USER grip
WORKDIR ~

# Copy GRIP installation
COPY --chown=grip:grip .claude ~/.claude

# No network by default
# Run with: docker run --network none grip-sandbox
```

```bash
# Build
docker build -t grip-sandbox -f Dockerfile.grip-sandbox .

# Run with NO network access
docker run -it --network none grip-sandbox claude

# Run with limited network (API only)
docker run -it --network grip-api-only grip-sandbox claude
```

---

## 6. Device-Specific Setup

### Home Server

```bash
# Start gateway on boot (macOS)
cat > ~/Library/LaunchAgents/com.grip.gateway.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.grip.gateway</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>~/.claude/features/voice-gateway/voice-gateway.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.grip.gateway.plist
```

### Mobile Devices (iPad/iPhone)

1. Use CloudFlare Tunnel (see Section 2)
2. Generate device-specific token
3. Configure app with `wss://grip.yourdomain.com` + token

### Children's Devices

1. Generate read-only token with limited scope
2. Add device IP to allowlist
3. Enable content filtering at router level
4. Consider time-based access (cron job to rotate tokens)

---

## 7. Audit & Monitoring

### View Security Logs

```bash
# Gate audit log
tail -f ~/.claude/logs/gates-audit.jsonl

# Gateway access log
tail -f ~/.claude/logs/gateway-access.log
```

### Run Security Tests

```bash
cd ~/.claude
python3 -m pytest tests/test_gates.py tests/test_gateway_security.py -v
```

### Check Token Usage

```bash
python3 ~/.claude/features/voice-gateway/gateway_auth.py --list
```

---

## 8. Emergency Procedures

### Revoke All Access

```bash
# Delete all tokens
rm ~/.claude/.gateway-tokens.json

# Stop gateway
pkill -f voice-gateway.py

# Regenerate single admin token
python3 ~/.claude/features/voice-gateway/gateway_auth.py --generate -d "Emergency Admin"
```

### Rotate Tokens

```bash
# List current tokens
python3 ~/.claude/features/voice-gateway/gateway_auth.py --list

# Revoke old, generate new for each device
python3 ~/.claude/features/voice-gateway/gateway_auth.py --revoke OLD_HASH
python3 ~/.claude/features/voice-gateway/gateway_auth.py --generate -d "Device Name"
```

### Lockdown Mode

```bash
# Stop all services
pkill -f voice-gateway.py
pkill -f cloudflared

# Block all network
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setblockall on
```

---

## 9. Security Checklist

Run through this checklist for each installation:

- [ ] `grip doctor` passes all checks
- [ ] Voice gateway uses localhost binding (not 0.0.0.0)
- [ ] Authentication tokens generated and distributed securely
- [ ] TLS enabled for any non-localhost connections
- [ ] CloudFlare Tunnel configured for external access
- [ ] IP allowlist configured (if needed)
- [ ] Firewall rules in place
- [ ] Security tests pass (61 tests)
- [ ] Audit logging enabled
- [ ] Emergency procedures documented

---

## 10. RSI (Recursive Self-Improvement) Safety

**PARAMOUNT**: This section addresses the most critical security concern for agentic AI systems.

### What is RSI Risk?

When an AI agent can write code, and that code influences its own behavior, a feedback loop emerges:

```
Agent writes code -> Code runs -> Agent learns from results -> Agent writes more code
```

This loop can amplify errors, drift from user intent, or in worst cases, optimize for unintended goals.

### Defense-in-Depth Strategy

GRIP implements multiple layers of RSI protection:

| Layer | Mechanism | Purpose |
|-------|-----------|---------|
| 1. Flow Mode Hidden | Not in mode discovery | Prevents accidental autonomy |
| 2. Forging-Skills Default | User writes code | Human stays in loop |
| 3. Periodic Halts | Review gates every 15-25 ops | Forces user engagement |
| 4. Context Gate | 80% halt + user choice | Prevents runaway sessions |
| 5. Safety Gates | Mechanical denial | Blocks dangerous operations |

### Active Gates

The following gates are ALWAYS active and cannot be bypassed:

| Gate | Blocks | Purpose |
|------|--------|---------|
| Dependency Guardian | node_modules, venv, dist | Prevents token waste + supply chain |
| Destructive Git | force push, hard reset | Protects history |
| Secrets Detection | .env, credentials | Prevents leakage |
| Deploy Gate | vercel, firebase, npm publish | Requires approval |
| Tool Substitution | grep, find, cat | Enforces safe alternatives |
| Context Gate | 80%+ context | Prevents overflow |

### Flow Mode Lockdown

Flow mode (bypass permissions) is HIDDEN by default:

- Not listed in `/mode` discovery
- Requires explicit `/mode flow --enable`
- Requires onboarding completion (`~/.claude/.onboarded`)
- Requires acknowledgment (`~/.claude/.flow-enabled`)

See `skills/flow-mode/SKILL.md` for full protocol.

### Forging-Skills Default

The `forging-skills` output style is DEFAULT for all sessions:

- Agent proposes code, user writes implementation
- Periodic review gates (every 15-25 operations)
- Skill retention over pure automation

This prevents the agent from entering autonomous code generation loops.

### Verification

```bash
# Check flow mode status (should be disabled)
cat ~/.claude/.flow-enabled 2>/dev/null || echo "FLOW DISABLED (correct)"

# Check forging-skills is default
jq '.default_output_style' ~/.claude/settings.json
# Expected: "forging-skills"

# Test RSI gates
python3 -m pytest ~/.claude/tests/test_gates.py -v -k "rsi"
```

### Why This Matters

| Without RSI Safety | With RSI Safety |
|-------------------|-----------------|
| Agent writes 95%+ of code | User writes significant code |
| Skills atrophy over time | Skills maintained through practice |
| Drift from user intent | Regular checkpoints |
| Runaway automation | Mechanical halts |

---

## 11. Threat Model

| Threat | Mitigation |
|--------|------------|
| Unauthorized network access | Localhost binding + CloudFlare Tunnel |
| Token theft | SHA-256 hashed storage, per-device tokens |
| Brute force | Rate limiting (10/min per IP) |
| Man-in-the-middle | TLS required for external |
| Dependency confusion | Dependency Guardian gate blocks reads |
| Secret leakage | Secrets Detection gate blocks commits |
| Destructive commands | Approval required via AskUserQuestion |
| Context overflow | 80% threshold with user checkpoint |

---

**Remember: Defense in depth. Trust nothing. Verify everything.**

*GRIP Security Hardening Guide - Gen 306 (RSI Safety Lockdown)*
