---
description: One-command LinkedIn feed intel run — launches Brave-CDP if needed and runs linkedin-feed-scout, surfacing the comment-draft report for manual review.
---

# /intel-linkedin

Invokes the `intel-linkedin` skill. See
`~/.claude/skills/intel-linkedin/SKILL.md` for full protocol.

Thin KISS orchestrator over `linkedin-feed-scout` (already-shipped
capability). Collapses the two-terminal workflow (launch Brave-CDP, run
scout, open report) into one slash command. Manual publish gate
preserved — V>> reviews drafts and pastes comments himself, GRIP never
auto-comments under V>>'s name.

## Quick reference

| Flag | Behaviour |
|------|-----------|
| (none) | Status check → live scout top-5 if CDP up; else loud HITL with `--auth` instruction |
| `--auth` | Launch Brave-CDP background, prompt V>> to log into LinkedIn manually |
| `--dry` | Extraction + L1 scoring only (no HAL L2, no comment drafts) |
| `--top N` | Override top-K (default 5) |
| `--status` | `bin/brave-cdp --status` + report, no scout |

Direct shell equivalent: `bin/intel-linkedin [flags]`.

Pass-through: any args after the flags above are forwarded to
`bin/linkedin-feed-scout scout`.
