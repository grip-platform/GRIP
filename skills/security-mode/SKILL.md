---
name: security-mode
description: Routes security auditing through STRIDE, OWASP, and DREAD frameworks. Use when /mode security is active.
status: Active
effort: medium
version: 1.0.0
triggers:
  - /mode security
  - security mode active
integrates:
  - vulnerability-detection
  - threat-modeling
  - sanitizing-public-releases
  - deployment-hardening
  - asking-users
  - creating-wizards
  - hitl
see_also:
  - secure-code-review
  - red-teaming-strategic-decisions
  - safety-guardrails
---

# Security Mode Orchestration

Routes security analysis through STRIDE threat categorisation, OWASP Top 10, and DREAD prioritisation. Orchestration layer only -- see `vulnerability-detection/SKILL.md` for scanning patterns and `threat-modeling/SKILL.md` for systematic analysis.

## Protocols

### 1. Attack Surface Identification

Before any assessment:

1. Identify entry points and trust boundaries
2. Map data flows across boundaries
3. Catalogue assets and their sensitivity levels

### 2. Threat Classification (STRIDE)

| Threat | Category | Example |
|--------|----------|---------|
| Spoofing | Authentication | Forged tokens, session hijack |
| Tampering | Integrity | SQL injection, parameter manipulation |
| Repudiation | Non-repudiation | Missing audit logs |
| Information Disclosure | Confidentiality | Data leaks, verbose errors |
| Denial of Service | Availability | Resource exhaustion |
| Elevation of Privilege | Authorisation | Broken access control |

### 3. Prioritisation (DREAD)

Score each finding 1-10:

| Factor | Question |
|--------|----------|
| Damage | How severe if exploited? |
| Reproducibility | How easy to reproduce? |
| Exploitability | How easy to exploit? |
| Affected users | How many users impacted? |
| Discoverability | How easy to discover? |

### 4. Remediation Routing

| Finding Severity | Action |
|-----------------|--------|
| Critical (DREAD 40+) | HALT -- AskUserQuestion immediately |
| High (DREAD 30-39) | Remediation required before release |
| Medium (DREAD 20-29) | Remediation recommended |
| Low (DREAD < 20) | Document and track |

## Workflows

### Quick (single file security check)

1. Pattern-based scan via vulnerability-detection
2. OWASP Top 10 checklist
3. Output findings

**Broly level**: THREAD (de-escalated)

### Standard (feature or PR security review)

1. Attack surface identification
2. STRIDE classification
3. DREAD scoring
4. Remediation recommendations
5. Output report

**Broly level**: CLUSTER (default)

### Deep (full application audit, compliance review)

1. Comprehensive attack surface mapping via Broly PIPELINE:
   - Authentication/authorisation agent
   - Data flow analysis agent
   - Infrastructure security agent
   - Compliance mapping agent
2. Cross-vector correlation
3. Prioritised findings
4. Remediation roadmap

**Broly level**: PIPELINE (escalated)

## AskUserQuestion Triggers

Mandatory HALT and ask when:

- Critical vulnerability discovered
- Severity assessment uncertain
- Remediation has significant trade-offs
- Business context needed for risk assessment
- Scope of audit not defined
- Compliance framework requirements unclear

## Output Style

- Findings-first format with severity badges
- British English throughout
- No emoji
- Include CWE/CVE references where applicable
- Code examples for remediation

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-09 | Initial creation -- orchestration layer for security mode |
