---
name: skill-creation-best-practices
description: PARAMOUNT quality gate for ALL skill creation. Enforces Anthropic best practices and GRIP standards before any skill is created, whether proposed, auto-generated, or autonomously discovered. Use when creating, reviewing, or approving ANY skill.
status: Active
effort: high
priority: PARAMOUNT
version: 1.0.0
created: 2025-12-29
triggers:
  - "create skill"
  - "skill proposal"
  - "approve skill"
  - /create-skill
  - auto-improvement cycle
  - learning-approve
integrates:
  - asking-users
  - creating-wizards
  - hitl
  - self-improvement-engine
  - recursive-learning
  - design-principles
see_also:
  - creating-skills
  - autonomous-learning
  - reviewing-contributions
---

# Skill Creation Best Practices

**PARAMOUNT**: This skill MUST be consulted before ANY skill creation in GRIP.

**@asking-users**: Gate A requires AskUserQuestion MANDATORY. See `asking-users` skill for full protocol.

## The Three Gates

All skill creation passes through one of three gates:

| Gate | Trigger | Validation |
|------|---------|------------|
| **A: Proposal** | Human/GRIP suggests skill | AskUserQuestion MANDATORY |
| **B: Auto-Generation** | self-improvement-engine creates | Quality score ≥70% |
| **C: Autonomous** | GRIP steering discovers pattern | Quality score ≥70% + Admin review |

---

## Gate 0: Latent Demand Check (Pre-Gate)

Before entering any of the three gates above, verify latent demand:

1. **Evidence of demand**: Has the user tried to do this task manually at least 3 times?
2. **Existing task acceleration**: Does it make an EXISTING workflow faster (not create a new one)?
3. **Model-native check**: Does the current model already handle this without instruction? If yes, skip.

If all three pass, proceed to Gate A/B/C. If any fail, HALT and explain why the skill isn't needed.

The best feature is the one you don't build because the model already handles it.

---

## Anthropic Best Practices (Official)

Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices

### 1. Conciseness is Key

The context window is a shared resource. Challenge every token:

- "Does Claude really need this explanation?"
- "Can I assume Claude knows this?"
- "Does this paragraph justify its token cost?"

**Default assumption**: Claude is already very smart. Only add context Claude doesn't already have.

### 2. Frontmatter Requirements

```yaml
---
name: skill-name           # Required, max 64 chars, lowercase+hyphens
description: Purpose here  # Required, max 1024 chars, non-empty
---
```

**Name rules:**
- Maximum 64 characters
- Lowercase letters, numbers, hyphens only
- No XML tags
- No reserved words: "anthropic", "claude"

**Description rules:**
- Non-empty, maximum 1024 characters
- Write in THIRD PERSON (not "I can help you" or "You can use this")
- Include both WHAT and WHEN: "Extracts PDF text. Use when working with PDF files."

### 3. Naming Convention

Use **gerund form** (verb + -ing):

| Good | Avoid |
|------|-------|
| `processing-pdfs` | `pdf-helper` |
| `analyzing-spreadsheets` | `spreadsheet-utils` |
| `managing-databases` | `db-tools` |
| `testing-code` | `test-helper` |

Avoid vague names: `helper`, `utils`, `tools`, `documents`, `data`, `files`

### 4. Body Constraints

- **Maximum 500 lines** for optimal performance
- **Minimum 3 sections** with `##` headers
- Split larger content into separate files (progressive disclosure)
- Structure longer files with table of contents

### 5. Progressive Disclosure

SKILL.md serves as overview pointing to detailed materials:

```text
skill-name/
├── SKILL.md              # Main instructions (loaded when triggered)
├── reference.md          # Detailed reference (loaded as needed)
├── examples.md           # Usage examples (loaded as needed)
└── scripts/
    └── utility.py        # Executed, not loaded into context
```

**Critical**: Keep references ONE level deep. Never nest references.

**Token discipline — when to split into `references/`**: when heavy reference
material (detailed API tables, exhaustive option lists, migration guides,
extended examples) pushes SKILL.md toward the 500-line ceiling, extract it into
a `references/` subdirectory. The main SKILL.md carries the overview and a
pointer; the reference file is read only on demand (not on every invocation of
the skill). Observed at N=2 in external skill corpora (obra/superpowers,
google/skills) and in GRIP itself (`skills/remotion-video-creation/`,
`skills/email/`) — a small sample that establishes the pattern but does not
prove universal benefit. Falsified if reference files are routinely read on
every invocation regardless of need (no selective loading in practice), making
the split cosmetic overhead; collapse both files if that is measured.

### 6. Degrees of Freedom

Match specificity to task fragility:

| Freedom | When to Use | Example |
|---------|-------------|---------|
| **High** | Multiple approaches valid | Text instructions for code review |
| **Medium** | Preferred pattern exists | Pseudocode with parameters |
| **Low** | Operations fragile/error-prone | Exact script, no modifications |

