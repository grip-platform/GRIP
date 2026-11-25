# CIR-001: Agent Skills and Effort Frontmatter Strategy

**Date:** 2026-03-23

## Intent

Enable all 39 GRIP workers to access relevant skills through explicit `skills:` frontmatter declarations, and tune reasoning depth via `effort:` frontmatter (CC 2.1.80).

## Behaviour

- **Given** a custom subagent with `skills: [design-principles]` in frontmatter
- **When** the agent is spawned via the Agent tool
- **Then** the agent has access to the design-principles skill protocol

- **Given** an agent with `effort: low` in frontmatter
- **When** the agent processes a mechanical task (linting, pruning, cleanup)
- **Then** the model uses minimal reasoning depth, reducing cost

## Constraints

- Built-in agents (Explore, Plan) CANNOT access skills (CC platform limitation)
- Skills must be explicitly listed; there is no wildcard or "all skills" option
- Each skill adds context tokens; over-assigning skills wastes agent budget
- Effort levels: `low`, `medium` (default), `high`

## Decisions

1. **Targeted skill assignment over blanket assignment**: Each agent gets only the 1-3 skills most relevant to its function. Rejected: assigning all 160 skills (token waste, context pollution).

2. **Model downgrade for mechanical agents**: dependency-guardian, file-read-optimizer, markdown-expert, context-refresh, history-mining, efficiency-auditor downgraded from opus to haiku/sonnet. Rejected: keeping opus for all (unnecessary cost for simple pattern-matching tasks).

3. **Effort: low for enforcers**: DRY/KISS/SOLID/GRASP/YAGNI enforcers set to `effort: low` because they perform rule-based checks, not creative reasoning. Rejected: high effort (would slow down enforcement hooks without quality benefit).

## Status

Active
