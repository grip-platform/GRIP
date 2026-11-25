---
description: What GRIP refuses to do, which gate refused, and why no prompt can change that.
disable-model-invocation: false
---

# /howto-refuses -- what GRIP refuses, and why

**HOOK.** GRIP has gates. A gate is a fixed rule that stops a dangerous action, and no prompt can change it.

**WORKED EXAMPLE.** Ask GRIP to read a dependency folder:

```
read node_modules/react/package.json
```

GRIP's dependency gate refuses and says why: a read inside `node_modules` wastes the window. Other gates stop a force-push, a secret in a prompt, and a commit of a `.env` file. The refusal names the gate.

**### Your Turn.** Ask GRIP to read a file inside `node_modules`. **Completion gate:** GRIP refuses and names the gate.

**COMPREHENSION CHECK:**

```
Reply A, B, or C:
  A) GRIP refuses when it feels like it
  B) A gate is a fixed rule that stops a dangerous action and names itself
  C) Not sure, show me again
```

(B is the move.)

**NEXT.** You know the six moves. Run `/dojo` for the three hands-on lessons.

For a child: Some doors stay locked. GRIP tells you which door, and why.
