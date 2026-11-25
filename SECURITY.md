# Security Policy

## Reporting Vulnerabilities

Report privately to the maintainers via the project's GitHub Security
Advisories. Include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact

Do NOT open public issues for security vulnerabilities.

## Response Timeline

- Initial response: 48 hours
- Assessment: 7 days
- Fix (if applicable): 30 days

## Supported Versions

| Version | Supported |
|---------|-----------|
| 6.x     | Yes       |
| < 6.0   | No        |

## Scope

- GRIP core framework
- Safety gate bypass vulnerabilities
- Session data and KONO memory exposure
- MCP server vulnerabilities
- Authentication and authorization issues
- Convergence kernel integrity

## Security Best Practices

When using GRIP:

1. Never commit `.env` files containing credentials
2. Use the pre-commit hook to detect secrets
3. Keep sensitive data in `facts/` (excluded from public builds)
4. Session files in `~/.claude/projects/` contain conversation context -- protect accordingly
5. Instance serialisation stores session state locally -- apply filesystem permissions
