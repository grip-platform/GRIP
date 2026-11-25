---
name: send-whatsapp
description: Send any content to a WhatsApp recipient via grip-channel. Accepts text, files, structured data, notes, function output — GRIP infers the delivery type. Guaranteed delivery via inner converge loop (3-attempt retry + escalation). V>>'s number is pre-configured. Configurable for any recipient.
status: Active
effort: low
version: 1.0.0
created: 2026-05-02
triggers:
  - /send-whatsapp
  - /send-to-phone
  - "send to whatsapp"
  - "send to V>>'s whatsapp"
  - "send to phone"
  - "whatsapp this"
  - "message V>> on whatsapp"
integrates:
  - mcp__grip-channel (channel_reply + channel_send_file)
  - skills/grip-notify/SKILL.md (sister skill — macOS/Slack push)
see_also:
  - grip-notify
  - slack-update
  - professional-pdf
---

# /send-whatsapp — Guaranteed WhatsApp Delivery

Send **any content** to a WhatsApp recipient. GRIP infers whether the input is
text, a file, structured data, or a note — selects the right transport path —
then runs a converge loop to guarantee delivery before returning.

**Plain English**: one command, any content, delivered or escalated. No manual
tool selection needed.

---

## Recipients

Pre-configured recipients live in this table. Invoke with just `/send-whatsapp`
and the content — GRIP uses the default recipient (V>>) unless told otherwise.

| Alias | Name | WhatsApp JID | Caption style |
|-------|------|-------------|---------------|
| `v>>` (default) | V>> | the operator ID (see below) | terse, no emoji |
| `self` | (same as v>>) | the operator ID (see below) | terse, no emoji |

Real WhatsApp IDs are never written in this file. They live in the private,
untracked `config/wa-identities.local.json` (shape:
`config/wa-identities.example.json`). Read the operator ID at send time with
`PYTHONPATH="$HOME/.claude" "$HOME/.claude/bin/grip-py" -c 'from lib.wa_identities import operator_jid; print(operator_jid())'`;
if it prints `None`, nothing is configured, so send nothing and say so.

To add a recipient: put their JID (`[country code][number]@s.whatsapp.net`) in
the `contacts` table of that private file, add a row here naming its key, and
read it with `contact_jid("<key>")`.

---

## Input Types & Delivery Paths

GRIP classifies the input before selecting a transport. Classification is
**deterministic** — the first matching rule wins.

| Rule | Detection | Path | Tool |
|------|-----------|------|------|
| **File** | Input is an absolute path that `os.path.exists()` confirms | Send binary + filename as caption | `channel_send_file` |
| **URL** | Input matches `https?://` | Send as text with URL on its own line | `channel_reply` |
| **Structured data** | Input is a `dict`, `list`, or parses as JSON | Format as ` ```json ``` ` code block | `channel_reply` |
| **Code / function** | Input starts with `def `, `class `, `function`, `const `, etc. | Format as ` ``` ``` ` code block with language hint | `channel_reply` |
| **Text / note / response** | Anything else | Send as-is (with optional prefix) | `channel_reply` |

### File path shorthand

If the user says "send the PDF I just generated" or references a draft by name,
resolve to the full absolute path before classification.

**The bridge sends files from one folder only: `~/.claude/data/outbound/`.**
`channel_send_file` and `channel_send_voice` read through `readConfinedFile` in
`mcp-servers/grip-channel/src/mcp-guard.ts`, which resolves symbolic links and
then refuses any path outside `DEFAULT_FILE_ROOT` (that folder), unless
`GRIP_CHANNEL_FILE_ROOTS` names more folders. Copy a file from
`~/.claude/drafts/` or anywhere else into `~/.claude/data/outbound/` first.
The bridge does not create that folder, so on a fresh install make it first
with `mkdir -p ~/.claude/data/outbound`, or the copy fails.
`data/` is gitignored, so nothing placed there is committed.

---

## Execution Protocol

When `/send-whatsapp` is invoked, execute this exact sequence:

