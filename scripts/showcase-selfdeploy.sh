#!/usr/bin/env bash
# showcase-selfdeploy.sh — VPS-resident self-pull for the generalised
# showcase tracker. Mirrors scripts/relay-selfdeploy.sh.
#
# Runs on the VPS as the `grip` user from cron (every 3 minutes). No human,
# no laptop, no inbound SSH ever required for ongoing updates. Same three
# safety properties:
#
#   1. IMPORT-GATE  — before any pm2 restart, `python3 -c "from
#      lib.showcase.server import main"` MUST succeed. py_compile only
#      validates syntax and would miss a missing transitive dep.
#   2. IDEMPOTENCE  — content sha over the canonical path set vs the last
#      successfully-deployed sha. Unchanged → exit 0 immediately.
#   3. FAIL-SAFE    — if the import-gate fails after checkout, the prior
#      working files are RESTORED and the pm2 service is NOT restarted.
#
# Install (one-time, on the VPS as the grip user):
#   (crontab -l 2>/dev/null; echo \
#     '*/3 * * * * ~/.claude/scripts/showcase-selfdeploy.sh >> ~/.claude/logs/showcase-selfdeploy.log 2>&1') \
#     | crontab -
#
# Exit codes: 0 = up-to-date OR deployed-and-healthy; 1 = import-gate failed
# (rolled back, service untouched); 2 = environment/precondition error.
set -euo pipefail

CD="${SHOWCASE_CLAUDE_CWD:-~/.claude}"
STATE_DIR="$CD/state/showcase-selfdeploy"
LAST_SHA_FILE="$STATE_DIR/selfdeploy-last-sha.txt"
LOCK_FILE="$STATE_DIR/selfdeploy.lock"

# Canonical path set (dynamic — auto-includes any newly-added lib/showcase/*.py).
# Anchored regex is the single source of truth for "is a canonical showcase file".
SHOWCASE_PATH_RE='^(lib/showcase/[A-Za-z0-9_]+\.py|bin/showcase-server|config/showcase-server\.config\.cjs)$'
SHOWCASE_TREES=(lib config bin)

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[selfdeploy $(ts)] $*"; }
die() { log "ERROR: $*"; exit 2; }

[ -d "$CD/.git" ] || die "no git repo at $CD"
command -v git >/dev/null 2>&1 || die "git not on PATH"
command -v python3 >/dev/null 2>&1 || die "python3 not on PATH"
mkdir -p "$STATE_DIR" "$CD/logs"

# Stale-lock guard: a SIGKILL'd run normally releases the flock, but a hung
# holder or filesystem quirk could wedge every subsequent tick forever.
LOCK_STALE_MIN="${SHOWCASE_LOCK_STALE_MIN:-15}"
if [ -f "$LOCK_FILE" ]; then
  find "$LOCK_FILE" -mmin "+${LOCK_STALE_MIN}" -delete 2>/dev/null \
    && log "removed stale lock (> ${LOCK_STALE_MIN}m old) — self-healing"
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another selfdeploy run holds the lock — skipping this tick"
  exit 0
fi

git -C "$CD" fetch origin main --quiet || die "git fetch origin main failed"

# Dynamic canonical path resolution from origin/main (not the working tree).
LSTREE_OUT="$(git -C "$CD" ls-tree -r origin/main --name-only -- \
  "${SHOWCASE_TREES[@]}")" \
  || die "git ls-tree of origin/main failed — refusing to deploy"
mapfile -t PATHS < <(printf '%s\n' "$LSTREE_OUT" | grep -E "$SHOWCASE_PATH_RE" | sort)

# Sanity floor: 6 showcase modules + bin/showcase-server + config = 8.
[ "${#PATHS[@]}" -ge 8 ] \
  || die "only ${#PATHS[@]} canonical showcase paths resolved from origin/main \
(expected >=8: __init__ + 5 modules + bin/showcase-server + config) \
— refusing to deploy a partial set"
log "resolved ${#PATHS[@]} canonical showcase paths from origin/main"

# Idempotence: sha over origin/main blobs of the resolved path set.
NEW_SHA="$(
  for p in "${PATHS[@]}"; do
    git -C "$CD" rev-parse "origin/main:$p" \
      || { echo "REVPARSE_FAILED:$p" >&2; exit 7; }
  done | sha256sum | awk '{print $1}'
)" || die "git rev-parse failed for a canonical path — aborting"
OLD_SHA="$(cat "$LAST_SHA_FILE" 2>/dev/null || echo "")"

if [ "$NEW_SHA" = "$OLD_SHA" ]; then
  log "canonical path set unchanged (sha ${NEW_SHA:0:12}) — nothing to deploy"
  exit 0
fi
log "canonical change detected (old=${OLD_SHA:0:12} new=${NEW_SHA:0:12}) — deploying"

# Back up current working copies for rollback.
BK="$(mktemp -d "${TMPDIR:-/tmp}/showcase-selfdeploy-bk.XXXXXX")"
trap 'rm -rf "$BK"' EXIT
for p in "${PATHS[@]}"; do
  if [ -f "$CD/$p" ]; then
    mkdir -p "$BK/$(dirname "$p")"
    cp -f "$CD/$p" "$BK/$p"
  fi
done

restore() {
  log "restoring prior files (fail-safe rollback — service NOT restarted)"
  for p in "${PATHS[@]}"; do
    [ -f "$BK/$p" ] && cp -f "$BK/$p" "$CD/$p"
  done
}

# Surgical checkout — never a broad `git pull` (VPS tree carries local mods).
if ! git -C "$CD" checkout origin/main -- "${PATHS[@]}"; then
  restore
  die "git checkout of canonical paths failed (rolled back)"
fi

# bin/showcase-server needs +x (preserved by git, defensive).
chmod +x "$CD/bin/showcase-server" 2>/dev/null || true

# IMPORT-GATE — resolve imports, not just syntax. Run from $CD so
# `lib.showcase.server` resolves as a package import.
if ! ( cd "$CD" && PYTHONPATH="$CD" python3 -c "from lib.showcase.server import main" ) 2>/dev/null; then
  restore
  log "IMPORT-GATE FAILED — lib.showcase.server does not import cleanly"
  exit 1
fi
log "import-gate passed"

# Only now restart the pm2 service.
if command -v pm2 >/dev/null 2>&1; then
  pm2 delete showcase >/dev/null 2>&1 || true
  ( cd "$CD" && pm2 start config/showcase-server.config.cjs >/dev/null )
  pm2 save >/dev/null 2>&1 || true
  log "showcase service restarted via canonical pm2 config"
else
  log "pm2 not found — files updated, manual restart required"
fi

# Persist deployed sha only AFTER healthy restart.
echo "$NEW_SHA" > "$LAST_SHA_FILE"
log "deploy complete — canonical sha ${NEW_SHA:0:12} recorded"
exit 0
