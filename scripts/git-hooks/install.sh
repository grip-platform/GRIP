#!/usr/bin/env bash
# Install GRIP's tracked git hooks into .git/hooks (copy, not symlink — portable
# to Windows/Git-Bash where the CodeTonight team runs). Idempotent. Re-run after
# updating any tracked hook. Origin 2026-06-27 (settings.json corruption gate).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/scripts/git-hooks"
DST="$ROOT/.git/hooks"
mkdir -p "$DST"
for hook in "$SRC"/*; do
  name="$(basename "$hook")"
  [ "$name" = "install.sh" ] && continue
  cp "$hook" "$DST/$name"
  chmod +x "$DST/$name"
  echo "installed: .git/hooks/$name"
done
