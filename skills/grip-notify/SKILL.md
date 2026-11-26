---
name: grip-notify
description: Cross-device push notifications from GRIP to the operator's devices. Composable multi-backend facade (macOS local + iMessage self-send + Slack bot DM) with git-crypted per-operator config. Pairs with /notify.
status: Active
effort: low
version: 1.0.0
created: 2026-04-11
generation: 509
triggers:
  - /notify
  - "push notification"
  - "send me a notification"
  - "notify my iphone"
integrates:
  - lib/grip_notify.py
  - docs/restricted/notify-targets.json (encrypted config)
  - skills/family-distance (sister skill, same composable pattern)
priority: Standard
privacy: local-only-plus-apple-or-slack
---

# GRIP Notify

Deliver GRIP notifications to the operator's devices — Mac, iPhone,
Slack mobile — via composable backends chosen by config.

## Why this exists

macOS `osascript display notification` raises a banner on the Mac
only. It does not forward to the operator's iPhone. There is no
built-in Continuity notification mirror. Cross-device delivery
requires explicit routing through one of:

1. **iMessage self-send** to the operator's own Apple ID email or
   phone number — native Apple path, uses APNs, the iPhone signed
   into the same Apple ID raises a Messages notification.
2. **Slack Web API direct post** to the operator's DM in a workspace
   where they have Slack mobile — Slack's own push infrastructure
   delivers the notification as a Slack banner.
3. **Pushover / ntfy / custom services** — third-party apps, requires
   install + token. (Not implemented in v1; see Future Work.)

This skill wraps all three behind a single `GripNotifier` facade so
callers pass one `Notification` and get per-backend `DeliveryResult`
objects back. One backend failing does not block the others.

## Architecture

```
GRIP caller
    │
    ▼
GripNotifier.send(Notification) ──► [DeliveryResult, DeliveryResult, ...]
    │
    ├── MacOSLocalBackend   (always available on macOS)
    ├── IMessageBackend     (needs handle in config)
    └── SlackBotBackend     (needs channel_id + keychain bot token)
```

Each backend inherits the template-method `Backend` base class and
overrides three hooks:

- `_probe()` — is this backend operational right now?
- `_permission_needed()` — what must the operator fix to make it work?
- `_do_send(notif)` — backend-specific delivery

Public `is_available()` and `send()` live only on the base class, so
the "check availability, raise PermissionNeeded if not, otherwise
delegate" pattern exists in exactly one place. Mirrors the
`lib/findmy_reader.py` template method from PR #2130.

## Config (git-crypted)

Per-operator targets live in `docs/restricted/notify-targets.json`,
encrypted at rest via the existing `docs/restricted/**` git-crypt
filter. Shape:

```json
{
  "version": 1,
  "backends": {
    "macos_local": { "enabled": true },
    "imessage":    { "enabled": true,
                     "handle": "<operator-apple-id-email-or-phone>" },
    "slack_bot":   { "enabled": true,
                     "channel_id": "<slack-dm-or-channel-id>",
                     "keychain_service": "grip-slack-bot" }
  }
}
```

Each backend entry is optional. Missing entries are disabled.
Missing config file → only `macos_local` is wired up.

The Slack bot token is resolved from the macOS keychain at send time
via `security find-generic-password -s <keychain_service> -w`. The
token itself is never written to the repo.

## Usage

### Check which backends are available

```bash
python3 -m lib.grip_notify status
# => {"macos_local": true, "imessage": true, "slack_bot": false}
```

### Send a notification

```bash
python3 -m lib.grip_notify send \
    --title "GRIP" \
    --subtitle "Alex safe home" \
    --body "Alex is 133 m from 12 Example Road — essentially at home."
```

Output is a JSON list of `DeliveryResult` entries, one per backend.

### Python API

```python
from lib.grip_notify import default_notifier, Notification

notifier = default_notifier()
results = notifier.send(Notification(
    title="GRIP",
    subtitle="Alex safe home",
    body="Alex is 133 m from 12 Example Road — essentially at home.",
))
for r in results:
    print(r.backend, r.ok, r.error or r.detail)
```

## Privacy

- **No third-party push services**. The only external endpoints are
  Apple's iMessage APNs (for the iMessage backend) and Slack's Web API
  (for the Slack backend). Both are already in the operator's trust
  boundary.
- **All personal data is git-crypted**. Apple ID email, phone number,
  Slack channel ID, bot token keychain service name — every piece of
  configuration that identifies the operator lives in
  `docs/restricted/notify-targets.json`, which git-crypt's clean filter
  encrypts on commit.
