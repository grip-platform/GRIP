---
description: "Pair with GRIP: you drive and GRIP navigates by default, the roles swap on a word, checkpoints run through the /hitl family, and /mesh carries the pair across machines or sessions"
disable-model-invocation: false
---

# /pair

Start pairing with GRIP. You drive and GRIP navigates by default: GRIP thinks
aloud before each step, challenges a step that looks wrong once and plainly,
proposes the next one, and never takes the wheel without asking. The roles swap
on a word.

```bash
sed -n '1,220p' ${CLAUDE_DIR:-$HOME/.claude}/skills/pair-mode/SKILL.md
```

Load the skill above and pair by it. In short:

| Said | What happens |
|------|--------------|
| "you drive" | GRIP takes the wheel for the stretch you name |
| "I'll drive" | You take it back; GRIP finishes its current step and navigates |
| "may I drive?" (GRIP asks) | GRIP takes the wheel only on your yes |

Checkpoints run through the `/hitl` family: `/asking-users` for one yes/no
before a risky step, `/creating-wizards` to set up a session with several
unknowns, and `/hitl-decision-matrix` to settle several trade-offs at once at a
design fork. `/hitl` decides between them when the shape is unclear.

When the pair spans two machines or two sessions, `/mesh` is the transport:
`/mesh connect` on each side, then `/mesh pair <name>`. Mesh's Grip is the
driver and its Guard is the navigator.

Spawning worker agents is `/delegate`, not pairing. `/mode pair` loads the same
posture as a mode, together with the whole `/hitl` family.
