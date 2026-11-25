#!/usr/bin/env bash
# happiverse-advance.sh — move ONE machine's GRIP checkout forward to main.
#
#   bash happiverse-advance.sh <repo> <main-tip-sha>
#
# Runs ON the machine being updated. lib/happiverse_keep_level.py pushes main's
# commits into the machine first, then sends this script over SSH on stdin, so
# the machine always runs the current copy rather than whatever version its own
# checkout holds. Written for the oldest shell and git in the fleet: bash 3.2 and
# git 2.16, both on ol ancient.
#
# It never forces anything, and prints exactly one RESULT line:
#   RESULT ALREADY <head>                    HEAD already contains main
#   RESULT UPDATED <old> <new> reset=N carried_lines=N restored=N dropped=N
#   RESULT BLOCKED <reason>                  the tree is left as it was found
#   RESULT MISSING-TIP                       main's commit never arrived
#   RESULT NO-REPO <reason>
#
# Local changes are handled these ways, and only these:
#   * a changed file whose content ALREADY EQUALS main's is reset first, because
#     the merge writes the same bytes. The VPS self-deploy stages main's relay
#     files exactly this way, and those files blocked every merge until reset.
#   * a changed file that main also changes, whose bytes are an OLDER version of
#     that file on main (git log --find-object finds them), or a tracked file
#     deleted here: restored to HEAD before the merge, because it holds no bytes
#     main's history lacks. the second Mac sat 888 commits behind for nine days
#     (2026-10-08 to 2026-10-17) on 13 such copies and 43 such deletions.
#   * an UNTRACKED file at a path main adds, holding exactly main's bytes: set
#     aside for the merge, which writes the same bytes. Different bytes block.
#   * a skip-worktree ledger (learning/*.jsonl, .mcp.json, flagged by
#     scripts/setup-local-git.sh) keeps the lines the machine appended itself:
#     they are carried onto main's version. Lines main has since removed are
#     NOT brought back, because only lines absent from the pre-merge HEAD count.
#   * any other local change that main also changes: BLOCKED, untouched.
set -u

# A non-interactive SSH session gets a minimal PATH, and git-crypt's filters
# (the smudge wrapper calls `git-crypt` by name) live in Homebrew's prefix. Without
# this the merge fails on the first encrypted file main changes (the second Mac,
# 2026-10-17). /usr/local/bin also keeps ol ancient's git 2.16 ahead of the
# broken /usr/bin/git shim there.
PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PATH

repo="${1:?usage: happiverse-advance.sh <repo> <main-tip-sha>}"
tip="${2:?usage: happiverse-advance.sh <repo> <main-tip-sha>}"

cd "$repo" 2>/dev/null || { echo "RESULT NO-REPO cannot cd to $repo"; exit 0; }
before=$(git rev-parse --verify -q HEAD) || { echo "RESULT NO-REPO no HEAD in $repo"; exit 0; }
git cat-file -e "${tip}^{commit}" 2>/dev/null || { echo "RESULT MISSING-TIP"; exit 0; }
if git merge-base --is-ancestor "$tip" HEAD; then
  echo "RESULT ALREADY $before"
  exit 0
fi
if [ -n "$(git ls-files -u)" ]; then
  echo "RESULT BLOCKED unmerged paths are present"
  exit 0
fi
if [ -f "$(git rev-parse --git-dir)/MERGE_HEAD" ]; then
  echo "RESULT BLOCKED a merge is already in progress"
  exit 0
fi

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t happiverse-advance) || { echo "RESULT BLOCKED no temp directory"; exit 0; }
trap 'rm -rf "$tmp"' EXIT
git diff --name-only "HEAD...$tip" > "$tmp/incoming"
git diff --name-only --diff-filter=A "HEAD...$tip" > "$tmp/added"
git ls-files -v | sed -n 's/^S //p' > "$tmp/flagged"
git diff --name-only HEAD > "$tmp/dirty"
: > "$tmp/reset"
: > "$tmp/restore"
: > "$tmp/drop"
: > "$tmp/carry"

touched() { grep -qxF -- "$1" "$tmp/incoming"; }
blob_of() { git hash-object --path="$1" "$1"; }
equals_main() { [ -f "$1" ] && [ "$(blob_of "$1")" = "$(git rev-parse -q --verify "$tip:$1" 2>/dev/null)" ]; }
# A file whose bytes are some version of it on main: restoring it loses nothing.
older_main() { [ -f "$1" ] && [ -n "$(git log "$tip" --format=%H -1 --find-object="$(blob_of "$1")" -- "$1" 2>/dev/null)" ]; }
deleted_here() { [ ! -e "$1" ] && git cat-file -e "HEAD:$1" 2>/dev/null; }
tracked() { git ls-files --error-unmatch -- "$1" >/dev/null 2>&1; }