### Step 0 — Pre-flight: the outbound-stop brake, then connectivity

```bash
test -e "$HOME/.claude/state/session/outbound-stop.json" && echo ENGAGED
```

- **ENGAGED**: the operator's outbound-stop brake (`grip-stop on`) is on. Send
  nothing, on WhatsApp or on any other channel, and say so in this session:
  `outbound-stop is engaged; nothing sent (release with grip-stop off)`.
  Never release the brake and never offer another channel instead: only the
  operator releases it.
- **Nothing printed**: the brake is off. Check the bridge next.

```bash
curl -sf http://127.0.0.1:3101/health > /dev/null 2>&1
```

- **Exit 0**: server is running. Proceed.
- **Non-zero**: server is down. Output:
  ```
  grip-channel is not running (port 3101 unreachable).
  Start it: ssh root@100.64.0.10 "pm2 start grip-channel"
  ```
  Then HALT — do not attempt delivery to a dead transport.

### Step 1 — Classify input

Apply the detection rules from the table above. For ambiguous input, prefer
the more specific rule (file > structured > code > text).

For files: confirm the file exists and is readable. If it is not at the given
path, look for the name in `~/.claude/data/outbound/`, then in
`~/.claude/drafts/`. Copy a file found anywhere but `~/.claude/data/outbound/`
into that folder before the send (see File path shorthand).

### Step 2 — Build the payload

**Text payload:**
- Strip leading/trailing whitespace.
- If a caption prefix was provided (e.g. "Add a note saying 'from GRIP:'"),
  prepend it on its own line separated by a blank line.
- Max 4096 chars per WhatsApp message. If longer: split into numbered parts
  `[1/N] ... [N/N]`, each ≤ 4000 chars with `...continued` markers.

**File payload:**
- `file_path`: absolute path to the file.
- `caption`: filename without extension (or user-provided caption).
- `jid`: recipient JID from the recipients table.

### Step 3 — Inner converge loop (delivery guarantee)

```
converge(
    criterion: delivery_confirmed
    improve:   retry_delivery
    max_depth: 3
)
```

**delivery_confirmed** = the MCP tool returned without an exception AND
(for `channel_reply`) the response indicates ok OR (for `channel_send_file`)
no error was thrown. A tool answer that starts `FAILED:` is an error.

**retry_delivery** on each attempt:
1. Call the tool.
2. If no error → mark confirmed, exit converge.
3. If the answer starts `FAILED: outbound-stop is engaged`, the brake came on:
   stop at once, with no retry and no escalation, and report it as in Step 0.
4. If any other error → log the error, wait 2 seconds, retry.

**After 3 failed attempts** (max_depth exhausted): escalate.

**Escalation** (converge exits unresolved):
1. Output the failure reason to the user.
2. Offer the fallback: "Shall I send via Slack DM instead?" Never offer it when
   the brake refused the send.
