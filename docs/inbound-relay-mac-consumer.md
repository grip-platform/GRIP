# Mac Inbound-Queue Drainer

Closes the inbound-relay gap: WhatsApp messages to GRIP that arrived while no
live MCP-subscribed Claude Code session existed were buffered to
`data/inbound-queue.jsonl` and **never actioned** (Gen 1538 diagnosis:
757 `notified` / 455 `queued`; the `queued` ones were orphaned). The VPS owns
the Slack/Discord relay; WhatsApp lives on the Mac (Hetzner IP blocked) and had
no analogue consumer. `lib/relay_queue_drain.py` is it.

## What it does (plain English)

A small always-on Mac helper watches the file the WhatsApp daemon already writes,
and for each NEW message that **explicitly addresses GRIP** ("GRIP: …", "@grip …",
"hey grip …", or "grip <do/list/run/…>"), it runs one locked-down `claude -p`
and replies on WhatsApp — exactly the pattern the VPS already uses for Slack.
It reuses the tested `lib/relay_dispatcher.py` for all of the actual work
(decision gate, capability lock, 3-rung "Heard → Still working → reply" acks,
the reply transport).

## Safety model (correct-by-construction)

| Control | Mechanism |
|---|---|
| **Never replies to the wrong person** | Reply recipient is resolved from the daemon-authenticated sender NAME via an explicit canonical-phone-JID allowlist — **never** the inbound `@lid` (which resolves to the wrong contact: `feedback_whatsapp_route_to_phone_jid_until_lid_fix`). Unknown sender → skipped. |
| **Operator-only-first** | v1 allowlist = `{V>>}` only. External parties are skipped — auto-replying to them needs the full `@lid`→phone resolver + GRIP-identity-prefix + comms-style gating (deferred). |
| **No ambient chatter actioned** | The Mac daemon captures V>>'s ENTIRE WhatsApp stream across all chats. The drainer requires an **explicit GRIP-address** before forwarding, so personal messages ("My babe!! Wanneer is lunch?") are never actioned. Live-verified: 71-row backlog → 0 actioned. |
| **Read-only spawn by default** | `--tools Read,Grep,Glob`. Full capability requires the opt-in `GRIP_RELAY_OPERATOR_FULL_CAPABILITY` (unset by default — preplan D-1). |
| **Exactly-once** | Per-row fingerprint + a persisted floor (`state/relay/inbound-drain.state.json`). A failed spawn is retried next poll (at-least-once), never silently dropped. |
| **New-only on first boot** | Existing buffered messages are NOT replayed (no burst of stale replies). `--drain-backlog` to opt in. |
| **Sole actioning path** | Run WITHOUT a `channels-enabled` Claude Code session so the in-session MCP subscriber never double-actions (preplan D-3). |

## Install + go-live

```bash
# 1. install the launchd agent. Substitutes the machine path, the ABSOLUTE
#    claude binary — launchd's PATH is minimal (/usr/bin:/bin), so a bare
#    "claude" raises FileNotFoundError (the 2026-06-01 storm root cause) —
#    AND the claude CONFIG DIR: a login made under CLAUDE_CONFIG_DIR lives in
#    a config-dir-scoped keychain entry, so a spawn without the var exits 1
#    with "Not logged in" inside the result JSON, stderr empty (the
#    2026-07-13 04:10 live failure).
CB="$(command -v claude)"; CD="$(dirname "$CB")"
GH="${CLAUDE_DIR:-$HOME/.claude}"; GCD="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
sed -e "s|__GRIP_HOME__|$GH|g" \
    -e "s|__CLAUDE_BIN__|$CB|g" \
    -e "s|__CLAUDE_DIR__|$CD|g" \
    -e "s|__CLAUDE_CONFIG_DIR__|$GCD|g" \
  "$GH/config/launchd/com.grip.relay-queue-drain.plist.template" \
  > ~/Library/LaunchAgents/com.grip.relay-queue-drain.plist
launchctl load ~/Library/LaunchAgents/com.grip.relay-queue-drain.plist
#    NOTE (AGORA #3971): the substituted values are RESOLVED ABSOLUTE PATHS,
#    baked at install time. If ~/.claude moves, the claude binary relocates
#    (brew upgrade), or CLAUDE_CONFIG_DIR changes, the agent silently uses the
#    stale paths — re-run this install block after any such change.

# 2. acceptance (the exogenous anchor — a real phone round-trip):
#    send "GRIP: are you there?" from WhatsApp → expect a reply within ~90s.
#    then bounce the channel daemon and confirm recovery — IF this machine runs
#    it under pm2 (`command -v pm2 && pm2 restart grip-channel`; not all
#    machines do — skip when pm2 or the process is absent, AGORA #3971), send
#    another message → expect a reply again.

# dry-run first (no replies sent), to see what WOULD be actioned:
GRIP_RELAY_CLAUDE_CWD=$HOME/.claude $HOME/.claude/venv/bin/python3 \
  $HOME/.claude/lib/relay_queue_drain.py --once

# uninstall:
launchctl unload ~/Library/LaunchAgents/com.grip.relay-queue-drain.plist
```

First message on a fresh channel should be a **question** ("GRIP: are you there?")
— it reliably classifies actionable and warms the per-channel session; subsequent
"GRIP: do X" commands then fire. (See deferred follow-ups.)

## Deferred follow-ups (tracked)

1. **Multi-sender** — extend the reply-JID allowlist + an `@lid`→phone resolver so
   Other parties' addressed messages can be actioned, with the GRIP-identity
   prefix + comms-style gates. Until then, external senders are surfaced for the
   operator to handle, not auto-replied.
2. **Cold-bootstrap** — a bare "GRIP: do X" on a session-less channel scores 0.25
   (context) under the shared `relay_decision` contract and won't fire until the
   channel is warm. Possible tuning: treat operator-addressed messages as actionable
   regardless of session warmth (drainer-local, without touching the VPS contract).
3. **Webhook path** — the preplan's "just set `GRIP_RELAY_WEBHOOK_URL`" idea is
   currently broken for WhatsApp (`postRelayWebhook` emits `channel_id=''` → 400 at
   the dispatcher); a push-based variant would need the daemon to emit a resolved
   WA `channel_id` first.
