# Learning Pipeline Reference

Detailed reference for the learning-new-skills pipeline.

## Full Pipeline Flowchart

```
                          +-----------------+
                          |  User Message   |
                          |  (via hook)     |
                          +--------+--------+
                                   |
                                   v
                    +-----------------------------+
                    |  learning-hook.py           |
                    |  (UserPromptSubmit)         |
                    |  - Check message length     |
                    |  - Skip if < 50 chars       |
                    +-------------+---------------+
                                  |
                                  v
                    +-----------------------------+
                    |  learning-detector.py       |
                    |  detect_learning_event()    |
                    +-------------+---------------+
                                  |
              +-------------------+-------------------+
              |                   |                   |
              v                   v                   v
        +-----------+      +-----------+      +-----------+
        |  Teaching |      |  New Term |      |  General  |
        |  Moment   |      |  Coined   |      |  Pattern  |
        +-----------+      +-----------+      +-----------+
              |                   |                   |
              +-------------------+-------------------+
                                  |
                                  v
                    +-----------------------------+
                    |  Calculate Learning Score   |
                    |  Count Triggers (need 2+)   |
                    +-------------+---------------+
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
            (< 2 triggers)              (>= 2 triggers)
                    |                           |
                    v                           v
               +--------+              +----------------+
               |  Exit  |              |  Evaluate      |
               | (noop) |              | Generalisability|
               +--------+              +-------+--------+
                                               |
                         +---------------------+---------------------+
                         |                     |                     |
                         v                     v                     v
                  +-----------+         +-----------+         +-----------+
                  |  Project  |         |  Infra    |         |  General  |
                  |  Specific |         |  Update   |         |  Skill    |
                  +-----------+         +-----------+         +-----------+
                         |                     |                     |
                         v                     v                     v
                  +-----------+         +-----------+         +-----------+
                  |  Document |         |  Flag for |         |  Create   |
                  |  Locally  |         | CLAUDE.md |         |  Candidate|
                  +-----------+         +-----------+         +-----------+
                                                                     |
                                                                     v
                                              +-----------------------------+
                                              |  save_skill_candidate()     |
                                              |  -> ~/.claude/learning/     |
                                              |       pending/{id}.json     |
                                              +-------------+---------------+
                                                            |
                                                            v
                                              +-----------------------------+
                                              |  Output [GRIP LEARNING]     |
                                              |  signal to hook output      |
                                              +-------------+---------------+
                                                            |
                                                            v
                                              +-----------------------------+
                                              |  Claude sees signal         |
                                              |  -> AskUserQuestion         |
                                              |     (MANDATORY)             |
                                              +-------------+---------------+
                                                            |
                                    +-----------------------+---+
                                    |                           |
                                    v                           v
                            +--------------+            +--------------+
                            |   Approve    |            |   Reject     |
                            +--------------+            +--------------+
                                    |                           |
                                    v                           v
                            +--------------+            +--------------+
                            | generate_    |            | Move to      |
                            | skill()      |            | rejected/    |
                            +--------------+            +--------------+
                                    |
                                    v
                            +--------------+
                            | Load template|
                            | Fill values  |
                            | Validate 70% |
                            +--------------+
                                    |
                                    v
                            +--------------+
                            | Save skill   |
                            | Register     |
                            +--------------+
```

## Detection Patterns

### Teaching Moment Patterns

```python
TEACHING_PATTERNS = [
    r"you should have",
    r"obvious enhancement",
    r"you missed",
    r"should've",
    r"better approach would be",
    r"correct way is",
    r"anti-pattern",
    r"don't do this",
    r"always use",
    r"never use",
    r"remember to",
    r"lesson learned",
]
```

### New Term Patterns

```python
NAMING_PATTERNS = [
    r"let's call this",
    r"I'll name this",
    r"this is (?:called|known as)",
    r"the \w+ principle",
    r"the \w+ pattern",
    r"(?:YSH|YAGNI|DRY|KISS|SOLID|GRASP)",
    r"dialectical",
]
```

