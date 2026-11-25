---
description: Mine recent sessions and git history for patterns worth an artefact, split into genuinely-new, recurring-despite-a-memory, and learned-but-unreachable.
disable-model-invocation: false
---

# /mine-patterns — what GRIP keeps re-learning, and what it already learned but cannot reach

GRIP writes down what it learns and then mostly cannot reach it. A dispatched subagent
inherits `MEMORY.md` and the safety core and nothing else
(`rules/binding-memory-records.md`), so a record with no pointer in the index cannot fire.

Measured 2026-09-01. The two figures are **nested scopes, not rival counts**:

| Scope | Records | Unreachable | Share |
|---|---|---|---|
| Feedback records — corrections GRIP was given | 221 | 190 | 86% |
| **The whole memory corpus**, which contains them | 451 | 343 | 76% |

The feedback slice sits inside the corpus, which is why its share is *higher*: the lessons
GRIP was explicitly corrected on are less reachable than its records in general.

Re-measure at any time. The corpus figure is one command, and it names the directory it
read, so the number can never quietly be about a corpus you did not mean:

```
$ bin/grip-mine-patterns reachability
records=452 reachable=108 unreachable=344 (76%)
```

452 rather than 451 because this session added two records after the first measurement —
the count is live, not a constant.

So this does not merely hunt for new patterns. Hunting only for new ones would add to a
pile that is already mostly unreachable — looking productive while making it worse. Every
finding carries a **class**, and the three need opposite fixes:

| Class | Meaning | The fix |
|---|---|---|
| **A** | genuinely new — recurs across sessions, nothing covers it | propose the smallest artefact |
| **B** | recurs **despite** an existing memory or rule | **mechanical** — a gate, shim or pin. Never another memory: writing it down already failed. |
| **C** | already learned, simply unreachable — no index pointer | **compete for a scarce slot** — see below |

Class B is the one nothing else detects, and it is the one that earns machinery. Class C is
arithmetic and costs nothing to compute.

### Class C is a capacity problem, not a backlog

The obvious fix — add a pointer for each unreachable record — **is not available**.
`lib/unified_memory_brief.py:608` caps the index at `MEMORY_LINE_LIMIT = 195` lines (a
5-line margin under a 200-line platform truncation) and trims the overflow. That cap is
**pre-existing platform behaviour, not something this tooling enforces** — `lib/pattern_mining.py`
only measures reachability and never writes the index — so the constant will not appear in
this change's diff. Confirm it in the tree rather than the diff:
`rg -n 'MEMORY_LINE_LIMIT' lib/unified_memory_brief.py`. `MEMORY.md` currently
uses 137 of those lines, so there is headroom for roughly **58 pointers against 343
unreachable records**.

Reachability is therefore **rival**: a pointer added is, past the cap, a pointer evicted.
Most records are cold storage by construction, and no amount of diligence changes that.

This makes the miner *more* useful rather than less. Its job for Class C is not to generate
pointers, it is to **rank candidates for a slot that is already nearly full** — which needs
exactly the evidence the ledger carries: how many distinct sessions the lesson would have
changed, and whether a real failure in the window traces to its absence. A record that
would have prevented something is worth a slot; a record nobody has needed in six months is
not, and saying so is the useful answer.

The same scarcity logic governs the pinned tier in `rules/binding-memory-records.md`, capped
at 1,200 characters precisely because an unbounded pinned tier decays into a second copy of
the index.

## Usage

```bash
/mine-patterns                    # sweep the last 20 sessions
/mine-patterns 40                 # widen the window
/mine-patterns --reachability     # Class C only: instant, no model, no cost
```

The deterministic half runs standalone and is scriptable:

```bash
bin/grip-mine-patterns reachability          # Class C, exact
bin/grip-mine-patterns friction --sessions 20  # correction-shaped turns, a PRE-FILTER
bin/grip-mine-patterns receipt --check       # is the cadence alive?
```

## What it does

1. **Class C first, free.** `bin/grip-mine-patterns reachability` computes the unreachable
   set exactly. No model touches it, so no tokens are spent on arithmetic.
2. **Narrow before spending.** `friction` selects operator turns that look like corrections
   — 340 candidates from 991 turns across 20 sessions when last measured, about a third.
   It **over-selects on purpose**: a missed correction is invisible, a false one is
   discarded by its reader. Never quote its count as a number of real frictions.
