#!/usr/bin/env bash
#
# Shared venv Python resolution utilities.
# Sourced by install.sh, bootstrap-semantic-rl.sh, and 25 bin/ commands
# (bin/grip, bin/grip-doctor, bin/grip-test, bin/grip-mode, ...).
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib/venv-utils.sh"
#   python_path="$(resolve_venv_python "/path/to/venv")"
#

# Every caller of this file is about to run GRIP Python, and GRIP Python prints
# box-drawing and status glyphs. On Windows, stdout defaults to the ANSI code
# page (cp1252 on a Western locale), which cannot encode them -- so the command
# dies with UnicodeEncodeError rather than doing its job. This killed
# `grip-doctor --check` on 2026-08-11.
#
# Set here because this file is the one seam every Python-launching GRIP command
# already passes through to find an interpreter. Assign-if-unset, never
# override: an operator who has deliberately chosen an encoding keeps it, and
# lib/_compat.py::ensure_utf8_stdio still protects the process from the inside.
: "${PYTHONIOENCODING:=utf-8}"
export PYTHONIOENCODING

# The one package GRIP's decision-record signing needs: both limbs of the dual
# seal, Ed25519 and ML-DSA-65. 48, not 47: 47.0.0 carried ML-DSA only for AWS-LC
# and BoringSSL builds, and 48.0.0 is the first release whose pip wheels have it
# (rules/never-system-python-for-grip.md). One definition, so the floor cannot
# drift between the installers that put it in a venv.
GRIP_SIGNING_REQUIREMENT="cryptography>=48"

# _python_answers <path>
#
# True when the candidate interpreter actually RUNS.
#
# Windows ships "App Execution Alias" entries at
# AppData/Local/Microsoft/WindowsApps/python3.exe which are symlinks to
# AppInstallerPythonRedirector.exe. They exist, they are executable, and
# `command -v python3` finds them -- but every invocation prints
# "Python was not found; run without arguments to install from the Microsoft
# Store..." and runs no Python. Returning one is worse than returning nothing:
# the caller reports a Python problem for what the operator typed as a GRIP
# command.
#
# Its exit code proves nothing. It has been measured exiting 49 on one machine
# and 0 on another (Windows 11 / Git Bash, commit aebd49b52), so the test is the
# OUTPUT: the candidate must write back the exact token the probe asks Python to
# write, the same judgement bin/grip-pull-check makes in answers(). stderr is
# discarded and stdout must equal the token exactly, so the placeholder fails
# whichever stream its advert goes to.
# sys.stdout.write rather than print, because on Windows print ends the line
# with "\r\n" and the comparison would fail on the "\r".
#
# This asserts the PROPERTY that makes an interpreter worth returning -- that it
# answers -- rather than the spelling of a path. A WindowsApps/ name test would
# also reject a genuine Microsoft-Store Python, which does run.
_python_answers() {
    [[ -n "${1:-}" ]] || return 1
    [[ "$("$1" -c 'import sys; sys.stdout.write("12345")' 2>/dev/null)" == "12345" ]]
}

# resolve_venv_python [venv_dir]
#
# Resolves the Python executable inside a virtualenv, handling both
# Windows (Scripts/python.exe) and POSIX (bin/python3, bin/python) layouts.
# Falls back to a system python3/python that actually runs if no venv binary is
# found. Returns 1 if no working Python can be found anywhere.
resolve_venv_python() {
    local venv_dir="${1:-}"

    if [[ -n "$venv_dir" ]]; then
        # Windows: Scripts/python.exe
        if [[ -x "$venv_dir/Scripts/python.exe" ]]; then
            echo "$venv_dir/Scripts/python.exe"
            return 0
        fi

        # POSIX: bin/python3 (preferred)
        if [[ -x "$venv_dir/bin/python3" ]]; then
            echo "$venv_dir/bin/python3"
            return 0
        fi

        # POSIX fallback: bin/python
        if [[ -x "$venv_dir/bin/python" ]]; then
            echo "$venv_dir/bin/python"
            return 0
        fi
    fi

    # No venv python found -- fall back to system, but only to an interpreter
    # that actually runs. `command -v` returns the FIRST match on PATH, which on
    # Git Bash for Windows is routinely the non-functional App Execution Alias
    # stub; without this loop the stub wins and every downstream caller dies
    # with a message about the Microsoft Store.
    local cand resolved
    for cand in python3 python; do
        resolved="$(command -v "$cand" 2>/dev/null || true)"
        [[ -n "$resolved" ]] || continue
        if _python_answers "$resolved"; then
            echo "$resolved"
            return 0
        fi
    done

    return 1
}

