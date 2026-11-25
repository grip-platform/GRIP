---
name: prompt-coach
description: Analyze user prompts and suggest improvements for clarity, specificity, and GRIP feature usage.
status: Active
effort: low
version: 1.0.0
triggers: ["/prompt-coach", "improve my prompt", "help me ask better"]
integrates: [auto_skill_generator.py, converge/SKILL.md]
see_also: [converge, rsi-aliases, creating-skills, insights-pipeline]
---

# Prompt Coach

Analyzes user prompts and suggests improvements. Helps users leverage GRIP features they may not know about.

## Protocol

### Step 1 — Analyze Input

Examine the user's prompt (or last 3 prompts if no explicit input) for:

1. **Missing specificity**: No success criterion, no scope boundary, vague outcome
2. **Missing context**: No file references, no branch, no error messages
3. **Verbose patterns**: Could use semantic prefixes or templates
4. **Underused features**: GRIP capabilities the user hasn't tried

### Step 2 — Generate Suggestions

For each issue found, provide:
- **What**: The specific gap
- **Why**: How it affects output quality
- **Fix**: Concrete rewrite with the improvement applied

Priority order: specificity > context > verbosity > feature discovery

### Step 3 — Template Matching

Check `data/prompt-templates/` for templates that match the user's intent:
- If a template fits, show the filled-in version
- If no template fits but pattern recurs 2x (YSH threshold), propose a new template

### Step 4 — Micro-Skill Genesis Hook

If prompt-coach detects a recurring prompt pattern (2x YSH threshold):
1. Log the pattern to `learning/metrics.jsonl`
2. Propose micro-skill via `auto_skill_generator.py` pattern detection
3. If approved, the pattern becomes a first-class skill

### Examples

**User types**: "fix the thing"
**Coach suggests**:
- Add specificity: What thing? Which file? What's broken?
- Add criterion: What does "fixed" look like?
- Rewrite: `/converge fix the auth middleware timeout in lib/auth.py — should return 401 within 200ms`

**User types**: `/converge --code --depth 5 refactor the database connection pooling`
**Coach suggests**:
- Use semantic prefix: `/converge 5: refactor the db connection pooling` (saves 12 chars)
- The `--code` flag is auto-detected from "refactor"

**User types**: "make it better"
**Coach suggests**:
- Missing: what is "it"? what dimension of "better"?
- Missing: no files, no branch, no context
- Rewrite: `/converge >85 improve the error handling in lib/api_client.py — reduce retry noise`

## Delight Integration

Prompt-coach should feel helpful, not pedantic:
- Only suggest when improvement is meaningful (>20% clarity gain)
- Lead with the improved version, explain why after
- Celebrate good prompts: "This is already specific and actionable — running as-is"
