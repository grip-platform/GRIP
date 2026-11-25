# Harness-Agnostic Hook Engine

Wave 1 of `CODEX_HOOKS_ULTRAPLAN.md` introduces the deterministic contract
between native hook payloads and GRIP policies. It does not activate new
production hooks.

## Runtime

```text
native payload
    -> adapters.adapt()
    -> immutable HookEvent
    -> engine.evaluate()
       -> deterministic policy selection
       -> pure policy evaluators
       -> deny-precedence aggregation
       -> redacted SHA-256 audit receipt
    -> renderers.render()
    -> host-valid response
```

`runtime.process()` composes that path in-process. Policy evaluators receive
only a `HookEvent` and canonical policy metadata; renderers alone know native
response keys.

## Package

| Module | Responsibility |
|---|---|
| `lib/hooks_engine/events.py` | Immutable, versioned event/tool/operation values |
| `lib/hooks_engine/adapters.py` | Claude, OpenCode, and Codex input translation |
| `lib/hooks_engine/verdicts.py` | Immutable verdicts, stable selection, aggregation |
| `lib/hooks_engine/audit.py` | Recursive redaction and content-addressed receipts |
| `lib/hooks_engine/engine.py` | Policy execution and declared failure behaviour |
| `lib/hooks_engine/renderers.py` | Event-specific native response generation |
| `lib/hooks_engine/compat.py` | Pure translation for existing Claude-shaped scripts |
| `lib/hooks_engine/runtime.py` | One adapt/evaluate/render composition seam |

The package uses only the Python standard library.

## Canonical event contract

`HookEvent` uses schema version `grip.hook-event/1` and contains:

- harness and canonical lifecycle event;
- session, turn, working directory, model, and transcript reference;
- actor or subagent identity;
- prompt;
- canonical tool kind, native name, and operations;
- result;
- opaque native metadata.

Values are recursively immutable and JSON-only. Unknown top-level fields fail
validation. Native-only keys are confined to `raw_metadata`; pure gates must
not read them. Canonical serialisation is sorted and compact, so equivalent
events have the same SHA-256 content identity.

Operations are `command`, `file_delta`, or `tool_invocation`. Wave 1 deliberately
represents Codex `apply_patch` as an opaque tool invocation. Safe patch parsing
and pre/post-image reconstruction belong to Wave 2.

## Canonical verdict contract

`HookVerdict` contains:

- `disposition`: `allow`, `deny`, `feedback`, or `continue`;
- reason, additional context, system message, and optional updated input;
- policy ID, severity, and failure mode;
- evidence and audit reference.

Aggregation is monotone:

```text
deny > feedback > allow > continue
```

An allow can never rescue a denial. Equal dispositions use severity, stable
policy ID, and canonical body as deterministic tie-breakers.

Policies are selected by event and harness, then ordered by explicit order,
descending priority, stable policy ID, and canonical policy body. A missing or
failed closed evaluator denies; a failed open evaluator produces visible
feedback. Evaluators cannot override registry identity, classification, or
failure mode.

## Host coverage

| Surface | Input adapter | Renderer |
|---|---|---|
| Claude Code | All 17 GRIP registry events | Event-specific Claude output |
| Codex | 11 native lifecycle events | Strict Codex output |
| OpenCode | Documented tool, permission, session, and prompt callbacks | Callback action object |

Codex handling follows the current command-hook schema:

- `PreToolUse` emits only `allow` or `deny`; it never emits `ask`;
- unsupported `updatedInput` is rejected before output;
- `PermissionRequest` uses its nested `decision.behavior` object;
- a Stop denial renders `decision: block`, which tells Codex to continue the
  turn;
- `SessionEnd` emits no stdout and remains inside Codex's three-second budget;
- timeout units are never inferred or converted by adapters.

The contract was checked against local `codex-cli 0.144.5` and OpenAI's
generated schemas in
[`codex-rs/hooks/schema/generated`](https://github.com/openai/codex/tree/main/codex-rs/hooks/schema/generated).
OpenCode callback names follow its plugin event surface and the existing
`integrations/opencode/grip-safety.js` bridge.

## Audit boundary

Every engine evaluation produces an immutable `grip-hook-audit/1.0` receipt.
Secrets are redacted before canonical JSON and SHA-256 hashing. Verification
recomputes the digest from the receipt payload.

Wave 1 receipts are pure in-memory primitives. No production hook was activated,
so this wave does not claim durable or externally anchored production audit
coverage. Activation must persist and anchor receipts before a migrated policy
is called production-grade.

## Legacy compatibility

`claude_payload_from_event()` creates the smallest Claude-shaped input an
existing script needs. A harness-owned runner may execute that script and pass
its parsed result to `verdict_from_claude_result()`.

The compatibility layer does not launch subprocesses. Exit `2` is an explicit
denial; other non-zero exits return a runtime error for the registry failure
policy. A legacy interactive `ask` cannot be represented on every host and is
conservatively translated to deny.

## Example

```python
from lib.hooks_engine import HookVerdict, process

policies = {
    "policy.command": {
        "events": ["PreToolUse"],
        "harnesses": ["claude", "opencode", "codex"],
        "classification": "hard",
        "failure_mode": "closed",
        "implementation": "command_gate",
    }
}

def command_gate(event, policy):
    command = event.tool.operations[0].command
    return HookVerdict(
        "deny" if command == "unsafe" else "allow",
        reason="unsafe command" if command == "unsafe" else "",
    )

result = process(
    "codex",
    native_payload,
    policies,
    {"command_gate": command_gate},
)
response = result.response
audit_reference = result.engine.audit.reference
```

## Wave boundary

Still intentionally absent:

- `apply_patch` parsing and path-boundary enforcement;
- migration of production gates into pure evaluators;
- hook configuration generation or local Codex trust changes;
- durable audit persistence and exogenous anchoring;
- claims of coverage for Codex tools that do not emit hooks.

Those boundaries prevent Wave 1 scaffolding from being mistaken for active
safety enforcement.
