---
name: learning-new-skills
description: >
  PARAMOUNT meta-skill for automatic skill creation from detected patterns.
  Orchestrates autonomous-learning, creating-skills, and self-improvement.
  Use when /learn invoked, teaching moments detected, or [GRIP LEARNING] in hook output.
status: Active
effort: high
version: 1.0.0
priority: PARAMOUNT
triggers:
  - /learn
  - session.start (auto-load check)
  - '"[GRIP LEARNING]" in hook output'
  - teaching moment detected
  - high novelty score
  - new term coined
integrates:
  - autonomous-learning
  - creating-skills
  - skill-creation-best-practices
  - self-improvement-engine
---

# Learning New Skills (Meta-Skill)

**PARAMOUNT**: Orchestrates automatic skill creation from detected patterns.

## Architecture

```
                     learning-new-skills
                    (PARAMOUNT Meta-Skill)
                            |
     +----------------------+----------------------+
     |                      |                      |
     v                      v                      v
 autonomous            creating             self-improvement
  learning              skills                  engine
     |                      |                      |
     +----------------------+----------------------+
                            |
                            v
            Unified Learning Pipeline
     Detection -> Evaluation -> Approval -> Generation
```

## The Three Gates (PARAMOUNT)

All skill creation passes through quality gates from `skill-creation-best-practices`:

| Gate | Trigger | Validation |
|------|---------|------------|
| **A: Manual** | `/learn "insight"` or user proposal | AskUserQuestion MANDATORY |
| **B: Auto-Gen** | Pattern detection (2+ triggers) | Quality score >= 70% |
| **C: Autonomous** | Proven twice (2x+, lowered from three on 2026-10-03) | Quality >= 70% + user review; first build with `/hitl`, then tested, monitored, ultralearned |

## Commands

| Command | Function |
|---------|----------|
| `/learn` | **Run full RSI cycle** (detect → generate → optimize) |
| `/learn detect` | Pattern detection only |
| `/learn generate` | Auto-generate skills for 3x patterns |
| `/learn list` | List pending skill candidates |
| `/learn approve <id>` | Approve specific candidate |
| `/learn reject <id>` | Reject specific candidate |
| `/learn "insight"` | Process specific learning input |

## RSI Cycle (Default) - Full 8-Step Loop

When `/learn` is invoked with no arguments, execute the **COMPLETE** RSI loop:

### Execution Protocol

```bash
# Step 1: Pattern Detection
cd ~/.claude && ./optim.sh detect

# Step 2: Autonomous Learning Scan
python3 ~/.claude/lib/learning_detector.py scan 2>/dev/null || echo "No learning detector"

# Step 3: Learning Candidates
python3 ~/.claude/lib/learning_detector.py list 2>/dev/null
# If candidates exist → AskUserQuestion MANDATORY

# Step 4: Skill Generation
./optim.sh auto-generate

# Step 5: Improvement Tracking (auto-logged by step 4)

# Step 6: Heatmap Check
python3 ~/.claude/lib/improvement_analyzer.py check
# If [IMPROVEMENT HEATMAP] → AskUserQuestion for SRP review

# Step 7: Meta-Learning
python3 ~/.claude/lib/improvement_analyzer.py summary

# Step 8: Meta-Optimization
./optim.sh optimize
```

### PARAMOUNT Gates

| Step | Gate | Action |
|------|------|--------|
| 3 | AskUserQuestion | If pending candidates, ask for approval |
| 6 | AskUserQuestion | If heatmap signal, ask for SRP review |

### Output Format

```
═══════════════════════════════════════════════════════════════
                     /learn - RSI LOOP
═══════════════════════════════════════════════════════════════

[1/8] PATTERN DETECTION
[2/8] AUTONOMOUS LEARNING
[3/8] LEARNING CANDIDATES
[4/8] SKILL GENERATION
[5/8] IMPROVEMENT TRACKING
[6/8] HEATMAP CHECK
[7/8] META-LEARNING
[8/8] META-OPTIMIZATION

═══════════════════════════════════════════════════════════════
RSI LOOP COMPLETE
═══════════════════════════════════════════════════════════════
```

Timeout: 600000ms (10 minutes) for full cycle.

## Learning Detection

