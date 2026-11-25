# Substrate Merge Strategy

## Problem

GRIP tracks a set of learning/memory/science files **on purpose** — they are
the cross-machine substrate that propagates learned rules, hypothesis
verification, and genome breeding between every machine running GRIP.
Untracking them is off the table: it breaks cross-machine propagation.

But concurrent GRIP sessions on multiple machines all append to or regenerate
these files every session. Without a merge strategy, `git pull` hits merge
conflicts on substrate, blocking pulls and creating cruft/friction for
everyone.

This document defines a per-file-class merge strategy so substrate churn
**never blocks a pull**, without untracking and without corrupting files.

## File taxonomy and strategy

| Class | Files | Hazard with naive merge | Strategy |
|-------|-------|-------------------------|----------|
| **1 — Append-only, line-independent JSONL** | `learning/insights.jsonl`, `learning/evo-convergence.jsonl`, `learning/insights-convergence.jsonl`, `data/dispatch-failures.jsonl` | Plain `merge=union` keeps **duplicate identical lines** when two machines append the same record | Custom driver `grip-substrate-union`: union of both sides, **deduplicated on exact line content**, ours-order-first |
| **2 — Keyed JSONL, in-place updates** | `learning/hypotheses.jsonl` | Records keyed by `id` (H001…); `status` mutates `pending`→`confirmed`/`refuted` across sessions. `merge=union` **duplicates and contradicts** records for the same id | Custom driver `grip-substrate-keyed`: id-keyed reconcile, **terminal-status precedence**, divergence surfaced (never silently dropped) |
| **3 — Whole-file regenerated structured** | `rules/learned-insights.md`, `data/genome-shared.json`, `data/genomes/*.json` | `merge=union` **corrupts** them (concatenates two JSON/markdown documents → invalid) | `merge=ours`, a one-line driver (`merge.ours.driver true`) the setup scripts register, because git does not build it in -- keeps the side being merged into, so no markers; the regenerator (`/learn`, `/save`, genome breeding) re-emits the authoritative file next cycle |
| **4 — Local timestamp markers** | `state/rsi/evo-last-run` (was `.evo-last-run`), `.last-cleanup` | Churned every session; zero cross-machine value but caused conflicts | **Untracked-by-design** (gitignored). Writers keep writing them locally; they stop being a conflict source |

### Why these specific choices

**Class-1 → dedup-union, not raw `merge=union`.** The built-in union driver
concatenates both sides' added hunks. If machine A and machine B both append
the *same* insight line (common — same insight detected on two machines),
union keeps both copies and KONO double-counts. `grip-substrate-union` dedups
on exact line content, so identical appends collapse to one.

**Class-2 → id-keyed reconcile, NOT union.** `hypotheses.jsonl` is the one
substrate file where lines are *mutated in place*, not just appended. When
machine A flips `H486` from `pending` to `confirmed` (rewriting that line)
and machine B independently flips `H487`, a plain union yields **both** the
old `pending` H486 line **and** the new `confirmed` H486 line — two
contradictory records for one hypothesis id. The keyed driver groups by the
`id` field and resolves per id, three ways against the ancestor git hands it:

- a record only **one** side changed since the ancestor takes that side's
  rows. So a rewritten claim (a redaction, say) or a status deliberately set
  back to `pending` survives a merge with a side that never touched it;