### 7. Workflows

For complex operations, provide checklists:

```markdown
## Workflow

Copy this checklist:
- [ ] Step 1: Analyse input
- [ ] Step 2: Validate data
- [ ] Step 3: Execute operation
- [ ] Step 4: Verify output
```

Implement feedback loops: **Validate → Fix → Repeat**

### 8. Anti-Patterns

| Anti-Pattern | Why Bad | Solution |
|--------------|---------|----------|
| Windows paths (`\`) | Fails on Unix | Always use `/` |
| Too many options | Confusing | Provide default with escape hatch |
| Vague names | Poor discovery | Use specific gerund form |
| Time-sensitive info | Becomes outdated | Use "old patterns" section |
| Nested references | Partial reads | Keep one level deep |
| Inconsistent terminology | Confusion | Pick one term, use throughout |

---

## GRIP-Specific Requirements

### Directory Structure

```text
~/.claude/skills/{skill-name}/
├── SKILL.md              # Required - main document
├── checklist.md          # Optional - validation checklist
├── agents/               # Optional - sub-agents
│   └── agent-name.md
└── templates/            # Optional - skill-specific templates
```

### GRIP Frontmatter Extensions

```yaml
---
name: skill-name
description: Purpose statement
status: Active              # Active, Pending, Deprecated
auto_generated: true        # If auto-generated
generation_date: 2025-12-29 # If auto-generated
source_pattern: pattern-name # If auto-generated
version: 1.0.0              # Recommended
triggers:                   # Recommended
  - /command-name
  - "natural language trigger"
integrates:                 # If combines other skills
  - other-skill
  - plugin@location
---
```

### Design Principles Compliance

All skills MUST comply with GRIP design principles:

| Principle | Requirement |
|-----------|-------------|
| **YAGNI** | Don't build features until needed |
| **KISS** | Simplest solution that works |
| **DRY** | Single source of truth |
| **YSH** | Pattern 2x proven → abstract NOW, through the promotion gate |
| **BIG-O** | O(n log n) ceiling, justify O(n^2)+ |

---

## Validation Rules

### Machine-Verifiable Criteria

#### Frontmatter Validation

| Field | Rule | Regex/Check |
|-------|------|-------------|
| `name` | Required, lowercase+hyphens | `^[a-z][a-z0-9-]*$` |
| `name` | Max 64 chars | `len(name) <= 64` |
| `name` | **Must equal the parent directory name** | strict loaders warn otherwise |
| `description` | Required, non-empty | `len(description) > 0` |
| `description` | Max 1024 chars | `len(description) <= 1024` |
| `description` | Third person | No "I ", "You ", "We " |
| `description` | Include trigger | Contains "Use when" or "when" |
| `status` | Valid value | `Active|Pending|Deprecated` |
| frontmatter | Strict-YAML parseable | no `: ` in unquoted scalars, no reserved start chars (`[`, `"`, `@`, backtick), quote-then-text items |

> **Mechanical gate:** `python3 lib/skill_lint.py check <root>` enforces the strict-loader
> spec (pi-coding-agent / OpenCode rules). A skill that fails it is **silently dropped**
> by those harnesses — a missing `description` or a YAML-invalid frontmatter means the
> skill does not exist for them, no warning shown. `tests/test_skill_lint.py` runs the
> same gate over every GRIP skill tree so this can never regress.

#### Body Validation

| Rule | Check |
|------|-------|
| Max 500 lines | `wc -l < 500` |
| Min 3 sections | Count `^## ` headers |
| No placeholders | No `\{\{.*\}\}` patterns |
| No empty sections | Content after each `##` |
| Code blocks tagged | No ` ``` ` without language |

#### Structure Validation

| Rule | Check |
|------|-------|
| Forward slashes only | No `\\` in paths |
| References one level deep | No `](*.md)` in referenced files |
| Concrete examples | Has `Example` or `example` section |

### Drift Audit (existing skills)

The rules above catch a *new* skill's defects. They do not catch **drift** in an existing
one: a skill written for an older model keeps passing every check while its content
quietly goes stale.

Claude Code ships an audit for exactly that — `Skill(claude-api, "prompt-audit")`. It is
an LLM-driven skill, not a CLI binary; there is no `claude prompt-audit` command. Its
authoritative pattern catalogue is the bundled `shared/prompt-audit.md`, which is the file
to read before trusting any finding it reports.

Two of its properties are load-bearing:

- **It proposes a diff and never applies one.** Its prime directive is that a finding you
  cannot tie to a named pattern is not a finding, and that an audit which finds nothing
  should change nothing.
- **Its "What not to flag" keep-list binds as hard as its pattern tables.** Prohibitions
  against *current, demonstrated* failures stay. That is why GRIP's verify-canonical, R0
  and `/prove-it` doctrines are out of its scope, even though the bundled Opus 5 migration
  guide advises deleting verification scaffolding in general.

GRIP's first sweep found one real defect class: **fossilised model pins**. Write model
*roles* ("Opus plans, Sonnet executes"), never model *versions* — a version in a
frontmatter `description` rides in every request and fossilises silently between
generations. See `docs/prompt-audit-sweep-2026-08-11.md` for that sweep, its coverage
limits, and what it deliberately left alone.

---

## Quality Checklist

See [checklist.md](checklist.md) for standalone version.

### Core Quality

- [ ] Name follows gerund convention (`processing-*`, not `*-helper`)
- [ ] Description specific with key terms
- [ ] Description includes WHAT and WHEN (trigger phrase)
- [ ] Description in third person
- [ ] Body under 500 lines
- [ ] At least 3 `##` sections
- [ ] Additional details in separate files (if needed)
- [ ] No time-sensitive information
- [ ] Consistent terminology throughout
- [ ] Concrete examples provided
- [ ] File references one level deep
- [ ] Progressive disclosure pattern used
- [ ] Workflows have clear steps

### GRIP Extensions

- [ ] Status field set (Active/Pending/Deprecated)
- [ ] Version specified (semver)
- [ ] Triggers listed
- [ ] Integration points defined (if applicable)
- [ ] Design principles compliant (YAGNI/KISS/DRY/YSH)

### Code/Scripts (if applicable)

- [ ] Scripts solve problems (don't punt to Claude)
- [ ] Explicit error handling
- [ ] No magic constants (all values documented)
- [ ] Required packages listed
- [ ] No Windows-style paths

---

## Gate A: Proposal Gate

When human or GRIP proposes a skill, AskUserQuestion is MANDATORY:

```text
[SKILL CREATION GATE]

Proposed: {skill_name}
Quality Score: {score}/100

Validation:
[x] Name follows gerund convention
[x] Description includes triggers
[x] Status field set
[ ] Token budget specified

Warnings:
- {list any validation failures}

Question: Approve skill creation?
Options:
  - "Yes, create" → Proceed
  - "Revise proposal" → Modify and re-validate
  - "Reject" → Cancel with reason
```

**NEVER create a skill without explicit approval through this gate.**

---

## Gate B: Auto-Generation Gate

When self-improvement-engine generates a skill:

1. **Pre-generation validation** - Check pattern quality
2. **Template compliance** - Use quality-enforced template
3. **Post-generation validation** - Verify all fields populated
4. **Quality threshold** - Reject if score < 70%

```text
Quality Score Calculation:
- Frontmatter complete: 30 points
- Name convention: 10 points
- Description quality: 20 points
- Body structure: 20 points
- No placeholders: 10 points
- Examples present: 10 points

Pass threshold: 70/100
```

---

## Gate C: Autonomous Discovery Gate

When GRIP steering mode discovers a pattern:

1. Same validation as Gate B
2. Mark for Admin review if borderline (70-80%)
3. Auto-reject if clearly fails (<70%)
4. Auto-approve only if excellent (>90%) AND pattern proven 2x AND its first
   implementation was built with a human in the loop (`/hitl`), then tested,
   monitored and ultralearned (`rules/knowledge-maturation-functor.md`)

---

## Integration Points

### Self-Improvement-Engine Hook

When `optim.sh generate_skill()` runs:

```text
1. Load skill-creation-best-practices
2. Validate pattern against rules
3. If score < 70%: REJECT
4. If score 70-90%: Mark for review
5. If score > 90%: Proceed
```

### Learning-Detector Hook

When `learning-detector.py create_skill_candidate()` runs:

```text
1. Apply quality validation
2. Add quality_score to candidate
3. Mark requires_review: true if borderline
4. Store validation results in candidate
```

### Template Integration

The quality-enforced template ensures:
- No unfilled placeholders on output
- All required fields populated
- Validation during generation, not after

---

## Examples

### Good Example: markdown-expert

- Clear problem statement with statistics
- Root cause analysis explaining WHY
- Specific rules with code examples
- Verification checklist
- Token budget documented

### Bad Example: meta-unused-skills-blocker

- Empty sections (unfilled placeholders)
- Missing actual rules/content
- Boilerplate without substance
- No concrete examples

---

## Token Budget

| Component | Tokens |
|-----------|--------|
| SKILL.md load | ~800-1000 |
| Validation execution | ~200-300 |
| AskUserQuestion (Gate A) | ~150-200 |
| **Total per invocation** | **~1250-1650** |

**ROI**: Prevents ~5000 tokens wasted per bad skill created.

---

## Related Commands

- `/create-skill` - Create new skill (triggers Gate A)
- `/approve-skill` - Approve pending skill candidate
- `./optim.sh learning-approve` - Approve learning candidate
- `Skill(claude-api, "prompt-audit")` - Audit an existing skill for dated-model patterns

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2025-12-29 | Initial creation from Anthropic best practices |

---


