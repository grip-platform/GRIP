#!/usr/bin/env bash
# Resolve a named block of test files to stdout, one path per line.
#
# SINGLE SOURCE, deliberately. Both the PR gate (.github/workflows/validate.yml)
# and the bare-runner probe (.github/workflows/test-baseline-probe.yml) call this.
# An inline copy in each would drift, and the drift would be silent: the gate
# would run a set the probe had never validated, which is the whole failure this
# block exists to avoid.
#
# The lean block's exclusions are the finding of GRIP#5861, and each has a
# DIFFERENT reason. They are listed by exact path rather than collapsed into a
# cleverer glob because the list is meant to be read. There were five; there are
# FOUR, because one of the five turned out not to be an environment problem at
# all -- see test_worker_settings.py below:
#
#   test_embeddings_registry.py
#       needs apsw. It IS declared (requirements.txt), but the gate's
#       minimal-install convention does not provide it. RE-MEASURED
#       2026-09-22 on a bare pytest+pyyaml venv at Python 3.12.13:
#       24 errors, `ModuleNotFoundError: No module named 'apsw'` raised at
#       lib/embeddings/ollama_backend.py:23. The reason holds, and it now
#       rests on a run rather than on a static import walk.
#
#   test_worker_settings.py was listed here with the same reason and it was a
#       MISDIAGNOSIS. Nothing in that file imports apsw. It errored because it
#       resolved settings.worker.json from $CLAUDE_DIR or ~/.claude with no
#       third rung, and a runner has neither -- while the artefact is TRACKED
#       and sits in the checkout. Fixed at the test by adding the checkout to
#       the resolution order (rules/tests-must-be-able-to-fail.md mandate 2),
#       measured 10 errors -> 9 passed / 1 skipped / 11 subtests on the same
#       bare venv, and re-admitted to the lean block below.
#
#   test_settings_reference_wiring.py
#       reads rules/operational-context.md, a git-crypt path. On a runner without
#       the key that is ciphertext, so these can NEVER pass there. Permanent, not
#       a bug, and not fixable by installing anything.
#   test_hook_output_schema.py, test_hitl_quality_hook.py
#       invoke hooks that fail open on an import error, emitting no output at all
#       (GRIP#5884).
#
# Measured on ubuntu-latest with pytest+pyyaml only, at the time of GRIP#5861:
#   full (64 files)  -> 10 failed, 34 errors
#   lean (59 files)  -> 1453 passed, 5 skipped, 0 failed, 75s
#
# Those counts are the ORIGINAL measurement and the glob has grown since: the
# full set is 65 files today and the lean set 61. Kept rather than overwritten
# because they are what justified the split; the current figures, measured
# 2026-09-22 on a bare pytest+pyyaml venv at Python 3.12.13:
#   lean (61 files)  -> 1470 passed, 6 skipped, 0 failed, 84s
set -euo pipefail

BLOCK="${1:-}"

# Optional `--shard I/N`: emit only the I-th of N slices of the resolved block.
# Absent, the whole block is emitted and every existing caller is unaffected.
#
# A STRIDE (`NR % n == i % n`), not a contiguous slice, and that was measured
# rather than assumed. The list is sorted alphabetically, and test cost clusters
# by name: neighbours share a subject and so share a fixture, a subprocess spawn
# or a sleep. A job's wall-clock is its SLOWEST shard, so clustering is exactly
# what hurts. Against lane 4's per-file timings for 617 tests/ files:
#
#            4 shards               6 shards               8 shards
#   contig   485.0s slowest         416.3s                 263.9s
#   stride   382.4s slowest         314.6s                 223.8s
#   ideal    344.3s                 229.6s                 172.2s
#
# Stride wins at every width and lands within 11% of a perfect split at 4. Those
# timings are the orphan set, not this block -- the same directory sorted the
# same way, so the clustering is the same shape, but the seconds are a proxy.
SHARD_I=""
SHARD_N=""
if [ "$#" -gt 1 ]; then
  if [ "$2" != "--shard" ]; then
    echo "unknown option: $2 (expected --shard I/N)" >&2
    exit 2
  fi
  spec="${3:-}"
  # REFUSED, never guessed. A typo that fell back to "the whole block" would
  # run every file on every runner -- N times the compute, no missing coverage,
  # every job green, and nothing anywhere to say so. Falling back to "nothing"
  # is no better: the caller would pass vacuously. `[1-9][0-9]*` rejects an
  # empty side, a non-number, and a zero index in one pattern.
  if ! printf '%s' "$spec" | grep -Eq '^[1-9][0-9]*/[1-9][0-9]*$'; then
    echo "bad --shard spec: '${spec}' (expected I/N, both >= 1)" >&2
    exit 2
  fi
  SHARD_I="${spec%%/*}"
  SHARD_N="${spec##*/}"
  if [ "$SHARD_I" -gt "$SHARD_N" ]; then
    echo "bad --shard spec: '${spec}' (index $SHARD_I exceeds total $SHARD_N)" >&2
    exit 2
  fi
