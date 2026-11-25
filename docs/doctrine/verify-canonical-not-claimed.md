# Verify Canonical, Not Claimed: annex

The narrative behind `rules/verify-canonical-not-claimed.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

Origin: 2026-05-16. Three recurrences: (a) dispatch agent claimed `status: completed`
after 1/3 of the work; (b) Tasks #1842-1847 marked "completed" yet the canonical
tree contradicted every claim; (c) PR-merged treated as "done" in a dirty-tree env.

## The Rule

Origin: 2026-06-01 task-drift
  RCA (a live-3-weeks site framed as an open decision off a stale `pending`).

## Self-Describing Artefacts Are Claims, Not Evidence

An earlier draft of this
section claimed a self-report carries "zero independent information" — that
absolute is refuted by signatures, and it is also the clause that manufactures
`assert path.exists()` ceremony and, one council warned, pressure to delete
audit records.

**The originating instance.** `lib/unified_memory_brief.py::inject_into_memory`
enforced a line cap by keeping the head and discarding the tail — where every
append lands — destroying 109 pointers in a sandboxed run while returning
`True` and logging nothing (`git show dcaa01c48^:lib/unified_memory_brief.py`,
lines 642-649; the run is recorded in the fix, GRIP#4997, commit `dcaa01c48`).
The success return was the only signal, and it was uninformative by
construction. The same defect had a test-side twin:
`test_truncation_is_recorded_not_silent` read `lines_kept` out of the JSONL the
code under test had just written, and that field is passed the constant
`MEMORY_LINE_LIMIT` at the call site, so it cannot disagree with itself.

Measured instance, re-run 2026-10-03 on `_trim_to_line_limit`:
the on-disk test added for exactly this reason adds nothing against keeping one
line too many, which an older on-disk check already kills, yet it alone kills
keeping one line too few or half of them. That is a surplus kill under the
clause below: the value was real, and specific to the mutant.

Measured on GRIP's suite: a naive detector flags 202 of 1,458 test
files (13.9%), over the 10% false-positive ceiling; a tight one flags 4 and
misses the real pattern. `tests-must-be-able-to-fail.md` refused its own shim
on a different measurement (`Path.home()`, about 97% false positives); both
are GRIP's suite, not a universal finding.

Origin, 2026-08-20: five non-Anthropic councils weighed this as a candidate
PARAMOUNT doctrine. None of the four asked about promotion backed a standalone
rule (`34931d95780d2c52` was cut off before its verdict), and they split on
where to fold it: here (`963f752931678405`), or `prove-it-claims-canonical.md`
(`28ab5955789e09ca`, `b00b421998c03b13`). The fifth judged only the mechanism
and said `tests-must-be-able-to-fail.md` (`55ff18b91cafeac7`). The records sit
in gitignored `state/sovereign-council/` under the shared demo key, so they are
not attributable, and a reader with only this checkout cannot open them.

### The discriminator, the carve-out, and why there is no mechanical gate

The discriminator for the self-describing-artefacts clause is checkability by a
non-producer, not self-reference as such. A cryptographic signature and a
checksum ARE legitimate self-evidence: the reader re-verifies against content
it also holds, so trusting the producer is not required (GRIP's own moat rests
on exactly that, `cryptographic-causation.md`). A return code, an `fsync()`
result, a database commit acknowledgement, or a log field populated from a
constant are not: none can be checked without re-running the operation.

Do not over-apply this. An assertion on the acted-on artefact is not
automatically worth more than one on a record — measure the surplus before
believing it. A test whose declared subject IS the telemetry is legitimate;
there the log is the artefact.

No mechanical gate exists for this clause: the log path is typically a
dynamically patched module attribute, so detection would need interprocedural
taint tracking through the filesystem. It is review-and-habit only.

Its own falsification: wrong if, over ~20 write-path changes, mutation runs
show surplus kill = 0 — assertions on the acted-on artefact kill no mutant
that self-report assertions miss. Then demote the clause to advisory, as the
parent rule's own falsification does. Count a prevented defect only when a
mutant is killed by the full suite AND survives a suite with artefact-state
assertions stripped; `assert path.exists()` earns no credit. Also wrong if
the checkable-by-a-non-producer carve-out proves so wide that nearly every
real self-report qualifies — narrow it, do not drop it.

### The five-step verification protocol (full form)

The rule's own bullets ("Code", "Service", "Task", "Task description") are the
condensed form of this. In full:

1. **Code**: `git show origin/main:<path> | head -5` — confirms the byte landed.
2. **Endpoint**: `curl -sf <url>/<route>` with an assertion on the response body.
3. **End-to-end / e2e**: a pytest or shell assertion that drives the full path.
4. **Task description**: per the canonical source matching each claim shape —
   `gh pr view <N>` for PR-pending claims, `git ls-remote` for branch-WIP
   claims, `ls/fd` for path claims, re-run the named test for "test fails"
   claims. Cheap; one tool call per candidate.
5. **Reconcile**: "completed" but tree contradicts it — trust the tree. "gap
   exists" but origin/main contradicts it — trust origin/main. Task
   description says "pending" but the PR/branch/file shows otherwise — trust
   the canonical source, surface BOTH in the synthesis so the operator
   decides whether to close, re-scope, or pivot.

Applied: verifying a write means reading the file; verifying a service means
probing the endpoint; writing a test for a function that emits a record means
asserting on what the function changed, not on the record's own self-report.

The rule's scope bound carries a falsification condition alongside it: wrong
iff verification cost exceeds prevented-false-claim cost, or canonical
reality always matched the claim. Never measure the percentage of tests
that read disk, and never count rule invocations.

### Status is telemetry; the referent is the anchor (sharpened 2026-06-01)

A task's `status` field is the task self-reporting done-ness, and it decays
silently in the append-only ledger. Its **referent** — the URL, PR or path it
names — is the exogenous anchor. Before driving any plan, agent or doc off a
set of tasks, reconcile: `/task-reconcile` (`lib/task_reality_reconcile.py`)
extracts each referent, probes it fail-open, and returns PROVABLY-DONE /
PROVABLY-OPEN / UNVERIFIABLE-STALE, so the verify reflex fires on sight rather
than on a read of the stale status word. The pre-dispatch behavioural gate —
carry the verified evidence into a brief, never the raw status — is
`rules/agent-dispatch-protocol.md` L4.
