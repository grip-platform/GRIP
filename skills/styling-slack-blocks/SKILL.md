---
name: styling-slack-blocks
description: Creates beautiful, consistent Slack Block Kit messages for GRIP notifications. Use when composing Slack messages, heartbeats, alerts, or digests that need rich formatting.
status: Reference (styling reference — not for update posts)
effort: high
version: 1.1.0
created: 2026-03-06
triggers:
  - /slack-blocks
  - "create slack message"
  - "format for slack"
  - "slack block kit"
integrates:
  - copilot-rituals
  - slack-api
priority: MANDATORY
---

# Styling Slack Blocks

Creates beautiful, consistent Slack Block Kit messages for GRIP.

## CONVERGE CRITERION

> **All Slack messages MUST use Block Kit styling. No plain text. No malformed markdown.**

This is a non-negotiable quality gate for all GRIP Slack communications.

### Enforcement Points

| Location | Function | Status |
|----------|----------|--------|
| `lib/slack_notify.py` | RSI notifications | ✅ Block Kit |
| `lib/vortex.py` | Heartbeat pulse | ✅ Block Kit |
| `lib/copilot/integrations.py` | `text_to_blocks()` | ✅ Converter |
| `lib/copilot/workflow_runner.py` | Notify steps | ✅ Uses `text_to_blocks()` |
| `lib/slack_canvas_digest.py` | Digests | ✅ Block Kit |
| `scripts/daily-team-digest.py` | Team digest | ✅ Block Kit |

### Validation Checklist

Before sending any Slack message:
- [ ] Uses `blocks` parameter (not just `text`)
- [ ] Has fallback `text` for accessibility
- [ ] Includes timestamp in context footer
- [ ] Uses emoji prefix for scannability
- [ ] No raw markdown (use mrkdwn or header blocks)

## Block Kit Fundamentals

Slack Block Kit uses JSON to compose rich messages. Key block types:

| Block Type | Purpose | Use For |
|------------|---------|---------|
| `header` | Large bold title | Message titles |
| `section` | Text with optional accessory | Main content |
| `divider` | Horizontal line | Visual separation |
| `context` | Small muted text | Metadata, timestamps |
| `actions` | Buttons, selects | User interactions |
| `rich_text` | Formatted text | Complex formatting |

---

## GRIP Design System

### Color Palette (via emoji accents)

| Purpose | Emoji | Usage |
|---------|-------|-------|
| Success | :white_check_mark: :green_circle: | Completed, passing |
| Warning | :warning: :large_yellow_circle: | Attention needed |
| Error | :x: :red_circle: | Failed, critical |
| Info | :information_source: :blue_circle: | Neutral updates |
| Heartbeat | :heartbeat: :dna: | System alive |
| Progress | :hourglass_flowing_sand: :arrows_counterclockwise: | In progress |

### Standard Headers

```python
HEADERS = {
    "heartbeat": ":dna: GRIP Heartbeat",
    "morning": ":sunrise: Morning Brief",
    "eod": ":city_sunset: EOD Digest",
    "alert": ":rotating_light: Alert",
    "success": ":white_check_mark: Success",
    "decision": ":scales: Decision Required",
}
```

---

## Block Templates

### Heartbeat Block

```python
def heartbeat_blocks(gen, skills, workflows, rituals_24h, fitness, uptime):
    """Beautiful GRIP heartbeat message."""
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":dna: GRIP Heartbeat", "emoji": True}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Generation*\n`{gen}`"},
                {"type": "mrkdwn", "text": f"*Fitness*\n`{fitness:.3f}`"},
                {"type": "mrkdwn", "text": f"*Skills*\n{skills}"},
                {"type": "mrkdwn", "text": f"*Workflows*\n{workflows}"},
            ]
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Rituals (24h)*\n{rituals_24h} :white_check_mark:"},
                {"type": "mrkdwn", "text": f"*Uptime*\n{uptime}"},
            ]
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f":heartbeat: _pulse_ · <!date^{int(time.time())}^{{date_short_pretty}} {{time}}|now>"}
            ]
        }
    ]
```

### Status Card Block

```python
def status_card(title, status, details, emoji=":information_source:"):
    """Generic status card with header, status badge, and details."""
    status_emoji = {
        "success": ":white_check_mark:",
        "warning": ":warning:",
        "error": ":x:",
        "pending": ":hourglass_flowing_sand:",
    }.get(status, ":blue_circle:")

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{emoji} *{title}*\n{status_emoji} {status.title()}"},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": details}]
        },
    ]
```

### KPI Progress Block

```python
def kpi_progress(kpis):
    """Display KPI progress with visual bars."""
    fields = []
    for kpi in kpis:
        pct = min(100, (kpi['current'] / kpi['target']) * 100)
        bar = progress_bar(pct)
        fields.append({
            "type": "mrkdwn",
            "text": f"*{kpi['name']}*\n{bar} {pct:.0f}%"
        })
    return [{"type": "section", "fields": fields[:10]}]  # Max 10 fields

def progress_bar(pct, width=8):
    """Generate text-based progress bar."""
    filled = int(pct / 100 * width)
    empty = width - filled
    return f"{'█' * filled}{'░' * empty}"
```

