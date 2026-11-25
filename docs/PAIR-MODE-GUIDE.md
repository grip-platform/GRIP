# GRIP Pair-Mode v3.0 Guide

Two developers, two GRIP instances, one codebase. Grip/Guard roles with
seamless intercept and genome breeding.

## 1. Overview

Pair mode connects two GRIP-equipped developers for real pair programming.
Each developer runs their own Claude Code session. One takes the **Grip**
(active builder), the other takes the **Guard** (navigator/reviewer). Roles
swap seamlessly via the **Intercept Protocol**, which transfers context and
cross-pollinates genome data between sessions.

Key difference from v2.0: the old `/pair` spawned local worker agents. That
functionality moved to `/delegate`. The new `/pair` connects two human
developers across machines.

## 2. Prerequisites

```bash
# Install websockets (Python)
pip install websockets

# Build MCP server (one-time)
cd ~/.claude/mcp-servers/grip-pair && npm install && npm run build

# Register MCP server
claude mcp add grip-pair node ~/.claude/mcp-servers/grip-pair/dist/index.js
```

Both developers need:

- GRIP installed (`~/.claude` with onboarding complete)
- Shared network (Tailscale, SSH, or same machine)
- `websockets` Python package

## 3. Quick Start (5 Steps)

```bash
# 1. Developer A: enable monitoring
export GRIP_MONITORING_ENABLED=true
export GRIP_STEERING_MODE=soft
claude

# 2. Developer B: connect
claude
/mode pair
/pair connect 100.64.0.10    # or session path, SSH target, bridge URL

# 3. B is Guard, A is Grip (first connector defaults to Guard)

# 4. Guard steers when needed
/pair steer "check the index on that query"

# 5. Swap roles when ready
/pair intercept
```

## 4. Transports

Pair mode auto-detects the transport from the target string:

```text
/pair connect env_01AxAwXd...        -> Claude Bridge (env_ prefix)
/pair connect 100.64.0.10         -> Tailscale VPN (CGNAT range 100.64.0.10/10)
/pair connect user@host              -> SSH Tunnel (@ detection)
/pair connect /tmp/grip-session-xxx  -> Unix Socket (path detection)
```

| Transport | Use Case | Auth | Latency |
|-----------|----------|------|---------|
| **Unix Socket** | Same-machine pairing (testing, local) | File permissions | Minimal |
| **Tailscale** | Remote pairing (proven with T>>) | WireGuard + HMAC | Low |
| **SSH Tunnel** | Standard remote access | SSH keys | Low |
| **Claude Bridge** | Web-to-CLI or CLI-to-Web pairing | Anthropic auth | Medium |

### Tailscale (Proven)

```bash
# Ensure both developers are on the same Tailnet
# Machine A:
export GRIP_WS_BIND=0.0.0.0
export GRIP_PAIR_AUTH=shared-secret
python3 ~/.claude/lib/tailscale.py expose --port 19042

# Machine B:
export GRIP_PAIR_AUTH=shared-secret
/pair connect 100.64.0.10
```

### Claude Bridge (EXPERIMENTAL)

Either developer can share or connect:

```bash
# Share YOUR session (generates bridge URL)
/pair share
# Output: https://claude.ai/code?bridge=env_01AxAwXd...

# Connect to THEIR session
/pair connect env_01AxAwXdPYpjT6Dh5mZmcuri
```

Implementation deferred until live testing with a real `env_` bridge URL
between two Claude Code instances. The transport detection and message routing
are wired but untested end-to-end.

### Unix Socket (Local)

```bash
# Terminal 1: monitored session
export GRIP_MONITORING_ENABLED=true
export GRIP_STEERING_MODE=soft
claude

# Terminal 2: monitor
claude
/pair connect /tmp/grip-session-abc123
```

## 5. Roles

| Role | Responsibility | Capabilities |
|------|---------------|--------------|
| **Grip** | Active builder -- writes and edits code | Full editorial control, all tools |
| **Guard** | Navigator -- reviews, steers, catches issues | Steering commands, flag concerns, suggest tools |

