#!/usr/bin/env bash
# test-leak-check.sh — local self-test for personal-artefact-leak-check workflow.
#
# Simulates the workflow's match-diff-against-denylist logic on synthetic
# changed-file lists to verify the gate fires correctly. Replicates the
# bash logic from .github/workflows/personal-artefact-leak-check.yml so
# we don't need a real PR to exercise the rule.
#
# Run from anywhere:  bash scripts/test-leak-check.sh

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
DENYLIST="${CLAUDE_DIR}/.github/personal-artefact-paths.txt"

if [[ ! -f "$DENYLIST" ]]; then
    echo "FAIL: denylist not found at $DENYLIST" >&2
    exit 1
fi

# match_diff PATTERN_FILE FILES_LIST  -> outputs one "match" line per leak
match_diff() {
    local patternfile="$1"
    local filelist="$2"
    local leaks=""
    while IFS= read -r pattern; do
        [[ -z "$pattern" || "$pattern" =~ ^# ]] && continue
        pattern="$(echo "$pattern" | xargs)"
        [[ -z "$pattern" ]] && continue
        while IFS= read -r f; do
            [[ -z "$f" ]] && continue
            if [[ "$pattern" == */ ]]; then
                if [[ "$f" == "${pattern}"* ]]; then
                    leaks="${leaks}${f}::${pattern}"$'\n'
                fi
            elif [[ "$pattern" == *"*"* ]]; then
                # shellcheck disable=SC2053
                if [[ "$f" == $pattern ]]; then
                    leaks="${leaks}${f}::${pattern}"$'\n'
                fi
            else
                if [[ "$f" == "$pattern" ]]; then
                    leaks="${leaks}${f}::${pattern}"$'\n'
                fi
            fi
        done < "$filelist"
    done < "$patternfile"
    echo -n "$leaks"
}

PASS=0
FAIL=0

run_case() {
    local name="$1"
    local files="$2"
    local expect_leak="$3"   # "yes" or "no"
    local expected_match="${4:-}"

    local tmp_files
    tmp_files="$(mktemp)"
    echo "$files" > "$tmp_files"

    local leaks
    leaks="$(match_diff "$DENYLIST" "$tmp_files")"
    rm "$tmp_files"

    local has_leak="no"
    [[ -n "$leaks" ]] && has_leak="yes"

    if [[ "$has_leak" == "$expect_leak" ]]; then
        if [[ -n "$expected_match" && "$leaks" != *"$expected_match"* ]]; then
            echo "FAIL  [$name]  leak detected but did not match expected pattern '$expected_match'"
            echo "      got: $leaks"
            FAIL=$((FAIL + 1))
        else
            echo "PASS  [$name]  leak=$has_leak (expected $expect_leak)"
            PASS=$((PASS + 1))
        fi
    else
        echo "FAIL  [$name]  leak=$has_leak (expected $expect_leak)"
        echo "      diff was:"
        echo "$files" | sed 's/^/        /'
        echo "      leaks were: $leaks"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== personal-artefact-leak-check self-test ==="
echo "Denylist: $DENYLIST"
echo ""

# Case 1: clean PR (skill addition only) — should pass
run_case "clean: new skill only" \
    "skills/new-feature/SKILL.md
skills/new-feature/handler.py" \
    "no"

# Case 2: genome leak — should fail
run_case "leak: data/genome-shared.json" \
    "data/genome-shared.json
skills/foo/SKILL.md" \
    "yes" "data/genome-shared.json"

# Case 3: KONO embeddings leak — should fail
run_case "leak: embeddings.db" \
    "embeddings.db" \
    "yes" "embeddings.db"

# Case 4: glob pattern (guild-graph-*) — should fail
run_case "leak: guild-graph glob" \
    "data/guild-graph-1234567890.json" \
    "yes" "guild-graph"

# Case 5: directory pattern (state/session/) — should fail
run_case "leak: state/session/ directory" \
    "state/session/yolo-mode.json" \
    "yes" "state/session"

# Case 6: legitimate state dir not on denylist — should pass
run_case "clean: state/queue/ allowed" \
    "state/queue/pending/task-001.md" \
    "no"

# Case 7: drafts NOT on denylist — should pass
# (we removed drafts/ from denylist intentionally so the cookbook can ship)
run_case "clean: drafts/cookbook allowed" \
    "drafts/grip-cookbook-2026-05-01.md" \
    "no"

# Case 8: settings.local.json — should fail
run_case "leak: settings.local.json" \
    "settings.local.json" \
    "yes" "settings.local"

# Case 9: rules/session-context.md (defensive) — should fail
run_case "leak: rules/session-context.md" \
    "rules/session-context.md" \
    "yes" "session-context"

# Case 10: legitimate rules edit — should pass
run_case "clean: rules/efficiency-rules.md allowed" \
    "rules/efficiency-rules.md" \
    "no"

echo ""
echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
echo "✓ all leak-check cases pass"
