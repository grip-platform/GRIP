---
name: send-to-phone
description: Universal phone delivery command. Routes any content to the operator's personal device via the best available transport (WhatsApp → Slack DM → iMessage cascade). Wraps /send-whatsapp as the primary path. V>>'s details pre-configured. Guaranteed delivery via converge loop.
status: Active
effort: low
version: 1.0.0
created: 2026-05-02
triggers:
  - /send-to-phone
  - "send to my phone"
  - "send to operator phone"
  - "push to phone"
  - "deliver to phone"
  - "phone me"
  - "forward to phone"
  - "whatsapp or slack this"
integrates:
  - skills/send-whatsapp/SKILL.md (primary transport)
  - mcp__slack__slack_post_message (fallback transport)
  - skills/osa/SKILL.md (iMessage fallback via imessage_send)
  - mcp__grip-channel (channel_reply)
see_also:
  - send-whatsapp
  - osa
  - grip-notify
  - osa-phone-action
---

# /send-to-phone — Universal Phone Delivery

**One command. Any content. Delivered to the operator's phone via the best
available transport.**

This is the canonical "push to phone" entry point. It wraps `/send-whatsapp`
as the primary path, cascades to Slack DM on WhatsApp failure, and falls back
to iMessage as a last resort. You never need to choose the transport — the
skill selects it.

**Plain English**: tell GRIP to send something to your phone. It figures out
the best path (WhatsApp, then Slack, then iMessage) and retries if one fails.

---

## Transport Cascade

```
Input
  │
  ▼
[0] outbound-stop brake engaged?     ← yes: HALT, nothing sent on ANY transport
  │  no
  ▼
[1] WhatsApp via grip-channel        ← primary (fastest, richest)
  │  on failure (3 retries)
  ▼
[2] Slack DM to U0EXAMPLE01          ← secondary (always available)
  │  on failure (3 retries)
  ▼
[3] iMessage to the maintainers  ← tertiary (AppleScript, no internet needed)
  │  on failure
  ▼
[HALT] All transports failed — report to operator
```

Each transport attempt uses an inner 3-retry converge loop before cascading
to the next. The escalation chain is deterministic: once transport N succeeds,
transports N+1..3 are not attempted.

**The brake comes first.** While `~/.claude/state/session/outbound-stop.json`
exists (`grip-stop on`), nothing is sent on any transport, `urgent` included,
and the skill says so in the session. A WhatsApp refusal from the brake is never
a reason to try Slack or iMessage: that would route around the operator's own
switch, which only the operator releases.

---

## Operator config (pre-built for V>>)

```yaml
operator: v>>
whatsapp_jid: operator  # read from config/wa-identities.local.json by lib/wa_identities.py
slack_user_id: "U0EXAMPLE01"
slack_workspace: "CodeTonight"
imessage_handle: "the maintainers"
default_transport: whatsapp
caption_style: terse
```

To configure for a different operator: override any key via args (e.g.
`/send-to-phone to:development@codetonight.co.za "message"`).

---

## Usage

```bash
/send-to-phone "Sprint complete — 3 PRs merged"
# → WhatsApp text to V>>'s number

/send-to-phone ~/.claude/data/outbound/happi-rwa-explainer-2026-05-02.pdf
# → WhatsApp file upload to V>>'s number

/send-to-phone '{"wave": 4, "status": "shipped"}'
# → JSON code block to V>>'s WhatsApp

/send-to-phone urgent "Server down on VPS"
# → skips WhatsApp, goes direct to iMessage (urgent flag)

/send-to-phone urgent "Server down on VPS"     # while `grip-stop on` is engaged
# → nothing sent on any transport, iMessage included; the session shows:
#   outbound-stop is engaged; nothing sent (release with grip-stop off)

/send-to-phone to:27000000009 "Test delivery to Jane"
# → sends to Jane's number (not in pre-config — requires confirm)
```

---

## Execution Protocol

### Step 0 — Parse args

First, the brake: if `~/.claude/state/session/outbound-stop.json` exists, stop
here and send nothing on any transport, `urgent` included. Say
`outbound-stop is engaged; nothing sent (release with grip-stop off)` in the
session. Only the operator releases the brake.

| Token | Meaning |
|-------|---------|
| `urgent` | Skip WhatsApp, go direct to iMessage |
| `to:<jid_or_email>` | Override recipient (requires confirm if not in config) |
| Everything else | The content to send |

### Step 1 — Classify content

