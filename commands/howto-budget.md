---
description: How GRIP spends a Claude Pro allowance, and how to see the settings that protect it.
disable-model-invocation: false
---

# /howto-budget -- your Pro allowance

**HOOK.** A Pro plan gives one allowance for every five hours and one for the week. GRIP spends it with care.

**WORKED EXAMPLE.** In the Terminal:

```bash
grip-tier --envelope
```

The output shows `"auto_compact_window": 180000` and `"launch_model": "claude-sonnet-5-5"`. GRIP compacts the window at 180K and runs Sonnet 5.5 at medium effort. Routine work goes to HAL's free providers through the redaction shield. Inside a session, the status line shows the allowance.

**### Your Turn.** Run `grip-tier --envelope` in the Terminal. **Completion gate:** the output names `claude-sonnet-5-5` and `180000`.

**COMPREHENSION CHECK:**

```
Reply A, B, or C:
  A) GRIP uses the full 1M window on Pro
  B) GRIP compacts at 180K and sends routine work to free providers, to protect the allowance
  C) Not sure, show me again
```

(B is the move.)

**NEXT.** Next: the receipt behind a verdict, `/howto-receipt`.

For a child: GRIP has a jar of coins for the week. It spends them slowly.
