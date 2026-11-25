# /linkedin-post

Transform a plain LinkedIn draft into a structured, distinctive, Unicode-
formatted post — then show a brief delta review. Reason + review first
(audience, intent, anti-fluff), lift into A/B/C structure, then apply the
7-element Unicode toolkit LinkedIn renders as bold/italic in plain text.

Invokes the `linkedin-post-craft` skill. Mechanical floor: `lib/linkedin_post_craft.py`.

## Usage

```
/linkedin-post <raw draft>            # craft + delta review
/linkedin-post                        # use the draft in the current message/clipboard
```

Optional context after the draft: audience, intent, "accessibility" (also emit a
plain-ASCII variant), or "satire"/"carousel" to bias the structure.

## What it does

1. **Reason + review** — audience, single intent, one-point-per-post check.
2. **Anti-fluff gate (by construction)** — runs `antifluff_violations(draft)`;
   any banned marketing token is a blocker, rewrite the root sentence in plain,
   number-led language. Styled fluff is still fluff.
3. **A/B/C structural lift** — pull the messy paragraph into framed sections
   (`⌜ A ⌟ … ⌜ B ⌟`) or tracked steps (`▸ … ─→`).
4. **Teaser + easter-egg pass** — name a coming feature without committing;
   preserve intentional markers (`V>>`, `◆`).
5. **Style last** — apply the toolkit (bold for the 1–3 load-bearing phrases,
   italic for meta-asides, structural glyphs for sections).
6. **Delta review** — a short before→after summary: what structure changed, which
   phrases got emphasis, and any anti-fluff violations that were fixed.

## Mechanical floor (verify-canonical)

The glyph conversion and anti-fluff detection are deterministic. Smoke-test:

```bash
PYTHONPATH=$HOME/.claude/lib python3 -c "import linkedin_post_craft as l; \
print(l.bolden('GRIP ships provability')); \
print(l.italicise('watch this')); \
print(l.frame('A')); \
print(l.antifluff_violations('This powerful, game-changer tool'))"
```

Tests: `PYTHONPATH=$HOME/.claude $HOME/.claude/venv/bin/pytest tests/test_linkedin_post_craft.py -q`

## The 7-element toolkit

| Element | Glyph | Use |
|---------|-------|-----|
| Bold | 𝗕𝗼𝗹𝗱 | emphasis (replaces `**bold**`) |
| Italic | 𝘐𝘵𝘢𝘭𝘪𝘤 | meta-asides ("watch this") |
| Corner brackets | ⌜ A ⌟ | A/B/C framing |
| Heavy rule | ━━━ | section breathing room |
| Tracked bullet | ▸ | step bullets |
| Arrow | → | step transition |
| Middot · / diamond ◆ | `A · B` / `◆` | handle joins · terminal marker |

Italic has no digit glyphs — `italicise` keeps digits as ASCII by design.

## Accessibility

Some screen readers announce Math glyphs inconsistently. Pass `accessibility`
(or whenever the post is accessibility-sensitive) to also emit a plain-ASCII
variant with the same structure. The glyphs are finish, never load-bearing
meaning.

## Cross-reference

Hard cross-ref to `authentic-writing` — the anti-fluff gate fires by
construction on every craft pass, not on opt-in. Full protocol + worked
examples: `skills/linkedin-post-craft/SKILL.md`.

## Out of scope

- Publishing to LinkedIn (manual — the operator publishes; this only crafts).
- Chrome-plugin productisation (separate `/broly` council + repo decision).
