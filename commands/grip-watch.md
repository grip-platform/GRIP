---
name: grip-watch
description: Find where to watch anything live, right now, for this viewer's region — sport, rocket launches, elections, keynotes, breaking news, court and parliament, esports, awards, concerts. Three lines, not a table. --follow pushes live commentary to WhatsApp for an event you cannot watch.
---

# /grip-watch — where to watch anything, live

## Usage

```
/grip-watch <anything>       where can I watch this, right now, from here
```

Backing CLIs that exist today:

```
python3 lib/grip_watch_resolve.py "<query>"           resolve to an event + its streams
python3 lib/grip_watch.py --viewer                    show the resolved region/timezone
python3 lib/grip_watch.py --check <url>               liveness of one URL
python3 lib/grip_watch_follow.py <url> <name> <jid>…  push new commentary to WhatsApp
```

**Not built yet, deliberately not advertised as if they were:** `--all` (full
comparison render), `--region XX` (per-call override; today only the
`GRIP_WATCH_REGION` environment variable works), and glue that turns a resolved
event straight into `--follow`. An earlier version of this file listed all three
as invocable flags with nothing behind them — a declared input that never
reaches an actor, which `rules/declared-input-must-reach-the-actor.md` forbids.

## What it returns

Three lines. The event with its start time in local terms, the single most direct
actionable pointer, and an honest access label — free, paywalled with the specific
subscription named, or geo-locked. A fourth line only when there is a real
free-to-air fallback worth naming.

The pointer is not always a URL. For paywalled South African sport the honest
answer is "open the DStv app, SuperSport Rugby" — an instruction. Forcing a URL
where none exists is how the answer becomes wrong.

## What it will not do

- It will not present a paywalled or geo-locked result as free.
- It will not guess your region. Unknown prints as unknown.
- It will not claim a link is playable because it returned HTTP 200 — geo-blocked
  services return 200 with a not-available-here page.
- It will not write you a geo-block bypass guide. It names the restriction; routing
  around it is your call.

## Behaviour contract

Read `skills/grip-watch/SKILL.md` before answering. Resolve the viewer once via
`lib/grip_watch.py::resolve_viewer`, rank options with `rank`, render with
`render`. For an ambiguous query — more than one candidate event in the time
window — ask exactly one disambiguation question rather than guessing silently.

Never name a source that was not confirmed at answer time. Rights rotate: the
design pass for this skill held Showmax as a live option weeks after it shut down.
An unconfirmed source is marked UNVERIFIED and falls through to a live check.

Log each invocation to `state/grip-watch-trials.jsonl` — hypothesis H713 is
measured from it.
