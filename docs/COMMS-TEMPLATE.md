# COMMS-TEMPLATE: Three-Layer Communications Architecture

The communications stack separates **what** to say from **how** to say it and **where** to
send it. Three layers handle each concern independently, making it straightforward to add
new platforms without touching existing code.

## The Three Layers

```
Operator intent
      │
      ▼
lib/comms_update.py   ← Layer 1: payload composition + adapter protocol
      │
      ▼
skills/slack-update/SKILL.md  ← Layer 2: ergonomic entry point (operator-facing)
      │
      ▼
grip-channel MCP (channel_post_rich)  ← Layer 3: execution + delivery
```

### Layer 1 — `lib/comms_update.py`

Defines two things:

**`CommsUpdate` dataclass** — the platform-agnostic payload:

```python
from lib.comms_update import CommsUpdate

update = CommsUpdate(
    headline="GRIP Gen 511 — features shipped",
    body="Three tracks, one sprint.",
    fields={"PRs": "3", "Tests": "21"},
    footer="GRIP v6.0.0",
    severity="info",          # "info" | "warning" | "error"
    origin="rsi-optimal",
    template_type="features", # "features" | "heartbeat"
)
```

`severity` controls the emoji prefix on the header block (`info` → ℹ, `warning` → ⚠,
`error` → 🔴). Fields overflow to thread when more than 5 items are present.

**`CommsAdapter` Protocol** — the interface every platform adapter must satisfy:

```python
class CommsAdapter(Protocol):
    supports_blocks: bool
    def render(self, update: CommsUpdate) -> object: ...
    def send(self, update: CommsUpdate, recipient: str) -> None: ...
```

`SlackAdapter` is the reference implementation. `DiscordAdapter`, `TelegramAdapter`,
`WebAdapter`, and `WhatsAppAdapter` are registered stubs that raise `NotImplementedError`
with a redirect to the relevant plan doc.

### Layer 2 — `skills/slack-update/SKILL.md`

The operator-facing skill. Use this instead of calling `lib/comms_update.py` directly.
It documents the common patterns (sprint summary, heartbeat, error alert), shows which
grip-channel MCP tool to use, and maps `CommsUpdate` fields to Block Kit concepts.

### Layer 3 — grip-channel MCP (`channel_post_rich`)

The execution layer. Accepts a list of Block Kit block dicts and a channel/recipient,
then posts via the configured Slack bot token. No business logic lives here.

```python
# Conceptual call — grip-channel handles auth and delivery
channel_post_rich(blocks=adapter.render(update), channel="C0EXAMPLE01")
```

---

## How to Add a New Platform (Discord Example)

Follow these steps in order. Each step has a verification gate.

### Step 1 — Implement the adapter class

In `lib/comms_update.py`, replace the `DiscordAdapter` stub:

```python
class DiscordAdapter:
    """Concrete CommsAdapter for Discord Embeds."""
    supports_blocks: bool = False  # Discord uses embed objects, not Block Kit

    def render(self, update: CommsUpdate) -> dict:
        """Translate CommsUpdate → Discord embed dict."""
        colour_map = {"info": 0x5865F2, "warning": 0xFEE75C, "error": 0xED4245}
        embed = {
            "title": update.headline,
            "description": update.body,
            "color": colour_map.get(update.severity, 0x5865F2),
            "fields": [
                {"name": k, "value": v, "inline": True}
                for k, v in list(update.fields.items())[:25]  # Discord embed limit
            ],
            "footer": {"text": f"{update.footer} | origin: {update.origin}"},
        }
        return {"embeds": [embed]}

    def send(self, update: CommsUpdate, recipient: str) -> None:
        """POST to Discord webhook URL stored in recipient."""
        import urllib.request, json as _json
        payload = self.render(update)
        req = urllib.request.Request(
            recipient,
            data=_json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
```

Gate: `python -c "from lib.comms_update import DiscordAdapter; print('ok')"` must succeed.

### Step 2 — Wire to grip-channel MCP (optional)

If you want grip-channel to route Discord messages alongside Slack:

1. Add a `discord_post` tool to the grip-channel server (see `grip-channel` source).
2. Update `skills/slack-update/SKILL.md` to document `channel_post_rich` vs `discord_post`.
3. Add `DISCORD_WEBHOOK_URL` to the grip-channel environment.

### Step 3 — Cross-reference the skill

Add a Discord section to `skills/slack-update/SKILL.md` (or create
`skills/discord-update/SKILL.md` if the UX diverges significantly).

### Step 4 — Add tests

```python
# tests/test_comms_update.py  (add alongside existing Slack tests)
def test_discord_adapter_render():
    adapter = DiscordAdapter()
    update = CommsUpdate(
        headline="Test", body="Body", fields={"k": "v"},
        footer="footer", severity="info", origin="test",
        template_type="features",
    )
    result = adapter.render(update)
    assert "embeds" in result
    assert result["embeds"][0]["title"] == "Test"
    assert result["embeds"][0]["color"] == 0x5865F2
```

Gate: `pytest tests/test_comms_update.py -q` must pass with zero failures.

### Step 5 — Delete the stub plan reference

Once the adapter is live, remove the `plans/discord-adapter.md` cross-reference from
the stub (or mark it `status: implemented`).

---

## Block Kit → Platform-Native Translation Reference

| Block Kit concept | Slack | Discord | Telegram |
|-------------------|-------|---------|----------|
| Header block      | `{"type":"header","text":{"type":"plain_text",...}}` | `embed.title` | bold first line |
| Section (mrkdwn)  | `{"type":"section","text":{"type":"mrkdwn",...}}` | `embed.description` | message text |
| Fields (2-col)    | `{"type":"section","fields":[...]}` | `embed.fields` (inline) | key: value lines |
| Divider           | `{"type":"divider"}` | visual separator in description | `---` |
| Context (footer)  | `{"type":"context","elements":[...]}` | `embed.footer` | italic last line |
| Severity colour   | emoji prefix on header | `embed.color` (hex) | no native colour |

Block Kit `overflow` items (> 5 fields) should become a thread reply on Slack, a second
embed on Discord, and a follow-up message on Telegram.

---

## See Also

- `lib/comms_update.py` — source of truth for the adapter protocol and `CommsUpdate` fields
- `plans/discord-adapter.md` — Discord sprint roadmap
- `tests/test_comms_update.py` — unit tests for `SlackAdapter` and payload composition