- every row the merge does not alter is written back with the exact bytes
  it came in with, in the place it stood. An id the ledger holds twice
  keeps both rows. Until 2026-10-12 the driver re-serialised the whole file
  with `json.dumps`' default spacing and folded repeated ids on every merge
  it ran, which turned a one-row change into a whole-file diff (found on
  GRIP#8044; `tests/test_substrate_merge_keeps_bytes.py`);
- a row that only one side holds survives. A row one side **removed**
  therefore comes back from the other side. That is unchanged, and it means
  removing a row from the ledger needs the other open branches merged first
  or the removal checked again after they land;
- an id held more than once that **both** sides changed is settled row by
  row, as long as all three versions hold the same number of its rows: a
  row only one side changed takes that side's version, and only a row both
  sides changed is weighed. When the row counts differ, or there is no
  ancestor, the rows cannot be paired, so they are weighed as one group and
  **one row survives**. That is the one case where a merge still loses a row;
- a record **both** sides changed is weighed: terminal status
  (`confirmed`/`refuted`/`inconclusive`/`expired`) beats `pending`;
- if **both** sides hold *different terminal* statuses for the same id (a
  genuine scientific disagreement across machines), the record with the
  later `verification_date` (then `registered`) wins, **and** the surviving
  record is annotated with `_merge_conflict: true` and a `[_merge_conflict]`
  note. The divergence is made visible for human review — it is never
  silently discarded.
- a line that is not a record with an `id` is kept where it stood, so
  nothing is lost (it used to be moved to the end of the file).

This is more specific than the `learning/*.jsonl merge=union` glob, and git
applies the **last matching `.gitattributes` pattern**, so the
`learning/hypotheses.jsonl merge=grip-substrate-keyed` line correctly
overrides the glob.

**Class-3 → `merge=ours`, not a content-merge driver.** Two reasons:

1. **git-crypt safety.** `data/genomes/*.json` is git-crypt encrypted
   (`.gitattributes` `filter=git-crypt`). On a *locked* clone the merge
   driver would receive ciphertext blobs for `%A`/`%B` and any JSON parse
   would fail. `merge=ours` is interpreter-free and never parses content, so
   it is safe regardless of encryption/unlock state.
2. **The file is regenerated wholesale anyway.** `rules/learned-insights.md`
   is rebuilt end-to-end by `/learn`; the genome JSONs are exported wholesale
   by `/save` / breeding. Keeping the local copy on conflict loses nothing
   durable — the next regeneration cycle re-emits the authoritative file with
   both machines' inputs already merged at the source (the insights / genome
   store), not at the git layer.

