---
name: mesh
description: Three-layer Claude Code session mesh — local WebSocket bus, Tailscale cross-machine relay, and presence/locking. Enables real-time inter-session messaging and pair programming across collaborator machines. It is the transport pair-mode runs over when a pair spans two machines or two sessions.
status: Active
version: 1.0.0
effort: low
triggers:
  - "/mesh"
  - "mesh connect"
  - "mesh send"
  - "mesh broadcast"
  - "inform all sessions"
  - "tell every session"
  - "wake the other sessions"
  - "mesh pair"
  - "start pair programming"
  - "connect sessions"
  - "inter-session"
  - "grip-session-mesh"
see_also:
  - pair-mode (the pairing posture this mesh carries)
  - grip-anywhere
  - grip-channel
source: https://github.com/CodeTonight-SA/grip-session-mesh
---

# /mesh — Session Mesh

Connect Claude Code sessions across machines. Real-time. Zero token cost at idle.

**Carries `/pair`.** pair-mode is the pairing posture and mesh is its transport
when a pair spans machines or sessions; it replaced pair-mode's old transport
with cross-machine Tailscale relay, presence locking, and the Monitor-based zero-token idle bus. Install from:
https://github.com/CodeTonight-SA/grip-session-mesh

## Wired CLI (`bin/mesh`) — KISS context transfer

H441 shipped a 167-line bash dispatcher at `~/.claude/bin/mesh` that turns the
three layers into eight subcommands. Each subcommand takes ≤3 arguments; all
sixteen Goodhart-proof tests in `tests/test_mesh_cli.py` pass.

| Subcommand | Purpose | Topology |
|---|---|---|
| `mesh push` | Force this session to publish `~/.claude/sync/<id>-LIVE.md` NOW (bypass 5-min rate limit) | source side of any topology |
| `mesh pull <id>` | Print another session's LIVE.md | one peer |
| `mesh pull all` | Print every session's LIVE.md in sequence | many → 1 (raw) |
| `mesh collect` | One-line digest of every session's branch + head + dirty + timestamp | **many → 1** |
| `mesh broadcast <msg>` | Queue a message into every other session's inbox unless that session has certainly gone; sessions it cannot confirm are queued anyway, and it reports running, couldn't-confirm and gone counts (delivered as `[MESH-INBOX from=X kind=broadcast]` stderr signal on their next tool call). Queues only: an idle session is not reached until its next tool call, so to inform every session follow "Inform all sessions" below | **1 → many (message)** |
| `mesh inbox` | Read this session's pending inbox messages | pull messages for me |
| `mesh sync` | `push` + `collect` in one shot | **many ↔ many (gossip)** |
| `mesh list` | Show the sessions a broadcast reaches, each marked running (Claude Code process alive, or a heartbeat in the last 3 minutes) or couldn't confirm, and count the gone ones | discovery |

## Inform all sessions: queue, wake, report

`mesh broadcast` on its own does not inform every session. It only queues: a session sees
the message on its next tool call, so a session idling at a prompt is never reached, and
the presence roster still lists sessions that have ended. When asked to inform all (or
several) sessions, do all three steps:

1. **Queue it** for the audit trail and for harnesses without native messaging (Codex,
   OpenCode): `mesh broadcast "<message>"`.
2. **Wake every live Claude session.** Call `ListAgents`, which is Claude Code's own live
   roster and shows each peer as idle, busy or background. Send ONE `SendMessage` per
   peer, with `to` set to the name exactly as listed. Make the first line a self-contained
   plain sentence, because it is the preview the other session's human sees. An idle
   session starts a turn when the message arrives; a busy one reads it at its next tool
   round. Only the model can do this step: hooks cannot call these tools.
3. **Report per session, never a single count:** sent, held for approval (the peer runs
   in a different permission mode), refused (a `[Cross-session delivery notice]` says so),
   or unreachable (absent from `ListAgents`, such as Codex, OpenCode or an ended session).

