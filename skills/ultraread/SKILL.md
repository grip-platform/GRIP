---
name: ultraread
description: Multi-model comprehension council. Take a target file (a spec, a source file or a saved PR diff), N models read it in parallel, structured analyses synthesised with cross-model agreement scores. Joins /ultrathink, /ultraplan, /ultrareview as the comprehension limb of the ultra* family.
command: /ultraread
effort: medium
version: 1.0.0
triggers:
  - /ultraread
  - ultraread
---

# UltraRead -- Multi-Model Comprehension Council

N models read the same target in parallel and produce a structured analysis.
Results are synthesised into a single Markdown brief with cross-model agreement scores.
Joins `/ultrathink`, `/ultraplan`, `/ultrareview` as the **comprehension** limb of the
`ultra*` family.

## Why This Exists

A single model reading a file or spec gives you one perspective. Two models occasionally
disagree. Four models that all independently cite the same claim give you high-confidence
signal. Four models that split 2:2 on a claim surface a genuine ambiguity worth resolving
before acting. `/ultraread` makes that diversity visible and scored.

The deliverable is a Markdown file you can link in a PR description, share with
teammates, or attach to a hypothesis registration.

## How It Works

1. **Read the target file** -- a spec, a source file or a saved PR diff.
   Anything that is not a readable file (a PR URL, a GitHub issue ref, a
   missing path) is refused with exit 2 before any model is called, because
   every seat would otherwise read nothing and still be a paid call. There is
   no stdin read. Save a PR diff or issue body to a file and pass that path.
2. **Resolve model list** -- from `--models`, or the four default seats (below).
   Each label resolves through `lib/council_seat.py`'s `model_address`: an
   explicit `provider/model` address passes through verbatim; a short name
   (`opus`, `gpt-oss-120b`, ...) resolves to a HAL address, catalogue-first,
   falling back to a known-good literal. An empty entry (`a,,b`, a trailing
   comma, `--models ""`) is refused. A later label that resolves to the same
   address as an earlier one is dropped, with a warning.
3. **Dispatch in parallel** -- each seat is PINNED to its own resolved
   address (`no_failover=True`): a model HAL cannot serve comes back empty,
   never quietly answered by a different model wearing the requested label.
   Each model receives the same target content.