fi

# `i % n` rather than a bare `i` is load-bearing: with n=4 and i=4, `NR % 4` is
# never 4, so the last shard would resolve to nothing and a quarter of the block
# would run nowhere behind four green jobs.
_shard_filter() {
  if [ -z "$SHARD_N" ]; then
    cat
    return 0
  fi
  awk -v i="$SHARD_I" -v n="$SHARD_N" 'NR % n == i % n'
}

_all() {
  ls tests/test_*hook*.py tests/test_*settings*.py tests/test_*registry*.py 2>/dev/null | sort -u
}

# The unbound-anchor candidate set (2026-09-20). Held in a FILE rather than a
# case branch because it is 213 paths and the point of this script is that its
# blocks can be read.
#
# Provenance: 244 test files describe themselves as anchors or guards against a
# specific past defect; 237 of them were reachable by no CI gate at all. That
# was found the hard way -- the extraction-parity test was one of them, and the
# same class of divergence landed on main THREE times in one day before anyone
# noticed the guard was running nowhere.
#
# The 183 are what the PROBE measured clean on a bare ubuntu runner -- not a
# local number. The local pre-measurement said 215 green on macOS with the GRIP
# venv and was a poor predictor: the probe's first run could not even COLLECT,
# and its second ran 214 files to 95 failed / 4983 passed / 24 errors.
#
# 74 of those 95 failures were ONE missing module, `cryptography`, and 20 more
# were `apsw`. So the blocker is the gate's minimal-install convention rather
# than the tests -- worth naming, because `cryptography` is a DECLARED
# requirement (requirements.txt) and without it this runner cannot exercise the
# signing path at all. Installing it would recover 74 failures in one step, and
# that is a decision about what a bare runner should model, not a cleanup.
#
# Exclusions, each with a MEASURED reason rather than a guess:
#
#   test_embeddings_registry.py      needs apsw, which the minimal install omits
#   test_settings_reference_wiring.py reads a git-crypt path; on a runner with no
#                                    key that is ciphertext, so it can NEVER pass
#     -- both from the lean block's existing findings.
#
#   8 more, named by the probe's FIRST run (34832189265 -> 35515375174), which
#   did not merely fail: it could not COLLECT. Eight modules fail to import on a
#   bare runner, and pytest aborts the whole session on a collection error, so
#   the other 205 never ran at all.
#     apsw   : test_embedding_dispatch, test_embeddings_rrf_tuning,
#              test_kono_lifecycle_mixin, test_kono_recall_honesty,
#              test_kono_stats_uninitialised, test_kono_substrate,
#              test_storage_kono_failure_reports_at_runtime
#     Crypto : test_ff_ops_cli
#
# And a correction in the other direction. The local pre-measurement ran against
# a `git archive` export, which is NOT a git repository -- 19 of these tests
# shell out to git, so their result there was meaningless. Re-measured in a real
# checkout, all 19 pass, and NINE of them had been wrongly excluded as red,
# including test_secret_files_are_gitignored and test_secrets_gate_never_bypassed.
# Excluding a secrets guard on an artefact of the harness is the failure mode
# this whole exercise is about.
_unbound_anchors() {
  local list="$(dirname "$0")/../config/test-blocks/unbound-anchors.txt"
  [ -f "$list" ] || { echo "missing block list: $list" >&2; return 1; }
  # Strip comments AND blanks. The list carries a header explaining its own
  # invariant; without the comment strip those lines reach pytest as file paths
  # and the job dies on 28 nonexistent files. `|| true` because grep exits 1 on
  # an empty result and this runs under -e.
  grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$list" || true
}

