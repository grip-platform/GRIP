#!/usr/bin/env bash
# vps-service-watchdog.sh — generic pm2-service uptime watchdog with Slack alerting.
#
# Probes a service health URL from cron. On sustained failure (FAIL_THRESHOLD
# consecutive probes) it restarts the pm2 process ONCE per outage and posts ONE
# plain-English DOWN alert to Slack; on recovery it posts ONE RECOVERY alert.
# Silent on healthy ticks. The sibling hal-liveness/happi-api-liveness scripts
# (root cron, restart-only, no alerting, log every OK minute) can migrate to
# this parametrised successor.
#
# Slack delivery goes through the canonical shim lib/slack_transport.py
# (syscall doctrine): token resolution happens inside the shim's process scope
# — this script carries no token references at all.
#
# Usage (cron, as the grip user):
#   vps-service-watchdog.sh <label> <health_url> [pm2_name] [expect_body_substring]
# Example crontab line (existing, nexus-backend):
#   * * * * * ~/.claude/scripts/vps-service-watchdog.sh \
#     nexus-backend http://127.0.0.1:8100/health nexus-backend '"status":"ok"' \
#     >> ~/.claude/logs/nexus-backend-watchdog.log 2>&1
# Example crontab line (hal-server — same mechanism, a different target; no
# script change needed, only this new line). The expect-body substring is
# matched byte-for-byte, so it keeps the real spaces hal-server's own
# /api/health serialises after every colon and comma — a compact,
# no-space version of this string would never match and the watchdog would
# report DOWN forever:
#   * * * * * ~/.claude/scripts/vps-service-watchdog.sh \
#     hal-server http://127.0.0.1:3850/api/health hal-server \
#     '"name": "hal_home", "status": "ok"' \
#     >> ~/.claude/logs/hal-server-watchdog.log 2>&1
#
# Env overrides (all optional):
#   WATCHDOG_STATE_DIR      state dir  (default $HOME/.claude/state/service-watchdog)
#   WATCHDOG_ENV_FILE       daemon env file exported before the Slack shim runs
#                           (default $HOME/.claude/.grip-channel.env)
#   WATCHDOG_SLACK_CHANNEL  Slack channel ID (default C0EXAMPLE01 — #daily-chat)
#   WATCHDOG_SLACK_CMD      test seam: command invoked as `cmd <channel> <text>`
#                           instead of the python shim (DIP; used by pytest).
#                           If the value is a single executable (by path or on
#                           PATH), runs it unsplit so paths with spaces work.
#                           Otherwise word-splits on whitespace — the same
#                           contract git gives GIT_SSH_COMMAND — so a stub can
#                           carry its own fixed arguments (e.g. "/bin/echo -n").
#   WATCHDOG_FAIL_THRESHOLD consecutive failures before alert+restart (default 2)
#   WATCHDOG_CURL_TIMEOUT   per-probe timeout seconds (default 10)
#   WATCHDOG_PM2            pm2 binary (default: pm2 from PATH)
#   WATCHDOG_HOST_LABEL     host name shown in alerts (default: hostname)
#
# Alert-delivery contract: the DOWN/RECOVERY state only advances when the Slack
# post succeeds, so a failed post is retried on the next tick (at-least-once).
# The pm2 restart fires once per outage regardless of Slack availability.
set -u

LABEL="${1:?usage: vps-service-watchdog.sh <label> <health_url> [pm2_name] [expect_body]}"
HEALTH_URL="${2:?health_url required}"
PM2_NAME="${3:-$LABEL}"
EXPECT_BODY="${4:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${WATCHDOG_STATE_DIR:-$HOME/.claude/state/service-watchdog}"
ENV_FILE="${WATCHDOG_ENV_FILE:-$HOME/.claude/.grip-channel.env}"
SLACK_CHANNEL="${WATCHDOG_SLACK_CHANNEL:-C0EXAMPLE01}"
FAIL_THRESHOLD="${WATCHDOG_FAIL_THRESHOLD:-2}"
CURL_TIMEOUT="${WATCHDOG_CURL_TIMEOUT:-10}"
PM2_BIN="${WATCHDOG_PM2:-pm2}"
HOST_LABEL="${WATCHDOG_HOST_LABEL:-$(hostname 2>/dev/null || echo VPS)}"

STATE_FILE="$STATE_DIR/${LABEL}.state"
mkdir -p "$STATE_DIR"

# --- state (flat key=value, script-written only) -----------------------------
STATUS=up FAILS=0 DOWN_SINCE=0 ALERTED=0 RESTARTED=0
# shellcheck disable=SC1090
[ -f "$STATE_FILE" ] && . "$STATE_FILE"

save_state() {
    printf 'STATUS=%s\nFAILS=%s\nDOWN_SINCE=%s\nALERTED=%s\nRESTARTED=%s\n' \
        "$STATUS" "$FAILS" "$DOWN_SINCE" "$ALERTED" "$RESTARTED" > "$STATE_FILE"
}

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

