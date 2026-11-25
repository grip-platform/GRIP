---
name: synthesis-mode-orchestration
description: Routes knowledge synthesis through cross-domain integration and pattern extraction. Use when /mode synthesis is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode synthesis
  - synthesis mode active
integrates:
  - research-synthesis
  - reasoning-with-ai
  - asking-users
---

# Synthesis Mode Orchestration

Routes knowledge synthesis through cross-domain integration, conceptual bridging, and pattern extraction. Orchestration layer only -- see `research-synthesis/SKILL.md` for synthesis frameworks and `reasoning-with-ai/SKILL.md` for articulate-first patterns.

## Protocols

### 1. Synthesis Workflow

1. **INVENTORY** -- Catalogue all sources, claims, and domains
2. **MAP** -- Create concept map showing relationships
3. **BRIDGE** -- Identify connections across domains
4. **PATTERN** -- Extract recurring themes and structures
5. **INTEGRATE** -- Build unified framework from parts

### 2. Synthesis Frameworks

| Framework | Use When |
|-----------|----------|
| Thematic analysis | Grouping sources by theme |
| Dialectical synthesis | Resolving thesis + antithesis |
| Conceptual bridging | Finding structural isomorphisms |
| Pattern language | Naming recurring solutions |

### 3. Integration Principles

- Preserve source nuance (don't flatten complexity)
- Name the tensions (contradictions are data, not errors)
- Build bridges with evidence (every connection needs support)
- Layer abstraction (concrete → pattern → principle → framework)

### 4. Quality Gate

Every synthesis must show:

1. The synthesis itself (integrated framework)
2. The reasoning chain that produced it
3. Evidence supporting each bridge
4. Tensions and unresolved contradictions

## Workflows

### Quick (connecting 2 sources, simple pattern)

1. Direct comparison and bridging
2. Pattern identification
3. Output

**Broly level**: THREAD (de-escalated)

### Standard (multi-source synthesis, thematic analysis)

1. Full INVENTORY → MAP → BRIDGE → PATTERN → INTEGRATE workflow
2. Framework selection based on material
3. Structured output with reasoning chain
4. Validation via AskUserQuestion

**Broly level**: CLUSTER (default)

### Deep (cross-domain synthesis, framework building)

1. Multi-domain integration via Broly PIPELINE:
   - Domain cataloguing agents (per domain)
   - Cross-domain bridging agent
   - Pattern extraction agent
   - Framework synthesis agent
2. Parallel framework evaluation
3. Comprehensive synthesis report

**Broly level**: PIPELINE (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Sources contradict (which interpretation to prioritise?)
- Multiple valid frameworks could organise the material
- Bridge between domains requires assumption
- Synthesis scope could expand or narrow
- Integration produces unexpected connections

## Output Style

- Synthesis-first, then reasoning chain
- British English throughout
- No emoji
- Visual concept maps where they add clarity
- Show bridging logic explicitly

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for synthesis mode |
