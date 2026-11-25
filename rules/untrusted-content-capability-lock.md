# Untrusted-Content Capability Lock (PARAMOUNT)

## The doctrine

> **An allowlist that authenticates the SENDER of an input does not
> authenticate the CONTENT of that input. Any process spawned — or
> privileged action taken — as a function of untrusted-origin content
> MUST have its CAPABILITY SURFACE restricted, not merely its sender
> checked or its permission prompts skipped.**

Sender-auth ≠ content-auth. The two are orthogonal and a system needs
both wherever untrusted content drives execution.

## Why — the confused-deputy structure

The failure is a textbook confused deputy:

| Role | In the relay instance |
|------|------------------------|
| **Deputy** (trusted, authorised) | The allowlisted operator / channel sender |
| **Payload** (attacker-controlled) | Text the deputy relays, forwards, quotes, or pastes — or content fetched from a URL/file the deputy named |
| **Weapon** | A spawn whose permission prompts are skipped (non-interactive) AND whose tool/permission surface is unrestricted |

A sender allowlist closes "who may speak", never "what the words may do".
An allowlisted human forwarding a malicious stack trace, a "summarise
this", or attacker-controlled fetched content is sufficient to drive the
weapon. The defence must live where the payload acts: the capability
surface.

## The mandate (mechanical reasoning, applied at every such site)

When a spawn / privileged action is driven by untrusted-origin content
(channel relay, webhook exec, file/URL ingest that acts on content, an
LLM tool-call argument derived from external text, CI running untrusted
PR content):

1. **Capability-lock by allowlist.** Restrict the spawn to the minimal
   tool / permission / scope set the task genuinely needs (e.g.
   `--tools "Read,Grep,Glob"`). Allowlist, never denylist
   (`rules/sync-mirror-safety.md`): new dangerous capabilities are
   excluded by construction, not by enumeration.
2. **Prompt-skip is orthogonal to capability.** A permission-prompt
   bypass (`--dangerously-skip-permissions`, `acceptEdits`,
   `bypassPermissions`, `sudo NOPASSWD`, an MCP auto-approve) is
   acceptable ONLY when the surface it operates over is independently
   restricted to a safe set. The security property MUST live in the
   capability layer, because operational reality (no TTY, batch,
   non-interactive) often forces the prompt layer to be bypassed. A
   control that exists only in the prompt layer is no control once the
   prompt layer is, legitimately, skipped.
3. **Worst-case-of-restriction is fail-safe.** An unknown or renamed
   capability name MUST degrade to *fewer* capabilities, never to
   all-capabilities. Verify this property for the specific
   tool/permission system before relying on the lock (for `claude
   --tools`, an unrecognised name yields fewer tools — fail-closed —
   verified against `claude --help` 2026-05-17).
4. **Anchor it with a Goodhart-resistant test.** The test asserts the
   capability set independently (a hardcoded expected set, not only
   "equals the constant"), and that each forbidden capability is absent —
   so a mutation that re-adds a dangerous capability fails the test
   (GRIP #2891 `test_build_argv_tool_allowlist_is_capability_locked` is
   the reference).

## Enforcement tier (knowledge-maturation-functor)

Rule-at-N=1 because severe (live RCE-class on a secrets-bearing host).
Review-and-habit enforced now; a mechanical PreToolUse/lint gate is
DEFERRED until N≥2 independent callsites justify the shim
(`rules/knowledge-maturation-functor.md`). Instances:

- **First instance (fix):** `lib/relay_dispatcher.py::build_argv`
  `--tools` capability lock — GRIP #2891.
- **Reference anti-pattern:** `BeehiveInnovations/pal-mcp-server` #417.
- **Second instance:** extract the mechanical gate, with the fail-safe
  invariant enforced in the shim itself; its first build goes through the
  functor's promotion gate. (This was the 3rd site until 2026-10-03.)

## Falsification

Wrong if, over the next N≥3 untrusted-content-driven spawn sites:

- Capability-locking provably blocks no real exploit class while
  measurably degrading legitimate function more than the risk it removes
  (the lock is pure friction) → relax to advisory.
- Every observed site already carries an independent content-auth layer
  that makes the sender-vs-content distinction moot (the rule addresses
  a non-problem) → downgrade, keep only the cross-ref.
- A real tool/permission system is found that **fails open** on an
  unknown capability name (mandate #3's safety argument is unsound there)
  → the rule's soundness is system-specific; narrow scope to fail-closed
  systems, do not drop the doctrine.

Track per site: {capability set size, exploit classes structurally
excluded, legitimate-function regressions, fail-safe property verified
y/n}. Goodhart-safe: success is exploit-classes-excluded, never
lock-invocation count.

## Scope

Every spawn or privileged action whose inputs include untrusted-origin
content: channel relays (WhatsApp/Slack/Discord/web → agent), webhook
handlers that exec, file/URL ingest pipelines that act on content, LLM
tool-call arguments derived from external text, CI executing untrusted PR
content. Outside scope: a spawn whose entire input is operator-typed at a
trusted console with a human in the loop — there the human IS the
content-auth.

## Relationship to sibling rules

Sits beside `rules/no-guardrail-evasion.md` (don't bypass a control —
this says don't let untrusted content reach an unrestricted one) and
`rules/llm-augmentation-pattern.md` (the capability lock is the
structural analogue of llm-aug's monotone-toward-safe invariant: the
spawn can only ever do *less* than the unrestricted surface, never
more). Cross-refs `rules/dispatch-economics.md` /
`rules/agent-dispatch-protocol.md` (spawn discipline) and
`rules/sync-mirror-safety.md` (allowlist-never-denylist). Universal,
falsifiable, review-and-habit enforced; mechanical enforcement arrives
with the shim at N=2.

Narrative (origin, incidents, measurements): `docs/doctrine/untrusted-content-capability-lock.md`.