- **No ambient logging**. Backends return `DeliveryResult` objects to
  the caller; the library itself does not log notification content
  anywhere persistent beyond the macOS unified log (which backends
  use automatically).

## Failure modes and how they're handled

| Condition | Behaviour |
|---|---|
| No config file | Only `macos_local` is wired up, degraded but functional |
| iMessage handle missing | Backend unavailable, `PermissionNeeded` with clear remedy |
| iMessage handle not registered as Apple ID receive-at | Messages.app AppleScript returns error, `DeliveryResult.ok=False` with Slack-style error text |
| Slack bot token not in keychain | Backend unavailable, `PermissionNeeded` tells operator the exact `security add-generic-password` command |
| Slack API rate limit | `NotifyUnavailable` raised with Slack's error code, caller sees failure result |
| One backend crashes | Facade catches the exception, wraps as `DeliveryResult(ok=False)`, other backends still fire |
| Non-macOS host | `macos_local` and `imessage` backends report unavailable; only `slack_bot` can deliver |

## Urgent tier (sound)

Four advisory priority levels are supported, all `str`:

| Constant | Value | Behaviour |
|---|---|---|
| `PRIORITY_LOW` | `"low"` | Silent, low-importance |
| `PRIORITY_NORMAL` | `"normal"` | Silent, default |
| `PRIORITY_HIGH` | `"high"` | Silent, advisory (used by sprint completion) |
| `PRIORITY_URGENT` | `"urgent"` | Plays chime locally, then fans out to backends |

When `priority="urgent"`, `GripNotifier.send()` calls
`_play_urgent_chime_if_needed()` before the backend fan-out. That helper
synthesises `cache/grip-chime.wav` in the GRIP root via `lib.handoff_chime.write_wav` on the
first urgent send per process (in-memory bool flag), then calls
`lib.handoff_chime.play_wav`. The chime is best-effort: any synthesis or
playback failure is logged to stderr and delivery continues normally.

```python
from lib.grip_notify import default_notifier, Notification, PRIORITY_URGENT

notifier = default_notifier()
results = notifier.send(Notification(
    title="GRIP",
    body="Sprint complete — action required.",
    priority=PRIORITY_URGENT,
))
```

Do NOT use `priority="urgent"` for routine notifications such as sprint
completion — `notify_sprint_completion` uses `"high"` intentionally to
keep that path silent.

## Out of scope (Future Work)

- **Pushover / ntfy / custom APNs backends** — would give a branded
  "GRIP" identity on iPhone notifications instead of "Messages" or
  "Slack", at the cost of requiring a 5 US dollar app install (Pushover) or
  self-hosted infrastructure (ntfy).
- **Per-notification routing rules** — "emergency → all backends",
  "info → only macos_local". Current implementation fans out to every
  enabled backend equally.
- **Notification deduplication** — if the same notification fires
  twice within N seconds, suppress the second. Not needed yet.
- **Tappable URLs on iMessage** — would require transforming the URL
  into a rich link attachment, which Messages.app doesn't expose via
  AppleScript.
- **Scheduled / delayed delivery** — fire a notification at a future
  time. Pair with `lib/schedule.py` in a follow-up.

## Test coverage

`tests/test_grip_notify.py` covers 32 test cases:

- `Notification` dataclass defaults + `to_dict` round trip + priority
- `DeliveryResult` dataclass (ok + failure cases)
- `PermissionNeeded` exception message format + `NotifyUnavailable` subclass
- `Backend` template method (4 test doubles: available, unavailable,
  crashy-do-send, crashy-probe)
- `MacOSLocalBackend._q` string escaping (quotes, backslashes, empty)
- `IMessageBackend` missing-handle behaviour + `_format_text` output
- `SlackBotBackend` missing-channel behaviour + `_format_markdown` output
- `GripNotifier.send` fan-out + failure isolation
- `GripNotifier.status` per-backend reporting
- `load_config` missing file, invalid JSON, valid JSON
- `default_notifier` empty/iMessage-enabled/Slack-enabled/disabled cases

All assertions compare output VALUES, not call counts. Mutation
testing: removing a `lines.append(notif.url)` from `_format_text`
breaks `test_imessage_format_text_includes_title_body_url`. Goodhart-safe.

Run: `PYTHONPATH=~/.claude pytest tests/test_grip_notify.py -v`

## Related

- `lib/grip_notify.py` — backend implementations + facade + CLI
- `docs/restricted/notify-targets.json` — git-crypted per-operator config
- `commands/notify.md` — slash command wrapper
- `skills/family-distance/SKILL.md` — sister skill using the same composable pattern (PR #2130)
- `docs/git-lock-architecture.md` — sibling sprint sharing the local-first design philosophy (PR #2128)
