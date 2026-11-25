---
name: secure-code-review
description: Security-focused code review against OWASP Top 10 and CWE patterns. Use when reviewing code for vulnerabilities or running a pre-merge security assessment.
---

# Secure Code Review

Security-focused code review using OWASP Top 10 and CWE patterns.

## When to Use

- Reviewing code for security vulnerabilities
- Pre-merge security assessment
- Audit of authentication/authorization code
- Input validation review

## Checklist

### OWASP Top 10

1. **Injection** (SQL, XSS, Command) — Check all user input paths
2. **Broken Auth** — Session management, credential storage
3. **Sensitive Data** — Encryption at rest and in transit
4. **XML External Entities** — Parser configuration
5. **Broken Access Control** — Authorization checks on every endpoint
6. **Security Misconfiguration** — Default credentials, verbose errors
7. **XSS** — Output encoding, CSP headers
8. **Insecure Deserialization** — Untrusted data deserialization
9. **Known Vulnerabilities** — Dependency scanning
10. **Insufficient Logging** — Audit trail completeness

### Review Protocol

1. Map all input entry points
2. Trace data flow from input to storage/output
3. Check each flow against OWASP checklist
4. Verify error handling doesn't leak information
5. Check dependency versions against CVE databases

### Severity Classification

| Level | Description | Action |
|-------|-------------|--------|
| Critical | Remote code execution, data breach | Block merge |
| High | Auth bypass, privilege escalation | Block merge |
| Medium | Information disclosure, DoS | Fix before release |
| Low | Best practice violation | Track for next sprint |

## Integration

Used by: review, security modes.