### Triggers (Require 2+ to fire)

| Trigger | Detection | Weight |
|---------|-----------|--------|
| **High novelty** | Embedding score > 0.4 | 0.4 |
| **Teaching moment** | "you should have", corrections | 0.3 |
| **New term** | "let's call this", "the X principle" | 0.2 |
| **Generalisation** | "in general", "as a rule" | 0.1 |

### Learning Score Calculation

```python
score = min(novelty, 0.4)         # Up to 0.4
score += 0.3 if teaching          # Teaching moment
score += 0.2 if new_term          # New term coined
score += 0.1 if generalisation    # Generalisation signal
# Threshold: score >= 0.5 AND triggers >= 2
```

## Pipeline

```
User message (via hook)
        |
        v
[learning-detector.py detect]
        |
        +---> No learning (< 2 triggers) -> Exit
        |
        v
[evaluate_generalisability]
        |
        +---> Project-specific -> Document in project
        |
        +---> Infrastructure -> Flag for CLAUDE.md update
        |
        v
[create_skill_candidate]
        |
        v
[save to ~/.claude/learning/pending/]
        |
        v
[Hook outputs: [GRIP LEARNING] signal]
        |
        v
Claude sees signal -> AskUserQuestion (MANDATORY)
        |
        +---> "Generate skill" -> approve_candidate -> generate_skill
        |
        +---> "Reject" -> reject_candidate with reason
```

## AskUserQuestion Protocol (MANDATORY)

When Claude sees `[GRIP LEARNING]` in hook output:

```
Question: "Learning detected: '{skill_name}'. Generate skill?"
Header: "Learning"
Options:
  - "Yes, generate skill" -> Approve and generate
  - "No, reject" -> Reject with option to provide reason
  - "View details" -> Show full candidate before deciding
```

**NEVER** silently process learning - always ask user.

## Skill Generation

When candidate approved:

1. Load template from `~/.claude/templates/skills/SKILL.template.md`
2. Fill placeholders from candidate data:
   - `{{SKILL_NAME}}` from candidate.skill_name
   - `{{DESCRIPTION}}` from candidate.description
   - `{{DATE}}` current ISO date
   - `{{GEN}}` from GRIP generation
   - `{{PATTERN_NAME}}` from source pattern
   - `{{TRIGGER_1}}`, `{{TRIGGER_2}}` from detected triggers
3. Validate quality score >= 70%
4. Save to `~/.claude/skills/{skill_name}/SKILL.md`
5. Register in `~/.claude/docs/SKILLS.md`

### Quality Validation

| Criterion | Points |
|-----------|--------|
| Frontmatter complete | 30 |
| Name follows gerund convention | 10 |
| Description with trigger phrase | 20 |
| 3+ sections with ## headers | 20 |
| No unfilled placeholders | 10 |
| Concrete examples | 10 |
| **Pass threshold** | **70/100** |

## Directory Structure

```
~/.claude/
  learning/
    pending/    # Candidates awaiting approval
    approved/   # Approved (triggers skill generation)
    rejected/   # Archived with reason
  templates/
    skills/
      SKILL.template.md  # Template for generation
  skills/
    learning-new-skills/
      SKILL.md           # This file
      pipeline.md        # Detailed pipeline reference
```

## Hook Integration

### UserPromptSubmit Hook

Processes every user message through learning detection:

```python
# hooks/learning-hook.py
# Lightweight check (~50ms, ~100 tokens)
# Only fires for messages > 50 chars
# Outputs [GRIP LEARNING] signal when pattern detected
```

### Signal Format

```text
[GRIP LEARNING] id=skill-20260123120000 skill=pre-commit-checking
Score: 0.75 | Triggers: teaching_moment, generalisation
Always verify git status before commit operations...
```

## CLI Commands

```bash
# List pending candidates
python3 ~/.claude/lib/learning_detector.py list

# Approve candidate
python3 ~/.claude/lib/learning_detector.py approve skill-20260123120000

# Reject with reason
python3 ~/.claude/lib/learning_detector.py reject skill-20260123120000 --reason "Too specific"

# Process message directly
python3 ~/.claude/lib/learning_detector.py process "Always use rg instead of grep"
```

## Integration Points

