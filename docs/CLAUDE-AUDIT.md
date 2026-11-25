# /audit-claude-footprint — Cache-Aware Audit Workflow

A 7-section audit of a GRIP-equipped Claude Code install. Reports the *actual*
loaded footprint, not a vanilla-CC approximation.

## Run

```bash
bash ~/.claude/scripts/claude-audit.sh         # human-readable
bash ~/.claude/scripts/claude-audit.sh --json  # token section as JSON
```

## What it reports

| Section | Source | Why this source |
|---------|--------|-----------------|
| CLAUDE.md size | `wc -w` on `~/.claude/CLAUDE.md` + project | Word count is a floor, not the whole loaded payload (`@-includes` push it up) |
| Active hooks | `jq '.hooks \| keys' ~/.claude/settings.json` | Lists every hook event with handlers |
| UserPromptSubmit cost | `jq` aggregate over the array | Per-prompt cost = sum of timeouts; async vs blocking matters |
| Plugins | `jq '.plugins \| keys' ~/.claude/plugins/installed_plugins.json` | The real manifest, not the cache directory |
| Skills | `ls ~/.claude/skills/ \| wc -l` | Count is informational; loaded-on-demand per prompt-routing |
| MCPs | `jq '.mcpServers \| keys' ~/.claude/.mcp.json` | GRIP puts MCPs in `.mcp.json` per `rules/mcp-org-isolation.md` |
| Token usage 7d | Python scan of `~/.claude/projects/**/*.jsonl` | The session JSONLs are the only durable token-usage record |
| GRIP footprint | `find rules/ + ls agents/ + ls hooks/` | Always-on surface beyond CLAUDE.md alone |

## The headline metric: TRUE input per call

Claude Code uses prompt caching aggressively. Each assistant turn records four
distinct token fields in its `usage` block:

| Field | What it measures |
|-------|------------------|
| `input_tokens` | Uncached, new-this-turn content. Often 5-50. |
| `cache_creation_input_tokens` | New material added to cache this turn. |
| `cache_read_input_tokens` | Material pulled from cache this turn. |
| `output_tokens` | Assistant response. |

**TRUE input per call = `input_tokens + cache_creation + cache_read`**.

Reading `input_tokens` alone (as v0 of this audit did) under-counts by ~99%.
A typical GRIP turn averages 90k+ TRUE input tokens; the same turn's
`input_tokens` field reads ~5-50.

## Why v0 was wrong

The v0 script (V>>'s original) hit three structural bugs on macOS + GRIP:

1. **MCPs from `settings.json`** → returns `[]`. Real source is `~/.claude/.mcp.json`.
2. **`grep -oP`** (Perl regex) → BSD grep on macOS doesn't support `-P`. Plus
   `awk: division by zero` because zero matches found.
3. **Plugins from `ls ~/.claude/plugins/`** → returns metadata files
   (`blocklist.json`, `cache`, `installed_plugins.json`), not actual plugins.

The v1 script in `scripts/claude-audit.sh` fixes all three.

## Calibration vs vanilla CC

The v0 script's targets (`combined < 1200 words`, `3-5 plugins`,
`3-5 skills active`) are calibrated for vanilla Claude Code, not GRIP. GRIP's
design philosophy is **breadth + on-demand routing**, not always-on minimalism.
Use this audit for *changes over time* in your own install, not for hitting
the v0 targets verbatim — they don't apply.

## Falsification

This audit is wrong if any of:

- The token section's TRUE input number diverges from `claude --usage` or the
  Anthropic billing dashboard by >10% over a 7-day window
- The MCPs section misses servers that are demonstrably active in
  `claude mcp list`
- Hook count diverges from what `claude hooks list` reports
- Skills count diverges from `find ~/.claude/skills -name SKILL.md | wc -l`

If any of these falsify, fix the script and bump the version comment.

## Origin

Codified 2026-05-03 from V>>'s audit-script deliberation. v0 was a reasonable
starting point; v1 is GRIP-aware after empirical bug-hunting on the actual
infrastructure layout.

Hypothesis tracking: see `lib/hypothesis_engine.py status` and grep for
`audit_claude_footprint` to find the registered prediction about this script's
accuracy over time.
