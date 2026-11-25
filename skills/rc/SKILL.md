---
name: rc
description: Remote-control THIS Claude Code session from your phone — continue your CURRENT conversation (with its context) over WhatsApp/Slack/Discord, not a blank remote sub-session. Thin operator verb over the already-built operator-session fork routing + grip-channel relay; reimplements nothing. Use for "/rc", "remote control", "continue on phone", "control this session from my phone", "drive this session from WhatsApp", "pick this up on my phone". Honest by design — if the session stamp is stale or the relay is down, it says so and degrades to a cold per-channel session rather than faking continuity.
effort: low
status: Active
version: 1.0.0
see_also:
  - grip-channel
  - afk
  - send-to-phone
---

# /rc — Remote-Control This Session From Your Phone (with context)

## What it is (plain language)

You're at your desk mid-flow — files open, a decision just made, a half-finished
PR. You need to step onto a train. `/rc` lets you keep driving **this same
session** from WhatsApp (or Slack/Discord): you message the bot and GRIP answers
**inside your current conversation's context**, not a cold blank session that has
no idea what you were doing.

It **invents nothing**. It arms and points at machinery that already exists:

1. **The stamp** — `hooks/operator-session-tracker.py` (SessionStart, wired) writes
   your live `CLAUDE_SESSION_ID` to `state/relay/operator-session-id.txt`.
2. **The fork routing** — `lib/relay_operator_session.py` reads that stamp (8h TTL,
   fail-closed) and, when enabled, makes the VPS relay spawn
   `claude -p --resume <your-sid> --fork-session` — forking **your** history
   without contending for your live REPL's session lock.
3. **The relay** — grip-channel (Mac WhatsApp daemon `:3101`, VPS Slack+Discord)
   carries the message to `relay-dispatcher` and the reply back to your chat.

`/rc` is the **one operator verb** that arms this, checks it's real, and tells you
where to message.

## Usage

| Command | Effect |
|---|---|
| `/rc` | Verify your session stamp is fresh → arm operator-session fork routing → confirm the relay is live → print the chat to message. |
| `/rc status` | Show stamp freshness (age vs 8h TTL), whether routing is armed, and the relay state — no change. |
| `/rc off` | Disarm: the relay reverts to per-channel sub-sessions (a phone message starts a fresh remote session, not your context). |

Pure skill — no binary, no new files. The four steps below run inline (the
fork-routing, atomic stamp, TTL fail-close, and capability lock are all pre-built
and tested; `/rc` re-implements none of them).

## The four steps (all over existing infra)

1. **Verify the stamp is fresh** —
   `PYTHONPATH=$HOME/.claude python3 -c "import relay_operator_session as r; print(r.read_active_session_id() or 'STALE')"`.
   - Returns a session id → your foreground session is forkable. Continue.
   - Returns `STALE`/`None` → surface *why* (no stamp / TTL expired) and **stop** —
     never claim armed-with-context when the stamp is dead. See "Known gaps".
2. **Arm the routing** — a LOCAL marker flip (task #405; no SSH, no env edit,
   no restart — the consumer reads it fresh per spawn):
   `PYTHONPATH=$HOME/.claude/lib python3 -c "import relay_operator_session as r; print(r.arm(source='skill'))"`.
   `/rc off` → `…r.disarm()`. From the phone (operator-only, sender-ID
   authenticated): text `GRIP: rc on` / `GRIP: rc off` / `GRIP: rc status` to
   the bot. The marker arms the consumer ON THE HOST THAT RECEIVES the
   message (Mac drainer for WhatsApp — the host that holds your transcripts,
   so the fork actually has your context). The legacy deployment env switch
   `GRIP_RELAY_OPERATOR_SESSION_ROUTING=1` still works and OVERRIDES a
   marker-`off` (it is deployment-owned; unset it in the relay's env to fully
   disable).
