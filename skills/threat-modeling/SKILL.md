---
name: threat-modeling
description: STRIDE-based threat analysis for security assessment. Use when reviewing a new system or feature for security risk, or running an architecture security assessment.
---

# Threat Modeling

STRIDE-based threat analysis for system security assessment.

## When to Use

- New system or feature security review
- Architecture security assessment
- Pre-deployment threat analysis
- Security-mode sessions

## STRIDE Framework

| Threat | Property Violated | Example |
|--------|-------------------|---------|
| **S**poofing | Authentication | Fake login credentials |
| **T**ampering | Integrity | Modified request payload |
| **R**epudiation | Non-repudiation | Denied transaction |
| **I**nformation Disclosure | Confidentiality | Leaked API keys |
| **D**enial of Service | Availability | Resource exhaustion |
| **E**levation of Privilege | Authorization | Admin access via bug |

## Protocol

### Step 1: Decompose System

- Identify trust boundaries
- Map data flows across boundaries
- List entry points and assets

### Step 2: Enumerate Threats

For each data flow crossing a trust boundary:
- Apply each STRIDE category
- Ask: "How could an attacker achieve this?"
- Document threat scenario

### Step 3: Score with DREAD

| Factor | Score (1-10) |
|--------|--------------|
| **D**amage potential | How bad if exploited? |
| **R**eproducibility | How easy to reproduce? |
| **E**xploitability | How much skill needed? |
| **A**ffected users | How many impacted? |
| **D**iscoverability | How easy to find? |

Risk = Average of DREAD scores.

### Step 4: Mitigate

| Risk Level | DREAD Avg | Action |
|------------|-----------|--------|
| Critical | 8-10 | Block release, fix immediately |
| High | 6-7 | Fix before next release |
| Medium | 4-5 | Schedule for sprint |
| Low | 1-3 | Accept or track |

## Integration

Used by: security mode. See also: secure-code-review, vulnerability-detection.
