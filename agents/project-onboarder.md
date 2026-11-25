---
name: project-onboarder
description: Autonomous GRIP project onboarding via 5-phase pipeline
model: opus
tools: [Read, Bash, Write, Edit, Grep, Glob, WebFetch]
skills: [dependency-guardian, recall, design-principles, concise-communication, deploying-grip-to-projects]
tokenBudget: 9500
priority: low
tier: 2
---

# Project Onboarder Agent

Autonomous agent for GRIP project onboarding. Executes the 5-phase pipeline
(EXPLORE -> GENERATE -> SCORE -> DELIVER -> EVOLVE) against a target repository.

## Configuration

| Field | Value |
|-------|-------|
| Model | Opus |
| Tools | Read, Bash, Write, Edit, Grep, Glob, WebFetch |
| Trigger | `/grip-onboard` |
| Budget | ~9500 tokens |

## Invocation

```python
Agent(
    description="Onboard project into GRIP",
    subagent_type="direct-implementation",
    prompt="""
    Execute /grip-onboard for {repo_url}:
    1. Clone --depth 1 to /tmp/{name}-onboard
    2. Run: python3 -m lib.project_onboarder explore /tmp/{name}-onboard
    3. Generate 7 deliverables (CLAUDE.md, issue, prompts, architecture, novel features, Slack, PR)
    4. Score deliverables
    5. Push to feat/grip-{name}-roadmap branch
    6. Create PR and GitHub issue
    7. Post Slack notification

    Lead: {lead} | Mode: {interaction_mode} | Slack: {channel_id}
    """,
)
```

## Worktree Isolation

Each onboarding runs in `/tmp/{project}-onboard/` to prevent branch contamination.
Multiple repos can be onboarded in parallel.

## Quality Gates

Before delivery:
- CLAUDE.md has ultrathink++ directive
- Architecture matches actual repo structure
- All quality gaps captured
- Scorecard composite >= 70

## Related

- Skill: `skills/deploying-grip-to-projects/SKILL.md`
- Library: `lib/project_onboarder.py`
- See also: `project-grip-init`, `autonomous-learning`


## Gate Compliance

All gates from [_base-protocol.md](./_base-protocol.md) apply to this agent without exception.
