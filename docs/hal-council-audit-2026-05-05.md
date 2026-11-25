# HAL Council / AGORA Reviewer Surface Audit — 2026-05-05

Wave 3 of `/ultrado` HAL overnight loop. Hypothesis **H368**: HAL Council
unified PR reviewer has <3 open bugs blocking standard flow.

## Inventory

CI workflows touching the HAL/AGORA review surface:

| Workflow | Path | Purpose | Status |
|----------|------|---------|--------|
| AGORA PR Review | `.github/workflows/agora-pr-review.yml` | 3-Speaker descriptive review (H346) | **BROKEN — fixed this wave** |
| HAL Council Review | `.github/workflows/hal-council-review.yml` | Binary PASS/BLOCK + auto-merge | Working |

Scripts on the same surface:

| Script | Path | Role |
|--------|------|------|
| `lib/agora_pr_review.py` | GRIP repo | AGORA Council CLI entrypoint |
| `lib/hal_council_dispatch.py` | GRIP repo | Multi-LLM dispatch primitives (REUSED by AGORA) |
| `lib/hal_council_pipeline.py` | GRIP repo | HAL Council convergence pipeline |
| `lib/hal_council_replay.py` | GRIP repo | Replay/debug HAL Council runs |
| `lib/improve_kernel.py` | GRIP repo | DFI-FIB convergence kernel (used by AGORA) |
| `.github/scripts/council_converge.py` | GRIP repo | HAL Council CI driver |

## Root cause of AGORA path-404

The workflow asserted that `agora_pr_review.py` lived inside the HAL repo at
`hal/lib/agora_pr_review.py` and built its run command + `PYTHONPATH` around
that assumption. In reality:

- `lib/agora_pr_review.py` lives in the **GRIP** repo (this repo).
- It imports `lib.hal_council_dispatch` and `lib.improve_kernel` — both also
  GRIP-resident.
- The HAL checkout is needed only so the `hal` CLI binary is on `PATH` for
  `_call_happi()` (the underlying transport for each Speaker).

The previous workflow checked out HAL into `hal/` and pointed `PYTHONPATH`
there, then ran `python3 hal/lib/agora_pr_review.py` — `[Errno 2] No such
file or directory` on every PR.

## Fix applied (this wave)

Single workflow patch on `.github/workflows/agora-pr-review.yml`:

1. Renamed HAL checkout path `hal` → `.hal` to match the HAL Council
   workflow's convention (parity with `hal-council-review.yml:98`).
2. Replaced `PYTHONPATH: ${{ github.workspace }}/hal` with
   `PYTHONPATH: ${{ github.workspace }}` so Python imports resolve against
   the GRIP checkout.
3. Added `HAL_HOME: ${{ github.workspace }}/.hal` and an `export
   PATH="$HAL_HOME/bin:$PATH"` line so the `hal` CLI is discoverable for
   `_call_happi()` (mirrors `hal-council-review.yml:119-121`).
4. Changed the run command from `python3 hal/lib/agora_pr_review.py …` to
   `python3 lib/agora_pr_review.py …`.

## Bugs found, severity, disposition

| # | Bug | Severity | Disposition |
|---|-----|----------|-------------|
| 1 | AGORA workflow path-404 on `hal/lib/agora_pr_review.py` | **BLOCKING** (every PR) | **Fixed this wave** |
| 2 | Migration note in `agora-pr-review.yml` (`hal-council-review.yml` deprecation step) is undone — both workflows still active | DOC-ONLY | Track for follow-up; intentional during AGORA pilot per file comment |
| 3 | AGORA workflow uses Python `3.13`; HAL Council uses `3.11` — minor drift, not breaking | MINOR | Track for follow-up; align once AGORA proves stable |

## Hypothesis H368 evidence

- Bugs found total: **3** (1 BLOCKING + 2 non-critical)
- Bugs blocking standard flow before fix: **1** (the path-404)
- Bugs blocking standard flow after fix: **0**
- BLOCKING + MAJOR after fix: **0**

**Verdict: CONFIRMED.** With the AGORA path-404 fixed, the HAL Council /
AGORA reviewer surface has zero blocking bugs. Bugs #2 and #3 are
documentation/parity drift that don't prevent the reviewer from running.

## Tracked for follow-up

- F1 (DOC-ONLY): When AGORA's pilot proves out (5+ clean PR runs, no
  rollback), execute the migration plan documented in
  `agora-pr-review.yml:91-98` — set `hal-council-review.yml`'s `if:` to
  `false` and remove `hal_council_dispatch.py` after the 30-day quiet
  period. Owner: V>>.
- F2 (MINOR): Align Python versions across both workflows (pick `3.11` or
  `3.13`, settle on one). Owner: next /ultrado wave on CI hygiene.

## Convergence note

This audit walked past the `lib/hal_council_dispatch.py` shared-substrate
deeper than the prompt required to confirm root cause. No further fixes
applied — Rule 11 anti-drift; one PR, one concern.