Roles are not fixed. Either party can request a swap via `/pair intercept`.

## 6. Steering (Guard Commands)

The Guard has three steering modes:

| Command | Purpose | Limit |
|---------|---------|-------|
| `inject_context` | Add missing information | 10KB max |
| `suggest_tool` | Recommend tool or approach | Single suggestion |
| `flag_concern` | Raise safety/quality issue | Blocks until addressed |

```bash
/pair steer "use the Redis connection pool, not individual connections"
/pair steer --flag "that query is O(n^2), check the index"
/pair steer --suggest "try rg instead of grep for this search"
```

### The 5-Second Rule

When the Guard sees the Grip do something "wrong", **wait at least 5 seconds**
before steering. The Grip might already have it in mind. Premature steering
breaks flow state and creates micro-management friction.

## 7. Intercept Protocol (Role Swap)

The defining feature of GRIP pair mode. When roles swap, knowledge transfers.

### Standard Intercept

```text
1. T>> [Guard] runs: /pair intercept
2. V>> [Grip] receives: "T>> requests the Grip. Accept?"
3. V>> accepts
4. INTERCEPT SEQUENCE:
   a. Outgoing Grip (V>>) session runs /save
   b. Outgoing Grip (V>>) session runs /evo
   c. Context summary generated:
      - Current file and line
      - Recent changes (git diff --stat)
      - Intent (what was being built)
      - Blockers (what was stuck)
      - Evo results (patterns detected)
   d. Genome breed: V>> x T>> (directed pairing)
      - python3 lib/genome_breeder.py breed --pair V>> T>>
      - Fitness crossover, KONO-permanent floor preserved
      - Bred offspring written to _bred.json
   e. Both sessions import bred genome
   f. Context summary injected into incoming Grip (T>>)
   g. Roles swap atomically
5. T>> is now [Grip], V>> is now [Guard]
6. Both sessions carry cross-pollinated knowledge
```

### Urgent Intercept

```bash
/pair intercept!
```

Safety-critical swap. Blocks the current Grip immediately (does not wait
for acceptance). Used when the Guard spots a dangerous operation in progress.
Still triggers the full genome breed sequence after the block.

### What Genome Breeding Achieves

When two developers pair and swap, their GRIP instances don't just exchange
context -- they **breed genomes**. Patterns learned by one session (what tools
worked, what approaches failed, what skills were effective) cross-pollinate
with the other session's evolutionary history.

After multiple intercepts in a session, both developers' GRIP instances
emerge stronger than either was alone. This is **evolutionary pair programming**.

### Implementation

The intercept protocol is implemented in `lib/pair_intercept.py`:

- `PairRole` enum: GRIP, GUARD, DISCONNECTED
- `InterceptState` dataclass: tracks roles, operators, intercept count
- `generate_context_summary()`: captures git diff, branch, working state
- `execute_intercept()`: full sequence (save, evo, breed, inject, swap)
- `request_intercept()` / `accept_intercept()`: handshake protocol messages

New session control protocol message types:

- `intercept_request`: Guard requests the Grip role
- `intercept_accepted`: Grip accepts the swap
- `intercept_complete`: Swap finished, roles updated
- `role_update`: Broadcast new role assignments to all monitors

## 8. Pomodoro Rotation

Research shows 25-minute focused blocks with 5-minute breaks optimise pair
programming effectiveness. GRIP can suggest intercepts at Pomodoro boundaries
via a timer.

- Block length: 25 minutes
- Break: 5 minutes
- Maximum daily pairing: 6 hours
- GRIP auto-suggests intercept at block boundary

## 9. Security (4-Layer Model)

| Layer | Protection |
|-------|-----------|
| **Network** | Tailscale WireGuard / SSH / Unix socket perms |
| **Consent** | GRIP_MONITORING_ENABLED + GRIP_STEERING_MODE env vars |
| **Operation** | Gate G blocks dangerous steered commands |
| **Audit** | All steering + intercepts logged to api-audit.jsonl |

### Gate G: Blocked Steering Operations

These commands cannot be executed via steering -- the Grip must run them
directly:

