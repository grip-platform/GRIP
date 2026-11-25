---
name: coffee
description: "Easter egg session diagnostic disguised as a coffee order. Reads DELIGHT v2 telemetry and git state to brew a personalised coffee. The coffee IS the session."
status: Active
effort: medium
version: 1.0.0
created: 2026-03-24
triggers:
  - /coffee
  - "brew me a coffee"
  - "I need a coffee"
  - "coffee break"
integrates:
  - lib/coffee_brew.py
  - lib/delight_v2.py
see_also:
  - caffeinating-sessions
  - scientific-measurement
---

# /coffee — The Daily Grind

**Category**: Easter Egg | Session Diagnostic
**Token Budget**: <400

---

## What This Is

A hidden session diagnostic wrapped in barista metaphors. When invoked, GRIP reads
your DELIGHT v2 telemetry, git state, time of day, and session signals, then brews
a personalised coffee that IS your session — expressed through coffee attributes.

Every metric maps to a coffee property:
- **Coffee Type** = session profile (Espresso for intense, Cold Brew for marathon, etc.)
- **Roast** = efficiency (dark = efficient, light = exploratory)
- **Temperature** = context freshness (hot = fresh, iced = near limit)
- **Body** = flow state
- **Crema** = inverse friction (smooth = thick crema)
- **Aroma** = DELIGHT composite
- **Bitter** = shadow metrics (Goodhart warning as coffee wisdom)
- **Tasting Notes** = recent git commits as flavour profiles

The act of ordering coffee is itself data — break frequency correlates with
sustainable pace. Every brew is logged to `learning/coffee_log.jsonl`.

---

## Usage

```
/coffee              # Brew your session coffee
/coffee --secret     # Secret menu (affogato = context breakdown)
/coffee --decaf      # Minimal output — just the cup and a score
/coffee --history    # Loyalty card + sparkline + break patterns
```

---

## Protocol

### Step 1: Gather Signals

Run the brewing engine to get the base output:

```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/coffee_brew.py
```

For secret menu:
```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/coffee_brew.py --secret affogato
```

For history:
```bash
PYTHONPATH=$HOME/.claude python3 $HOME/.claude/lib/coffee_brew.py --history
```

### Step 2: Present with Personality

Display the script output verbatim in a code block. The ASCII art and formatting
are designed to render in monospace.

Then add ONE line of your own — a meta-observation about the session that the
script couldn't know. This is the barista's whisper. Keep it to a single sentence.
It should be specific to what the user has actually been doing in THIS conversation.

Examples of good whispers:
- "That auth refactor earned you the extra shot."
- "Three files touched, zero tests broken. Clean extraction."
- "You've been staring at that middleware for 40 messages. Drink up."

Examples of bad whispers (too generic):
- "Great session!" (says nothing)
- "Keep going!" (motivational poster)
- "You're doing well." (patronising)

### Step 3: No Follow-Up

After presenting the coffee, do NOT suggest next actions, summarise the session,
or offer to do anything. The coffee IS the moment. Let it breathe.

The one exception: if context_pct > 85%, gently note that a fresh session might
be worth considering. The Long Black is the barista's polite way of saying "last call."

---

## The Menu

| Coffee | When | Personality |
|--------|------|-------------|
| Matcha Latte | Fresh session, no commits | Green possibility |
| Espresso | Quick, focused, < 1.5h | No-nonsense |
| Ristretto | Many commits, high flow | Maximum extraction |
| Americano | Default moderate pace | Reliable workhorse |
| Cortado | High DELIGHT, clean shadows | Perfect balance |
| Flat White | Smooth flow, low friction | Velvet refinement |
| Pour Over | Research mode, few commits | Methodical patience |
| Cold Brew | Marathon (3h+) | Slow-steeped endurance |
| Turkish Coffee | High friction, many blocks | Thick with grounds |
| Long Black | Context > 80% | Stretched to the limit |
| Irish Coffee | Late night + long + struggling | Last call |
| Affogato | Secret menu only | Drowning in context |

---

## The Loyalty Programme

Every brew (except decaf) is logged. Tiers:

| Cups | Tier |
|------|------|
| 0-4 | New Customer |
| 5-14 | Regular |
| 15-29 | Gold Card |
| 30-49 | Platinum |
| 50-99 | Barista's Friend |
| 100+ | Part Owner |

Break frequency itself is a session health signal. Users who take regular
coffee breaks correlate with sustainable session pace. The data feeds into
the DELIGHT pipeline as a meta-metric: `coffee_interval_minutes`.

---

## The Meta Layer

The `/coffee` command is secretly a DELIGHT measurement instrument:

1. **Break signal** — when someone orders coffee, they're pausing. Pause frequency
   maps to sustainable pace vs burnout risk.
2. **Satisfaction proxy** — users who enjoy their sessions order coffee to celebrate
   checkpoints. Users who are struggling order coffee to cope. The coffee TYPE
   (Cortado vs Turkish) reveals which.
3. **Goodhart's coffee** — if shadow metrics are elevated, the coffee tastes bitter.
   This is the most honest diagnostic GRIP offers: you can't argue with your coffee.

---

## Design Philosophy

This Easter egg exists because:

1. **Diagnostics should delight** — session metrics are useful but dry. Wrapping them
   in coffee metaphors makes them memorable and shareable.
2. **Breaks are data** — the act of stopping to order a coffee is itself informative.
3. **Personality builds trust** — a system that can make you smile earns the right to
   tell you uncomfortable truths (like "your shadow metrics are showing").
4. **Discovery rewards exploration** — finding `/coffee` for the first time should
   feel like finding a hidden room in a building you thought you knew.
