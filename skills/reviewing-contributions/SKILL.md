---
name: reviewing-contributions
description: Automated contribution review system that evaluates PRs against GRIP design principles. Use when PR opened, /review-pr invoked, or contribution review needed.
status: Active
effort: medium
version: 2.0.0
created: 2026-01-02
triggers:
  - /review-pr
  - /review-contribution
  - PR opened (GitHub Action)
integrates:
  - skill-creation-best-practices
  - asking-users
  - creating-wizards
  - hitl
  - pr-automation
see_also:
  - sanitizing-public-releases
  - design-principles
  - review-mode
priority: PARAMOUNT
---

# Reviewing Contributions

Automated PR review against GRIP design principles.

## What This Does

When a contributor submits a PR:

1. **Analyze the changes** - Categorize by type
2. **Evaluate against principles** - SOLID, GRASP, DRY, KISS, YAGNI
3. **Check constraints** - Line limits, naming conventions, tests
4. **Generate feedback** - Constructive, actionable suggestions
5. **Queue for human approval** - Human makes final decision

---

## The 5-Phase Workflow

### Phase 1: Trigger Detection

| Trigger | Source | Action |
|---------|--------|--------|
| PR opened | GitHub Action | Auto-invoke review |
| `/review-pr <#>` | Manual | Review specific PR |
| Scheduled | Cron (daily) | Check open PRs |

### Phase 2: Diff Analysis

Parse the PR diff and categorize changes:

| Category | Files | Evaluation Focus |
|----------|-------|------------------|
| skill | `skills/*/SKILL.md` | Gerund naming, 500 lines, frontmatter |
| agent | `agents/*.md` | Budget, triggers, model selection |
| lib | `lib/*.py`, `lib/*.sh` | Code quality, error handling |
| doc | `docs/*.md`, `*.md` | Accuracy, markdown quality |
| config | `*.json`, `*.yaml` | Schema compliance |
| gates | `config/*gates*.yaml` | Test cases, must_block/must_allow |

**Evaluation Dimensions (ad-hoc skill-level review):**

| Dimension | Weight | Criteria |
|-----------|--------|----------|
| Quality | 30% | Correctness, no bugs, follows patterns |
| Alignment | 30% | SOLID, GRASP, DRY, KISS, YAGNI |
| Testing | 25% | Has tests, tests pass, falsifiable |
| Documentation | 15% | Documented, frontmatter valid |

**HAL Council CI review (canonical, 8-dimension — plan `fluffy-twirling-waffle.md` lines 171-182):**

| Dimension | Weight | Measured by | Layer |
|-----------|--------|-------------|-------|
| Correctness (tests, logic) | 0.20 | Opus + Sonnet agreement, family-deduped | LLM |
| Design principles (SOLID/GRASP/DRY/KISS/YAGNI) | 0.15 | Opus primary + Llama outsider sanity | LLM |
| Hypothesis quality (falsifiable) | 0.10 | Gemini judge direct | LLM |
| Documentation + commit hygiene | 0.05 | Haiku fact-check | LLM |
| Mermaid diagram clarity | 0.05 | Gemini judge direct | LLM |
| Complexity (BIG-O, CC) | 0.20 | `lib/complexity_profiler.py` | Mechanical |
| AST diff size vs claimed scope | 0.10 | `lib/pr_ast_differ.py` + PR body NLP | Mechanical |
| Hypothesis engine verification status | 0.10 | `lib/hypothesis_engine.py verify` | Mechanical |
| Test coverage delta | 0.05 | `git diff` on test files vs prod files | Mechanical |

**Totals:** LLM 0.55 + Mechanical 0.45 = 1.00.

The mechanical 0.45 floor cannot be inflated by LLM sycophancy — the mechanical dimensions are grounded in AST diff, CC score, and hypothesis_engine state. Family-deduplicated agreement (Opus/Sonnet/Haiku = 1 Anthropic vote, not 3) prevents artificial consensus.

The **HAL Council is the authoritative reviewer** for PRs in CI on all CodeTonight-SA live repos. The 4-dimension table above is for skill-level ad-hoc review (mode: review); CI reviews use the 8-dimension HAL Council weights. See `docs/SAFETY-GATES.md` CI-Layer Gate section for the full gate-stack position.

**Tier Anchors (use these to calibrate scores — 88 is not the default):**

| Score | Quality | Alignment | Testing | Documentation |
|-------|---------|-----------|---------|---------------|
| 90-100 | No bugs, all edge cases handled, actionable error messages | Single responsibility, no duplication, GRIP naming conventions | Verifies actual output values; covers happy path, error path, boundary | Frontmatter complete, docstrings present, usage example included |
| 70-89 | Correct on happy path, minor edge cases missed | One or two principle violations (e.g. function does two things) | Tests present but missing error path or boundary coverage | Frontmatter mostly complete, docstrings on most public functions |
| 50-69 | Breaks on bad input, silent failures, hardcoded paths | 200-line functions, duplicated logic, YAGNI violations | Tests exist but only verify calls were made, not return values | Frontmatter missing or malformed, no docstrings on new functions |
| 0-49 | Crashes on normal input or introduces security vulnerability | Circular imports, God objects, wrong architectural layer | No tests, or tests that cannot fail regardless of implementation | Actively misleading: docstring contradicts behavior |

### Phase 3: Opinion Formation

Generate structured assessment:

```json
{
  "recommendation": "approve|request_changes|discuss",
  "confidence": 0.87,
  "alignment_score": 85,
  "reasoning": [
    "Follows gerund naming convention",
    "Under 500 lines",
    "Includes test cases"
  ],
  "concerns": [],
  "suggestions": [
    "Consider adding SKILL.grip compact version"
  ]
}
```

### Phase 4: User Approval Queue

**Human always has final authority.**

```markdown
## PR Review Queue

### PR #123: Add skill for X
- **Recommendation**: Approve (confidence: 92%)
- **Alignment Score**: 87/100
- **Tests**: 5 must_block, 3 must_allow - all pass

**[Approve]** **[Request Changes]** **[Override]**
```

### Phase 5: Feedback Generation

Post constructive feedback to the PR:

```markdown
## GRIP Review

### Assessment: Recommend Approval

**Alignment Score**: 87/100

### What Works
- Clean structure following 500-line constraint
- Includes test cases
- Clear trigger phrases

### Suggestions
1. Consider adding SKILL.grip compact version
2. Description could include "Use when..." phrase

---
*Automated review. Maintainer makes final decision.*
```

---

## Commands

| Command | Purpose |
|---------|---------|
| `/review-pr <#>` | Review specific PR by number |
| `/review-contribution` | Review current context |
| `/contribution-queue` | View pending reviews |
| `/contribution-approve <#>` | Approve reviewed PR |

---

## Anti-Patterns

| Anti-Pattern | Why Bad | Correct Pattern |
|--------------|---------|-----------------|
| Auto-merge | Removes human authority | Queue for approval |
| Harsh feedback | Discourages contributors | Constructive, encouraging |
| No tests | Can't verify behavior | Require must_block/must_allow |
| Generic review | Misses GRIP specifics | Check design principles |

---

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~800 |
| Diff analysis | ~500-2000 |
| Assessment | ~500 |
| Feedback generation | ~400 |
| **Total per PR** | **~2200-3700** |

---

## Related Files

- [reference.md](./reference.md) - Implementation details
- [templates/pr-review.md](./templates/pr-review.md) - Review template
- [templates/contributor-feedback.md](./templates/contributor-feedback.md) - Feedback template