**Recorded dissent (devil's advocate, minority view).**
`data/genome-shared.json` is *not* encrypted and *could* take a smarter
newest-wins-by-`exported_at` driver instead of blunt `merge=ours` (which
discards the remote export entirely if both sides changed before either
regenerates). The majority accepted uniform `merge=ours` for KISS and
git-crypt uniformity (one rule for all class-3; the file is re-exported every
`/save`). **Revisit trigger:** if `genome-shared.json` ever stops being
wholesale-regenerated every session, replace its `merge=ours` with a
timestamp-merge driver keyed on `exported_at`.

**Class-4 → untrack in place.** `.evo-last-run` (a unix timestamp) and
`.last-cleanup` (an ISO string) record only *this machine's* last run of a
maintenance task. They have zero cross-machine propagation value but were
rewritten every session, so they were a pure-noise conflict source. They are
now gitignored; writers continue to write them locally. (They were first
gitignored in place rather than relocated under `state/`, on the belief that
no static code reference to either filename existed, so relocating risked
breaking an unfound writer. For `.evo-last-run` that belief was wrong:
`lib/evo.py` had written it by a static path since 2026-03-11, and nothing
in the tree reads it. On 2026-10-17 it moved to `state/rsi/evo-last-run`
(`lib/state_paths.py` `RSI_EVO_LAST_RUN`), which `.gitignore` also ignores,
so `tests/test_state_paths.py` can hold every state file to `state/`.
`.last-cleanup` still has no writer in the tree and stays gitignored in
place; the root `.evo-last-run` entry stays too, for copies written before
the move.)

## Mechanism

- **`.gitattributes`** maps each file-class to its driver/strategy. These
  lines travel with the repo, so every clone inherits the mapping
  automatically. `.git/info/attributes` is absent, so tracked
  `.gitattributes` is authoritative.
- **`scripts/substrate_merge.py`** is the merge driver (pure Python 3
  standard library — `json`, `sys` only; no third-party imports, no
  bashisms). Modes selected by `argv[1]`: `jsonl-union` (class-1),
  `jsonl-keyed` (class-2). Git invokes it as
  `<driver> %O %A %B %P` and the driver overwrites the `%A` (ours) path with
  the resolved result, exiting 0 on success.
- **`scripts/setup-local-git.sh`** registers the drivers per-clone via
  `git config merge.<name>.driver`. The class-3 `ours` driver is the one
  exception that needs no interpreter (it is `true`), so it is registered
  unconditionally there, and also by `scripts/install.sh` (after every clone
  and before every pull) and by `scripts/blackbook-xp` (its clone phase). git
  builds in only `text`, `binary` and `union`; every other name, `ours`
  included, is undefined until a script registers it. Driver registration
  *cannot* live in `.gitattributes` — it is repo-local config. The script
  is the canonical one-time per-clone hook and is already cross-platform:
  it detects `python3` vs `python` (Windows Git Bash has only `python`)
  using the same pattern proven in `grip-pull.sh`, and has an MSYS/Windows
  branch.

### Per-clone setup (run once)

```bash
bash scripts/setup-local-git.sh
```

This is idempotent and also sets `merge.renormalize`, the `git grip-pull`
alias, and `skip-worktree` on `learning/*.jsonl`. If a clone never runs any
of the registering scripts, every driver is undefined and git falls back to
an ordinary text merge. For the JSONL classes that is a normal conflict. For
class-3 it is worse than it sounds: a pull or stash pop that meets a local
regeneration stops with `UU data/genome-shared.json` and conflict markers
inside a JSON file (measured on git 2.54.0; fleet report 2026-10-02, A-6).
On a clone that predates the registration, one line repairs it:
`git config merge.ours.driver true`.

## Interaction with existing infra

- **git-crypt:** class-3 uses `merge=ours` precisely so no driver ever parses
  a possibly-encrypted file. Class-1/2 targets (`insights.jsonl`,
  `hypotheses.jsonl`, etc.) are *not* encrypted (`learning/*.jsonl` was
  explicitly removed from git-crypt — see `.gitignore` note "Removed from
  git-crypt"), so the JSON-aware drivers operate on plaintext.
- **`git grip-pull` / `scripts/grip-pull.sh`:** unchanged. It handles
  git-crypt smudge phantom diffs (a different problem). The substrate drivers
  fire during the `git pull`'s merge step inside `grip-pull.sh`.
- **`rebase.autoStash=true` / `pull.rebase=true`:** the drivers run during
  rebase merges too (`%O %A %B`), so rebased pulls reconcile class-1 and
  class-2 substrate identically. Class-3 differs in which copy survives:
  `ours` keeps the side being merged INTO, which is the local copy on a plain
  merge but the upstream copy during `pull --rebase` and `stash pop`
  (measured on git 2.54.0). All three finish cleanly, and the next
  regeneration re-emits the file either way.

## Testing

`tests/test_substrate_merge.py` is Goodhart-proof: for each file-class it
constructs two divergent inputs and asserts the merged result is correct
*and* would fail if the driver logic were wrong (no call-count assertions,
real content checks):

- **Class-1:** disjoint appends → all lines present; *duplicate* appends →
  collapsed (proves dedup, not raw union).
- **Class-2:** machine A confirms H1 while machine B refutes H2 → exactly one
  record per id, correct statuses, no duplicate ids; divergent terminal
  statuses for the *same* id → single surviving record carrying
  `_merge_conflict` (proves divergence is surfaced, not silently dropped).
- **Class-3** is validated by `tests/test_gitattributes_drivers_defined.py`,
  which runs each registering script in a throwaway clone, asserts every
  driver `.gitattributes` names is defined, and pulls over a regenerated
  genome in the merge, rebase and stash-pop shapes.
- **Class-4** (gitignore) is validated structurally via the `.gitattributes`
  assertions in the test.

## Hypothesis

Registered via `lib/hypothesis_engine.py`:

> **Claim:** the per-file-class substrate merge strategy eliminates
> substrate-induced `git pull` conflicts across concurrent multi-machine
> GRIP sessions.
> **Metric:** `substrate_pull_conflicts` · **Prediction:** `0` ·
> **Deadline:** 2026-06-15.

Falsified if any substrate file produces a `git pull`/rebase merge conflict,
or if a driver corrupts/duplicates records, before the deadline.
