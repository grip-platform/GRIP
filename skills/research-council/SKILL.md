---
name: research-council
description: Multi-LLM broly mesh council for deep research with SMT analysis and Popperian falsification. Use when investigating external tools, frameworks, papers, or comparing systems to GRIP. Spawns parallel research agents with structured evidence evaluation.
disable-model-invocation: true
effort: high
---

# /research-council — Multi-LLM Mesh Research with Falsification

Deep research with rigorous evidence evaluation. Spawns broly mesh council for multi-perspective analysis with SMT structural mapping and Popperian falsification.

## Usage

```bash
/research-council <URL or topic>                    # Full council analysis
/research-council compare <URL> to GRIP             # Comparative analysis
/research-council investigate <topic> for GRIP       # Applicability study
```

## Protocol

### Phase 1: Discovery (Explore)

1. If URL provided: fetch and analyse the target (README, docs, source)
2. If topic: web search for authoritative sources (papers, repos, docs)
3. Extract: architecture, key features, design patterns, philosophy

### Phase 2: Broly Mesh Council (Parallel)

Spawn 2-3 parallel agents with different analytical lenses:

**Agent A — Structural Analyst (Opus)**
- SMT (Structure Mapping Theory) analysis
- Identify genuine structural mappings vs surface analogies
- Rate each mapping: deep structural / partial / surface-only

**Agent B — Falsification Council (Opus)**
- Pre-register 4-6 hypotheses about the target
- Attempt to falsify each with evidence
- Report: survived / weakened / killed

**Agent C — Integration Architect (Sonnet)**
- What can GRIP learn from this?
- What does GRIP already have?
- What's genuinely novel?
- Concrete implementation recommendations

**Agent D — Fact Checker (Haiku)** [CLUSTER+ only]
- Web search to verify claims from Agents A/B/C
- Cross-reference key assertions against current sources
- Flag any claim that contradicts publicly available evidence
- Fires only at CLUSTER level or above (THREAD/FORK skip for efficiency)

### Phase 3: Synthesis

1. Compile all agent findings into a single article
2. Apply SMT coherence enforcement (reject surface-only mappings)
3. Include falsification results table
4. Write recommendations with evidence grades
5. Save article to `/tmp/{topic}-analysis.md`
6. Create GitHub issue with findings
7. Post summary to Slack if significant

### Phase 4: Absorption (Optional)

If findings warrant GRIP integration:
1. Create GitHub issue with implementation plan
2. Add to INS stack as actionable item
3. Create CIR documenting the decision

## Quality Gates

- Every claim must be traceable to source code or documentation
- SMT mappings must identify mapping type (deep/partial/surface/anti)
- Falsification must pre-register hypotheses BEFORE investigating
- No surface-level hand-waving — evidence or silence

## Live Context (DCI)

**Current GRIP version:** !`cd ~/.claude && git log --oneline -1 2>/dev/null`
**Skills count:** !`ls -d ~/.claude/skills/*/SKILL.md 2>/dev/null | wc -l | tr -d ' '`
**Agents count:** !`ls ~/.claude/agents/*.md 2>/dev/null | wc -l | tr -d ' '`