3. If user says yes: invoke `mcp__slack__slack_post_message` to
   `U0EXAMPLE01` (V>>'s user_id) with the same content.
4. Log the failure to `learning/insights.jsonl` with tag `whatsapp_delivery_failure`.

### Step 4 — Confirm to user

On success:
```
Sent to V>> on WhatsApp. [File: filename.pdf | Text: first 60 chars...]
```

On escalation to Slack:
```
WhatsApp delivery failed after 3 attempts. Sent to V>> via Slack DM instead.
```

---

## Multi-part message example

Input: a 5,000-char analysis text → automatically split:

```
[1/2] Analysis: Private Credit AI Layer — HAPPI × RWA
The core insight is that deterministic replay...
[continues]
...continued

[2/2] Analysis: Private Credit AI Layer — HAPPI × RWA
...the yield compression flywheel relies on...
```

---

## Phase 2 — Bidirectional Phone Bridge (shipped 2026-05-02)

This skill is Phase 1 — WhatsApp outbound. Phase 2 shipped in the same
sprint and delivers the full bidirectional bridge:

| Skill | Direction | What it does |
|-------|-----------|--------------|
| `/send-whatsapp` (this) | Mac → Phone | Outbound: any content → WhatsApp |
| `/send-to-phone` | Mac → Phone | Smart cascade: WhatsApp → Slack → iMessage |
| `/osa-phone-action` | Phone → Mac | Inbound: `/mac`/`/osa` WhatsApp commands → HAL OSA |

**`/send-to-phone`** is now the canonical "push to phone" entry point.
Use it when you want guaranteed delivery regardless of WhatsApp availability.

**`/osa-phone-action`** lets V>> text `/mac battery`, `/mac mute`, `/mac notify
Sprint done` etc. from WhatsApp and the Mac executes it. Powered by
`lib/osa_phone_bridge.py` — the OsaPhoneBridge shim. Register once:

```bash
PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
from lib.osa_phone_bridge import OsaPhoneBridge
OsaPhoneBridge().register()
"
```

**Phase 3** (future): priority routing by content type, read receipts,
broadcast to multiple team members.

---

## Guardrails

- **Never send secrets**: before sending any content, the input is scanned
  for patterns matching API keys, tokens, or `ANTHROPIC_API_KEY` shapes.
  If detected, HALT and warn.
- **grip-channel must be running**: the pre-flight check is non-skippable.
  If the server is down, surface the restart command — do not silently fail.
- **File size**: warn if the file exceeds 16 MB (WhatsApp's document limit).
  For PDFs over 16 MB, suggest compressing via `gs` before sending.
- **Recipient confirmation for new JIDs**: if the JID is not in the pre-configured
  recipient table, output the JID and ask the user to confirm before sending.

---

## Examples

```bash
/send-whatsapp "HAPPI × RWA call is at 09:00 tomorrow with a contact"
# → text message to V>>'s WhatsApp

/send-whatsapp ~/.claude/data/outbound/happi-rwa-prototype-2026-05-05.pdf
# → file upload to V>>'s WhatsApp, caption "happi-rwa-prototype-2026-05-05"

/send-whatsapp '{"phase": 1, "status": "complete", "prs_merged": 3}'
# → JSON code block to V>>'s WhatsApp

/send-whatsapp def analyse_cid(cid): ...
# → code block (Python) to V>>'s WhatsApp

/send-whatsapp to:contact "Meeting confirmed for Monday 09:00"
# → NOT supported — that contact's JID is not pre-configured. Add to recipients table first.
```

---

## Failure modes and mitigations

| Condition | Behaviour |
|-----------|-----------|
| grip-channel port 3101 down | Pre-flight HALTS with restart command |
| Outbound-stop brake engaged (the marker exists, or an answer starts `FAILED: outbound-stop is engaged`) | HALT: no retry, no other channel; tell the operator, who alone releases it |
| channel_reply error (e.g. "not connected") | Inner loop retries ×3, then Slack escalation |
| WhatsApp connection lost (QR re-pair needed) | Error will include "405" or "QR"; log and tell user to run `pm2 logs grip-channel` on VPS |
| File not found at given path | Look in `~/.claude/data/outbound/`, then `~/.claude/drafts/`; copy a file found outside `data/outbound/` there; if still missing, HALT and report |
| File outside the bridge's folders (`FAILED: Refused to read ...`) | Copy it into `~/.claude/data/outbound/` and send again |
| File > 16 MB | Warn, offer `gs`-compress command |
| Secrets pattern detected in content | HALT immediately, never send |

---

## Related

- `bin/grip-stop` and `rules/full-power-stop-override.md` — the outbound-stop brake this skill obeys first
- `mcp-servers/grip-channel/src/mcp-guard.ts` — the one folder the bridge sends files from
- `rules/operational-context.md` — V>>'s user_id `U0EXAMPLE01` and Slack fallback DM
- `docs/mobile-steering-guide.md` — full inbound/outbound architecture
- `skills/grip-notify/SKILL.md` — macOS + iMessage + Slack cross-device push
- `lib/grip_notify.py` — programmatic notification facade (Slack/macOS backends)