### Generalisation Patterns

```python
GENERALISATION_PATTERNS = [
    r"in general",
    r"as a rule",
    r"this applies to",
    r"universally",
    r"always works",
    r"pattern that",
    r"principle:",
    r"lesson:",
    r"takeaway:",
    r"generalis[ea]",
    r"paramount",
    r"critical learning",
    r"key insight",
    r"important pattern",
    r"cross-project",
    r"any (?:project|repo|codebase)",
]
```

## Candidate JSON Format

```json
{
  "id": "skill-20260123120000",
  "skill_name": "pre-commit-verification",
  "description": "Always verify git status before commit operations",
  "full_content": "You should always check git status before committing...",
  "learning_score": 0.75,
  "triggers": {
    "high_novelty": false,
    "teaching_moment": true,
    "new_term": false,
    "generalisation": true
  },
  "evaluation": {
    "is_generalisable": true,
    "action": "generate_skill",
    "reason": "Learning appears generalisable across projects",
    "is_project_specific": false,
    "is_infra_improvement": false,
    "suggested_skill_name": null
  },
  "status": "pending",
  "created_at": "2026-01-23T12:00:00+00:00",
  "source": {
    "type": "dialectical_learning",
    "project": "~/project"
  }
}
```

## Quality Score Breakdown

```
+-----------------------------+--------+
| Criterion                   | Points |
+-----------------------------+--------+
| name: valid                 |     5  |
| name: gerund convention     |     5  |
| description: non-empty      |     5  |
| description: has trigger    |    10  |
| description: third person   |     5  |
| status: Active              |     5  |
| body: 3+ sections           |    15  |
| body: < 500 lines           |     5  |
| body: no placeholders       |    10  |
| body: has examples          |    10  |
| triggers: defined           |    10  |
| protocol: defined           |    15  |
+-----------------------------+--------+
| TOTAL                       |   100  |
| PASS THRESHOLD              |    70  |
+-----------------------------+--------+
```

## Signal Parsing

When Claude sees hook output, parse signals:

```python
# Signal format
[GRIP LEARNING] id={id} skill={skill_name}
Score: {score} | Triggers: {triggers}
{description}...

# Regex to parse
r'\[GRIP LEARNING\] id=(\S+) skill=(\S+)'
r'Score: ([\d.]+) \| Triggers: (.+)'
```

## CLI Reference

```bash
# Initialise directories
python3 ~/.claude/lib/learning_detector.py init

# Process message for learning
python3 ~/.claude/lib/learning_detector.py process "message" \
    --novelty 0.5 \
    --project /path/to/project

# Quick detection (no candidate creation)
python3 ~/.claude/lib/learning_detector.py detect "message" --novelty 0.5

# List pending candidates
python3 ~/.claude/lib/learning_detector.py list

# Approve candidate
python3 ~/.claude/lib/learning_detector.py approve skill-20260123120000

# Reject with reason
python3 ~/.claude/lib/learning_detector.py reject skill-20260123120000 \
    --reason "Too project-specific"
```

## Bash Wrapper Functions

```bash
# Source the library
source ~/.claude/lib/learning.sh

# Quick detection
detect_learning "message" 0.5

# Full processing
process_learning "message" 0.5 "/path/to/project"

# List pending
list_pending_skills

# Approve/Reject
approve_skill "skill-20260123120000"
reject_skill "skill-20260123120000" "reason"
```

## Metrics Tracking

Learning events are logged to `~/.claude/metrics.jsonl`:

```json
{
  "event": "learning_detected",
  "timestamp": "2026-01-23T12:00:00+00:00",
  "learning_score": 0.75,
  "trigger_count": 2,
  "triggers": {
    "high_novelty": false,
    "teaching_moment": true,
    "new_term": false,
    "generalisation": true
  },
  "is_learning_event": true
}
```

Query recent learning events:
```bash
cat ~/.claude/metrics.jsonl | jq -s '[.[] | select(.event == "learning_detected")]'
```

---