Delegate to `/send-whatsapp`'s classification logic:
- Absolute file path → file upload
- JSON/dict → code block
- Code fragment → code block with language hint
- URL → text with URL on its own line
- Anything else → plain text

### Step 2 — Transport cascade converge loop

```
converge(
    criterion: delivery_confirmed_on_any_transport
    improve:   try_next_transport
    max_depth: 3   (three transports)
)
```

**Before every transport, check the brake again.** The operator can engage it
while an earlier transport is failing. If
`~/.claude/state/session/outbound-stop.json` exists now, HALT the cascade and
report it as in Step 0, even though an earlier transport was already tried.

**Transport 1 — WhatsApp:**
- Pre-flight: `curl -sf http://127.0.0.1:3101/health`
- If healthy: invoke `/send-whatsapp` protocol (includes its own 3-retry inner loop)
- On success: `delivery_confirmed`, exit cascade
- On failure (grip-channel down OR 3 retries exhausted): cascade to Transport 2
- An answer that starts `FAILED: outbound-stop is engaged` is the brake, not a
  failure: HALT the whole cascade, as in Step 0

**Transport 2 — Slack DM:**
- Invoke `mcp__slack__slack_post_message` with `channel_id=U0EXAMPLE01`
- For files: use `mcp__slack__slack_upload_file` then permalink post
- On success: `delivery_confirmed`, exit cascade; note in output "sent via Slack"
- On failure: cascade to Transport 3

**Transport 3 — iMessage:**
- Invoke `/osa imessage_send handle=the maintainers text=<content>`
- For files: iMessage cannot natively receive GRIP files via AppleScript — send
  a text summary with the file path instead
- On success: `delivery_confirmed`, exit cascade; note in output "sent via iMessage"
- On all transports exhausted: HALT with full error log

### Step 3 — Confirm to user

```
Delivered via WhatsApp. [content summary, ≤60 chars]
```
or, when WhatsApp answered `FAILED: outbound-stop is engaged` (Slack and
iMessage were never tried):
```
outbound-stop is engaged; nothing sent (release with grip-stop off)
```
or
```
WhatsApp unavailable — delivered via Slack DM.
```
or
```
WhatsApp + Slack unavailable — delivered via iMessage.
```
or
```
All transports failed. Reason: [last error]. Manual action required.
```

---

## Urgency mode

`/send-to-phone urgent "message"` bypasses the cascade and goes directly to
iMessage. It never bypasses the brake: while `grip-stop` is engaged, `urgent`
sends nothing either. Use when:
- grip-channel is known to be down
- The message is time-critical (system alert, security event)
- You want guaranteed phone delivery without any internet dependency

iMessage via AppleScript works as long as the Mac is online + Messages.app
is authorised — it uses Apple's own APNs infrastructure, not grip-channel.

---

## Differences from /send-whatsapp

| Aspect | `/send-whatsapp` | `/send-to-phone` |
|--------|-----------------|----------------|
| Transport | WhatsApp only | WhatsApp → Slack → iMessage cascade |
| Failure | Slack escalation + stop | Full cascade + report |
| Use when | You know WhatsApp is up | You need guaranteed delivery regardless |
| Entry point | WhatsApp-specific | Transport-agnostic |

**Recommendation**: use `/send-to-phone` as the default. Use `/send-whatsapp`
directly only when you explicitly want WhatsApp semantics (e.g. file upload
with WhatsApp caption, thread reply).

---

## Future / Phase 3

- **Priority routing**: route by content type (e.g. code always to Slack for
  syntax highlighting, files always to WhatsApp for native preview)
- **Read receipts**: if the recipient's WhatsApp signals "read", log to
  `learning/insights.jsonl` as a delivery confirmation
- **Broadcast**: `/send-to-phone broadcast "message"` sends to multiple
  configured team members simultaneously

---

## Related

- `bin/grip-stop` and `rules/full-power-stop-override.md` — `grip-stop on` writes the outbound-stop marker this skill checks first; `grip-stop off` releases it, and only the operator runs either
- `skills/send-whatsapp/SKILL.md` — primary transport implementation
- `skills/osa/SKILL.md` — iMessage via AppleScript (tertiary transport)
- `skills/osa-phone-action/SKILL.md` — INBOUND path (phone → Mac)
- `skills/grip-notify/SKILL.md` — macOS-local + iMessage + Slack push notifications
- `rules/operational-context.md` — V>>'s user_id `U0EXAMPLE01`, WhatsApp JID
