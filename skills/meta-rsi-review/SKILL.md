---
name: meta-rsi-review
description: Self-review of GRIP PR reviews and suggestions. Enables recursive reasoning about own suggestions to close the RSI loop. Use when /review-my-review invoked or after PR review.
status: Active
effort: high
version: 1.1.0
triggers:
  - /review-my-review
  - meta-review
  - review my review
  - review your suggestions
---

# Meta-RSI Review

GRIP reviews its own reviews to close the recursive self-improvement loop.

## Purpose

Enable GRIP to critically analyse its own PR review suggestions before implementation, using AskUserQuestion to guide users through the meta-analysis.

---

## Protocol

### Step 1: Retrieve Review

Fetch the PR review using one of:

```bash
# From PR number
gh pr view <PR_NUMBER> --comments

# From most recent PR
gh pr list --state all --limit 1 --json number | jq -r '.[0].number' | xargs gh pr view --comments
```

Or accept review content directly from user.

---

### Step 2: Parse Suggestions

Extract each distinct suggestion/concern from the review. For each:

```json
{
  "suggestion_id": 1,
  "text": "Add validation step to auto-generation",
  "category": "quality|performance|security|architecture|style",
  "code_location": "optim.sh:cmd_auto_generate()"
}
```

---

### Step 3: Critical Reasoning (Per Suggestion)

For EACH suggestion, apply the 5-Question Framework:

| Question | Purpose |
|----------|---------|
| 1. "What assumption am I making?" | Surface implicit beliefs |
| 2. "What could go wrong if implemented?" | Pre-mortem thinking |
| 3. "Is this actually necessary (YAGNI check)?" | Prevent over-engineering |
| 4. "What evidence supports this?" | Ground in facts |
| 5. "Confidence level: high/medium/low?" | Calibrate certainty |

Document analysis in structured format:

```markdown
### Suggestion: {text}

**Assumptions:**
- {assumption_1}
- {assumption_2}

**Risks if implemented:**
- {risk_1}

**YAGNI Assessment:**
- Needed now: {yes/no/maybe}
- Reason: {explanation}

**Evidence:**
- {evidence_1}

**Confidence:** {high|medium|low} ({percentage}%)
```

---

### Step 4: AskUserQuestion Gate (MANDATORY)

Present analysis to user via AskUserQuestion:

```yaml
Header: "Meta-Review"
Question: "Suggestion: '{suggestion}'. Analysis: {summary}. Confidence: {level}%. Accept this assessment?"
Options:
  - "Agree - implement as suggested"
  - "Disagree - explain why"
  - "Modify - refine together"
  - "Skip - next suggestion"
```

**Teaching**: User learns the reasoning behind each suggestion.
**Collecting**: User's decision on each suggestion.

---

### Step 5: Document Decision

Record each decision for audit trail:

```json
{
  "timestamp": "2026-01-26T12:00:00Z",
  "suggestion_id": 1,
  "suggestion_text": "Add validation step",
  "analysis_confidence": 85,
  "user_decision": "agree|disagree|modify|skip",
  "user_reasoning": "optional user explanation",
  "outcome": "implemented|rejected|modified|deferred"
}
```

Store in session state or append to `.claude/meta-reviews.jsonl`.

---

### Step 6: Execute (If Approved)

Only implement changes user explicitly approved.

For "modify" decisions:
1. Discuss refinements with user
2. Re-present modified suggestion
3. Get explicit approval before implementing

---

### Step 7: Create Implementation Plan (Default)

After user decisions are collected, the **default action** is to create an implementation plan.

**Do NOT ask "should I implement now?"** - instead, create a plan file:

1. Create `~/.claude/plans/{context}-implementation.md`
2. For each approved suggestion, document:
   - Location (file, function, line)
   - Current vs proposed behaviour
   - Implementation steps
   - Token budget estimate
3. Order by complexity (simple items first)
4. Add verification steps

**Only skip plan creation if user explicitly says:**
- "implement now" → Execute immediately
- "defer" → Document decisions only, no plan

This ensures meta-review decisions are actionable, not just documented.

---

## Example Interaction

