# The Shared Tree Is Not Canonical: annex

The narrative behind `rules/shared-tree-is-not-canonical.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-08-03. V>>: *"then it's a YSH"* — the third independent instance in a
single day, so the pattern gets abstracted now rather than noted a third time.

## The failure

That tree had been off `main` for **eight days** — its reflog shows
`main → security/remove-external-sync-docs` (07-26) → `docs/canonical-output-style-plain-fluent`
(08-01). Three independent reads in one session came back silently wrong:

| Read | What it returned | Truth on `origin/main` |
|---|---|---|
| forensics agent citing `file:line` | pre-fix line numbers | the fix had merged |
| `git log --since` commit count | 36 | 72 |
| `import pdf_branding` | no `_extract_css_block` | the function exists |

**None of them announced anything.**