4. **Compute agreement** -- cross-model agreement score = unanimous claims /
   total claims, over the seats that gave a live reading, one vote per served
   model. A seat that HAL served with the same model as an earlier seat is
   left out of the vote, with a warning and a banner, so a model never agrees
   with itself. With fewer than two distinct models voting, the agreement
   line reads `n/a`. A seat that repeats a claim counts once for it.
   Claims match on the identifiers they cite, not on their wording
   (GRIP#9204): a seat supports a claim when it made the same claim, or when
   its own claims cite every identifier that claim cites. An identifier is a
   code-shaped word (`snake_case`, `CONSTANT_CASE`, `camelCase`), a
   backticked name or a name written as a call. The target's own file name
   does not count, and a name that is not in the text the seats were shown
   does not count either; a claim whose every name is refused this way
   matches only a claim with the same text, ignoring case, spacing and a
   closing full stop.

   A claim that names no identifier at all -- the usual case on a prose
   target -- matches on the uncommon words it shares with ONE claim of
   another seat (H1008). A seat backs it when one of its own claims, with
   identifiers or without, shares at least three words with it, and the
   shared words carry at least a quarter of the weight of all the words
   either claim uses. Words are lowercased and cut to their first six
   letters, so `article` and `articles` count as one; function words (`the`,
   `that`, `has`, ...) and reporting verbs (`claims`, `says`, `states`,
   `asserts`, `argues` and their past forms) are left out, because a
   reporting verb says who made a claim, not what it says. Each word weighs
   log(N / n), where N is the number of distinct claims in the run and n
   the number that use the word, so a word every claim uses, such as the
   name of the person a text is about, weighs nothing. The match is claim to
   claim, never a seat's words pooled across its claims: pooled, a name in
   most claims would let one seat back every claim that names it. The same
   claim word for word still matches, as before. The agreement line says
   how many claims the word rule judged, for example `citing none (14 of
   14), on the uncommon words they share`.

   Known limits of the word rule. A word that every claim in the run uses
   weighs nothing, so when each of two seats makes a single claim, the two
   claims match only on the same text. A claim that bundles several facts
   may not match a seat that stated only one of them. Two claims that share
   fewer than three words never match, so two very short paraphrases worded
   differently stay apart. And two very short claims that share a two-word
   name and an ordinary verb can match although they say different things:
   "Jane Example praised Smalltalk images" and "Jane Example praised Python
   typing" do in a run of four claims. Claims that seats write are rarely
   that short: of 910 stored claims, the median keeps 12 words after the
   filtering above, and 32 keep five or fewer.

   The weights come from the whole run, so adding or removing an unrelated
   claim can move a borderline pair across the threshold. A match means two
   seats attended to the same claim, not that they agree on every detail.
   On 2026-11-25, a hand audit of three live prose runs found 42 matched
   pairs. Of those, 40 were the same claim, and 2 shared a core claim but
   contradicted each other in a later clause. Other attribution verbs
   ("notes", "replies", "mentions") stay in the compared words, because
   several are also content words. With them in the list, none of those 42
   matches changed.

   Both rules measure shared attention, not shared conclusions: two seats
   that say opposite things about one function still support each other,
   and so do two seats that use the same uncommon words to say opposite
   things.
5. **Synthesise** -- merge structural maps, tag each claim by how many seats
   support it, union risks and gaps, flag dissents.
6. **Write deliverable** -- Markdown to `state/ultraread/<slug>-<timestamp>.md`.
7. **Print path** -- so you can open, link, or attach it.

## Default Seats

```
deepseek/deepseek-v4-pro, xai/grok-4.6, gemini/gemini-4-carbon, qwen/qwen3.7-plus
```

Four Pool B seats, each observed serving through the HAL gateway (the Gemini
seat, gemini-4-carbon, on 2026-11-26 when it replaced gemini-3.1-pro-preview; the
others on 2026-11-12),
none Anthropic and none OpenAI. A default run must not spend the weekly Claude
limit (`rules/fan-out-spends-the-weekly-limit.md` point 4) or the paid OpenAI
key (`rules/gpt-on-the-subscription-never-credits.md`). `opus` and `sonnet`
are still requestable BY NAME, and doing so spends the weekly Claude limit; an
OpenAI model id is refused unless the paid API-key opt-in is set
(`rules/gpt-on-the-subscription-never-credits.md`).

## Usage

```bash
/ultraread path/to/file.py
/ultraread path/to/pr-2435-diff.md   # save the PR diff/body to a file first -- a bare URL is refused
/ultraread --models deepseek/deepseek-v4-pro,xai/grok-4.6 path/to/spec.md
```

Via CLI directly:
```bash
bin/grip-py -m lib.ultraread path/to/file.py
bin/grip-py -m lib.ultraread --models deepseek/deepseek-v4-pro,xai/grok-4.6 path/to/spec.md
```

Each model reads only the first 8,000 characters of the target (`PROMPT_CHARS`), and the
command says so on stderr when a target is longer. A seat that is not served, or whose
reply fails to parse, prints its reason on stderr and becomes a labelled stub -- a stub
never outvotes a live seat. On a live run where EVERY seat degraded to the stub,
`/ultraread` refuses: it writes no deliverable and exits 2 with `REFUSED` on stderr
(`ULTRAREAD_USE_HAL=0` gives an explicit, labelled stub run instead).

## What Gets Written

`state/ultraread/<slug>-<timestamp>.md` containing:

- **Cross-model agreement score** (% of claims every live seat supports, matched on
  the identifiers they cite or, for a claim that names none, on the uncommon words
  it shares with one claim of each seat; one vote per served model; `n/a` with
  fewer than two distinct models)
- **Structural map** -- each live seat's view of the target's shape
- **Key claims** -- tagged `[unanimous]`, `[3/4]`, `[2/4]` etc. by how many seats support them
- **Risks** -- union across the live seats
- **Gaps & questions** -- union across the live seats
- **Per-model confidence** -- `deepseek/deepseek-v4-pro 0.91 ...`
- **Dissents** -- each claim not every seat supports, with the seats that do and do not
- **Seats without a live reading** -- only when a stub sits beside a live seat: each
  stub's label, resolved address (when known) and reason

## Per-Model Schema

Each model returns:
```json
{
  "model": "opus",
  "address": "anthropic/claude-opus-5",
  "served_model": "claude-opus-5-5",
  "provider": "anthropic",
  "source": "live",
  "structural_map": "...",
  "key_claims": [{"claim": "...", "lines": [10, 25]}],
  "risks": ["..."],
  "gaps": ["..."],
  "questions": ["..."],
  "confidence": 0.91
}
```

`model` is the label the seat was asked for; `address` is what it resolved to;
`served_model` is what HAL reports it actually served (may be unset -- never
substituted with the address); `source` is `"live"` or `"stub"`.

## HAL Integration

Live dispatch is WIRED and the default: `_dispatch_real` resolves each seat's label to a
HAL address through `lib/council_seat.py`'s `model_address`, then calls
`lib/hal_llm_adapter.HALLLMAdapter.infer(model=address, audit=False, no_failover=True,
timeout_s=240)` per model, with `ULTRAREAD_USE_HAL=0` selecting the labelled stub path
(hermetic tests, explicit offline runs). Seats dispatch IN PARALLEL. Any per-model failure
(seat not served, malformed JSON, adapter exception, missing fields) falls back to the
stub, whose every field self-identifies with a `[model-stub]` marker -- silent real-to-stub
degradation is visible in the deliverable itself, and a stub never outvotes a live seat.
There are no TODOs in `lib/ultraread.py`.

## Falsification

This skill is wrong if:
1. Per-model outputs are identical (no actual model diversity -- check by
   comparing `structural_map` fields across models in the deliverable).
2. Agreement score is always 100% regardless of model spread (would mean
   the claim-collection step is broken), or always 0% for models that
   plainly agree (the exact-text matching GRIP#9204 replaced for code
   claims, and H1008 replaced for prose claims).
3. The deliverable Markdown is unreadable (broken tables, missing headings).
4. A `--models` label ever reaches a model other than the one it names -- the
   defect this skill was fixed for (GRIP#9187).

## Related

- `/ultrathink` -- single-model deep reasoning before action
- `/ultraplan` -- context-aware remote planning session
- `/ultrareview` -- parallel specialist code review (security, BIG-O, tests)
- `/ultrajudge` -- multi-model verdict council with role assignment
- `lib/ultraread.py` -- orchestrator source
- `lib/ultrajudge.py` -- verdict council source
- `lib/council_seat.py` -- shared seat-resolution helpers used by both councils