```
User: /review-my-review PR #7

GRIP: [Fetches PR #7 review comments]

GRIP: Found 3 suggestions to analyse:
1. "Add validation step to auto-generation"
2. "Track skill_version for drift detection"
3. "Cleanup RSI_PATTERNS_READY env variable"

[AskUserQuestion]
Header: "Meta-Review"
Question: "Suggestion 1: 'Add validation step'. My analysis:
- Assumption: Generated skills may contain placeholders
- Risk: Overly strict validation could reject valid skills
- YAGNI: Needed NOW (prevents bad skills entering system)
- Evidence: Previous stubs had [Pattern-specific] placeholders
- Confidence: 85%

Accept this assessment?"
Options:
- "Agree - implement validation"
- "Disagree - explain issue"
- "Modify - adjust criteria"
- "Skip - next suggestion"
```

---

## Integration Points

| Component | Integration |
|-----------|-------------|
| `reviewing-contributions` | Chain: contribution review -> meta-review |
| `asking-users` | Uses PARAMOUNT AskUserQuestion protocol |
| `creating-wizards`, `hitl` | Siblings of `asking-users`: multi-step wizards, and the dispatch contract that chooses between them |
| `design-principles` | Applies YAGNI/KISS checks to suggestions |
| `skill-creation-best-practices` | Validates any skill-creation suggestions |

---

## Workflow Diagram

```
/review-my-review
        |
        v
+-------------------+
| Fetch PR Review   |
| (gh pr view)      |
+--------+----------+
         |
         v
+-------------------+
| Parse Suggestions |
| Extract concerns  |
+--------+----------+
         |
         v
+-----------------------------+
| For Each Suggestion:        |
|                             |
|  1. State the suggestion    |
|  2. Identify assumptions    |
|  3. Counter-arguments       |
|  4. Evidence assessment     |
|  5. Confidence score        |
|                             |
|  +---------------------+    |
|  | AskUserQuestion     |<---+ MANDATORY
|  | (present analysis)  |    |
|  +----------+----------+    |
|             |               |
|     User Decision           |
|             |               |
|  +----------v----------+    |
|  | Document + Execute  |    |
|  | (if approved)       |    |
|  +---------------------+    |
+-----------------------------+
         |
         v
+-------------------+
| Summary Report    |
| Decisions made    |
+-------------------+
```

---

## Anti-Patterns

| Anti-Pattern | Why Bad | Solution |
|--------------|---------|----------|
| Auto-implementing without asking | Bypasses human judgment | ALWAYS use AskUserQuestion |
| Generic "this looks good" analysis | No value added | Use 5-Question Framework |
| Skipping YAGNI check | Over-engineering risk | Always ask "needed NOW?" |
| Not documenting decisions | No audit trail | Log to meta-reviews.jsonl |

---

## Token Budget

| Component | Tokens |
|-----------|--------|
| Skill load | ~1200 |
| Per suggestion analysis | ~200-400 |
| Per AskUserQuestion | ~150 |
| **Typical session (5 suggestions)** | **~2500** |

---

## Related Commands

- `/review-pr` - Review incoming PR (input to meta-review)
- `/create-pr` - Create PR (may receive reviews for meta-review)
- `/audit-efficiency` - Run efficiency audit (complementary analysis)

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.1.0 | 2026-01-26 | Added Step 7: Create Implementation Plan as default action |
| 1.0.1 | 2026-01-26 | Added loop closure verification, deduplication in post-review.py |
| 1.0.0 | 2026-01-26 | Initial creation from RSI loop closure plan |

---

## Loop Closure Verification

**Status**: Verified

**Evidence**:
- GitHub Action triggers on `synchronize` (each commit to PR)
- `post-review.py` now deduplicates comments (updates instead of duplicates)
- Meta-review skill enables recursive analysis of own suggestions

**Chain**:
```
Commit -> GitHub Action -> GRIP Review -> PR Comment
                                              |
                                              v
                           /review-my-review -> Meta-Analysis
                                              |
                                              v
                                    AskUserQuestion -> Human Decision
                                              |
                                              v
                                    Loop Closed (or iterate)
```

---

*Closes the recursive self-improvement loop: GRIP reviews its reviews.*

---

