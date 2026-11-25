---
name: grip-watch
description: Find where to watch ANYTHING live, right now, and return the one pointer that actually works for this viewer — sport, rocket launches, elections, keynotes, breaking news, court and parliament proceedings, award shows, concerts, esports. Answers in three lines because the user is usually minutes from missing the start, and labels paywalled or geo-locked results as such rather than presenting them as free. Use when the operator asks where to watch something, how to stream an event, whether something is on free-to-air, or invokes /grip-watch. The --follow mode pushes live commentary to WhatsApp for an event they cannot watch.
status: Active
---

# /grip-watch — where to watch anything, live

## What it is

`/grip-watch <anything>` answers one question: **where can I watch this, right
now, from where I am.** Not a comparison table — the single most direct
actionable pointer, plus an honest label saying whether it is free, paywalled or
geo-locked for this viewer.

`--follow` inverts it: when the operator *cannot* watch, it tails the event and
pushes commentary to WhatsApp.

## Why three lines and not a table

The user is usually seconds from missing the start. Evaluating a six-column
matrix costs exactly the thirty seconds they do not have. A table optimises for
completeness; the real question is "am I about to miss this". `--all` renders the
full comparison for someone genuinely planning around a tournament — opt-in, never
the default. Same discipline as `/tldr`, applied to one domain.

```
Springboks v All Blacks — kickoff 17:10 SAST (in 12 min)
Watch: DStv app -> SuperSport Rugby — PAYWALLED (needs a DStv subscription)
If unsubscribed: SABC sometimes sub-licenses Tests free-to-air — not confirmed for this match
```

**"The one link" is sometimes not a URL.** For paywalled SA sport the honest
answer is "open the DStv app, channel 216" — an instruction, not something
clickable. Forcing a URL where none exists is how the answer becomes wrong.

## The taxonomy, ordered by how wrong it can go

Build Tier A first. It is where the tool can be right nearly every time, cheaply.
Tier C is where confidently-wrong lives.

| Tier | Class | Canonical live source | Access tendency |
|---|---|---|---|
| A | Rocket launches | provider's own stream; schedule + video via Launch Library 2 | free, near-universally |
| A | Product keynotes | the company's own newsroom + YouTube channel | free, first-party |
| A | Esports | organiser's official Twitch + YouTube simulcast | free, ad-supported |
| A | Court / parliament | the institution's own channel | free where it exists — often it does not |
| B | Breaking news | a major broadcaster's 24/7 free YouTube feed | free, YouTube-native |
| B | Elections | electoral commission portal + national broadcaster | free, public-interest simulcast |
| C | Live sport | **no canonical source** — the rights holder for that competition, in that region, re-derived per event | paywalled by default |
| C | Award shows | host broadcaster for that ceremony, that territory, that year | usually paywalled |
| C | Concerts | whatever the artist or promoter chose — sometimes no stream exists | fully variable |

## Never hardcode a rights map as permanent

The design session falsified its own prior belief: **Showmax shut down on
30 April 2026** and folded into DStv Stream. A skill carrying a hardcoded "canonical
SA streaming source" table would have shipped that dead pointer this week. Rights
rotate under you. Timestamp every mapping and re-verify past a freshness window.

**Geo-locks are not always foreign services blocking you.** URC.tv — the league's
own service — is geo-blocked *inside* South Africa precisely *because* SuperSport
holds the local rights. The block protects a local paywall rather than excluding a
foreign viewer.

## What an HTTP 200 does not prove

It proves the URL resolves. It does **not** prove the viewer can watch:
geo-blocked and paywalled services routinely return 200 with a "not available in
your region" page. `lib/grip_watch.py::check_link` says so in its own docstring,
and no function name in this skill may imply otherwise. A machine can verify
*reachable* and *wrong-event*; it cannot verify *playable-for-you* without
probing from the viewer's own network.

## Region

