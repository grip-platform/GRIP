---
name: dogfood-domain
description: Adds a newly announced vendor model to the HAL and GRIP catalogues and wires its harness-in-a-harness capability without letting it become a default. Covers proving the model exists from vendor primary sources, separating documented from served from working, classifying the billing path, placing the catalogue and cost rows in both repositories (with parity tests that name any file still missing the id), and the traps that make a new generation silently miss every guard written for the last one - family prefixes, first-match ordering, the [1m] layer, client-version floors and point releases that collapse to their sibling's tokens. Use when a vendor announces a model GRIP should be able to reach, when a launch flag should resolve to it, or when an existing catalogue row for it needs auditing.
status: Active
effort: medium
version: 1.2.0
created: 2026-09-08
updated: 2026-09-24
user_invocable: true
command: dogfood-domain
triggers:
  - /dogfood-domain
  - "add a new model to the catalogue"
  - "wire a newly released model into HAL"
  - "register a vendor model so a launch flag reaches it"
integrates:
  - hal-provider-refresh
  - prove-it
  - create
see_also:
  - hal-provider-refresh
  - llm
  - create
---

# Adding a newly announced model

A vendor announces a model. Someone wants GRIP to be able to reach it. The
temptation is to add one line to a models list and call it done, and that is
where the damage happens: the model becomes reachable by accident before anyone
has established that the account can call it, what a call costs, or which of
the guards written for the previous generation still apply to it.

The procedure below separates those questions and answers them in an order
where a wrong answer is cheap. It is written for the model-onboarding domain
specifically, and it exists because the same handful of traps recur: a
generation prefix that no longer matches, a resolver whose list order is
semantics rather than chronology, and a price that changes shape partway up the
context window.

Not to be confused with `.claude/workflows/dogfood-idempotency-domain.js`, which
dogfoods an idempotency kernel onto a new domain and used to share this name.
It was renamed on 2026-09-24 because the Workflow tool runs a saved workflow by
name, so a `/dogfood-domain` request could be read as consent to run it - and it
opens a PR and messages the operator. `tests/test_skill_workflow_names.py` now
refuses a shared name unless the skill names the workflow's file.

## The three claims, kept apart

The single most common error is collapsing these into one:

| Claim | What settles it |
|-------|-----------------|
| **Documented** | The vendor's own model page, pricing page and changelog, quoted verbatim. A search summary is a claim about the docs, not the docs. |
| **Served** | An exhaustive list call against EACH credential path the account has (a subscription token and a direct key can list different sets), plus one real call through the harness that would use it. The list is free; the call is one message. |
| **Working** | A round trip with the caller's default parameters, which is the only thing that tells you which parameters the model rejects. Record it PER CALLER PATH (Claude Code, HAL's CCH signer, a direct key): a whole Claude Code session proves nothing about HAL's signer, and a 429 settles Served on a path, never Working. |

Documented and not served is a normal, expected state: rollout is gated per
account, per surface and sometimes per administrator. It is not a failure and
it is not a reason to skip the work. It changes the deliverable from a live
route to a forward anchor - a test written before enablement, asserting that
nothing names the model implicitly, so the day access opens the change is one
line and the guards are already standing.

Never write "we cannot verify this" until the vendor's documentation has
actually been read. A probe that timed out yields no evidence at all, and
reporting it as an attempt launders an unrun search into a completed one.

## Workflow

0. **Look for concurrent work first.** `gh pr list -R <repo> --state all --search
   <id>` and `git log --all --oneline -S<id>` in BOTH repositories, and write the
   result down. On 2026-09-24 two sessions onboarded Opus 5.5 the same morning
   without knowing it, and diverged on exactly the judgement calls below (see
   "Two runs, one model"). Both searches see only work that has been pushed, so
   two sessions that start within the same hour can both pass them. To be
   visible before you push, check `lock_status` on the grip-presence server and
   take the advisory lock `onboard:<id>` with `lock_acquire`. It only helps when
   the other session takes it too; `session_list` on the same server shows which
   sessions are live on every machine, but not what they are working on.
1. **Read the vendor's primary pages and quote them.** Model page, pricing
   page, changelog entry. Record the public name, the exact API id, the release
   date, the context window, the maximum output, the knowledge cutoff, the
   endpoints it is served on, every sampling parameter it refuses, and the full
   pricing table including any threshold where the rate changes. For Anthropic
   the pages have a Markdown form that `curl` reads directly:
   `platform.claude.com/docs/en/about-claude/pricing.md`,
   `.../about-claude/models/overview.md`,
   `.../build-with-claude/context-windows.md`.
