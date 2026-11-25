---
name: osa-phone-action
description: Inbound WhatsApp → macOS OSA action bridge. Decodes WhatsApp messages in the format "/osa <script>" or "/mac <script>" from authorised JIDs and dispatches to the HAL OSA registry. The inbound half of the phone bridge (outbound is /send-to-phone).
status: Active
effort: low
version: 1.0.0
created: 2026-05-02
triggers:
  - /osa-phone-action
  - "phone triggered mac action"
  - "whatsapp to mac"
  - "remote mac control via whatsapp"
  - "phone mac bridge"
  - inbound OSA via WhatsApp
integrates:
  - skills/osa/SKILL.md (OSA dispatcher)
  - lib/osa_phone_bridge.py (parse + dispatch library)
  - mcp__grip-channel (channel_read for inbound)
  - skills/send-to-phone/SKILL.md (confirmation replies)
see_also:
  - osa
  - send-to-phone
  - send-whatsapp
  - grip-notify
---

# /osa-phone-action — Phone → Mac OSA Bridge

**Send a WhatsApp message from your phone. Your Mac does it.**

This skill is the inbound half of the phone bridge. It monitors incoming
WhatsApp messages from authorised numbers, parses `/osa` and `/mac` commands,
and dispatches them to the HAL OSA registry — the same registry `/osa` uses
interactively.

**Plain English**: you can text your own Mac from your phone and make it do
things. Open Finder, play a song, lock the screen, send a notification — if
it's in the OSA registry, you can trigger it from WhatsApp.

---

## Security model

**PARAMOUNT**: only messages from authorised JIDs are ever dispatched.
Messages from any other number are silently dropped (no reply, no error).

| JID | Name | Permitted tiers |
|-----|------|----------------|
| the operator ID, from `config/wa-identities.local.json` | V>> | Tier 1 + 2 |

Tier 3 scripts (`sleep_now`, `shutdown`, `restart`) are **never permitted
via phone command** — they require interactive confirmation in a live GRIP
session. The tier ceiling for phone-originated commands is tier 2.

This table only describes the allowlist; nothing reads it. The allowlist itself
is `ALLOWED_JIDS` in `lib/osa_phone_bridge.py`, built from the operator ID in the
private, untracked `config/wa-identities.local.json` (`lib/wa_identities.py`).
With nothing configured it is empty and every message is dropped. A real
number is never written in this file or in the code.

---

## Command format

Messages from an authorised JID are parsed for these patterns:

| Pattern | Example | Maps to |
|---------|---------|---------|
| `/osa <script>` | `/osa battery_get` | OSA script `battery_get` |
| `/osa <script> <params>` | `/osa volume_set level=50` | OSA script with params |
| `/mac <script>` | `/mac mute` | Same as `/osa` (alias) |
| `/mac mute` | `/mac mute` | OSA `mute` |
| `/mac play` | `/mac play` | OSA `music_play` |
| `/mac pause` | `/mac pause` | OSA `music_pause` |
| `/mac next` | `/mac next` | OSA `music_next_track` |
| `/mac volume <n>` | `/mac volume 40` | OSA `volume_set level=40` |
| `/mac notify <text>` | `/mac notify Build done` | OSA `notification body=Build done` |
| `/mac lock` | `/mac lock` | OSA `screen_lock` |
| `/mac battery` | `/mac battery` | OSA `battery_get` |
| `/mac list` | `/mac list` | Reply with list of permitted scripts |
| `/mac help` | `/mac help` | Reply with command format guide |

**Composite macros** (multi-step rituals, dispatched as `MacroCall`):

| Command | Steps |
|---------|-------|
| `/mac morning` | caffeinate 8h + screen wake + music play + battery-aware spoken greeting |
| `/mac sprint` | caffeinate 2h + mute + screen wake + focus notification |
| `/mac meeting` | music pause + screen wake + volume report + notification |
| `/mac wrap` | music pause + EOD notification + screen lock |
| `/mac status` | battery + now playing + frontmost app snapshot |

Any message that does NOT start with `/osa` or `/mac` is passed through to
the active GRIP session as a normal steering command (the existing
grip-channel relay handles this path — `/osa-phone-action` only intercepts
the `/osa`/`/mac` prefix).

---

## Execution Protocol

### Step 0 — Pre-flight

1. Verify HAL is installed: `test -x $HOME/.hal/bin/osa`
   - If missing: skip dispatch, reply "HAL not installed — /osa-phone-action unavailable"
2. Verify grip-channel is running: `curl -sf http://127.0.0.1:3101/health`
   - If down: cannot receive inbound messages — log and exit

### Step 1 — Inbound message handling

This step is triggered by `lib/osa_phone_bridge.py::OsaPhoneBridge.on_message()`,
which grip-channel calls when a WhatsApp message arrives from a registered JID.