| Component | Purpose |
|-----------|---------|
| `lib/learning_detector.py` | Core Python detection |
| `lib/learning.sh` | Bash wrapper for hooks |
| `hooks/learning-hook.py` | UserPromptSubmit trigger |
| `hooks/session-start.sh` | Check pending on start |
| `templates/skills/SKILL.template.md` | Skill generation template |
| `autonomous-learning/SKILL.md` | Dialectical framework |
| `creating-skills/SKILL.md` | Manual wizard reference |
| `self-improvement-engine/SKILL.md` | Full improvement cycle |
| `skill-creation-best-practices/SKILL.md` | Quality gates |

## Token Budget

| Component | Tokens |
|-----------|--------|
| Meta-skill load (on demand) | ~800 |
| Learning hook (per message) | ~100 |
| Pattern detection | ~200 |
| Skill generation | ~500 |
| Quality validation | ~200 |
| **Total per learning event** | **~1,800** |

**ROI**: Each auto-generated skill saves 5,000-50,000 tokens per session.

## Safety Gates

| Gate | Purpose |
|------|---------|
| **2+ triggers required** | Prevents false positives |
| **Quality >= 70%** | Ensures skill usability |
| **User approval** | Human-in-the-loop for all creation |
| **Similarity check** | Prevents duplicates |

## Examples

### Teaching Moment Detection

```text
User: "You should always check git status before committing"

[Learning Detection]
- Teaching moment: "you should always" (matched)
- Generalisation: "always" (matched)
- Score: 0.5 (teaching 0.3 + generalisation 0.1 + novelty 0.1)
- Triggers: 2+

[GRIP LEARNING] id=skill-20260123120000 skill=pre-commit-verification
Score: 0.50 | Triggers: teaching_moment, generalisation
You should always check git status before committing...

Claude: [Uses AskUserQuestion]
"Learning detected: 'pre-commit-verification'. Generate skill?"
```

### New Term Detection

```text
User: "Let's call this the 'batch-first principle' - always batch reads"

[Learning Detection]
- New term: "let's call this" (matched "batch-first principle")
- Generalisation: "always" (matched)
- Score: 0.4 (new_term 0.2 + generalisation 0.1 + novelty 0.1)
- Triggers: 2+

[GRIP LEARNING] id=skill-20260123121500 skill=batch-first-principle
```

## Improvement Tracking (Gen 248)

The recursive learning loop includes improvement tracking via `lib/improvement_analyzer.py`.

### Commands

| Command | Purpose |
|---------|---------|
| `python3 lib/improvement_analyzer.py summary` | Overall improvement stats |
| `python3 lib/improvement_analyzer.py heatmap` | Component heat map (design smells) |
| `python3 lib/improvement_analyzer.py triggers` | What causes improvements |
| `python3 lib/improvement_analyzer.py recent` | Last 5 improvements |
| `python3 lib/improvement_analyzer.py check` | Check for refactor candidates |
| `python3 lib/improvement_analyzer.py log <type> <comp> <desc>` | Log improvement |

### Refactor Detection

Components improved 3+ times trigger `[IMPROVEMENT HEATMAP]` signal. This indicates potential Single Responsibility Principle violation.

```
[IMPROVEMENT HEATMAP] Components need refactor: lib/learning.sh, hooks/context-gate-hook.py
```

When you see this signal:
1. Review the component's responsibilities
2. Consider splitting into focused modules
3. Document refactor decision in improvement log

### Logging Improvements

Log improvements when:
- Skill created (auto or manual)
- Rule added to `rules/*.md`
- Bug fixed in infrastructure
- Refactor completed

```bash
python3 ~/.claude/lib/improvement_analyzer.py log \
  skill_created \
  skills/new-skill \
  "Created from pattern detection" \
  auto
```

### Integration with Learning Loop

```
Pattern detected (2+ triggers)
        |
        v
Skill candidate created
        |
        v
User approves → Skill generated
        |
        v
Log improvement (auto)
        |
        v
Check heatmap (auto)
        |
        +---> 3+ improvements? → Emit [IMPROVEMENT HEATMAP]
```

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.1.0 | 2026-01-27 | Add improvement tracking section (Gen 248) |
| 1.0.0 | 2026-01-23 | Initial creation - unified meta-skill |

---

