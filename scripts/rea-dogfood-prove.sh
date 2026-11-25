#!/usr/bin/env bash
# rea-dogfood-prove.sh <target> [subcommand]
#
# The two-inspection idempotence proof for dogfood domain #7: inspect a target
# twice with REA and prove the normalised Evidence hashes equal
# (lib/rea_evidence.py prove). Prints the plan; RUNS it only when
# GRIP_REA_DOGFOOD_RUN=1. Outputs go to a fresh temporary directory, never the
# caller's cwd (Codex review of GRIP#7966: a run from a directory that already
# held run1.json silently truncated it).
#
# The default subcommand `inspect-artifact` was read from `rea --help` on 6.3.0
# ("Inspect one artifact and return all available observations and next
# probes"); `inspect-macho` gives the richer static facts on a Mach-O target.

set -euo pipefail

if ! command -v rea >/dev/null 2>&1; then
    echo "Error: 'rea' is not on PATH (install the pinned release: grip-harness-install rea)." >&2
    exit 2
fi

target="${1:-}"
subcommand="${2:-inspect-artifact}"
if [[ -z "$target" ]]; then
    echo "Usage: $0 <target_path> [inspect-artifact|inspect-macho|...]" >&2
    exit 1
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
out_dir="$(mktemp -d "${TMPDIR:-/tmp}/rea-dogfood.XXXXXX")"

echo "Plan (outputs under $out_dir):"
echo "  rea $subcommand \"$target\" --json > $out_dir/run1.json"
echo "  rea $subcommand \"$target\" --json > $out_dir/run2.json"
echo "  $repo_root/bin/grip-py $repo_root/lib/rea_evidence.py prove $out_dir/run1.json $out_dir/run2.json"

if [[ "${GRIP_REA_DOGFOOD_RUN:-0}" == "1" ]]; then
    echo "Executing..."
    rea "$subcommand" "$target" --json > "$out_dir/run1.json"
    rea "$subcommand" "$target" --json > "$out_dir/run2.json"
    "$repo_root/bin/grip-py" "$repo_root/lib/rea_evidence.py" prove "$out_dir/run1.json" "$out_dir/run2.json"
    echo "Evidence kept at $out_dir"
fi
