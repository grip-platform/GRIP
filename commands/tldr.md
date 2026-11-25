---
name: tldr
description: Ultra-compressed answer mode for this turn. ≤3 lines / ≤25 words. Auto-suppressed on safety-gated topics.
---

# /tldr

Invokes the `tldr` skill — see `skills/tldr/SKILL.md`.

This is the `line` preset of `/clarify`. The rules shared by every preset in `skills/clarify/SKILL.md` apply too: nothing added, words to type unchanged, 80% ASD-STE100.

Reply in ≤3 lines / ≤25 words. Lead with the load-bearing fact. Drop reasoning trace. Keep evidence tokens (paths, SHAs, counts). Auto-Clarity Override applies — safety-gated topics return full prose.

Examples:
- `/tldr what file failed?` → `test_auth.py:42 — stale token.`
- `/tldr next step?` → `Push branch. Open PR. Await CI.`

Synonyms (same effect): `just the answer`, `one line`, `short answer`.
