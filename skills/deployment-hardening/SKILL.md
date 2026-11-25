---
name: deployment-hardening
description: Security hardening protocol for production deployments — pre-deployment checklist, infrastructure hardening, and verification. Use before shipping to production or when reviewing deployment security.
---

# Deployment Hardening

Security hardening protocols for production deployments.

## When to Use

- Pre-deployment security checklist
- Infrastructure hardening
- Production environment setup
- Security-mode sessions

## Hardening Checklist

### Network

- [ ] TLS 1.3 enforced (no TLS 1.0/1.1)
- [ ] HSTS headers with preload
- [ ] No services on default ports exposed unnecessarily
- [ ] Firewall rules: deny-all + explicit allow
- [ ] Rate limiting on all public endpoints

### Authentication

- [ ] No default credentials
- [ ] Token rotation policy (90-day max)
- [ ] MFA on admin accounts
- [ ] Session timeout configured
- [ ] Password hashing (bcrypt/argon2, not MD5/SHA1)

### Application

- [ ] Debug mode disabled
- [ ] Verbose errors disabled (generic error pages)
- [ ] Security headers set (CSP, X-Frame-Options, X-Content-Type-Options)
- [ ] Input validation on all endpoints
- [ ] CORS restricted to known origins

### Infrastructure

- [ ] Container images scanned
- [ ] Secrets in vault (not env vars or config files)
- [ ] Logs do not contain PII or secrets
- [ ] Backups encrypted and tested
- [ ] Monitoring and alerting configured

### Access Control

- [ ] Principle of least privilege
- [ ] Service accounts have minimal permissions
- [ ] SSH key-based auth only (no password)
- [ ] Audit logging enabled

## Verification

Run deployment gate before every production push:
```bash
# Verify no secrets in codebase
rg -i "(password|secret|api_key)\s*=" --glob '!*.md' --glob '!*.example'

# Check for debug flags
rg -i "debug\s*=\s*true|DEBUG_MODE" --glob '*.py' --glob '*.js'
```

## Integration

Used by: security mode. See also: threat-modeling, sanitizing-public-releases.
