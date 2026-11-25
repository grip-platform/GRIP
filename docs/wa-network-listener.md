# GRIP WA Network Listener — Operator Guide

Always-on Mac-side WhatsApp inbound listener. Polls the local
`grip-channel` MCP daemon (port 3101), gates each incoming message
through the per-contact control schema, and spawns a capability-locked
local `claude -p` for any allowed message — then replies through
`lib.whatsapp_transport.post()` with a single "GRIP here" prefix.

## Why Mac-side

WhatsApp's noise-protocol handshake is blocked on our Hetzner VPS, so
the WA bridge has to live on the Mac. Slack and Discord run from the
VPS through the `relay-dispatcher` service. This listener is the
symmetric Mac-side equivalent for WhatsApp.

## What it does

For every JID in the allowlist, every 7 seconds (configurable):

1. Poll the local `grip-channel` daemon for new messages after the
   per-JID cursor.
2. For each new message:
   - Drop silently if the JID is not on the allowlist.
   - Drop silently if `state/wa-scopes/<slug>/control.json` reports
     mute, opt-out, or an unexpired pause.
   - If the message is a control command (parsed by the symmetric
     `lib/wa_control_commands` module), dispatch it and acknowledge.
   - Otherwise, spawn `claude -p --tools "Read,Grep,Glob"` with the
     message as the prompt, then send the answer back via
     `whatsapp_transport.post`.
3. Advance the per-JID cursor on success (atomic write).
4. Append a structured line to `state/wa-network-listener/listener.log`.

## Files at a glance

| Path | Purpose |
|------|---------|
| `lib/wa_network_listener.py` | The listener module + `WANetworkListener` class |
| `config/wa-network-listener.ecosystem.cjs` | pm2 entry for the listener |
| `config/grip-caffeinate.ecosystem.cjs` | pm2 entry for `caffeinate -i` |
| `state/wa-network-listener/allowlist.json` | Phone JID -> human name (gitignored) |
| `state/wa-network-listener/cursor.json` | Per-JID last-seen message id (gitignored) |
| `state/wa-network-listener/listener.log` | Per-event structured log (gitignored) |
| `state/wa-network-listener/sessions/` | Per-JID claude session ids (gitignored) |

## Start it

```bash
cd $HOME/.claude

# 1. Stop any manual ``caffeinate -i &`` you previously started by hand
#    (avoid double-locking sleep).
pkill -f "caffeinate -i" || true

# 2. Make sure the local grip-channel daemon is alive on port 3101.
curl -sf http://127.0.0.1:3101/health | jq .

# 3. Create the allowlist (see below for the canonical contacts).
nano state/wa-network-listener/allowlist.json

# 4. Start both pm2 entries.
pm2 start config/wa-network-listener.ecosystem.cjs
pm2 start config/grip-caffeinate.ecosystem.cjs
pm2 save
```

## Allowlist contents

The contacts allowed to drive a spawn, keyed by canonical phone ID (per
`feedback_whatsapp_route_to_phone_jid_until_lid_fix`). The values below are
obvious fakes showing the shape; the real IDs live only in the untracked
allowlist on the machine that runs the listener:

```json
{
  "27000000000@s.whatsapp.net": "V (V>>)",
  "000000000000000000@g.us": "CodeTonight group"
}
```

The listener treats any JID not in this file as off-allowlist and drops
the message silently — no spawn, no reply.

## Stop / status / logs

```bash
pm2 status grip-wa-network-listener
pm2 logs grip-wa-network-listener -- --raw
pm2 stop grip-wa-network-listener
pm2 restart grip-wa-network-listener        # picks up allowlist edits
pm2 delete grip-wa-network-listener         # full removal
```

Per-event structured log:

```bash
tail -f $HOME/.claude/state/wa-network-listener/listener.log
```

## Caffeinate transition

If you were running the manual stopgap `caffeinate -i &`, kill it
before starting the pm2 entry. Two caffeinate processes will both
keep the system awake, but the manual one is invisible to `pm2 list`
and will survive a reboot only by accident.

```bash
pkill -f "caffeinate -i"
pm2 start config/grip-caffeinate.ecosystem.cjs
pm2 save
pm2 list                # confirm 'grip-caffeinate' is online
```

## Troubleshooting

| Symptom | First check |
|---------|-------------|
| Listener offline (`pm2 status`) | `pm2 logs grip-wa-network-listener` — look for ImportError, missing venv |
| No messages picked up | Local grip-channel up? `curl -sf 127.0.0.1:3101/health` |
| Allowlisted JID still silent | Check `state/wa-scopes/<slug>/control.json` — mute / opt-out / pause set by broly #1's control commands |
| Replies missing the "GRIP here" prefix | Read of upstream `lib/whatsapp_transport.py::post` should still go through `wa_network_listener.grip_prefix` — verify no caller is bypassing |
| Tight CPU usage | Lower the poll cadence: `GRIP_WA_LISTENER_POLL_SEC=15 pm2 restart grip-wa-network-listener --update-env` |

## Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `GRIP_WA_LISTENER_POLL_SEC` | `7` | Seconds between poll ticks |
| `GRIP_WA_DAEMON_URL` | `http://127.0.0.1:3101/mcp` | MCP endpoint for grip-channel |
| `GRIP_WA_SPAWN_TIMEOUT_S` | `120` | Max seconds for a single claude spawn |
| `GRIP_WA_CLAUDE_BIN` | `claude` | Path to the Claude Code CLI |
| `CLAUDE_DIR` | `$HOME/.claude` | Override the GRIP install path |

## Capability lock (do not relax)

The listener spawns `claude -p` with `--tools "Read,Grep,Glob"`. This
is the only barrier between untrusted WhatsApp content and arbitrary
host write/exec (`rules/untrusted-content-capability-lock.md`). The
test `test_capability_lock_present_in_argv` is the canary if anyone
ever tries to widen the allowlist.

## Integration with broly #1's control commands

`lib/wa_control_commands.py` (parser) and `lib/wa_control_state.py`
(state reader) are owned by broly #1's PR. The listener imports them
lazily and degrades gracefully if either is missing — the listener
keeps running, the control gate is just inactive until that PR lands.
Both PRs merge in parallel.
