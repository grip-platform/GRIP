# /rave

Italian Brainrot rave-mode milestone narration.

## Usage

```bash
/rave "<title>" "<body>"
/rave "W3 COMPLETE" "plugin integration done"
```

## Implementation

Generates a deterministic GRIP-GUILD rave card via `lib/rave_card.py` and prints it. Optionally pipes through `lib/grip_notify.py` to fire as an iPhone notification.

```bash
python3 -m lib.rave_card "$ARGUMENTS[0]" "$ARGUMENTS[1]"
```

Backed by `skills/rave/SKILL.md` (externally validated 0.95 by Gemini, 22 hermetic tests).