3. **Then the fleet.** Invoke the saved workflow with both paths supplied:

   ```
   Workflow({ name: 'session-pattern-mining',
              args: { grip: '<repo root>', scratch: '<writable dir>', sessions: 20 } })
   ```

   Four lanes over disjoint slices — operator friction, artefact history, binding failure,
   retrospective — with cross-learning between rounds and an adversarial verify pass.
   `.claude/workflows/session-pattern-mining.js`. The paths are **required**: it throws
   rather than defaulting, because a default that resolves somewhere plausible is how a
   sweep reports a confident answer about the wrong corpus.

   **If the name does not resolve, pass `scriptPath` instead.** The workflow registry is
   built once per session, so a script written *during* this session is not yet invocable
   by name — verified 2026-09-01, when this very workflow returned `not found` from the
   session that created it while sitting on disk in both trees. `scriptPath` takes
   precedence over `name` and always works:

   ```
   Workflow({ scriptPath: '<repo root>/.claude/workflows/session-pattern-mining.js',
              args: { grip: '<repo root>', scratch: '<writable dir>', sessions: 20 } })
   ```

## What it produces

A **ranked ledger**, not a pile of new artefacts. Each entry carries its class, verbatim
evidence attributed to a session, the count of **distinct** sessions, the smallest artefact
that would fix it, and an adversarial verdict.

**N counts distinct sessions, never observations.** Three mentions in one conversation is
one observation (`rules/knowledge-maturation-functor.md`). Counting rows is the cheapest way
to manufacture the evidence that licenses building machinery, so the verifier recounts and
reports `n_sessions_verified` even when it is lower than claimed.

## The auto-mint boundary

Operator-ratified 2026-09-01. Only the **fully reversible tier** may be created without a
human: a **memory record**, or a **`MEMORY.md` pointer**. Deleting either breaks nothing.

Everything else — rules, skills, agents, hooks, gates — routes to HITL. And per the
bootstrap carve-out in `rules/exogenous-anchor-law.md`, **anything touching a safety gate is
never self-ratified**, regardless of how good any council verdict looks.

A finding is auto-mintable only if *all* of: the proposal is `memory-record` or
`index-pointer`; **verified** distinct sessions ≥ 2; and it touches no gate, no PARAMOUNT
rule, no credential and nothing outward-facing. The verifier names which condition decided
it, so the call is inspectable rather than implicit.

This matters because creation is not the scarce thing. GRIP has **310 skills and 78 already
archived**, plus `pruning-unused-skills` and `meta-unused-skills-blocker` whose whole job is
clearing auto-generated bloat. What is scarce is evidence that something deserves to exist,
which is why the ledger leads with N and evidence rather than with a proposal.

## Always-active, and why it carries a receipt

The cadence writes a **liveness receipt** on every run, and `receipt --check` fails loudly
once the newest stamp is older than its staleness budget.

That is not ceremony. GRIP's previous always-on learner **stopped producing on 2026-08-19
and nobody noticed for twelve days** while 245 session directories accumulated; its
killswitch plist still points at a home directory that does not exist on this machine. A
scheduled job that fails silently is worse than no job at all, because it also supplies the
belief that something is watching. Silence is the failure this reports, not the success.

Install the cadence from `config/launchd/com.grip.pattern-mining.plist.template`, which
substitutes `__GRIP_HOME__` at install time rather than baking in a path.

## Where this sits

Wired into `/learn`. It deliberately does **not** live inside `/ultralearn`: a
cross-provider sovereign council de-scoped that command to BUILD-MINIMAL (seal
`530ddaa79e8086d4`) and explicitly rejected the mega-command framing as internal theatre.
Honouring that seal was an operator decision, not an accident.

Composes `skills/converge-fleet` (the fan-out mechanism), `lib/pattern_mining.py` (the
deterministic floor), and `rules/knowledge-maturation-functor.md` (the N threshold).
Reimplements none of them.

## Anti-patterns

| Anti-pattern | Why wrong |
|---|---|
| Reporting `candidate_turns` as a count of real frictions | It is a pre-filter that over-selects by design |
| Counting observations instead of distinct sessions | Manufactures N=2 from one conversation |
| Proposing another memory for a class B finding | Writing it down is what already failed |
| Auto-minting a rule, skill or agent | Outside the reversible tier the operator ratified |
| Running the workflow without `args.grip` / `args.scratch` | It throws, deliberately — a plausible default sweeps the wrong tree |
| Treating a missing receipt as "nothing to report" | Absence of a run is the loudest finding available |
