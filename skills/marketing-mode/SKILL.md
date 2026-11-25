---
name: marketing-mode
description: Routes content across formats with anti-fluff enforcement and multi-format repurposing. Use when /mode marketing is active and content creation begins.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode marketing
  - marketing mode active
integrates:
  - authentic-writing
  - medium-article-writer
  - professional-pdf
  - asking-users
  - creating-wizards
  - hitl
---

# Marketing Mode Orchestration

Routes content creation across formats with anti-fluff enforcement and revision gates. Orchestration layer only -- see `authentic-writing/SKILL.md` for banned vocabulary and `medium-article-writer/SKILL.md` for article templates.

## Protocols

### 1. Format Routing

| Signal | Format | Route To |
|--------|--------|----------|
| "Medium article", "technical article" | Medium | medium-article-writer |
| "LinkedIn post", "professional post" | LinkedIn | Direct generation |
| "blog post", "company blog" | Blog | Direct generation |
| "PDF", "branded document" | PDF | professional-pdf |
| "repurpose", "multi-format" | Campaign | Multi-format workflow |
| Ambiguous | HALT | AskUserQuestion for format selection |

### 2. Anti-Fluff Enforcement

Active on ALL output in marketing mode. Before finalising any content:

1. Scan against banned vocabulary list (from authentic-writing)
2. Scan against banned phrases list
3. Check structural anti-patterns (rule of three, em dash overuse, staccato)
4. If violations found: auto-fix and note changes
5. If uncertain about tone: AskUserQuestion

### 3. Multi-Format Repurposing

When creating a campaign from a single source:

1. Generate primary format first (longest form)
2. Derive shorter formats from primary (preserve key message, adapt structure)
3. Validate each derived format independently
4. Present all formats for review

### 4. Revision Gates

After first draft of any content:

1. Present draft to user
2. AskUserQuestion: "Revise, approve, or change direction?"
3. Maximum 3 revision rounds before forcing a direction decision

## Format Specifications

| Format | Word Count | Structure | Constraints |
|--------|-----------|-----------|-------------|
| Medium article | 1500-2500 | Hook, problem, solution, results, philosophy | Technical depth, code snippets welcome |
| LinkedIn post | 150-300 | Hook line, 3-5 short paragraphs, CTA | No hashtag spam, max 3 hashtags |
| Blog post | 800-1500 | Intro, sections, conclusion | SEO-aware headings |
| PDF document | Variable | Swiss Minimalism brand style | Professional branding via professional-pdf |

## Quality Gates

Every piece of content must pass:

| Gate | Check |
|------|-------|
| Banned vocabulary | Zero matches against authentic-writing banned list |
| Banned phrases | Zero matches against authentic-writing phrase list |
| Anti-patterns | No rule-of-three, no manufactured urgency, no fake vulnerability |
| Voice check | Active voice, specific over general, direct statements |
| Spelling | British English (colour, organisation, analyse) |
| Length | Within format word count range |

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Tone or voice not specified (formal, conversational, technical)
- Target audience not clear
- Key message priorities not ranked
- Call-to-action not defined
- Repurposing targets not selected
- Brand voice conflicts with content type
- Multiple valid angles for the same topic

## Output Style

- British English throughout
- Active voice, direct statements
- Specific numbers and examples over vague claims
- No emoji in professional content
- Format-appropriate structure (see specifications table)
- Author's authentic voice preserved (no AI slop)

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for marketing mode |
