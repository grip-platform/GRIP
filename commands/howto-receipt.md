---
description: What a sealed record proves, what it does not, and how to check one yourself.
disable-model-invocation: false
---

# /howto-receipt -- the receipt behind a verdict

**HOOK.** GRIP seals its council verdicts. A seal shows that nobody changed the record after the seal, not that the record is right.

**WORKED EXAMPLE.** The voice-tools council sealed its verdict on the author's Mac:

```bash
grip-sovereign-council --verify 566891a365283721
```

The command computes the record's sha256 and prints whether the record is intact. One changed character prints TAMPERED. On this Mac the record list starts empty, so the root command shows zero verdicts:

```bash
grip-sovereign-council --merkle
```

**### Your Turn.** Run `grip-sovereign-council --merkle`. **Completion gate:** the output has a `council verdicts committed` line and a `Merkle root` line.

**COMPREHENSION CHECK:**

```
Reply A, B, or C:
  A) A seal proves the answer is true
  B) A seal proves the record was not changed after the seal. A person still judges whether it is right
  C) Not sure, show me again
```

(B is the move.)

**NEXT.** Next: what GRIP refuses to do, `/howto-refuses`.

For a child: GRIP puts a sticker on its notes. If someone changes a note, the sticker breaks.
