---
name: remind-me
description: "Durable operator reminders — \"remind me later/at X about Y\" arms TWO independent backends: the always-on VPS (grip-cloud-remind over ssh, systemd one-shot -> Slack DM + Discord via the relay bots; fires even when the Mac is off) and the Mac's launchd (grip-remind, WhatsApp + local notification). These are SEPARATE tools with SEPARATE ids — grip-remind is local-only and does NOT arm the cloud. Use when the operator says \"remind me\", \"ping me at/in\", \"don't let me forget\", or asks for a nudge. NEVER satisfied by the in-session scheduler alone (session-only; its durable flag is silently ignored on CC 2.1.202) — the in-session cron is at most an optional same-session echo."
version: 2.2.0
---

# /remind-me — durable reminders

One law: **a reminder the operator asked for must not die with the session —
nor with the operator's Mac.** Origin 2026-07-15 (session-only scheduler), and
hardened 2026-08-19: the Mac was OFF at fire time and a launchd-only reminder
silently missed, so the always-on VPS tier was added.

Corrected 2026-09-28 (v2.2.0): the two tiers are NOT one tool. `grip-remind`
is **local-only** (its `--help` says so: "There is no third backend"). The
cloud tier is a SEPARATE tool on the VPS (`/usr/local/bin/grip-cloud-remind`),
armed over ssh. The old "arms BOTH in one call" claim was wrong and is gone.

## Tiers (pick top-down)

| Tier | Mechanism | Survives | Use |
|------|-----------|----------|-----|
| 1 (DEFAULT) | Arm BOTH, as two separate calls: (a) `grip-remind "msg" "HH:MM"` — Mac launchd -> WhatsApp GRIP-prefixed + local notification; and (b) `ssh root@grip-remote-v2 /usr/local/bin/grip-cloud-remind arm "msg" "YYYY-MM-DD HH:MM"` — VPS systemd one-shot -> operator's Slack DM AND the Discord GRIP guild channel via the relay bots. Two ids; each tool lists/cancels its own. | Mac off entirely, session exit, reboot | every operator reminder — the Slack DM is the guarantee |
| 2 (local-only) | `grip-remind "msg" "HH:MM"` alone (skip the ssh cloud arm) | sleeps fire on wake; Mac OFF at fire time MISSES it | only when the operator explicitly asks for Mac-local only |
| 3 (in-session echo) | in-session CronCreate one-shot, same content | nothing — dies with the session | never presented as durable; only IN ADDITION to tier 1 when the operator is likely still in this session at fire time |

## Protocol

1. **Parse the ask** — message + time. Relative ("in 2 hours", "later tonight") → compute a concrete LOCAL time; apply the off-minute discipline (avoid :00/:30 when the ask is approximate). "later" with no anchor → pick a sensible slot in the operator's active hours and SAY which you picked.
2. **Arm local**: `~/.claude/bin/grip-remind "message" "HH:MM"` (or `"YYYY-MM-DD HH:MM"`; a bare past-today HH:MM rolls to tomorrow and the tool says so). The third positional arg overrides the WhatsApp recipient (defaults to the operator). This arms launchd ONLY.
3. **Arm cloud (separate call)**: `ssh root@grip-remote-v2 /usr/local/bin/grip-cloud-remind arm "message" "YYYY-MM-DD HH:MM"`. The VPS tool takes its time input as **SAST (GMT+2)** and converts to UTC internally — pass the SAST wall-clock time, NOT UTC (verified 2026-09-28: input "07:05" fired at 05:05 UTC, two hours early; the correct input for 09:05 SAST is "09:05"). The message should be the same self-contained plain English.
4. **Confirm from the TOOLS' OUTPUT** (never self-report): relay both armed ids + fire times + the honest boundaries — the VPS cloud tier fires even with the Mac off (Slack DM guaranteed; Discord once its leg is live — see Discord note); WhatsApp/local notification fire only if the Mac is on at fire time. If the ssh cloud arm fails (VPS unreachable, no keys), SAY SO plainly: the reminder is then Mac-local only.
5. **Reminder content discipline**: the fired message reaches the operator cold — full plain English (Rule 17 surface), self-contained, internal numbers glossed. For "remind me about the remaining tasks" asks, the message should instruct RE-VERIFYING live state rather than embedding a stale snapshot.
6. **Manage**: local — `grip-remind --list` / `grip-remind --cancel <id>`; cloud — `ssh root@grip-remote-v2 /usr/local/bin/grip-cloud-remind list` / `... cancel <id>`. The two stores are separate; cancelling one does not cancel the other.

## Discord (LIVE since 2026-08-19)

The cloud tier posts to BOTH Slack and Discord, proven end-to-end
(slack ok + discord ok in one fire). Discord leg: the GRIP guild (server
1420385411376746689), bot renamed GRIP-GUILD -> GRIP (app 1486499846990074018),
posting to the guild's system channel (#ai-tests) THROUGH the residential
SOCKS tunnel (127.0.0.1:1081 -> your-mac) because Discord's
Cloudflare ban (error 1010) blocks the VPS's datacenter IP. Token lives
root-only at /var/lib/grip-remind/discord.token; the channel override at
/var/lib/grip-remind/discord.channel. If the token is ever reset again, one
command re-activates:

`ssh root@grip-remote-v2 /usr/local/bin/grip-cloud-remind-activate-discord <fresh-token>`

## Honesty rails

- The confirmation states delivery surfaces per channel: Slack DM + Discord (cloud, Mac-independent) vs WhatsApp + local notification (Mac-local echo). Never claim unconditional delivery.
- The in-session scheduler is NEVER presented as durable; if used as an echo, name it as the ephemeral tier.
- E2E-verified 2026-07-15 (launchd -> WhatsApp, self-cleaned) and 2026-08-19 (VPS systemd -> Slack DM ok:true, dual-channel fire script, tokens read from the relay env at fire time, payload retained on a full miss).

## Falsification

Wrong if: VPS systemd one-shots prove less reliable than launchd over ≥10
reminders (then the tier order inverts); or Slack DM delivery misses
repeatedly (then re-wire the cloud tier through another relay surface); or
CC's `durable` cron flag starts actually persisting (retest each CC upgrade);
or `grip-remind` gains a real cloud backend (then this two-tool split is
stale and the tiers collapse back to one call).