```python
from lib.osa_phone_bridge import OsaPhoneBridge
from lib.wa_identities import operator_jid

bridge = OsaPhoneBridge()
bridge.on_message(
    jid=operator_jid(),
    text="/mac battery",
)
```

The bridge library handles all parsing, allowlist checking, and dispatch.
This skill documents the protocol; `lib/osa_phone_bridge.py` is the runtime.

### Step 2 — Parse command

Call `bridge.parse_command(text)` which returns:
- `ScriptCall(script_name="battery_get", params={})` — valid single command
- `MacroCall(name="morning")` — composite macro (multi-step ritual)
- `None` — message is not an OSA command (pass through to GRIP relay)
- `ParseError(reason=...)` — unknown script name → reply with `/mac list`
- Tier-3 attempt → `TierViolation` raised in dispatch

### Step 3 — Dispatch

For a valid `ScriptCall`:
```bash
TOKEN=$($HOME/.hal/bin/osa mint <script_name> --ttl 60)
$HOME/.hal/bin/osa run <script_name> [params...] --token "$TOKEN"
```

Tier-1 scripts skip the token step (auto-approved).

### Step 4 — Reply to phone

After dispatch, send the result back to the originating JID:

**Success:**
```
battery_get: 87% — not charging
```
or for mutation scripts:
```
volume_set: done (level=50)
```

**Failure:**
```
osa error: script mute failed — Audio output unavailable
```

Reply is sent via `channel_reply` back to the same JID.

### Step 5 — Audit

Every dispatch appends one JSON line to `~/.claude/logs/osa-phone.log`:
```json
{"ts":"2026-05-02T...","jid":"27000000000@s.whatsapp.net","cmd":"/mac battery","script":"battery_get","result":"ok","output":"87%"}
```

---

## Example session from V>>'s phone

V>> opens WhatsApp and texts his own number (the grip-channel bridge number):

```
V>>: /mac battery
Mac: battery_get: 91% — charging

V>>: /mac mute
Mac: mute: done

V>>: /mac notify Sprint complete — 4 PRs merged
Mac: notification: delivered

V>>: /mac shutdown
Mac: Tier-3 action 'shutdown' is not permitted via phone. Open a GRIP session to run it.

V>>: /mac list
Mac: Permitted scripts:
     battery_get, volume_get, volume_set, mute, unmute,
     screen_lock, screen_wake, notification, speak,
     music_now_playing, music_play, music_pause,
     music_next_track, music_previous_track, clipboard_set

V>>: What's left to ship?
Mac: [passes through to active GRIP session via grip-channel relay]
```

---

## Integration with grip-channel

`/osa-phone-action` is not a standalone daemon — it hooks into grip-channel's
inbound message routing. When a WhatsApp message arrives at grip-channel:

1. grip-channel checks if the message starts with `/osa` or `/mac`
2. If yes: grip-channel hands it to `OsaPhoneBridge.on_message()`
3. If no: grip-channel passes it to the active GRIP session relay (existing path)

The hook is registered in `lib/osa_phone_bridge.py`. grip-channel does not
need to be modified — it exposes an `on_message` registration API.

---

## Setup

1. Ensure HAL is installed at `~/.hal`:
   ```bash
   git clone https://github.com/CodeTonight-SA/hal.git ~/.hal
   ```

2. Ensure grip-channel is running on port 3101:
   ```bash
   pm2 start grip-channel  # local Mac
   ```
   or on VPS:
   ```bash
   ssh root@100.64.0.10 "pm2 start grip-channel"
   ```

3. Register the bridge hook (one-time):
   ```bash
   PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
   from lib.osa_phone_bridge import OsaPhoneBridge
   OsaPhoneBridge().register()
   print('Bridge registered with grip-channel')
   "
   ```

4. Test from phone: text your bridge number `/mac battery`

---

## Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Unauthorised JID | Silently dropped — no reply, no error log |
| HAL not installed | Reply: "HAL not installed" |
| Unknown script | Reply: "Unknown script — send /mac list" |
| Tier-3 attempt | Reply: "Tier-3 not permitted via phone" |
| HAL dispatch error | Reply with sanitised error; log full error to `logs/osa-phone.log` |
| grip-channel down | Bridge unregistered — hook not called (messages go to relay or drop) |

---

## Related

- `lib/osa_phone_bridge.py` — runtime library (parse + dispatch + hook)
- `skills/osa/SKILL.md` — interactive OSA skill (same dispatcher, different entry point)
- `skills/send-to-phone/SKILL.md` — OUTBOUND path (Mac → phone)
- `skills/send-whatsapp/SKILL.md` — WhatsApp-specific outbound
- `~/.claude/logs/osa-phone.log` — audit log for all phone-originated dispatches
