---
name: witness
description: "WITNESS — one gesture fusing the short answer, the openable deliverable, and GRASP provenance. Three rungs — /see-it (open the deliverable links), /witness --no-seal (add the deterministic cite.verify floor over [[cite:ID]]-bound claims), /witness (seal an IDR leaf + memory-chain node and reference the newest anchored root). Use when the operator invokes /witness or /see-it, when a substantive answer carries [[cite:ID]]-bound claims (auto-fire proves, never auto-seals — ratified 2026-08-13), or as the closing mechanism of /verdict, /prove-it, /generate-pdf and /professional-pdf. Also the authoritative spec for the see-it line grammar and the witness card that close every canonical block. GRIP's productised answer to the EU AI Act moment: a watermark says \"an AI touched this\"; WITNESS shows the sealed, checkable record of what was decided, believed and claimed — witness it, don't trust it."
---

# /witness — see it, prove it, seal it

## What it is (plain language)

An answer worth keeping deserves three things at once: a short block you can
read in one pass, an openable full version you can keep and share, and a
provenance record anyone can check without trusting us. /witness makes those
one gesture. It composes machinery GRIP already has — it invents no new
crypto and no new spec.

Origin: V>> 2026-08-13, verbatim — *"now with provenance, here: show the
people who think that transparency is important our response to the EU AI
Act — GRASP — be all end all skill/workflow/infra OSS code: WITNESS
`/witness`."* Design ratified the same day (amendments A1–A3, the
`/update-config` wizard); concept council seal `1ba40ebcbfb44b60`
(non-Anthropic speakers, judge gemini-3.1-pro-preview) — its three fatal
objections are binding constraints below.

## The three rungs (each contains the one below)

| Rung | Verb | Does | Cost |
|---|---|---|---|
| 1 — SEE | `/see-it` | Open/emit the deliverable link(s) for the last answer's full depth; render the full version if only the terminal block exists. | Cheap. No floor run, no ledger writes. |
| 2 — PROVE | `/witness --no-seal` | Rung 1 + run the cite.verify floor over the answer's `[[cite:ID]]`-bound claims; emit the tally + grounding bar. | Authoring cost of binding claims to verbatim quotes. |
| 3 — SEAL | `/witness` | Rungs 1–2 + append a signed IDR leaf and cross-referencing memory-chain node (fail-open); surface the verify handle and the newest covering anchored root. | Two append-only ledger writes. |

**`/see-it` is rung 1, not an alias of /witness.** Three load-bearing
reasons: cost asymmetry (an alias would write two ledgers every time the
operator says "show me that bigger"); different auto-fire floors (the
see-it line fires on *anything openable*; the prove rung on the
deterministic `[[cite:ID]]` floor); and in-place escalation — `/witness`
after `/see-it` reuses the same spec-addressed artifact (sha256 of the
canonical spec, first 12 hex), adding tally and seal without a second
render. Honest caveat: the artifact *address* is idempotent; the ledger
*append* is not deduplicated, by design — the IDR forest is an event log,
and witnessing the same answer twice is two witnessing events.

## Auto-fire (deterministic — ratified: prove, never seal)