# _venv_run <dry> <command...>
#
# Runs the command, or on a dry run (<dry> is --dry-run) prints it instead.
# A function rather than an array prefix, because bash 3.2 (macOS's /bin/bash)
# treats an empty array as unbound under `set -u`.
_venv_run() {
    local dry="$1"
    shift
    if [[ "$dry" == "--dry-run" ]]; then
        echo "[grip-venv] would run: $*"
    else
        "$@"
    fi
}

# ensure_grip_venv <target> <python> [--dry-run]
#
# Makes <target>/venv a Python environment GRIP can sign with, so bin/grip-py
# finds it and signed records never fall back to the forgeable demo seal. It
# creates the venv with <python> when <target>/venv has none, installs
# <target>/requirements-core.txt (the packages a fresh install needs, GRIP#9399)
# and then <target>/requirements.txt when the target has one, makes sure
# cryptography>=48 is there, and proves the venv can make both halves of GRIP's
# dual signature (ML-DSA and Ed25519). Safe to run again: an existing venv is
# reused and pip skips what is already installed. A package that fails to
# install (llama-cpp-python needs a compiler, for one) is reported and the step
# goes on; a venv that cannot sign returns 1. --dry-run prints the commands
# instead of running them.
#
# Every pip line passes --prefer-binary: cryptography 50.0.2 has no Intel Mac
# wheel, so without it pip on an Intel Mac tries a Rust build instead of taking
# 48.0.1's wheel.
ensure_grip_venv() {
    local target="$1" python="$2" dry="${3:-}"
    local venv="$target/venv" vpy probe
    vpy="$(resolve_venv_python "$venv" || true)"
    if [[ "$vpy" == "$venv/"* ]]; then
        echo "[grip-venv] reusing the Python environment at $venv"
    else
        echo "[grip-venv] creating a Python environment at $venv"
        _venv_run "$dry" "$python" -m venv "$venv" || {
            echo "[grip-venv] ERROR: could not create $venv (on Debian or Ubuntu, install python3-venv first)" >&2
            return 1
        }
        vpy="$(resolve_venv_python "$venv" || true)"
        [[ "$vpy" == "$venv/"* ]] || vpy="$venv/bin/python3"   # a dry run made no venv
    fi
    if [[ -f "$target/requirements-core.txt" ]]; then
        echo "[grip-venv] installing $target/requirements-core.txt"
        _venv_run "$dry" "$vpy" -m pip install --quiet --prefer-binary -r "$target/requirements-core.txt" \
            || echo "[grip-venv] WARNING: a core package did not install; run \"$vpy\" -m pip install --prefer-binary -r \"$target/requirements-core.txt\" when online"
    fi
    if [[ -f "$target/requirements.txt" ]]; then
        echo "[grip-venv] installing $target/requirements.txt (this can take several minutes)"
        _venv_run "$dry" "$vpy" -m pip install --quiet --prefer-binary -r "$target/requirements.txt" \
            || echo "[grip-venv] WARNING: some packages in requirements.txt did not install; GRIP's core still works, and a feature that needs one will say so"
    fi
    _venv_run "$dry" "$vpy" -m pip install --quiet --prefer-binary "$GRIP_SIGNING_REQUIREMENT" || {
        echo "[grip-venv] ERROR: could not install $GRIP_SIGNING_REQUIREMENT into $venv; check the internet connection and run the installer again" >&2
        return 1
    }
    probe='from cryptography.hazmat.primitives.asymmetric import mldsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey'
    _venv_run "$dry" "$vpy" -c "$probe" || {
        echo "[grip-venv] ERROR: cryptography in $venv cannot make GRIP's signatures. Run: \"$vpy\" -m pip install --upgrade --prefer-binary '$GRIP_SIGNING_REQUIREMENT'" >&2
        return 1
    }
    if [[ "$dry" == "--dry-run" ]]; then
        echo "[grip-venv] dry run: nothing was changed"
    else
        echo "[grip-venv] ready: bin/grip-py will run on $vpy, and GRIP's records will be properly signed"
    fi
}
