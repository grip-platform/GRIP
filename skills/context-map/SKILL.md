---
name: context-map
description: Generate and maintain path-scoped context rules for feature areas. Proactive context injection so Claude understands multi-actor flows before writing code.
status: Active
effort: medium
version: 1.0.0
triggers:
  - "/context-map"
  - "context map"
  - "map feature context"
  - "generate context rule"
integrates:
  - context_staleness.py
see_also:
  - converge
  - design-principles
  - pre-mortem-facilitation
---

# /context-map -- Feature Context Mapping

Generates and maintains path-scoped context rules so Claude automatically loads feature context when touching relevant files.

## Commands

```
/context-map generate <path-or-feature-name>    # Create new context rule
/context-map refresh [feature-name]              # Update stale rules
/context-map status                              # Show staleness report
```

## Generate Flow

Creates a new context rule via guided interview.

### Step 1: Explore

Explore the codebase to understand the feature:

1. Use Grep/Glob to find all files related to the feature name
2. Read key files (views, models, templates, tests)
3. Identify actors, state mutations, request lifecycles
4. Map cross-directory dependencies

### Step 2: Draft

Create an initial context rule draft covering:

- Business purpose (what and why)
- Actor types (who interacts with this feature)
- Request lifecycle (step-by-step flow)
- State mutations (what changes, where)
- Edge cases (multi-actor, temporal, parameter-as-gate)
- Key files (primary implementation files)
- Dependencies (which other features interact)

### Step 3: Interview (HITL Gate -- MANDATORY)

Present the draft to the user via AskUserQuestion. Ask in sequence:

**Question 1: Actors and Business Context**
```
"I found these actors for [feature]: [list]. Are there others?
What business rules should I know that aren't obvious from code?"
```

**Question 2: Edge Cases and Known Bugs**
```
"What edge cases have caused bugs? Any multi-actor scenarios
I should document? Known gotchas?"
```

**Question 3: Related Directories**
```
"I found these directories: [list]. Are there others that
relate to this feature? Any shared utilities or templates?"
```

### Step 4: Save

1. Incorporate interview answers into the context rule
2. Save to `<project>/.claude/rules/features/<feature>.md`
3. Use the template from `~/.claude/skills/context-map/context-rule-template.md`
4. Set `last_validated` to today's date
5. Set `validation_threshold` to 10 (default)

### Step 5: Update Feature Index

Add or update the feature's row in the project's CLAUDE.md feature map table:

```markdown
## Feature Map
| Feature | Actors | Touches | Context Rule |
|---------|--------|---------|-------------|
| [name]  | [actors] | [modules] | features/[name].md |
```

If the Feature Map section doesn't exist, create it.

## Refresh Flow

Updates existing context rules that have drifted from code.

### Step 1: Detect Staleness

Run `context_staleness.py` against the project's context rules:

```bash
python3 ~/.claude/lib/context_staleness.py <project-root>/.claude/rules/features/
```

This outputs a markdown report of rules where commits since `last_validated` exceed `validation_threshold`.

### Step 2: Analyse Changes

For each stale rule:

1. Run `git log --oneline --since=<last_validated> -- <paths>` to see what changed
2. Read the changed files to understand the nature of changes
3. Draft specific updates to the context rule sections

### Step 3: Propose Updates (HITL Gate -- MANDATORY)

Present proposed changes to the user via AskUserQuestion:

```
"[feature] context rule has [N] commits since last validated.
Key changes: [summary]. Proposed updates:
- [section]: [change]
- [section]: [change]
Approve these updates?"
```

Options: "Approve as-is", "Modify first", "Skip this rule"

### Step 4: Apply

1. Edit the context rule with approved changes
2. Update `last_validated` to today's date
3. Update feature index if actors/dependencies changed

## Status Command

Runs the staleness checker and displays the report without making changes:

```
/context-map status
```

Output format:

```
Context Rule Staleness Report
=============================
[feature]: [N] commits since [date] -- [STALE/OK]
[feature]: [N] commits since [date] -- [STALE/OK]
```

## Path Pattern Guidelines

For mixed/interleaved codebases, use granular path patterns:

```yaml
paths:
  - "apps/feature/**"              # Primary feature directory
  - "apps/other/views/feature*"    # Cross-directory views
  - "apps/models/*feature*"        # Related models
  - "**/templates/feature/**"      # Templates anywhere
  - "tests/**/test_feature*"       # Related tests
```

Rules:
- Prefer specific globs over broad wildcards
- Include test directories so rule loads during test work
- Use `**/` prefix for files that could be anywhere in the tree
- Each path pattern should match real files (verify with Glob)

## HITL Gates Summary

| Gate | When | Human Role |
|------|------|-----------|
| Generation interview | Creating new rule | Review draft, add business context, correct actors |
| Refresh approval | Staleness threshold exceeded | Review proposed changes, approve/modify/skip |
| Pre-task loading | Claude touches matching files | None (automatic via Claude Code path matching) |

## Origin

Created to prevent bparty-class failures (PR #2572) where Claude fixed symptoms without understanding multi-actor context. Context rules inject feature knowledge proactively via Claude Code's native path-scoped rule loading.
