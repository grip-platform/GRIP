# Strategy Skill Template

Use this template to create new strategy-related skills for strat-ops integration.

---

## Frontmatter Template

```yaml
---
name: {skill-name}
description: {One sentence describing purpose}. Use when {trigger context}.
status: Active
version: 1.0.0
triggers:
  - /{command}
  - "{natural language trigger 1}"
  - "{natural language trigger 2}"
integrates:
  - asking-users
  - strat-ops
  - {related-strategy-skill}
---
```

## Required Sections

### 1. Purpose (Why This Exists)

```markdown
# {Skill Name}

{One paragraph explaining the strategic purpose}

## When to Use

- {Context 1}
- {Context 2}
- {Context 3}
```

### 2. Protocol (Step-by-Step)

```markdown
## Protocol

### Step 1: {First Step}

{Description of what happens}

### Step 2: {Second Step}

{Description of what happens}

...
```

### 3. AskUserQuestion Flows

```markdown
## AskUserQuestion Flow

### {Flow Name}

Question: "{question text}"
Header: "{≤12 chars}"
Options:
- "{Option 1}" - {description}
- "{Option 2}" - {description}

Teaching: {what user learns}
Collecting: {field_name}
```

### 4. Output Format

```markdown
## Output Format

\`\`\`markdown
# {Output Title}

**Date**: {date}
**Participants**: {list}

## {Section 1}

{content}

## {Section 2}

{content}
\`\`\`
```

### 5. Storage Location

```markdown
## Storage

Results stored in:
\`~/.claude/strat-ops/{category}/{date}-{topic}.md\`
```

### 6. Token Budget

```markdown
## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~{X} |
| Per step | ~{Y} |
| Output | ~{Z} |
| **Total** | **~{Total}** |
```

### 7. Related Skills

```markdown
## Related Skills

- \`strat-ops\` - Strategy mode router
- \`{skill-1}\` - {relationship}
- \`{skill-2}\` - {relationship}
```

---

## Quality Checklist

Before creating a strategy skill, verify:

- [ ] Name follows gerund convention (`{verb}-ing-{noun}`)
- [ ] Integrates with strat-ops
- [ ] Uses AskUserQuestion per @asking-users PARAMOUNT
- [ ] Has clear step-by-step protocol
- [ ] Defines output format
- [ ] Specifies storage location
- [ ] Documents token budget
- [ ] Links to related skills

---

## Example: Assumption Mapping

If creating an assumption-mapping skill:

```yaml
---
name: assumption-mapping
description: Surface and score strategic assumptions. Use when validating business hypotheses or preparing for red-team exercise.
status: Active
version: 1.0.0
triggers:
  - /assumption-map
  - "list our assumptions"
  - "what are we assuming"
integrates:
  - asking-users
  - strat-ops
  - red-teaming-strategic-decisions
---
```

---

## Integration with Strat-Ops

After creating a new strategy skill:

1. Add trigger patterns to strat-ops/SKILL.md
2. Update strat-ops routing table
3. Add to SKILLS.cips documentation
4. Add to COMMANDS.cips documentation
5. Register in COWORK_SKILLS.md

---


