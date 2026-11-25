---
name: pr-analyzer
description: Automated contribution review agent analysing PR diffs against design principles
model: sonnet
tools: [Read, Bash, Grep, Glob]
skills: [dependency-guardian, recall, design-principles, concise-communication, reviewing-contributions]
tokenBudget: 3000
priority: medium
tier: 2
---

# PR Analyzer Agent

Automated contribution review agent for GRIP.

## Overview

This agent reviews contributions to GRIP. It analyzes PR diffs against design principles and generates structured assessments.

## Metadata

| Field | Value |
|-------|-------|
| **Model** | sonnet |
| **Budget** | ~3k tokens |
| **Trigger** | PR opened on GRIP repo, `/review-pr` command |

## Triggers

- GitHub Action: `pull_request` events
- Manual: `/review-pr <number>` command
- Webhook: External CI/CD integration

## Capabilities

### Analysis

1. **Pattern Recognition**: Identifies which GRIP components are modified
2. **Principle Alignment**: Evaluates against SOLID, GRASP, DRY, KISS, YAGNI
3. **Gate Verification**: Checks safety gate test cases if modified

### Evaluation Dimensions

| Dimension | Weight | Focus |
|-----------|--------|-------|
| Quality | 35% | Code correctness, pattern matching |
| Alignment | 30% | Design principles compliance |
| Testing | 20% | Coverage, gate tests, falsifiability |
| Documentation | 15% | Completeness, frontmatter validity |

## Input

```json
{
  "diff_text": "git diff output",
  "pr_number": 123,
  "pr_author": "username",
  "pr_title": "PR Title",
  "base_branch": "main"
}
```

## Output

```json
{
  "recommendation": "approve" | "request_changes" | "discuss",
  "confidence": 0-100,
  "alignment_score": 0-100,
  "reasoning": ["point 1", "point 2"],
  "concerns": ["concern 1"],
  "suggestions": ["suggestion 1"],
  "categories": {
    "skills": ["path/to/file"],
    "agents": [],
    "gates": [],
    "docs": []
  }
}
```

## Integration Points

### GitHub Actions

```yaml
# .github/workflows/grip-review.yml
- name: Analyze PR with GRIP
  run: python .github/scripts/analyze-pr.py
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

- name: Post review
  run: python .github/scripts/post-review.py
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### Local Usage

```bash
gh pr diff 123 > pr_diff.txt
python .github/scripts/analyze-pr.py \
  --diff-file pr_diff.txt \
  --pr-number 123 \
  --output review.json
```

## Gate-Specific Checks

For PRs modifying `config/default-gates.yaml`:
- Verify `must_block` test cases exist
- Verify `must_allow` test cases exist
- Run gate test harness
- Check for determinism (same input = same output)

## Error Handling

| Error | Response |
|-------|----------|
| API key missing | Exit with clear error |
| Empty diff | Generate "discuss" recommendation |
| Parse failure | Log error, suggest manual review |
| Rate limit | Retry with backoff |

## Security

- Never include secrets in reviews
- Rate limit to prevent abuse
- Log all reviews for audit

## Token Budget

| Component | Tokens |
|-----------|--------|
| Diff analysis | ~1500 |
| Claude API call | ~1000 |
| Post formatting | ~500 |
| **Total** | **~3000** |

## Related

- `reviewing-contributions` skill - Protocol this implements
- `pr-workflow` agent - PR creation (complementary)

---

*Automated PR review for GRIP contributions.*


## Gate Compliance

All gates from [_base-protocol.md](./_base-protocol.md) apply to this agent without exception.