- The **see-it line** fires whenever anything openable exists (the style
  layer's job — `output-styles/grip-canonical.md`).
- **Rung 2 auto-fires** when the response carries at least one
  `[[cite:ID]]`-bound claim (GRASP's `salient` footer mode ported to GRIP;
  `CITE_RE`) — "is this substantive?" never rests on a judgement call.
- **Rung 3 NEVER auto-fires** (V>> wizard, 2026-08-13: open question 1
  resolved B). Sealing runs only on explicit `/witness` or on composed
  surfaces that seal by contract (/verdict's substantive path, sealed
  PDFs). An auto-fired cited answer therefore ships provable-yet-unsealed —
  the leaner ledger was priced and chosen by the operator.
- Never fires on one-line rulings, pass/fails, or RSI-loop iterations.
  Substantive-but-uncited stays review-tier: the fix is to cite it, which
  makes the floor fire on its own.

## Behaviour contract (every composing surface honours this)

- **Terminal shape.** The see-it line (lite) or the witness card (full) is
  the block's closing EVIDENCE element — after the manifest and after any
  ★ Insight block. One line follows it and only one: the recommended next
  step, which closes the BLOCK
  (`rules/canonical-output-style.md`, amended 2026-09-11). This narrows the
  earlier "nothing after the block, ever" wording, which predated that line
  and is retired; nothing else may follow. Links are bare `https://` URLs or
  plain paths, one per labelled segment; `file://` is never emitted (dead in
  the operator's terminal, probed 2026-08-13).
- **Deliverable routing** (the L2 lock, by weight): substantive verdicts
  and reports → claude.ai artifact page (load `artifact-design` first;
  favicon stable; private by default), carrying the three mandated parts —
  the ruling at a glance, the evidence chain with every token verbatim,
  and the **limits section** (what could not be adjudicated; an artifact
  without it is marketing). Working detail → local rendered HTML
  (`lib/prove_it.py::render`) or branded PDF (`skills/professional-pdf`).
  Loss-would-hurt artefacts additionally through `bin/grip-backup`.
  **Client-context substantive verdicts route to the local branded-PDF
  path ONLY — never a cloud page** (V>> wizard 2026-08-13, open question 2
  resolved B: data residency wins over shareability; the sweeps govern
  content either way). Artifact URLs are always the full form
  `https://claude.ai/code/artifact/<full-uuid>` — a truncated id breaks
  both the click and the footer badge.
- **Language lock (council Fatal A).** The card, the artifact, the README
  and every composing surface say **sealed / tamper-evident /
  complement-to-watermarks**. They never say signed, non-repudiable,
  compliant, or true. A ✓ means the quote exists verbatim in the named
  source and the record has not been altered since sealing — not that the
  source is right. **Bound-only tally (Pool C dissent, folded):** the
  tally and grounding bar speak for `[[cite:ID]]`-BOUND claims only — the
  card prints the denominator as `N bound`, and material assertions left
  unbound are named in the block's inline limits clause ("only N claims
  cite-bound"), never silently covered by the ✓. The floor measures
  citation discipline, not answer correctness, and the card presents
  exactly that.
- **Sealing is additive and fail-open.**
  `lib/prove_it_provenance.py::record_proveit_provenance` never raises; a
  recording failure degrades the card to `decision unrecorded (degraded)` —
  a witness must not lie about its own seal.
- **Anchor honesty (council Fatal C).** No per-response Bitcoin anchor,
  ever. The card references the newest anchored Merkle root covering the
  sealed leaf (`bin/grip-moat-anchor` standing job; OSS `grasp anchor`),
  and prints `sealed · not yet covered by an anchored root` when that is
  the truth — sealed and anchored are different strengths and never blur.
  **Checkable referent (Pool C dissent, folded):** the anchor row names
  the anchored root's MANIFEST ID (e.g. `moat-20260813T000121Z`), never a
  bare date — a printed card is a receipt of state at emission, and the
  manifest id is the handle by which anyone can re-check it against
  Bitcoin later. Everything else is lovely offline via `grasp verify` and
  `tools/grasp-verify-receipt` (stdlib, zero network, zero trust in us);
  `--anchor` runs the tiered network check opt-in.
- **Sweeps.** Artifact/PDF content passes
  `rules/visual-artifact-leak-prevention.md` and
  `rules/no-sensitive-info-in-grip.md`, plus `lib/redaction_shield.py` for
  client-context content; client-scoped material never publishes to a
  GRIP-public surface. The provenance block names the human author and the
  specific assisting models, inside the artifact
  (`rules/provenance-name-the-authors.md`).

## The see-it line (authoritative grammar)

```
see-it   := "See it:" SP segment ( " · " segment ){0,2}
segment  := label SP link
label    := "open" | "verify" | "file" | "pr"
link     := bare-https-URL | plain-path[ ":" line ]
```

| label | payload | meaning |
|---|---|---|
| `open` | https URL or path | THE deliverable. At most one. |
| `verify` | path or https URL | the provenance record (seal JSON, prove-it artifact, IDR leaf). |
| `file` | path[:line] | a local working file worth opening. |
| `pr` | https URL | the pull request — full URL only; short repo-qualified forms live in prose, where the footer badge supplies the click. |

Rules, each carrying its probed reason: one link per segment, **link last
in the segment** (the terminal's linkifier glues trailing text into the
underline); bare https or plain paths only, never `file://`; repo-relative
paths preferred; no trailing punctuation after a link token; separator
` · `; max 3 segments, priority `open` > `verify` > `file` > `pr`,
overflow to `/see-it` on demand; **nothing openable → no see-it line** (no
empty ceremony). **Split-pane wrap rule (V>> 2026-08-13):** chat output
cannot know the pane width, so with 2+ segments, or any segment whose
payload is a URL, emit one segment per line under a bare `See it:` head —
a URL on its own line soft-wraps and stays clickable; a multi-segment line
hard-wrapped by a narrow pane breaks mid-separator. A single short path
segment may stay inline. A copyable command is not a link and never spends
a segment — commands live in the witness card. The line doubles as the
badge feed: its URLs and the prose's repo-qualified refs match
`footerLinksRegexes` in settings.json, so one emission gets two click
surfaces (inline now, footer badge after scrollback).

## The witness card

In chat, the card is the **boxless stanza** — short label rows (each under
~56 columns by construction) plus `┆` fineprint rows with one link per
line. A fixed-width box shatters when a pane is narrower than the art, and
chat output cannot know the pane width; the box form is CLI-only
(`grasp/card.py` reads the real terminal width). The value is the handles,
not the border — every row is a verification handle a hostile screenshot
cannot fake, because a faked card dies at `grasp verify`.

```text
WITNESS ✓ sealed — facta, non verba
model    ◆ claude-fable-5
claims   3 bound — ✓3 ≈0 ✗0 · grounding ██████████ 1.00
decision idr 41e3e9c8bc90 · memory m-7f21
anchor   root moat-20260813T000121Z
verify   grip-sovereign-council --verify 41e3e9c8
┆ open    https://claude.ai/code/artifact/2f0c8a1e-4b7d-4e2a-9c3f-1a5b8d7e6f90
┆ verify  state/sovereign-council/41e3e9c8bc901234.json
┆ file    drafts/witness/2026-08-13-report.html
```

- **States, honest by construction:** `✓ sealed` (all bound claims ✓, seal
  recorded) · `⚠ degraded` (recording failed and the card says so) ·
  `✗ unproven` (any bound claim NOT-FOUND; the CLI exits non-zero). A
  grounding bar below 1.00 is the visible residual-risk signal — the card
  cannot show a clean face over an unclean tally, and when the tally shows
  ≈ or ✗ the block's inline limits clause names it.
- **The `verify` pairing is deliberate:** the stanza row is the command
  you *run*; the fineprint row is the record you *open* — two affordances,
  one check.
- **The card carries handles, never the argument's evidence** — evidence
  stays woven in the answer's prose and lives in full in the artifact. The
  card is a receipt, not a ledger.
- `/see-it` lite and un-witnessed answers emit the single see-it line
  only — no stanza, no ledger. The card appears exactly when the full
  gesture ran (ratified A1).
- Motto: `facta, non verba` — deeds, not words.

## Closing-step wiring (each surface keeps its identity and delegates)

| Surface | Wired behaviour |
|---|---|
| `/verdict` | Moment card (`lib/verdict_card.py`) unchanged; the substantive path's closing step is *invoke /witness (full)* — /witness supplies artifact, floor tally, seal and links. |
| `/prove-it` | IS rung 2. Ends with the witness stanza + see-it line; `--seal` adds rung 3. |
| `/generate-pdf` | Minimum: close with the see-it line (plain path). With `[[cite:ID]]` claims: escalate to full /witness; the PDF colophon gains the witness block. |
| `/professional-pdf` | Same; the witness block renders in the variant's colophon style; brand resolution untouched. |
| Canonical style v3 | The see-it line is a first-class closing part; the style files point here for machinery. |
| `footerLinksRegexes` | The stanza emits badge-matchable ids in stable positions (artifact URLs, repo-qualified PR refs). |

## Machinery (compose, never rebuild)

GRIP: `lib/prove_it.py::render` · `lib/prove_it_provenance.py::record_proveit_provenance`
· `bin/grip-sovereign-council --verify` · `bin/grip-moat-anchor` · the
Artifact tool + `artifact-design` · `skills/professional-pdf` /
`lib/pdf_branding.py` · `lib/verdict_card.py` (kept) ·
`lib/redaction_shield.py` · `bin/grip-backup`.
GRASP (`~/CodeTonight/grasp`, AGPL): `grasp/footer.py` (salient floor,
provider glyphs, `_artifact_id`) · `grasp/prove_it.py` (`CITE_RE`,
`STATUS_*`) · `grasp/provenance.py` · `grasp/card.py` · the `grasp` CLI ·
`tools/grasp-verify-receipt`. `grasp` is NOT importable from GRIP's venv
(verified ModuleNotFoundError): in-session /witness uses the GRIP twins
and shells to the `grasp` CLI only when installed. The twins are pinned by
a cross-implementation agreement test on shared spec vectors
(`tests/test_witness_twins.py`, skipif when grasp is absent).

The OSS form is `grasp witness` (`grasp/witness.py` — thin composition, no
new crypto, no new spec; `--no-seal`, `--anchor`; exits non-zero when any
bound claim renders ✗). Distinct from: `/prove-it` (the floor engine —
rung 2 IS it), `/verdict` (the ruling moment; /witness is its closing
mechanism), `/realise` and the RSI witness ledger (witness the *system*;
/witness witnesses *one answer*), and watermarking (the mark on the
artifact; WITNESS is the record under it — complementary, and the contrast
is the pitch).

## Falsification

Wrong if, over ~20 witnessed answers: the salient floor makes authors
avoid citing to dodge the gesture (decouple auto-fire); verify handles are
never invoked (theatre — measure verifies, not emissions); every /see-it
is followed by a full /witness anyway (collapse the rungs); ledger growth
degrades IDR verify time before N=10k leaves (add rotation, never stop
sealing); or the limits clause degrades into boilerplate nobody reads
(then it is the residual-risk theatre the council warned against — make it
specific or fail the block).

## Provenance

V>> (Lourens Cornelius "Laurie" Scheepers) — the lightbulb, the four locks
and the ratified amendments. Design and build with Claude Fable 5
(claude-fable-5[1m]); concept council deepseek-v4-pro, grok-4.5,
gemini-2.5-flash, llama-3.3-70b-versatile, judge gemini-3.1-pro-preview
(seal `1ba40ebcbfb44b60`, record
`state/sovereign-council/1ba40ebcbfb44b60.json`); dissent kimi-k3 (Pool C
via codex) — two of three defects folded, one refuted in part.
