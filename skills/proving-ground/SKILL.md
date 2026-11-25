---
name: proving-ground
description: Runs a five-stage acceptance gauntlet (LEARN, INSTALL SAFELY, DRY-RUN, interactive /hitl QA, sealed VERDICT) so a new tool is tested before it is trusted. Use when installing or adopting a new app, plugin, MCP server, skill, or package; when asked to "dry-run", "QA", or "test before install"; or to emit a provenance-sealed ACCEPT/REJECT verdict from any harness.
status: Active
version: 1.0.0
triggers:
  - /proving-ground
  - /dry-run
  - "test before I trust it"
  - "QA this tool"
integrates:
  - absorb-knowledge
  - absorb-graph
  - dojo
  - testing-qa-lifecycle
  - hitl
---

# Proving Ground

The dry-run for every new tool: turn "installed successfully" into witnessed, provenance-sealed
evidence of what a tool actually does. Any harness (GRIP, Claude Code, Codex, OpenCode) can run
it against an app, plugin, MCP server, skill, or package.

## The honesty contract (non-negotiable)

- The output is a scoped verdict, never "installed successfully".
- Verdicts say "safe rehearsal with coverage limits" — NEVER "proof you can trust this".
- Unsupported isolation is reported as "blocked" / "not tested", never a pretend-safety pass.
- User acceptance is never auto-passed: the agent cannot approve on the operator's behalf.
- Vendor claims stay distinct from observed behaviour.

## Workflow — five stages

Run the stages in order. Gate each with `testing-qa-lifecycle` checkpoints; batch low-risk ones.

- [ ] **1. LEARN** — `absorb-knowledge` + `absorb-graph`: absorb the tool's docs, schema,
      permissions, dependencies, and stated capabilities into a knowledge graph. Turn each
      vendor claim into a proposed acceptance test. Record "what the vendor says" separately
      from "what we observed".
- [ ] **2. INSTALL SAFELY** — install into a disposable environment, not the operator's working
      one. Record dependencies, requested privileges, install changes, and teardown steps.
- [ ] **3. DRY-RUN** — `/dojo` extended outward: exercise representative tasks with synthetic
      data, a disposable filesystem, and blocked or mocked external services. Capture attempted
      writes, network calls, and permission requests. Confirm zero changes escape the disposable
      boundary.
- [ ] **4. INTERACTIVE QA / UAT** — `testing-qa-lifecycle` + `/hitl`: the operator runs the tool,
      challenges results, adds edge cases, and revises criteria. Branch dynamically into retries,
      regression checks, or an explicitly-approved live test.
- [ ] **5. VERDICT** — seal via GRASP: ACCEPT / ACCEPT-WITH-CONDITIONS / REJECT / INCONCLUSIVE,
      scoped to the tested use case. Attach artifact identity, environment, tests, failures,
      untested claims, and approvals. Fill `references/verdict-template.md`.

## Composition (reuse, don't restate)

| Stage | Reuses |
|-------|--------|
| LEARN | absorb-knowledge, absorb-graph |
| INSTALL SAFELY | the dogfood-install pattern (local product install) |
| DRY-RUN | dojo |
| INTERACTIVE QA | testing-qa-lifecycle, hitl |
| VERDICT | GRASP (provenance seal) |

The genuinely new pieces (a portable trial runtime, an acceptance-session controller, and an
evidence-to-verdict contract) are the product-hardening layer. This skill guides the workflow
with the existing substrate until that layer exists.

## Example — Antepaste (the first dogfood)

1. LEARN: absorb `@antepaste/mcp`'s CLI help and the `clip_status` schema; proposed test —
   "clip_status reports version, transport, and uptime".
2. INSTALL SAFELY: wire `node .../bin/antepaste-mcp.mjs --stdio` into the harness MCP config.
3. DRY-RUN: invoke `clip_status` against a throwaway session; confirm it performs no clipboard
   writes.
4. INTERACTIVE QA: the operator copies text, builds a tower, fires "Copy as context", and checks
   the `# Context:` block lands in a chat.
5. VERDICT: seal ACCEPT or ACCEPT-WITH-CONDITIONS with the observed results.

## Falsification (of this skill)

DELETE this skill if, over roughly five uses: the stages collapse back into hand-rolled
install-and-test (the checklist adds no structure); a verdict is ever emitted as "proof of trust"
in violation of the honesty contract; or the workflow is never invoked. Track: verdicts emitted,
and the false-acceptance rate (sandbox verdict vs an authorised live test).