2. **List before you call.** The exhaustive list call (for Anthropic,
   `GET /v1/models` with `has_more=false`) against every credential path. It is
   free, so it goes first.
3. **Classify the billing path before writing any row.** A metered API model
   needs a catalogue row and a cost row. A model reached through a subscription
   harness needs no *provider entry* - a `providers.yaml` provider block of its
   own - because that would misattribute subscription spend as metered spend.
   It still needs its catalogue row and its cost row, and GRIP still lists it in
   the anthropic provider's `models:` list, because that list is what the
   family resolver reads. HAL has no anthropic provider block at all, by
   design: Claude reaches HAL through CCH, the subscription signer.
4. **Put the cost row in before the first paid call.** A model absent from the
   pricing table does not fail loudly - it bills at whatever the tracker falls
   through to. Both trackers now refuse to price a point release as its sibling
   (`claude-opus-5-5` is not `claude-opus-5`); an unpriced id is logged as
   unpriced instead, but the number is still not its cost.
5. **Then probe with one message, against controls in the same run.** Send a
   known-good id and a deliberately nonsense id alongside the new one, and read
   every status relative to them - never on its own. On the Anthropic
   subscription path in September 2026, real ids answered 429 (rate-limited,
   so recognised) and the nonsense id answered 404. A 404 on the new id then
   means *not served to this account*, which is not *does not exist*: the
   probe cannot tell a typo from a rollout gate. Concluding that an id is not
   real also needs it absent from every `/v1/models` listing AND from the
   vendor's pages, and the spelling probed must be byte-identical to the one
   quoted in step 1. A 4xx that NAMES a version ("version 2.1.280 or newer is
   required") is neither: the id is real and served, and the refusal is a
   client-version floor - record it for step 7. HAL's
   `scripts/probe_models.py --models <new> <known-good> <nonsense>` does this in
   one command. Record the verbatim error text; never record the credential.
6. **Place the rows in both repositories, catalogue first.** Add the
   catalogue row, then run the parity tests BEFORE touching anything else:
   their failures name every other file that must carry the id, and that list
   is this step's checklist - observed red, then worked to green. Editing
   another file first is caught too: `test_every_acting_surface_names_a_catalogued_model`
   fails until the catalogue row exists. See Touchpoints below. HAL and GRIP
   each carry their own copies; changing one leaves the other a generation
   behind, which is how a reviewer ends up pinned to a model the other file
   removed. In September 2026 HAL was found missing Fable 5.1 and Sonnet 5
   entirely, and its cost tracker had no Claude 5 row at all.
7. **Audit every guard written for the previous generation.** Search for the
   old generation's prefix. Each hit is a guard the new model silently misses,
   and a missed guard is not an error - it is a wrong request on the wire.
   Include the client-version floor (see traps) and every vendor table a
   council uses to keep its judge independent of its speakers.
8. **Decide reachability deliberately, then encode it.** Named tier, flagship,
   routing chain, council seat - these are four separate decisions, and the
   launch pin is a fifth. A model priced above the incumbent earns the first
   and none of the others until someone measures that it is worth the
   difference. When the operator does move a default, record which surfaces
   moved and which did not, and give each a test (both directions are
   decisions).
9. **Check what the resolver's list order does.** GRIP's family resolver answers
   a one-token qualifier (`--claude opus`, `--claude 5`) with the FIRST
   `providers.yaml` id carrying that token, so position is behaviour. A full id
   and a multi-token qualifier (`--claude 5.5`, `--claude opus-5`) are
   order-independent: they must name exactly one model, and a tie is refused
   with the candidates listed. Pin every landing you decide on with a test, the
   way `test_claude_5_qualifier_stays_on_sonnet_5` and
   `test_claude_opus_qualifier_launches_opus_5_5` in
   `tests/test_model_onboarding_parity.py` pin Opus 5.5's: the first fails,
   naming `providers.yaml` and the trap, if a new id is listed ahead of Sonnet 5.
10. **Give it a display rule.** Without one the switcher title-cases the id and
    shows something the operator did not name.
11. **Write the tests red first, and end on a green parity run.** Every assertion
    observed failing against the pre-change code, or against a named mutation
    that was confirmed applied by diff before the result was read. The
    never-a-default assertions pass before the change too, so each needs its own
    mutation to prove it can fail. Then run the parity tests below: they fail
    naming every file that still lacks the id, which is the part of this
    procedure that used to depend on memory.

## Touchpoints for an Anthropic model

The catalogue is the source of truth; each row below must carry the new id.
The last column is the test that fails, naming the file, when it does not.

| Repository | File | What it needs | Enforced by |
|------------|------|---------------|-------------|
| GRIP | `config/model_catalog.yaml` | the row, with `context_window` (the model-switch guard reads it) | `tests/test_model_onboarding_parity.py` |
| GRIP | `config/providers.yaml`, anthropic `models:` | the id, at a deliberate POSITION (step 9) | same |
| GRIP | `lib/hal/cost_tracker.py` `_PRICING` | its OWN row, never a sibling's | same |
| GRIP | `bin/grip` `_GRIP_1M_MODELS` | the id, when its window is 1M | same |
| GRIP | `lib/hal/providers/anthropic_native.py` `list_models()` | the id, as `/v1/models` spells it | same |
| HAL | `config/model_catalog.yaml` | the row (no `providers.yaml` entry for Anthropic) | `tests/test_anthropic_catalogue_parity.py` |
| HAL | `lib/hal/cost_tracker.py` `_PRICING` | its OWN row | same |
| HAL | `lib/hal/utils/cch.py` `_MODEL_MIN_CC_VERSION` | the floor, when the API names one | `tests/test_cch_cc_version.py` |

Only when a default moves (step 8): `bin/grip` `GRIP_1M_MODEL`, `settings.json`
`model`, `lib/grip_harness_family.py` `FAMILY_FLAGSHIP`, `lib/grip_model.py`
`FRONTIER_MODELS` with `skills/activate-grip-tui` Step 5.66, the fix text in
`hooks/child-session-1m-guard.py`, and `.claude/CLAUDE.md`. Routing and council
seats (`lib/active_llm.py` `FRONTIER_CLAUDE`, HAL's `raillm_ladder.py`) are
separate decisions again.

The reverse direction is checked too, for seven surfaces that ACT on a Claude
id - the provider list, the launch pin, the settings fallback, the flagship,
HAL's frontier seat, the wizard and the roundtable's Anthropic seats: each may
only name a catalogued model (`test_every_acting_surface_names_a_catalogued_model`).
Two HAL Council rosters are not covered yet, because they seat Opus 4.7, which
the catalogue dropped (GRIP#6579). The 1M list and the price table may cover
more, because they record vendor facts.

The two catalogues cannot see each other from inside either repository, so
compare them directly:

```bash
diff <(rg -o '^  - address: anthropic/\S+' ~/.claude/config/model_catalog.yaml | sort) \
     <(rg -o '^  - address: anthropic/\S+' ~/.hal/config/model_catalog.yaml | sort)
```

## Touchpoints for a decision-model vendor

A decision model returns typed answers (a probability, a choice, a score), never
text. TypeSafe's Jev, onboarded 2026-10-10, is the first. Its touchpoints differ
from a chat model's, and the chat catalogue is the wrong home for it:

- **HAL `config/decision_models.yaml`, never `config/model_catalog.yaml`.** The chat
  catalogue's loader keeps every entry with an address, so a decision model there
  could be routed as chat. Pin the versioned id the API answers with; a reply from
  another version sets the result's `drifted` flag.
- **A provider class under `lib/hal/decide/`**, behind the `DecisionProvider`
  Protocol, not a `providers.yaml` row: the chat rows are all handled as
  OpenAI-compatible chat.
- **The spend guard.** A new source name in `lib/hal/spend_guard.py`, listed in
  `METERED` so its cap is enforced rather than shown as "not enforced"; the
  operator's opt-in and daily cap in `config/spend_guard.json`; and two tests that
  pin the committed policy (`tests/test_spend_guard.py`,
  `tests/test_anthropic_key_route.py`) updated by name. In GRIP, the billing word in
  `config/spend-allocations.json` `billing_by_prefix`.
- **The redaction shield.** A decision body's prose is not under prose keys, so
  every send goes through `review_redaction.shield_decision_payload` (GRIP#7933).
- **Both test fences.** HAL's `tests/conftest.py` fences the keychain item and its
  environment fallback; GRIP's turns the classifier's decision rung off, so no
  test reaches the vendor by accident.
- **Documented versus served.** Probe the error shapes and budgets live before
  coding against them. Jev's docs say 422 for a malformed request, and the API
  answers 400; an over-budget request is refused, never truncated. HAL
  `docs/decide.md` records the rest.

## The traps worth naming

**The generation prefix.** Guards accumulate as prefix matches against the
generation that was current when they were written. A new major generation
matches none of them, which produces no error anywhere - the request simply
goes out shaped for a model that is not the one being called. Search for the
old prefix and treat every hit as a decision, then be careful in the other
direction: widening a guard blindly is equally wrong when the new model refuses
something the old one required.

**First-match ordering.** A resolver that returns the first entry carrying a
qualifier makes list position load-bearing. A newly added id can silently
capture a qualifier that used to answer with a cheaper sibling, and nothing
reports the change. Two defences, and they compose: place an expensive opt-in
model where an ambiguous qualifier cannot reach it, and make a qualifier that
every model in the family carries fall back to the explicit flagship pin rather
than to whatever sits first. `5` is not such a qualifier: Opus 4.8 and Sonnet 4.6
do not carry it, so it resolves by position, and
`test_claude_5_qualifier_stays_on_sonnet_5` fails naming `config/providers.yaml`
and this trap the moment a new id is listed ahead of Sonnet 5.

**The point release that collapses to its sibling.** `claude-opus-5-5` starts
with `claude-opus-5`, so any lookup by prefix bills it as Opus 5, and its token
SET is Opus 5's exactly (`{claude, opus, 5}`), so a set-based resolver cannot
tell the two apart at all. Before 2026-09-24, `--claude 5.5` and even the full
id were rejected. A price lookup must treat a trailing short number as a
different model and a trailing date as the same one; a resolver must compare
token sequences, not sets.

**The layer trap.** `claude-opus-5-5[1m]` is Claude Code's spelling: its own
`/model` picker writes it and Claude Code strips the suffix before the API.
The API itself answers HTTP 404 to it. A fact about a spelling is a fact about
one layer, so name the layer when you record it - a stored note that said "Opus
5.5 takes no [1m] suffix" was true of the API and false of Claude Code.

**The client-version floor.** Anthropic gates new models on the Claude Code
version the caller advertises, and names the floor in the refusal: "Claude
Code 2.1.251 does not support this model; version 2.1.280 or newer is
required". HAL's CCH signer derives the version from the installed CLI and
falls back to a constant when that fails - a launchd job, a host without the
CLI - so every gated model's floor is a row in `_MODEL_MIN_CC_VERSION`, and the
fallback is computed from the rows.

## Example: GPT-6 Astra, 8 September 2026

**Documented.** OpenAI's changelog records GPT-6 Astra released on 3 September
2026, API id `gpt-6-astra`, on the responses and chat-completions endpoints.
The model page gives a 1,050,000-token context window, 128,000 maximum output,
and a 30 April 2026 cutoff. It refuses the `none` reasoning effort, and refuses
custom temperature, top_p and logprobs outright. Tool calling requires the
Responses API.

**Priced with a cliff.** Ten dollars per million input tokens and fifty on
output, against the previous generation's four and twenty. Above 272,000 input
tokens the input and cache rates double and output goes to one and a half
times, for the whole request. So the cost of a long prompt is not linear in its
length, which is the fact that decides everything downstream.

**Served, per surface.** Listed on the CLI, the IDE extension and the chat
clients, and explicitly not on the cloud runner. Surface availability is a
per-surface fact, not a property of the model, so "it is available" is not a
claim that can be made about the model as a whole.

**What was encoded.** The id was added to the provider model list *last*,
against that file's newest-first habit, because the resolver answers a
qualifier with the first match and the token `6` is carried by the three 5.6
tiers as well. Last keeps that ambiguous qualifier on the cheaper model and
leaves the unambiguous name as the only way in. The flagship pin was left
alone, so a bare family flag still launches the previous model. No routing
chain, no fallback link and no council seat names it.

**The defect the audit surfaced.** A qualifier carried by every model in a
family singles out nothing, so the resolver was answering it by list position.
That was already wrong before this model existed: the family-name qualifier for
Anthropic returned the sonnet entry while a bare flag returned the opus pin.
Left alone it would have made the family-name qualifier select the new,
costlier model. Such a qualifier now returns the flagship and says so in the
resolution receipt.

```bash
# The named tier reaches it; nothing else does.
gg++ codex --gpt astra

# These stay on the cheaper flagship, and each has a test that proves it.
gg++ codex --gpt          # bare flag
gg++ codex --gpt 6        # ambiguous digit, also carried by the 5.6 tiers
gg++ codex --gpt gpt      # names the whole family, so it is not a tier
```

## Example: Claude Opus 5.5, 24 September 2026

Triggered by the model-switch guard itself: the operator picked "Opus 5.5 (1M
context)" in `/model` and GRIP warned that `claude-opus-5-5[1m]` was not in the
catalogue. GPT-6 Astra (OpenAI, through Codex) and Claude Fable 5.1 were each
given the same brief and asked to review this procedure against the run; the
formal, cross-vendor ratification is the AGORA review of the resulting PRs.

**Documented.** `claude-opus-5-5`, 1M context by default, 128K output, June
2026 cutoff; $4 in, $5 and $8 for 5-minute and 1-hour cache writes, $0.20 for
cache hits (0.05x input), $20 out - cheaper than Opus 5 on every column.
Thinking cannot be disabled and the default effort is `medium`.

**Served, and working per path.** `/v1/models` listed it first on both the
subscription token and the direct key (12 ids, `has_more=false`). Working on
the Claude Code path: 2.1.281 ran a whole session on it, the API's own model
field reading `claude-opus-5-5`. On HAL's CCH path the version gate is passed,
because the signer advertised Claude Code 2.1.281, but a completion is
UNVERIFIED: that path answered 429.

**What the run found.** Every trap above except the first. Pricing billed it as
Opus 5; `bin/grip`'s 1M list missed it (the third time for that list); the
resolver could not name it; HAL's CCH fallback (2.1.251) sat below its floor
(2.1.280), proven by a controlled pair; a council vendor table let a Fable 5.1
judge pass as independent of an Opus 5.5 speaker; and two provider ids that had
never existed (`claude-*-4-6-20250514`) answered 404 beside their undated
controls - and appear in no listing and on no vendor page, which is what
makes them never-existed rather than merely not-served.

**What was encoded.** The operator made it the launch default (the `bin/grip`
pin, the `settings.json` fallback, bare `--claude` and `--claude opus`) and kept
HAL routing and council seats on Opus 5 until measured. It sits after
`claude-sonnet-5` in `providers.yaml`, so `--claude 5` still means Sonnet 5.

**Two runs, one model.** A concurrent session landed the default the same
morning (GRIP#6563). The two runs agreed on the facts and diverged exactly where
this procedure had relied on judgement: that run listed the model first,
silently moving `--claude 5` from Sonnet 5 to Opus 5.5, and it did not reach
pricing, the resolver, the council vendor table or HAL, because nothing named
them. The merge kept its unsuffixed pin (the Claude Code changelog: "now the
default Opus model — 1M context") and restored the qualifier. The touchpoint
table and the parity tests exist so that the next two runs converge on the same
list without having to compare notes.

## Anti-patterns

- Treating a documentation mention as access. It is not, and the gap is often
  weeks of administrator rollout.
- Adding the catalogue row and skipping the pricing row. The tracker will not
  complain; it will just report a number that is not the cost.
- Reading a status code without a control in the same run.
- Making the newest model the family default because it is newest. Newest and
  best disagree often enough that the pin exists precisely to hold them apart.
- Widening an existing generation guard by prefix without checking each
  behaviour it enforces. Some of them are exactly wrong for the new model.
- Updating one repository's catalogue and leaving the sibling's. A per-file fix
  structurally cannot find the copy next door; the parity tests and the diff
  above can.

## Falsification

This skill is worthless if, over the next handful of model onboardings, the
model is served on first probe every time and none of the guards written for
the previous generation turn out to matter - then the three-claims separation
is ceremony and the procedure should collapse to steps 1, 3 and 6. It is also
wrong if the ordering discipline in step 9 never once changes where a qualifier
lands, since that step costs a measurement each time. The Opus 5.5 run is one
datapoint against collapse: it was served on first probe, and still five
previous-generation guards mattered.

Track two numbers: models reaching a default, a chain or a seat without a
recorded decision that they should (target zero), and requests going out shaped
for the previous generation because a prefix guard was missed (target zero).
Do not track how often this skill is invoked; a procedure scored by its own use
optimises for being used.

## Relationship

`bin/grip-model-watch` (`lib/model_watch.py`) is the nightly floor: it reads each
provider's own list endpoint, so a new id arrives already SERVED, and opens this
procedure's checklist as a GitHub issue; it fills no rows.
`hal-provider-refresh` researches and verifies what a provider currently
serves; this skill takes a single verified model and lands it. `prove-it` and
`rules/prove-it-claims-canonical.md` supply the evidence standard the three
claims rest on, including the rule that an unverifiable claim must first have
searched the vendor's own documentation. `rules/tests-must-be-able-to-fail.md`
governs the red-first requirement in step 11 and the mutation proof for the
never-a-default anchors. `rules/knowledge-maturation-functor.md` decides when a
recurring trap here earns a mechanical guard rather than another paragraph -
the parity tests are that guard for the three traps that had recurred.
`llm` switches the active model once a row exists.