Resolved once, not per query: `$GRIP_WATCH_REGION` -> the system timezone mapped
through an explicit table -> UNKNOWN. An unknown region is a real answer that gets
printed as unknown — never a guessed default, because a wrong region silently
produces a confidently wrong answer. Stored preference outranks IP geolocation: an
operator on a VPN or Tailscale would otherwise be mis-placed every time.

State the restriction plainly. Do not write a bypass guide — naming the geo-lock is
this skill's job; routing around it is the operator's decision.

## Failure modes and the cheap check for each

| Failure | Cheap check |
|---|---|
| Stale rights map | timestamp mappings, re-verify past a freshness window |
| Timezone error | compute off UTC via a real tz library; print UTC alongside local |
| Dead event-specific link | prefer channel-level URLs; HEAD-check event URLs live, never from cache |
| Wrong event on an ambiguous query | count same-window candidates; more than one forces one disambiguation question, never a silent guess |
| Scheduled but scrubbed | cross-check a live status field where the source has one |
| Pre-show mistaken for the event | track pre-show and main-event start as separate timestamps |
| Fabricated source name | never name a source that was not fetched and confirmed; unconfirmed -> mark UNVERIFIED and fall through to live search |

## Hypothesis

**H713**, deadline 2026-09-22, **scoped to what actually ships**.

The hypothesis was registered claiming "at least four event classes". Only the
launch class resolves, so as written it was unsatisfiable by its own
implementation — a prediction that could never be tested is not a hypothesis.
Corrected scope: over at least 20 real **launch-class** queries, at least 80%
resolve to the correct event with at least one reachable link. Falsified if
resolution drops below 80% at N=20.

**The access limb has been REMOVED, not weakened.** It read "100% of results
labelled free prove not paywalled", but the label was assigned and recorded by
the same expression — this instrument can never emit "paywalled", so the limb
could never fire. That is unfalsifiable by construction, the exact defect this
section already claims to have corrected once. What the record now carries is
`first_party_unverified`: the provenance of the link is known, the viewer's
entitlement to it is not, and deciding entitlement needs a probe from the
viewer's own network that nothing here performs. The four-class limb is likewise
deferred until a second class ships. Both notes stand so the over-scope is not
quietly forgotten.

Trials append to `state/grip-watch-trials.jsonl` — every invocation, including
unresolved ones, since the failures are exactly what the hypothesis needs. Each
record carries the resolution outcome, the reason when it failed, link
reachability, the access label and the launch status, because those are the
limbs H713 is decided on.

## Implementation

- `lib/grip_watch.py` — deterministic core: `resolve_viewer`, `check_link`,
  `rank`, `render`. Pure stdlib, no network in tests.
- `lib/grip_watch_resolve.py` — query to event: `classify`, `resolve_launch`,
  `resolve`. Launch class only, against Launch Library 2 (free, keyless). Two
  invariants are load-bearing and both are regression-pinned, because a live run
  on 2026-08-22 violated each: futureness is verified per candidate rather than
  trusted from an endpoint named `upcoming` (it returned a launch that had flown
  nine hours earlier), and watch options are read from the event's own `vidURLs`
  and nowhere else (a hardcoded table answered a CASC lunar mission with a
  SpaceX link that returned HTTP 200 and looked entirely reasonable).
- `lib/grip_watch_follow.py` — `--follow`: polls a live blog, diffs new entries,
  pushes to WhatsApp. De-duplicates on the line, not the minute, because live
  blogs revise a line in place.

Sport, award shows and concerts are deliberately NOT resolved here. They have no
comparable free structured source, so they stay search-shaped; naming a source
for them would reproduce the failure the second invariant exists to prevent.

## Provenance

Designed 2026-08-22 across three pools simultaneously: Pool A (broly, taxonomy and
output contract), Pool B (sovereign council `e67888705c237b25`, falsification
regime), Pool C (codex, deterministic core). Built live during the first Test of
the 2026 Springboks-All Blacks series, in the spirit of `/afk` being built live for
a guest to witness on 2026-07-10.
