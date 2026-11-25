# /notify — Send a GRIP notification to every configured backend

Deliver a push notification through all enabled backends (macOS local,
iMessage self-send, Slack bot) in one call. Uses the shipped
`lib/grip_notify.py` facade and reads per-operator config from the
git-crypted `docs/restricted/notify-targets.json`.

## Usage

```
/notify "title" "body"
/notify "Alex safe home" "Alex is 133 m from 12 Example Road"
/notify --subtitle "From Find My" "GRIP" "Alex arrived"
/notify --url https://github.com/x/y/pull/2134 "PR merged" "2134 is live"
```

## Protocol

1. Parse args into a `Notification(title, body, subtitle?, url?, priority?)`
2. Call `default_notifier()` which loads config from
   `docs/restricted/notify-targets.json` (git-crypted) and wires up
   every enabled backend.
3. Call `.send(notif)` — fan-out to all backends in parallel.
4. Report per-backend `DeliveryResult` as JSON.
5. If every backend failed, exit non-zero; otherwise exit 0.

## Direct CLI (equivalent)

```bash
python3 -m lib.grip_notify send \
    --title "GRIP" \
    --subtitle "Alex safe home" \
    --body "Alex is 133 m from 12 Example Road — essentially at home."
```

## Status check

```bash
python3 -m lib.grip_notify status
# => {"macos_local": true, "imessage": true, "slack_bot": false}
```

`false` entries come with a `PermissionNeeded` error when `.send()`
is called against that backend — the error carries the exact remedy
(e.g. "set handle in notify-targets.json", "add bot token via security
add-generic-password -s grip-slack-bot").

## Safety

- **Never send notifications containing sensitive data** outside the
  operator's own devices. Backends deliver to configured addresses
  only — no broadcast, no "send to everyone".
- **The Slack backend posts to a single configured channel**. Do not
  override `channel_id` at call time; edit the git-crypted config.
- **iMessage handles and Slack channel IDs live ONLY in the git-crypted
  config**. Never in plaintext code, commit messages, or chat logs
  persisted elsewhere.
- **No third-party services**. The only external calls are Apple
  iMessage (APNs) and Slack Web API — both already in the operator's
  trust boundary.

## Related

- `skills/grip-notify/SKILL.md` — full skill documentation
- `lib/grip_notify.py` — multi-backend notifier + facade + CLI
- `docs/restricted/notify-targets.json` — encrypted per-operator config
- `tests/test_grip_notify.py` — 32 hermetic tests
