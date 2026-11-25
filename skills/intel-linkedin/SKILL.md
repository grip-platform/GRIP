---
name: intel-linkedin
description: One-command orchestrator that launches Brave-CDP (if down) and runs linkedin-feed-scout, surfacing the comment-draft report path for manual review and publish. Use when V>> wants to do a fast LinkedIn feed engagement round without the two-terminal dance. Tier-1 attended only — manual publish gate preserved.
status: Active
effort: low
version: 1.0.0
created: 2026-05-21
generation: 656
triggers:
  - /intel-linkedin
  - "intel linkedin"
  - "linkedin intel"
  - "scout linkedin"
priority: Standard
integrates:
  - linkedin-feed-scout
  - sneak
  - 140-85
see_also:
  - linkedin-feed-scout
  - stealth
---

# /intel-linkedin — LinkedIn Feed Intel Run

> *"Codec receiving. Frequency 140.85."*

Thin KISS orchestrator around `linkedin-feed-scout` (the already-shipped
capability: extraction + L1/L2 scoring + V>>-voice comment drafts). This
skill collapses the two-terminal workflow (launch Brave-CDP, run scout,
open report) into a single `/intel-linkedin` invocation. Manual publish
gate is preserved — V>> reviews drafts and pastes comments himself.

## When to use

- V>> wants to do a quick LinkedIn engagement round (drop in, scout
  top-5, comment manually on what resonates, move on)
- Brave-CDP either already up or needs a one-command launch + login
- Manual publish posture is wanted (per `/sneak` discipline — no
  auto-comments under V>>'s name, ever)

## When NOT to use

| Situation | Use instead |
|-----------|-------------|
| Auto-commenting (forbidden) | Never — manual publish gate is load-bearing |
| LinkedIn DMA API for V>>'s own data | `linkedin-dma` skill |
| Full Tier-2 stealth (Geonode SOCKS5) | Not shipped; raise with V>> first |
| Deep feed analysis beyond top-K | Run `bin/linkedin-feed-scout scout --top N` directly |

## Usage

```bash
/intel-linkedin              # Status check → live scout (top 5) if CDP up; else loud HITL
/intel-linkedin --auth       # Launch Brave-CDP background, prompt for LinkedIn login
/intel-linkedin --dry        # Extraction + L1 only (no HAL scoring, no drafts)
/intel-linkedin --top 8      # Override top-K (default 5)
/intel-linkedin --status     # Just `brave-cdp --status` + report
```

Direct shell (bypasses Claude Code):

```bash
bin/intel-linkedin                  # Same defaults
bin/intel-linkedin --auth
bin/intel-linkedin --dry --top 3
```

## Operational flow

1. **Status probe**: `bin/brave-cdp --status` checks CDP endpoint :9222
2. **Decision**:
   - CDP **up** → run `bin/linkedin-feed-scout scout --top N` (default 5)
   - CDP **down**, `--auth` flag → `bin/brave-cdp --background` then
     instruct V>> to log into LinkedIn in spawned Brave + re-run
   - CDP **down**, no `--auth` → loud HITL: print exact `--auth` command
     and exit non-zero (NEVER silent fail)
3. **Surface report**: print the report path produced by scout
4. **TLDR** (≤ 3 lines per `rules/canonical-output-style.md`):
   `Scouted N posts. Top-K ranked. Drafts: <path>`

Per `/sneak` posture: NO outbound to Slack/Discord/WhatsApp. Drafts stay
local. Per `rules/efficiency-rules.md` Rule 11: tight Done/Remaining/
Open/Next when wrapping.

## Cross-references

- Underlying capability: `skills/linkedin-feed-scout/SKILL.md`
- Stealth posture: `skills/sneak/SKILL.md`
- Codec/voice register: `skills/140-85/SKILL.md`
- Output style: `rules/canonical-output-style.md`
- Plan grounding: `plans/log-into-linkedin-with-polished-flurry.md`

## Falsification

This skill's value claim: "A single `/intel-linkedin` slash command is
used by V>> more frequently than the underlying `bin/linkedin-feed-scout`
direct invocation, indicating the KISS orchestrator removes real
friction."

Falsified by:

- V>> never invokes `/intel-linkedin` over 30 days post-merge and keeps
  running `bin/linkedin-feed-scout` directly → the orchestration was
  not actually friction; delete the skill
- The `--auth` flow proves more friction than the manual two-terminal
  dance (e.g. detached Brave never reaches the CDP endpoint reliably)
- LinkedIn bans V>>'s account within 30 days of first `/intel-linkedin`
  invocation (would falsify the entire Tier-1 attended hypothesis,
  cascading from `linkedin-feed-scout`)

Tracked via H-INTEL-LINKEDIN-1 (30-day deadline, registered at PR-create
time per `rules/scientific-method.md`).

## Origin

Created 2026-05-21 (Gen 656) per V>>-authorised `/140-85 --mission`.
Watermark `V>>--<<V`. Thin orchestrator over `linkedin-feed-scout` (PR
#3162 sha `29626abc5`, merged earlier today). The two-step manual
workflow (`bin/brave-cdp` then `bin/linkedin-feed-scout`) was the
friction this skill removes.

— V>>--<<V
