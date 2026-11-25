---
name: email
description: Send and receive email via AgentMail. Pre-built inbox `you@agentmail.to`. Use for outbound notifications, onboarding emails, scheduled drafts, reading replies. Backed by the syscall-doctrine shim `lib/agentmail.py`.
---

# /email — AgentMail Email Operations

Send, list, reply to, and draft emails through AgentMail
(`https://api.agentmail.to`). The shim at `lib/agentmail.py` handles auth,
retries, error mapping; the user-facing surface is plain English.

V>>'s default inbox is `you@agentmail.to`. Other inboxes accessible to the
API key are listed by `list_inboxes()`.

## Invocation

| Form | What it does |
|------|--------------|
| `/email list` | Show all messages in the default inbox (latest 20) |
| `/email send <to> <subject> <body>` | Send an email from the default inbox |
| `/email reply <message_id> <body>` | Reply in-thread to a specific message |
| `/email inboxes` | Show every inbox the API key can reach |
| `/email threads` | List conversation threads in the default inbox |
| `/email drafts` | List unsent drafts |
| `/email schedule <to> <subject> <body> <ISO-8601-time>` | Create a draft scheduled for future delivery |

The exact arguments after `/email <verb>` are interpreted contextually — if
the user says "/email Jane the proposal summary", resolve `Jane` to the
correct address (per `rules/operational-context.md` CodeTonight Team table)
and write a fitting subject.

## Implementation pattern

Always invoke through the shim, never via raw curl. The shim reads the
API key from macOS Keychain (`grip-agentmail`) inside its own process scope
— call sites never touch the token. Example:

```bash
PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/python -c "
from lib import agentmail
result = agentmail.send_message(
    'you@agentmail.to',
    to=['jane@example.com'],
    subject='Proposal summary',
    text='Hi Jane, attached is the summary…',
)
print(result['message_id'])
"
```

For interactive use inside a session, prefer in-process:

```python
from lib import agentmail
agentmail.list_messages('you@agentmail.to', limit=20, labels=['unread'])
```

## Defaults & conventions

- **Default inbox**: `you@agentmail.to` (V>>'s primary AgentMail inbox).
  Never assume — call `list_inboxes()` if uncertain.
- **Subject hygiene**: short, specific subject lines (~6 words). No
  "Re:" prefix when starting a new thread; AgentMail handles threading
  via `reply_to_message()` automatically.
- **Body**: plain text by default (`text=`); add `html=` only when the
  recipient is known to render HTML email cleanly.
- **CC convention**: per `feedback_cc_laurie_on_all_grip_slack_messages.md`,
  CC V>>'s personal address (`licensing@codetonight.co.za`) on any external
  GRIP-originated email so the audit trail stays complete.
- **From**: AgentMail uses the inbox's address as the sender — V>> does not
  appear as the sender unless the inbox is configured under a custom domain
  he owns. Make this clear when sending on his behalf.

## Error handling

The shim raises `AgentMailError` with plain-language messages including the
fix command. For example, on 401:

> "AgentMail rejected the API key (401 Unauthorized). Rotate it via the
> AgentMail dashboard, then re-store with: security add-generic-password
> -a grip -s grip-agentmail -w <NEW_KEY> -U"

Do not retry blindly on 429 — the shim surfaces `Retry after N seconds`;
respect the value before any retry.

## Falsification

This skill is wrong if:

- AgentMail's API surface changes such that the shim's function signatures
  no longer cover common use cases (extend or revise the shim).
- The API key gets rotated and the keychain entry is no longer valid —
  the shim's plain-language error tells V>> exactly what to run.
- A different email provider replaces AgentMail; the skill's contract
  (verbs above) should still hold while the shim's backend changes.
  This is the syscall-doctrine compliance guarantee.

## See also

- `lib/agentmail.py` — the shim implementation
- `tests/test_agentmail.py` — Goodhart-proof regression tests (26 mocked + 1 live)
- `rules/syscall-doctrine.md` — the doctrine this shim implements
- `rules/operational-context.md` — Slack/WhatsApp/keychain references
- `feedback_cc_laurie_on_all_grip_slack_messages.md` — CC convention