### Alert Block

```python
def alert_block(level, title, message, actions=None):
    """Alert with severity level and optional actions."""
    level_config = {
        "critical": (":rotating_light:", "#E01E5A"),
        "warning": (":warning:", "#ECB22E"),
        "info": (":information_source:", "#36C5F0"),
    }
    emoji, _ = level_config.get(level, (":bell:", "#666666"))

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} {title}", "emoji": True}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": message}
        },
    ]

    if actions:
        blocks.append({
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": a["label"]},
                 "action_id": a["id"], "style": a.get("style")}
                for a in actions
            ]
        })

    return blocks
```

---

## Composition Patterns

### Section Fields (2-column layout)

```python
# Use fields for side-by-side data (max 10 fields)
{
    "type": "section",
    "fields": [
        {"type": "mrkdwn", "text": "*Label*\nValue"},
        {"type": "mrkdwn", "text": "*Label*\nValue"},
    ]
}
```

### Context Footer

```python
# Always end with context for metadata
{
    "type": "context",
    "elements": [
        {"type": "mrkdwn", "text": ":robot_face: _GRIP Gen {gen}_ · {timestamp}"}
    ]
}
```

### Dividers for Visual Breaks

```python
# Use sparingly between logical sections
{"type": "divider"}
```

---

## Full Heartbeat Example

```python
import time
from datetime import datetime

def build_heartbeat():
    """Build complete heartbeat message for Slack."""
    from pathlib import Path
    import json

    CLAUDE_DIR = Path.home() / ".claude"

    # Gather stats
    gen = "?"
    fitness = 0.0
    genome_path = CLAUDE_DIR / "cache" / "genome.json"
    if genome_path.exists():
        data = json.loads(genome_path.read_text())
        gen = data.get("generation", "?")
        genes = list(data.get("genes", {}).values()) if isinstance(data.get("genes"), dict) else data.get("genes", [])
        living = [g for g in genes if isinstance(g, dict) and g.get("fitness", 0) > 0.2]
        fitness = sum(g.get("fitness", 0) for g in living) / max(len(living), 1)

    skills_dir = CLAUDE_DIR / "skills"
    skill_count = len(list(skills_dir.glob("*/SKILL.md"))) if skills_dir.exists() else 0

    workflows_dir = CLAUDE_DIR / "data" / "copilot" / "workflows"
    workflow_count = len(list(workflows_dir.glob("*.yaml"))) if workflows_dir.exists() else 0

    # Rituals in last 24h
    history_path = CLAUDE_DIR / "data" / "copilot" / "rituals" / "history.jsonl"
    rituals_24h = 0
    if history_path.exists():
        cutoff = time.time() - 86400
        for line in history_path.read_text().strip().splitlines()[-100:]:
            try:
                entry = json.loads(line)
                ts = datetime.fromisoformat(entry.get("timestamp", "").replace("Z", "+00:00"))
                if ts.timestamp() > cutoff and entry.get("status") == "success":
                    rituals_24h += 1
            except:
                pass

    now = datetime.now()
    ts = int(time.time())

    return {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":dna: GRIP Heartbeat", "emoji": True}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Generation*\n`Gen {gen}`"},
                    {"type": "mrkdwn", "text": f"*Fitness*\n`{fitness:.3f}`"},
                ]
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Skills*\n{skill_count} :gear:"},
                    {"type": "mrkdwn", "text": f"*Workflows*\n{workflow_count} :repeat:"},
                ]
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Rituals (24h)*\n{rituals_24h} :white_check_mark:"},
                    {"type": "mrkdwn", "text": f"*Status*\n:green_circle: Alive"},
                ]
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f":heartbeat: _pulse_ · <!date^{ts}^{{date_short_pretty}} {{time}}|{now.strftime('%H:%M')}>"}
                ]
            }
        ]
    }
```

---

## Anti-Patterns

| Anti-Pattern | Why Bad | Correct Pattern |
|--------------|---------|-----------------|
| Plain text only | Misses Block Kit richness | Use section with mrkdwn |
| Too many dividers | Visual clutter | Max 2-3 per message |
| No context footer | Missing metadata | Always include timestamp |
| Walls of text | Hard to scan | Use fields for data |
| Missing emojis | Less scannable | Lead with relevant emoji |

---

## Integration with Copilot

When composing ritual notifications:

```python
# In workflow notify step
def build_notify_blocks(step_outputs, template):
    """Build blocks from workflow step outputs."""
    # Use this skill's templates
    if template == "heartbeat":
        return build_heartbeat()
    elif template == "status":
        return status_card(...)
    # etc.
```

---

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~400 |
| Template selection | ~100 |
| Block generation | ~200 |
| **Total** | **~700** |