# The measured half of the remaining unbound tail (2026-09-22).
#
# Coverage before this block: 277 of 1689 test files (16.4%) were reached by any
# workflow that runs on a pull request -- counting literal path mentions plus
# everything the other blocks resolve. The other 1412 ran nowhere on a PR, so a
# green PR said nothing whatever about them.
#
# Binding all 1412 was never the plan; the tree's baseline is 383 failing of
# 40756, so a blind widening lands unrelated red on the only gate the repo has.
# Each of the four narrowings below threw away files for a DIFFERENT measured
# reason, and the counts are the evidence rather than an assertion:
#
#   1412  unbound
#   1322  pass when run on their own          (90 dropped: 86 failed, 2 collected
#                                              nothing, 2 exceeded 90s)
#    979  still import with pytest+pyyaml     (343 dropped by a static import
#                                              walk through lib/: cryptography
#                                              152, apsw 114, sqlite_vec 113,
#                                              llama_cpp 9, anthropic 6, PIL 6)
#    965  survive being run TOGETHER          (14 dropped: they pass alone and
#                                              fail in combination, which is the
#                                              exact hazard the unbound-anchors
#                                              job's own header warns about)
#    883  fit the time budget                 (82 dropped as the most expensive)
#    879  not recorded unrunnable elsewhere   (4 dropped on the block's FIRST CI
#                                              run -- a git-crypt path, two
#                                              fail-open hooks, and one apsw
#                                              import reached through importlib,
#                                              which a static walk cannot see)
#    828  survive a REAL bare runner           (51 dropped -- 249 failures and 12
#                                              errors on ubuntu-latest that a
#                                              macOS run with a full venv and a
#                                              static walk both called clean)
#    738  fit the budget ON THAT RUNNER        (90 dropped; CI cost 1.93x the
#                                              local per-file sum, so the first
#                                              budget was set against the wrong
#                                              machine)
#
# The local batched run of the 883 is the time measurement; the numbers are in
# the validate.yml job header. Local green is a PRE-FILTER and not the verdict --
# this machine has the full requirements set, so the import walk above is doing
# the work that a bare runner would otherwise do by failing.
#
# Every file kept OUT is in config/test-blocks/unbound-sweep-excluded.txt with
# its measured reason -- 55 of them, and not one is a missing top-level import,
# which is why neither the local pass nor the static walk caught any of them.
#
# This block is a LIST, not a glob, for the same reason unbound-anchors is: the
# set was chosen by measurement, and a glob would quietly re-admit whatever the
# measurement rejected.
_unbound_sweep() {
  local list="$(dirname "$0")/../config/test-blocks/unbound-sweep.txt"
  [ -f "$list" ] || { echo "missing block list: $list" >&2; return 1; }
  # Same comment/blank strip as _unbound_anchors, and for the same reason: the
  # list carries a header, and without this those lines reach pytest as paths.
  # `|| true` because grep exits 1 on an empty result and this runs under -e.
  grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$list" || true
}

_emit() {
case "$BLOCK" in
  unbound-anchors-candidate)
    _unbound_anchors
    ;;
  unbound-sweep)
    _unbound_sweep
    ;;
  hooks-settings-registry)
    _all
    ;;
  hooks-settings-registry-lean)
    # `|| true` because grep exits 1 on an empty result and this runs under -e.
    _all \
      | grep -vx 'tests/test_embeddings_registry.py' \
      | grep -vx 'tests/test_settings_reference_wiring.py' \
      | grep -vx 'tests/test_hook_output_schema.py' \
      | grep -vx 'tests/test_hitl_quality_hook.py' || true
    ;;
  *)
    echo "unknown block: ${BLOCK:-<empty>}" >&2
    # Keep this list in step with the case arms above. It went stale the
    # moment a third block was added, which is how a caller learns a valid
    # name is invalid.
    echo "valid: hooks-settings-registry, hooks-settings-registry-lean,"\
         "unbound-anchors-candidate, unbound-sweep" >&2
    exit 2
    ;;
esac
}

# Captured into a variable rather than piped, so the unknown-block `exit 2`
# above stays at the top level instead of dying inside a pipeline's subshell.
# The empty guard matters too: `printf '%s\n' ""` emits one BLANK line, which a
# caller testing for a non-empty file would read as a block of size 1.
RESOLVED="$(_emit)"
if [ -n "$RESOLVED" ]; then
  printf '%s\n' "$RESOLVED" | _shard_filter
fi
