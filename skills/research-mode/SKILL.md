---
name: research-mode
description: Routes systematic research through PICO and CRAAP evidence frameworks. Use when /mode research is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode research
  - research mode active
integrates:
  - reasoning-with-ai
  - research-synthesis
  - asking-users
  - creating-wizards
  - hitl
---

# Research Mode Orchestration

Routes systematic research through PICO question formulation and CRAAP evidence evaluation. Orchestration layer only -- see `research-synthesis/SKILL.md` for synthesis protocols and `reasoning-with-ai/SKILL.md` for articulate-first patterns.

## Protocols

### 0. GRIP-First Check

Before dispatching any external research, check KONO and /recall for existing
GRIP knowledge on the topic. This avoids redundant web fetches when the answer
already exists in GRIP's own knowledge base.

### 1. Research Workflow

1. GRIP-first KONO check (/recall or /remind-yourself)
2. Question formulation (PICO framework)
3. Source discovery (multi-type)
3. Evidence collection (structured extraction)
4. Evidence evaluation (CRAAP scoring)
5. Synthesis (weighted by quality)

### 2. Evidence Hierarchy

| Tier | Source Type | Weight |
|------|-----------|--------|
| 1 | Peer-reviewed research, formal specs | Highest |
| 2 | Official documentation, RFCs | High |
| 3 | Technical blogs, conference talks | Medium |
| 4 | Community consensus | Low |
| 5 | Individual opinions | Lowest |

Always trace claims to sources. Note confidence levels.

### 3. CRAAP Evaluation

Score each source:

| Criterion | Question |
|-----------|----------|
| Currency | When was it published/updated? |
| Relevance | Does it address the research question? |
| Authority | Who is the author? What are their credentials? |
| Accuracy | Is the information supported by evidence? |
| Purpose | Why was this information created? |

Scoring: 0-4 per criterion. Total 20. Below 10 = unreliable.

### 4. Source Quality Gate

| CRAAP Score | Action |
|-------------|--------|
| 15-20 | Include with full weight |
| 10-14 | Include with caveats -- AskUserQuestion if borderline |
| Below 10 | Exclude or flag as unreliable |

## Workflows

### Quick (single source lookup, factual question)

1. Direct source consultation
2. Quick finding with citation
3. Output

**Broly level**: THREAD (de-escalated)

### Standard (technical research, literature review)

1. PICO question formulation
2. Multi-source discovery
3. CRAAP evaluation per source
4. Evidence synthesis weighted by quality
5. Output with citations and confidence levels

**Broly level**: CLUSTER (default)

### Deep (systematic review, technology evaluation)

1. Multi-domain research via Broly PIPELINE:
   - Source discovery agent (per domain)
   - Evidence evaluation agent
   - Synthesis agent
   - Contradiction resolution agent
2. Cross-domain integration
3. Comprehensive research report

**Broly level**: PIPELINE (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Research direction could branch multiple ways
- Key finding contradicts prior understanding
- Source quality borderline (CRAAP 10-14)
- Synthesis ready for validation
- Research scope expanding beyond original question

## Output Style

- Evidence-first with source citations
- British English throughout
- No emoji
- Confidence levels for each claim
- Structured findings with evidence hierarchy

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for research mode |
