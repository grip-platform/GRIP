---
description: Harness/provider-agnostic idempotent workflow kernel (lib/ultracode) — content-addressed f∘f=f replay, monotone verify, exogenous synthesize, CoT/ToT/FoT topologies, HAPPI harness seam. Distinct from the native Claude Code `ultracode` Workflow trigger / effort tier — GRIP's kernel is the delta on top.
disable-model-invocation: false
harness: universal
---

# /ultracode

Load skill ultracode.

Namespace: the bare word `ultracode` in a prompt is Claude Code's native
dynamic-workflow opt-in, and `/effort ultracode` is a native effort tier. This
command loads GRIP's kernel skill — the idempotent, provider/harness-agnostic
layer those native surfaces do not provide (adopt-native-and-build-more,
`feedback_ultracode_native_namespace_reconcile`).

- Kernel: `lib/ultracode/` (pure stdlib; Pool-B HALAdapter optional)
- Proofs: H-UC-IDEM · H-UC-PROVIDER · H-UC-HARNESS (tests/test_ultracode_*.py)
- Headless: `echo '<happi envelope>' | PYTHONPATH=$HOME/.claude python3 -m lib.ultracode.harness`
