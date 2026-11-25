## Origin

V>> stated this rule on 2026-09-29 while sending the Aubrey fraud package. He
writes an em dash as two short dashes (` -- `), never as the Unicode em dash
character (U+2014, `—`).

## The rule

In any prose written in the operator's voice or on his behalf (emails,
documents, posts, briefs, messages), render an em dash as two spaced short
dashes: ` -- `. Never emit the Unicode em dash `—` (U+2014).

## Why

The operator's established style is `--`; it appears throughout his own
documents (for example the 2026-09 affidavit draft reads `"Human In The Loop" --
menslike toesig`). Matching his hand matters more than typographic convention,
and a `—` in output reads as someone else writing.

## Falsification

This rule is working iff both of the following hold for every draft produced for
the operator, each checked on its own:

1. the draft contains no `—` (U+2014) -- a count of that character over the text
   is zero;
2. wherever an em dash is intended the draft uses ` -- ` -- the spaced form, with
   a space on each side of the two hyphens.

If a draft ships with `—`, the rule was not loaded in that session; promote or
re-verify the tier.

## Scope

All prose drafted in the operator's voice and on his behalf. Code, identifiers,
and text copied verbatim from a source that genuinely contains `—` are left
unchanged. The same holds for a Markdown table or a fenced code block that sits
inside such prose: the rule governs the sentences, not the cells or the code,
and a quoted line keeps whatever dash its source used. The rule applies to new
prose only; it never licenses retroactive edits of existing source text, records
or quotations to replace a dash they already carry. The literal to emit is
` -- ` -- a space, two hyphens, a space -- and the `(--)` shorthand in any
summary means that string, never the dashes without their spaces.
