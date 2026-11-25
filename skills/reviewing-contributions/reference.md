# Reviewing Contributions - Reference

Implementation details for the automated contribution review system.

## GitHub Action Workflow

```yaml
# .github/workflows/grip-review.yml
name: GRIP Contribution Review

on:
  pull_request:
    types: [opened, synchronize, reopened]
    branches: [main]

permissions:
  contents: read
  pull-requests: write

jobs:
  grip-review:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install anthropic pyyaml

      - name: Get PR diff
        id: diff
        run: |
          gh pr diff ${{ github.event.number }} > pr-diff.txt
          echo "diff_size=$(wc -l < pr-diff.txt)" >> $GITHUB_OUTPUT
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: GRIP Review
        if: steps.diff.outputs.diff_size > 0
        run: |
          python scripts/analyze-pr.py \
            --pr-number ${{ github.event.number }} \
            --diff-file pr-diff.txt \
            --output review-output.json
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

      - name: Post Review Comment
        if: steps.diff.outputs.diff_size > 0
        run: |
          python scripts/post-review.py \
            --pr-number ${{ github.event.number }} \
            --review-file review-output.json
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

## Review Categories

| Pattern | Category | Check |
|---------|----------|-------|
| `skills/*/SKILL.md` | skill | Gerund naming, <500 lines, frontmatter |
| `agents/*.md` | agent | Budget, triggers |
| `lib/*.py` | lib | Code quality |
| `config/*gates*.yaml` | gates | Test cases present |
| `docs/*.md` | doc | Accuracy |

---

## Evaluation Criteria with Tier Anchors

### Quality (30%)

| Score | What it looks like |
|-------|--------------------|
| 90-100 | No bugs. Idiomatic GRIP patterns throughout. Handles all edge cases: empty input, missing files, concurrent access. Error messages are actionable. |
| 70-89 | Correct for the happy path. Minor edge cases missed (e.g. no handling for empty diff, no timeout on subprocess). Follows patterns but with small deviations. |
| 50-69 | Works in the demo case but breaks predictably: uncaught exceptions on bad input, hardcoded paths, silent failures that return wrong data instead of raising. |
| 0-49 | Crashes on normal input, introduces a security vulnerability, or corrupts existing state. |

### Alignment — Design Principles (30%)

| Score | What it looks like |
|-------|--------------------|
| 90-100 | Single responsibility per function. No duplication with existing lib/. YAGNI: no speculative abstractions. Naming matches GRIP conventions (gerund skills, snake_case lib). |
| 70-89 | Generally follows principles. One or two violations: a function does two things, a constant is duplicated rather than imported, a variable name deviates from convention. |
| 50-69 | Noticeable structural issues: a 200-line function that could be split, copy-pasted logic from an existing utility, or a new class where a function would suffice (YAGNI violation). |
| 0-49 | Fundamentally misaligned: circular imports, God objects, feature added to wrong layer, or the entire approach contradicts how GRIP works. |

### Testing (25%)

| Score | What it looks like |
|-------|--------------------|
| 90-100 | Tests verify actual output values, not just that calls were made. Covers happy path, error path, and boundary case. Gates include `must_block` and `must_allow`. Tests would catch a regression if the implementation changed. |
| 70-89 | Tests present and mostly meaningful. Missing one of: error path, boundary case, or real integration (relies entirely on mocks). Gates present but missing `must_allow` cases. |
| 50-69 | Tests exist but weak: `mock.assert_called_once()` without checking arguments or return value. Gates have `must_block` but no `must_allow`. |
| 0-49 | No tests, or tests that cannot fail regardless of implementation (empty test file, `assert True`, or patches the function under test itself). |

### Documentation (15%)

| Score | What it looks like |
|-------|--------------------|
| 90-100 | Skill/agent frontmatter complete (name, description, status, triggers). Docstrings on public functions. Usage example present. Existing docs that reference modified code are updated. |
| 70-89 | Frontmatter present but incomplete (missing `triggers` or `status`). Docstrings on most but not all public functions. No usage example for a new command. |
| 50-69 | Frontmatter missing or malformed. No docstrings. New skill with no description of when to invoke it. |
| 0-49 | Actively misleading: docstring describes wrong behavior, README contradicts implementation, or frontmatter with wrong field types. |

---

## Output Format

```json
{
  "recommendation": "approve|request_changes|discuss",
  "confidence": 0.87,
  "alignment_score": 85,
  "reasoning": ["..."],
  "concerns": ["..."],
  "suggestions": ["..."]
}
```

---

## Integration with Safety Gates

For PRs that modify `config/default-gates.yaml` or add new gates:

1. **Require test cases** - Every gate must have `must_block` and `must_allow`
2. **Run gate tests** - `python lib/gates/test_harness.py`
3. **Check falsifiability** - Can we prove the gate fails?
4. **Verify determinism** - Same input = same output

---

## Human Override

The automated review is advisory. Human maintainer always has final authority.

```
Recommendation: request_changes
Human: V>>! merge anyway
Result: Merged (human override logged)
```