# Pass 1 decides and touches nothing, so a BLOCKED run leaves the tree exactly as found.
while IFS= read -r f; do
  touched "$f" || continue
  if equals_main "$f"; then
    echo "$f" >> "$tmp/reset"
  elif deleted_here "$f" || older_main "$f"; then
    echo "$f" >> "$tmp/restore"
  else
    echo "RESULT BLOCKED local change to $f differs from what main brings"
    exit 0
  fi
done < "$tmp/dirty"
while IFS= read -r f; do
  [ -e "$f" ] || continue
  tracked "$f" && continue
  if equals_main "$f"; then
    echo "$f" >> "$tmp/drop"
  else
    echo "RESULT BLOCKED untracked file $f differs from what main adds"
    exit 0
  fi
done < "$tmp/added"
while IFS= read -r f; do
  if touched "$f" && [ -f "$f" ]; then echo "$f" >> "$tmp/carry"; fi
done < "$tmp/flagged"

# Pass 2 acts. Restored and set-aside files keep a copy, so a failed merge puts
# them back exactly as they were found.
while IFS= read -r f; do git checkout -q HEAD -- "$f"; done < "$tmp/reset"
n=0
while IFS= read -r f; do
  n=$((n + 1))
  if [ -e "$f" ]; then cp -p "$f" "$tmp/kept.$n"; fi
  git checkout -q HEAD -- "$f"
done < "$tmp/restore"
n=0
while IFS= read -r f; do
  n=$((n + 1))
  mv "$f" "$tmp/drop.$n"
done < "$tmp/drop"
n=0
while IFS= read -r f; do
  n=$((n + 1))
  cp -p "$f" "$tmp/local.$n"
  git show "$before:$f" > "$tmp/base.$n" 2>/dev/null || : > "$tmp/base.$n"
  git update-index --no-skip-worktree -- "$f"
  git checkout -q -- "$f"
done < "$tmp/carry"

ident=()
git config user.email >/dev/null 2>&1 || ident=(-c user.name=GRIP-keep-level -c user.email=keep-level@localhost)
if git merge-base --is-ancestor HEAD "$tip"; then
  git merge -q --ff-only "$tip" > "$tmp/out" 2>&1
  rc=$?
else
  git ${ident[@]+"${ident[@]}"} merge -q --no-edit "$tip" > "$tmp/out" 2>&1
  rc=$?
  [ "$rc" -eq 0 ] || git merge --abort >/dev/null 2>&1
fi

# The ledgers go back whatever happened: the machine's own lines onto whichever
# version is now on disk, then the flag, so services keep appending unseen.
n=0
lines=0
while IFS= read -r f; do
  n=$((n + 1))
  awk 'NR == FNR { base[$0] = 1; next } !($0 in base)' "$tmp/base.$n" "$tmp/local.$n" > "$tmp/added.$n"
  awk 'NR == FNR { have[$0] = 1; next } !($0 in have)' "$f" "$tmp/added.$n" > "$tmp/new.$n"
  if [ -s "$f" ] && [ "$(tail -c1 "$f" | wc -l)" -eq 0 ]; then echo >> "$f"; fi
  cat "$tmp/new.$n" >> "$f"
  git update-index --skip-worktree -- "$f"
  lines=$((lines + $(wc -l < "$tmp/new.$n")))
done < "$tmp/carry"

if [ "$rc" -ne 0 ]; then
  # Put the reset files back as main's version, staged — how they were found.
  while IFS= read -r f; do git checkout -q "$tip" -- "$f"; done < "$tmp/reset"
  # Restored files go back to their own bytes, or absent if deleted here.
  n=0
  while IFS= read -r f; do
    n=$((n + 1))
    if [ -e "$tmp/kept.$n" ]; then cp -p "$tmp/kept.$n" "$f"; else rm -f -- "$f"; fi
  done < "$tmp/restore"
  # Set-aside untracked files return to where they were.
  n=0
  while IFS= read -r f; do
    n=$((n + 1))
    mkdir -p "$(dirname "$f")" && mv "$tmp/drop.$n" "$f"
  done < "$tmp/drop"
  echo "RESULT BLOCKED merge failed: $(head -1 "$tmp/out")"
  exit 0
fi
echo "RESULT UPDATED $before $(git rev-parse HEAD) reset=$(wc -l < "$tmp/reset" | tr -d ' ') carried_lines=$lines restored=$(wc -l < "$tmp/restore" | tr -d ' ') dropped=$(wc -l < "$tmp/drop" | tr -d ' ')"
