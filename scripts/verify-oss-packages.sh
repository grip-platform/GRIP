#!/usr/bin/env bash
# Build every package in oss/, install each into a CLEAN virtual environment, and
# import it there. Publishes nothing.
#
# Why this exists: the parity tests in tests/ run each package from a COPIED SOURCE
# TREE. That proves the code works; it does not prove the package installs. Those are
# different failures -- a wrong `packages.find` directive, missing package data, or a
# bad entry point breaks installation while every test stays green. This runs the
# thing that would actually happen on publish.
#
# Measured 2026-09-17, first run: all five build, install and import, and idr-chain
# signs / verifies / detects tampering from site-packages.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${GRIP_PYTHON:-$HOME/.grip/venv/bin/python}"
WORK="$(mktemp -d)"
# Clean up ONLY on success. A run that fails prints log paths, and deleting the logs
# it just told you to read makes the failure undiagnosable -- which this script did on
# its own first run.
cleanup() { [ "${KEEP_LOGS:-0}" = "1" ] || rm -rf "$WORK"; }
trap cleanup EXIT

# setuptools writes build/ and *.egg-info/ INTO the source tree, not the outdir, so
# verifying a package dirties it. This runs on EVERY path out of verify_pkg -- failed
# build, missing artefact, failed install, success -- because the first version of this
# cleanup sat on the success path only, behind three `continue`s. That left the
# directories behind on exactly the runs that had bad news to report: the verification
# dirtied the repository precisely when a human was most likely to be reading a diff.
# Measured 2026-09-17 against a package whose install cannot succeed: build OK, install
# FAILED, and both oss/<pkg>/build and oss/<pkg>/*.egg-info survived the run.
# An unmatched *.egg-info glob is passed through literally and `rm -rf` exits 0 on it
# (measured under `set -euo pipefail`), so no nullglob guard is needed to stay alive.
# A cleanup that CANNOT remove them is reported and counted as a failure, never left to
# `set -e`: an aborting `rm` skipped `KEEP_LOGS=1`, so the exit trap deleted the logs the
# run had just told the reader to see, and the packages after it went unchecked
# (measured 2026-10-03 with one read-only entry inside build/, GRIP#6164). The call site
# must stay bare and unconditional -- tests/test_verify_oss_packages_cleanup.py pins
# that -- so the failure is recorded here, in the loop's global `fail`.
tidy_source_tree() {
  rm -rf "$1/build" "$1"/*.egg-info && return 0
  printf '%-26s CLEANUP FAILED (build/ or *.egg-info left behind; rm error on stderr)\n' \
    "$(basename "$1")"
  fail=1
}

# Verify ONE package. Returns non-zero on any failure and never cleans up after
# itself -- the caller does that unconditionally, which is the whole point.
verify_pkg() {
  local pkg="$1" name="$2"

  # BOTH artefacts. A wheel is built from the source tree, so it can succeed while the
  # sdist is missing files -- and pip will build from the sdist whenever no wheel
  # matches. Testing only wheels leaves that path completely unexercised.
  if ! "$PY" -m build --wheel --sdist --outdir "$WORK/dist" "$pkg" >"$WORK/$name.build.log" 2>&1; then
    printf '%-26s BUILD FAILED (see %s)\n' "$name" "$WORK/$name.build.log"
    return 1
  fi

  # The importable name is the distribution name with hyphens swapped for underscores.
  local mod="${name//-/_}"
  local wheel sdist
  wheel="$(ls -t "$WORK/dist/${mod}"-*.whl 2>/dev/null | head -1 || true)"
  sdist="$(ls -t "$WORK/dist/${mod}"-*.tar.gz 2>/dev/null | head -1 || true)"
  if [ -z "$wheel" ] || [ -z "$sdist" ]; then
    printf '%-26s MISSING ARTEFACT (wheel=%s sdist=%s)\n' "$name" \
      "${wheel:-none}" "${sdist:-none}"
    return 1
  fi

  # A fresh venv per ARTEFACT, so one package's dependencies cannot mask another's
  # missing declaration -- the exact failure a shared environment hides -- and so an
  # sdist cannot pass by finding the wheel's already-installed files.
  local artefact_fail=0 artefact kind venv
  for artefact in "$wheel" "$sdist"; do
    kind="wheel"; case "$artefact" in *.tar.gz) kind="sdist";; esac
    venv="$WORK/venv-$name-$kind"
    python3 -m venv "$venv" >/dev/null
    if ! "$venv/bin/pip" install --quiet "$artefact" >"$WORK/$name.$kind.install.log" 2>&1; then
      printf '%-26s %s INSTALL FAILED (see %s)\n' "$name" "$kind" \
        "$WORK/$name.$kind.install.log"; artefact_fail=1; continue
    fi
    if ! (cd "$WORK" && "$venv/bin/python" -c "import $mod") >"$WORK/$name.$kind.import.log" 2>&1; then
      printf '%-26s %s IMPORT FAILED (see %s)\n' "$name" "$kind" \
        "$WORK/$name.$kind.import.log"; artefact_fail=1
    fi
  done
  [ "$artefact_fail" -eq 0 ] || return 1

  printf '%-26s wheel ok  sdist ok  (%s)\n' "$name" "$(basename "$wheel")"
  return 0
}

echo "building into $WORK/dist"
"$PY" -m pip install --quiet --upgrade build >/dev/null

fail=0
for pkg in "$REPO"/oss/*/; do
  name="$(basename "$pkg")"
  [ -f "$pkg/pyproject.toml" ] || continue

  verify_pkg "$pkg" "$name" || fail=1
  tidy_source_tree "$pkg"
done

if [ "$fail" -ne 0 ]; then
  KEEP_LOGS=1
  echo "FAILED — at least one package does not install cleanly"
  echo "logs kept in $WORK"
  exit 1
fi
echo "all packages build, install and import from a clean environment (wheel AND sdist)"