A session accepts these messages only with `"crossSessionInbound": "accept"` in
`settings.json`. A session started before that key was set, or one whose settings an older
session re-serialised, may refuse; report it as refused rather than retrying. Never ask a
peer to do something your own session was refused permission for.

The same wake can ask a peer to take part, for example to read its inbox with `mesh inbox`
or to publish its state with `mesh push`, because the woken session's model acts on the
request.

Wake only when someone asks to inform sessions. Each wake starts a model turn in the woken
session, which uses that session's allowance and can interrupt its work, so routine
automated broadcasts (for example the `holistic-updated:` notices) stay queue-only.

Proved 2026-10-02: a test sent to one idle interactive peer woke it, and its reply came back
within a minute over the peer's own `uds:/tmp/cc-socks/<pid>.sock` socket. V>> approved
waking sessions the same day.

Why step 2 is needed: a queued message is seen only on the receiver's next tool call. If
an idle session ever starts receiving queued messages without one, step 2 can be dropped.

### Topology recipes (KISS — copy-paste ready)

```bash
# 1-to-many (publish my state for others to read):
mesh push                       # forces fresh dump now
# others, in their session:     mesh pull cc-<my-session-id>

# many-to-1 (gather state from every concurrent session):
mesh collect                    # one-line summary per session
mesh pull all                   # full LIVE.md from each session

# many-to-many (full gossip, every session both publishes and consumes):
mesh sync                       # = push + collect; run periodically
```

### Underpinnings (set up earlier; no manual action required)

- `hooks/mesh-auto-register-hook.py` — every CC session auto-joins the presence DB on first tool call
- `hooks/mesh-inbox-poll-hook.py` — every CC session surfaces pending inbox messages to stderr on every tool call
- `hooks/mesh-live-dump-hook.py` — every CC session refreshes its LIVE.md every 5 min (override via `GRIP_MESH_DUMP_INTERVAL_S`); `mesh push` bypasses this rate limit

The CLI is composable with shell pipes (`mesh collect | grep dirty=true`, `mesh pull all > snapshot.md`, etc.) and inherits `CLAUDE_MESH_DB_PATH` / `CLAUDE_MESH_SYNC_DIR` / `CLAUDE_MESH_HOOKS_DIR` env overrides for test isolation.

## Conceptual commands (future / pair-mode roadmap)

| Command | Description |
|---------|-------------|
| `/mesh connect [name]` | Join the session bus. Name defaults to `git rev-parse --show-toplevel` basename. |
| `/mesh list` | List all sessions — local + Tailscale remotes. |
| `/mesh send <name> <message>` | Direct message a session by name. |
| `/mesh broadcast <message>` | Send to every connected session. |
| `/mesh status` | Show own connection, name, peer count, roles. |
| `/mesh disconnect` | Leave gracefully. Notifies peers. |
| `/mesh remote add <tailscale_ip>` | Bridge to a remote machine's bus (Layer 2). |
| `/mesh remote list` | List configured Tailscale peer IPs. |
| `/mesh remote remove <ip>` | Remove a peer bridge. |
| `/mesh lock <resource>` | Acquire advisory lock for a file/resource (Layer 3). |
| `/mesh unlock <resource>` | Release advisory lock. |
| `/mesh locks` | Show all active locks and their holders. |
| `/mesh inbox` | Read broadcast inbox (messages sent while offline). |
| `/mesh pair <name>` | Invite a session into pair mode. |
| `/mesh grip` | Take the Grip role (active builder). |
| `/mesh guard` | Take the Guard role (navigator/reviewer). |
| `/mesh intercept` | Request a role swap. |
| `/mesh intercept!` | Urgent role swap — safety concern. Blocks the Grip. |

## Three Layers

| Layer | Technology | Scope |
|-------|-----------|-------|
| 1. Local bus | WebSocket + Monitor tool | Same machine, ms latency, zero token cost at idle |
| 2. Remote relay | Tailscale SSH + TCP bridge | Cross-machine, any OS — first solution to cover this |
| 3. Presence mesh | SQLite MCP server | Advisory locks + broadcast inbox |

