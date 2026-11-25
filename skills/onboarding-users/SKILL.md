---
name: onboarding-users
description: Legacy onboarding skill. Now redirects to /activate-grip for unified authentication and identity setup. Maintained for backward compatibility.
status: Active
effort: low
version: 2.2.0
triggers:
  - /onboard
  - first-run detection (legacy)
integrates:
  - activate-grip-tui
  - asking-users
  - creating-wizards
  - hitl
---

# Onboarding Users

Legacy onboarding - redirects to `/activate-grip` wizard.

## Redirect Notice

As of Gen 219, `/onboard` redirects to `/activate-grip` which provides:

1. Claude authentication check
2. Identity setup (name)
3. Persona selection (Developer/Designer/Writer/Founder)
4. Signature system (N-mind)
5. Usage mode (Solo/Team)
6. Confirmation
7. Persona skill auto-installation

## Legacy Behavior

If `/onboard` is invoked:

```text
"The /onboard command has been unified into /activate-grip.
Running /activate-grip wizard..."

→ Invoke /activate-grip-tui skill
```

## Migration Path

| Old Command | New Command |
|-------------|-------------|
| `/onboard` | `/activate-grip` |
| `grip onboard` | `grip activate-grip` |

## Why Unified?

The original `/onboard` focused only on GRIP identity setup. Users often needed to also authenticate with Claude API separately.

The unified `/activate-grip` wizard:
- Checks Claude authentication first
- Sets up GRIP identity
- Configures persona and skills
- All in one flow

## Backward Compatibility

- `/onboard` command still works
- Automatically redirects to `/activate-grip`
- No user action required

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.2.0 | 2026-01-01 | Redirect to /activate-grip (Gen 219 unification) |
| 2.1.0 | 2025-12-29 | Added persona selection |
| 2.0.0 | 2025-12-20 | Bespoke configuration |
| 1.0.0 | 2025-12-02 | Initial creation |

---