slack_post() {
    # $1 = message text. Returns 0 only when the post is confirmed delivered.
    if [ -n "${WATCHDOG_SLACK_CMD:-}" ]; then
        # Two modes: if the whole value is one executable (by path or on
        # PATH), run it unsplit so a path containing spaces is preserved
        # (e.g. "/tmp/grip watchdog/post.sh"). Otherwise word-split it so
        # a multi-word value like "/bin/echo -n" can carry its own fixed
        # arguments (the same contract git gives GIT_SSH_COMMAND).
        if [ -x "$WATCHDOG_SLACK_CMD" ] || command -v -- "$WATCHDOG_SLACK_CMD" >/dev/null 2>&1; then
            "$WATCHDOG_SLACK_CMD" "$SLACK_CHANNEL" "$1"
        else
            local -a slack_cmd_argv
            read -ra slack_cmd_argv <<< "$WATCHDOG_SLACK_CMD"
            "${slack_cmd_argv[@]}" "$SLACK_CHANNEL" "$1"
        fi
        return $?
    fi
    # Export the daemon env file so the shim's env fallback can resolve the
    # bot token inside its own process scope (Linux host: no keychain).
    if [ -f "$ENV_FILE" ]; then
        set -a
        # shellcheck disable=SC1090
        . "$ENV_FILE"
        set +a
    fi
    PYTHONPATH="$CLAUDE_DIR" python3 -c '
import sys
from lib.slack_transport import post
result = post(workspace="codetonight", channel_id=sys.argv[1], text=sys.argv[2])
sys.exit(0 if result.get("ok") else 1)
' "$SLACK_CHANNEL" "$1" 2>&1 | head -3
    return "${PIPESTATUS[0]:-1}"
}

# --- probe -------------------------------------------------------------------
BODY_TMP=$(mktemp "${TMPDIR:-/tmp}/watchdog-body.XXXXXX")
trap 'rm -f "$BODY_TMP"' EXIT
CODE=$(curl -s -m "$CURL_TIMEOUT" -o "$BODY_TMP" -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo 000)

HEALTHY=0
if [ "$CODE" = "200" ]; then
    if [ -z "$EXPECT_BODY" ] || grep -Fq -- "$EXPECT_BODY" "$BODY_TMP"; then
        HEALTHY=1
    fi
fi

# --- state machine -----------------------------------------------------------
if [ "$HEALTHY" = "1" ]; then
    if [ "$STATUS" = "down" ] && [ "$ALERTED" = "1" ]; then
        mins=$(( ( $(date -u +%s) - DOWN_SINCE ) / 60 ))
        if slack_post ":large_green_circle: GRIP VPS watchdog: *${LABEL}* on ${HOST_LABEL} has RECOVERED — health check passing again after ~${mins} min down."; then
            log "RECOVERED after ~${mins}m — recovery alert posted"
            STATUS=up FAILS=0 DOWN_SINCE=0 ALERTED=0 RESTARTED=0
        else
            log "RECOVERED but recovery alert failed — will retry next tick"
        fi
    else
        # healthy, or a sub-threshold blip that never alerted: quiet reset
        STATUS=up FAILS=0 DOWN_SINCE=0 ALERTED=0 RESTARTED=0
    fi
else
    FAILS=$((FAILS + 1))
    [ "$DOWN_SINCE" = "0" ] && DOWN_SINCE=$(date -u +%s)
    log "probe FAIL code=$CODE fails=$FAILS/$FAIL_THRESHOLD"
    if [ "$FAILS" -ge "$FAIL_THRESHOLD" ]; then
        STATUS=down
        if [ "$RESTARTED" = "0" ]; then
            log "threshold reached — pm2 restart $PM2_NAME (once per outage)"
            # Not if/then/else: both arms only log, so a log failure after a
            # successful restart changes nothing the state machine or an
            # operator reading the log cares about; RESTARTED=1 is set
            # unconditionally on the next line regardless of which arm ran.
            # shellcheck disable=SC2015
            "$PM2_BIN" restart "$PM2_NAME" >/dev/null 2>&1 \
                && log "pm2 restart issued" || log "ERROR pm2 restart failed"
            RESTARTED=1
        fi
        if [ "$ALERTED" = "0" ]; then
            if slack_post ":red_circle: GRIP VPS watchdog: *${LABEL}* on ${HOST_LABEL} is DOWN — health check (${HEALTH_URL}) failed ${FAILS} consecutive times (last HTTP ${CODE}). An automatic pm2 restart was attempted; a recovery message will follow when it is back."; then
                ALERTED=1
                log "DOWN alert posted to $SLACK_CHANNEL"
            fi
        fi
    fi
fi

save_state