## Protocol

### Step 1: Start the bus (once per machine)

```bash
# From the grip-session-mesh directory
npm run start --prefix server      # Layer 1: local WebSocket bus on :9474
```

Or if auto_start is configured:
```
/plugin config grip-session-mesh auto_start true
```

### Step 2: Connect this session

```
/mesh connect [optional-name]
```

This registers the current session and starts a `Monitor` tool watching
`~/.grip-session-mesh/inbound/<session-id>.jsonl` for incoming messages.
Incoming messages arrive as prompts and are acted on as instructions.

### Step 3: Discover peers

```
/mesh list
```

Shows: session name, machine, role (if in pair mode), last seen.

### Step 4: Send messages

```
/mesh send hal "what's your status on the auth refactor?"
/mesh broadcast "pushing to main in 5 — anyone blocking?"
```

### Step 5: Cross-machine (Layer 2)

Add a Tailscale peer to bridge the local buses:

```
/mesh remote add 100.64.0.10   # Andre's machine
/mesh remote add 100.64.0.10  # Toopie's machine
/mesh remote add 100.64.0.10   # GRIP VPS
```

Known GRIP collaborator IPs (from `rules/operational-context.md`):
- Andre: `100.64.0.10`
- Toopie: `100.64.0.10`
- VPS: `100.64.0.10`

### Step 6: Locks (Layer 3, requires presence MCP)

```
/mesh lock src/auth/middleware.py
# ... make your edits ...
/mesh unlock src/auth/middleware.py
```

Advisory only — other sessions warned, not blocked. Locks expire after 10 minutes.

## Pair Programming Protocol

Two developers, two sessions, one codebase.

```
# Developer A (Grip — active builder)
/mesh connect hal
/mesh pair andre
/mesh grip

# Developer B (Guard — navigator/reviewer)
/mesh connect andre
# ... accepts the pair invitation ...
/mesh guard
```

### Roles

| Role | Responsibilities |
|------|-----------------|
| **Grip** | Writes code, runs tools, makes commits |
| **Guard** | Reviews, steers, raises concerns via `/mesh steer` |

### Role swap

```
/mesh intercept    # polite request — Grip decides when to hand over
/mesh intercept!   # urgent — immediately pauses Grip's current tool call
```

## Inbound Message Handling

Monitor tool fires on message arrival. Messages prefixed:

```
[mesh:hal] what's your current branch?
[mesh:broadcast] stand-up in 5 minutes
[mesh:pair:andre] INTERCEPT — please pause before that delete
```

Safeguard: messages containing destructive keywords (`rm -rf`, `DROP TABLE`,
`git push --force`) always require explicit `/mesh approve` before execution.

## Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `port` | 9474 | WebSocket bus port |
| `auto_start` | false | Start bus on plugin load |
| `idle_shutdown_minutes` | 30 | Shutdown when no sessions connected |
| `presence_enabled` | true | Enable Layer 3 MCP |
| `require_confirmation` | false | Review all mesh messages before acting |
| `pair_role` | null | Current pair role (grip/guard) |

## Security

- Bus token at `~/.grip-session-mesh/token` (mode 0600)
- Bus binds `127.0.0.1` by default — not network-exposed
- Layer 2 relay uses Tailscale auth (mutual device certs)
- Destructive commands require explicit `/mesh approve`
- All messages logged to `~/.grip-session-mesh/log.jsonl`

## Troubleshooting

```bash
curl -sf http://127.0.0.1:9474/health | jq .
sqlite3 ~/.grip-session-mesh/presence.db "SELECT * FROM sessions;"
tail -f ~/.grip-session-mesh/log.jsonl | jq .
```

## Tests (9/9 relay + 8/8 presence passing, 2026-05-06)

```bash
cd ~/CodeTonight/grip-session-mesh && npm test
```
