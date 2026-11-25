---
name: plain-language
description: The entry point to Simplified Technical English in GRIP. Rewrites text in 80% ASD-STE100 through /clarify and lints the result. Also translates GRIP metrics, gate messages and terms to plain English.
user_invocable: true
command: plain
triggers:
  - /plain
  - translate to plain language
  - explain in plain english
---

# /plain -- Plain Language Translation

`/plain-language` is the entry point to Simplified Technical English in GRIP.
Its free-text mode writes in 80% ASD-STE100. `/clarify` defines this register
in `skills/clarify/SKILL.md`. The other four modes translate one GRIP metric,
gate message, session state or term through `lib/plain_language.py`. That job
does not change here.

GRIP is not certified or endorsed by ASD. ASD-STE100 is a trademark of ASD.

## Usage

```bash
/plain <text>                 # Rewrite text in 80% ASD-STE100 (the STE path)
/plain metric <name> <value>  # Explain a numeric metric
/plain gate <principle> <msg> # Translate a gate message
/plain session                # Translate current session state
/plain term <word>            # Look up a single term
```

## The STE path: `/plain <text>`

This mode is the `plain` preset of `/clarify` (`skills/clarify/SKILL.md`).
Its shared rules apply here too: add nothing, and keep the words to type
unchanged. Write in 80% ASD-STE100.

1. Run `/clarify plain` on the passage. Swap each insider term for its plain
   word. The terminology map in `lib/plain_language.py` names the ones GRIP
   already knows.
2. Lint the rewrite, exactly as `commands/clarify.md` shows:

   ```bash
   "${CLAUDE_DIR:-$HOME/.claude}/bin/grip-py" "${CLAUDE_DIR:-$HOME/.claude}/lib/ste_lint.py" <<'DRAFT'
   <the draft, verbatim>
   DRAFT
   ```

3. Fix every error the linter names. Show the rewrite, then the linter's
   tally line under it -- for example `0 errors, 0 warnings`.

This is what 80% means. The structural rules apply in full: sentence length,
paragraph length, the word list, and the determiner floor. An approved word
is preferred over one that is not. A technical name is kept exactly as
written.

## The other four modes

`metric`, `gate`, `session` and `term` stay a lookup or a threshold table
through `lib/plain_language.py`, not a rewrite. This skill update does not
touch them.

## Implementation

Use `lib/plain_language.py` directly:

```python
from lib.plain_language import translate_text, explain, explain_gate, explain_session, translate_term
```

### For metric explanation (`/plain metric flow 0.85`):

```python
result = explain(metric_name, float(value))
```

### For gate messages (`/plain gate KISS "function is 80 lines"`):

```python
result = explain_gate(principle, raw_message)
```

### For session state (`/plain session`):

```python
result = explain_session({"flow": 1.0, "velocity": 9.3, "system_health": 0.75})
```

### For single terms (`/plain term genome`):

```python
result = translate_term(term)
```

## Examples

| Input | Output |
|-------|--------|
| `/plain Loading 3 skills and 2 agents` | Loading 3 playbooks and 2 workers |
| `/plain metric flow 0.85` | Running smoothly, no blockages |
| `/plain term KONO` | knowledge base |
| `/plain gate KISS "func is 80 lines"` | Function is too long (80 lines). Split into smaller functions |

## Reference

Full terminology map and metric translators in `lib/plain_language.py`.
Rule 17 (Plain Language Output) governs all user-facing output.