3. **Confirm the relay consumer is live** — the fork only happens if
   `GRIP_RELAY_WEBHOOK_URL` is set and `relay-dispatcher` is listening. Probe it;
   if it's down, say so plainly (verify-canonical — never assert a working phone
   link that isn't).
4. **Print the target chat** — the allowlisted JID/channel to message
   (WhatsApp: V>>'s own chat, the operator ID in `config/wa-identities.local.json`, or the Slack/Discord surface per
   `rules/operational-context.md`). Message that chat and your next reply forks
   this session with its context.

The reply on your phone is capability-locked exactly as the relay always is
(`--tools "Read,Grep,Glob"` unless the dispatcher config widens it). `/rc` changes
the **resume target**, never the **capability surface**
(`rules/untrusted-content-capability-lock.md`).

## Operator-only fork (context-leak guard — 2026-07-13)

Armed routing forks **only for messages the OPERATOR sent** (sender-ID keyed
via `relay_principal_classifier`, never message content). Any other sender —
co-steerer or external — stays on per-channel sessions even while armed:
forking V>>'s private foreground history into someone else's reply would be a
context leak, the resume-routing sibling of the capability lock. Caveat that
remains YOURS to manage: in a *shared* chat (V>> and a co-steerer), the forked session
becomes that chat's per-channel continuity after your first armed turn — drive
`/rc` from your own 1:1 chat if working-context privacy matters there.

## Known gaps (honest scope — updated 2026-07-13, do not oversell)

1. **Stamp propagation needs a CURRENT tree.** The stdin-payload fix landed
   (the operator-session tracker PR, #3948), but the hook executes from the
   LIVE `~/.claude` tree — a machine whose main tree pre-dates #3948 still
   runs the env-only tracker and writes no stamp (observed 2026-07-13: main
   tree 6 behind, stamp absent). Fallback unchanged: stamp manually with
   `python3 -c "import relay_operator_session as r; r.write_stamp('<sid>')"`.
2. **VPS fork-with-context needs transcript sync.** Arming is now local
   everywhere (the marker), but the VPS consumer can only fork a session
   whose transcript exists on the VPS — the operator's Mac transcripts are
   not synced there, so Slack/Discord (VPS-owned) degrade to per-channel
   even when armed. WhatsApp (Mac drainer, transcripts local) is the real
   fork-with-context surface today.
3. **The consumer must be running.** Step 3 is not ceremony: on a Mac with
   no `relay_queue_drain` process (observed 2026-07-13), no inbound is
   consumed at all — start the drainer before claiming the link is live.

## Distinct from its siblings (no overlap)

- `/afk` = **unattended autonomous continuation** (marker + `/auto /rsi` loop).
  `/rc` = **attended remote control with context** (you drive, from your phone).
- `send-to-phone` = **push a notification out**. `/rc` = **pull the session to your
  phone**.
- `grip-channel` = the **transport**. `/rc` = the operator-session **arming** on top.

## Safety + honesty contract

- **Never claim continuity the stamp can't back.** Stale stamp or dead relay →
  `/rc` says so; the link degrades honestly, it does not pretend.
- **Capability lock untouched.** Arming resume routing cannot widen the spawned
  session's tools; that lock lives in `relay_dispatcher.build_argv` and is tested.
- **Pool-A cost.** Each phone-driven fork is a `claude -p` spawn on the Anthropic
  harness — it burns the weekly limit like the REPL (`pool-a-offload-routing.md`).
  Not "free" remote control.
- **Fully reversible.** `/rc off` reverts the relay to byte-identical prior
  behaviour.

## Provenance

Composes `hooks/operator-session-tracker.py` + `lib/relay_operator_session.py`
(both live, wired into SessionStart) and the grip-channel relay topology
(`rules/operational-context.md` → *VPS relay CONSUMER* + *Resume target
precedence*). Designed via the `unified-remote-control` workflow (2026-07-12) and
cut to a pure skill by its KISS adversary (verdict: OVERBUILT → ship zero-file);
the two known gaps above were found by verify-canonical, not assumed away.
