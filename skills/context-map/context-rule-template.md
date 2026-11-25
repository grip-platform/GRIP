---
paths:
  - "apps/FEATURE/**"
  - "apps/OTHER/views/FEATURE*"
  - "**/templates/FEATURE/**"
last_validated: YYYY-MM-DD
validation_threshold: 10
---
# FEATURE_NAME

## Business Purpose

[What this feature does and why it exists. 2-3 sentences max.]

## Actors

- **Actor 1**: [role description]
- **Actor 2**: [role description]
- **System**: [automated triggers, webhooks, crons]

## Request Lifecycle

1. [Actor] does [action] -> [result]
2. [Actor] hits [endpoint/view] ([HTTP method], [read-only/mutates])
3. [System] processes [event] -> [outcome]

## State Mutations

- `GET /endpoint`: MUST NOT mutate state (read-only)
- `POST /endpoint`: updates [model.field], creates [record]
- [Webhook/cron]: updates [state]

**Mutation audit**: For every `.save()`, `.update()`, `UPDATE` query, verify:
- Is this a tracking ID or a permission gate?
- Does this mutation block other actors?

## Edge Cases

- [Multi-actor scenario]: [what should happen]
- [Temporal ordering]: [what if actors arrive in different order]
- [Parameter-as-gate]: [tracking IDs must NOT block other actors]
- [Empty state]: [what happens before any actor has visited]

## Key Files

- `path/to/primary/view.py`
- `path/to/model.py`
- `path/to/template.html`
- `path/to/tests/test_feature.py`

## Dependencies

Interacts with: [Feature A], [Feature B], [Feature C]

Shared state: [database tables, cache keys, session data]