- `git push --force`
- `git reset --hard`
- `rm -rf`
- `npm publish`
- `vercel` / `firebase deploy` / `docker push`
- Any deployment command

### Auth for TCP

TCP connections (remote) require `GRIP_PAIR_AUTH` env var on both sides.
Unix socket connections (local) are trusted by filesystem permissions.

Mutual auth (HMAC-SHA256 challenge-response) available via `--require-mutual`
flag on session_control_server.

## 10. Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GRIP_MONITORING_ENABLED` | `false` | Enable control server on session |
| `GRIP_STEERING_MODE` | `off` | off / soft / hard |
| `GRIP_PAIR_AUTH` | (empty) | Shared secret for TCP auth |
| `GRIP_STEERING_SOURCE` | (empty) | Set by control server on steered ops |
| `GRIP_WS_BIND` | `127.0.0.1` | Bind address (0.0.0.0 for remote) |

## 11. Migration from v2.0

The v2.0 "pair mode" was local worker spawning via Teams API. That
functionality has moved to `/delegate`:

| v2.0 Command | v3.0 Equivalent |
|--------------|-----------------|
| `/pair "task"` | `/delegate "task"` |
| `/pair status` | `/delegate status` |
| `/pair steer "msg"` (to worker) | `/delegate steer "msg"` |
| `/pair stop` | `/delegate stop` |

v3.0 `/pair` is exclusively for two-developer pair programming.

## 12. Testing Checklist (v3.0)

### Infrastructure

- [ ] `pip install websockets` installed
- [ ] `cd ~/.claude/mcp-servers/grip-pair && npm install && npm run build` succeeds
- [ ] MCP server registered via `claude mcp add grip-pair`
- [ ] Start session with `GRIP_MONITORING_ENABLED=true` -- socket appears at `/tmp/grip-session-*.sock`

### Local Pairing

- [ ] `python3 ~/.claude/lib/session_monitor.py list` shows the session
- [ ] `/pair connect <session_path>` establishes connection
- [ ] Events stream between sessions
- [ ] Steering: inject context appears in monitored session
- [ ] Gate G: steered force-push is blocked

### Intercept Protocol

- [ ] `/pair intercept` sends request to paired session
- [ ] Acceptance triggers save + evo + breed sequence
- [ ] Context summary injected into new Grip session
- [ ] Roles swap atomically (verify via `/pair status`)
- [ ] Genome bred offspring exists at `data/genomes/_bred.json`
- [ ] Urgent intercept (`/pair intercept!`) blocks Grip immediately

### Remote Pairing

- [ ] Tailscale peers can reach each other (`tailscale.py verify`)
- [ ] TCP auth with `GRIP_PAIR_AUTH` succeeds
- [ ] Event streaming works over Tailscale
- [ ] Steering works over Tailscale

### Unit Tests

- [ ] `python3 -m pytest tests/test_pair_intercept.py -v` -- 17 tests pass
- [ ] `python3 -m pytest tests/test_genome_breeder.py::TestDelightFilter -v` -- 5 tests pass

## File Locations

| File | Purpose |
|------|---------|
| `lib/session_control_server.py` | WebSocket server (per session) |
| `lib/pair_intercept.py` | Intercept protocol orchestrator |
| `lib/session_monitor.py` | Monitor client + CLI |
| `lib/tailscale.py` | Tailscale VPN helper |
| `lib/genome_breeder.py` | Genome breeding engine |
| `hooks/session-broadcast.py` | PostToolUse event broadcasting |
| `mcp-servers/grip-pair/` | MCP server (pair tools) |
| `modes/definitions/pair-mode.yaml` | Mode definition |
| `skills/pair-mode/SKILL.md` | Full skill documentation (source of truth) |
| `commands/pair.md` | /pair command definition |
| `commands/delegate.md` | /delegate command (v2.0 worker spawning) |
| `hooks/context-gate-hook.py` | Gate G (steering protection) |
| `tests/test_pair_intercept.py` | Intercept protocol tests |
| `tests/test_genome_breeder.py` | Genome breeder tests (incl. delight filter) |
