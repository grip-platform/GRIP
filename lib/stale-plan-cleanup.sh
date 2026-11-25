#!/usr/bin/env bash
#
# stale-plan-cleanup.sh -- remove stale plan files that git does not track.
#
# Usage:
#   source "$LIB_DIR/stale-plan-cleanup.sh"
#   remove_stale_untracked_plans "$CLAUDE_DIR/plans" [days]
#
# Claude Code writes its plan-mode files into $CLAUDE_DIR/plans, where they pile
# up, so hooks/session-end.sh removes the ones older than a week. When CLAUDE_DIR
# is the GRIP repository itself (~/.claude links to ~/.grip), plans/ also holds
# committed documents, and the old one-line `find ... -delete` removed those too:
# twelve tracked plans vanished from a Windows checkout on 2026-10-06, and a
# commit of a shared tree's pending changes (GRIP#7570) carried three such
# deletions into main. A file git tracks is a document, so it is kept.
# Everything else keeps the old rule, including every file outside a repository.

remove_stale_untracked_plans() {
    local plans_dir="${1:-}" days="${2:-7}" f
    [[ -d "$plans_dir" ]] || return 0
    # find runs from inside plans_dir, so every path is relative to it. git reads
    # a relative pathspec against its -C directory, and a path that began with a
    # RELATIVE plans_dir ("plans/x.md" under -C plans) matched no index entry, so
    # a tracked plan was deleted after all.
    while IFS= read -r -d '' f; do
        # Tracked means committed: git manages it, this cleanup does not.
        if git -C "$plans_dir" ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
            continue
        fi
        rm -f -- "$plans_dir/$f"
    done < <(cd "$plans_dir" && find . -name '*.md' -mtime +"$days" -print0 2>/dev/null)
    return 0
}
