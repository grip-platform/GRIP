# Provenance — Name the Authors (PARAMOUNT)

This **REVERSES** the prior no-AI-attribution stance
(`commit-standards.md`; CLAUDE.md "no AI attribution"): AI attribution is no longer
forbidden — it is **required**, as provenance.

## The Law

Every substantive deliverable names the **human author(s) AND the AI models that
materially contributed**. Provenance is honesty about who and what did the work —
the same discipline as the signed IDR council record that names each speaker and
the exogenous judge.

Authorship block format (deliverables — papers, reports, generated docs/PDFs,
public artefacts):

```
V>> (Lourens Cornelius "Laurie" Scheepers)
___
With assistance of
  Claude Opus 4.8
  DeepSeek 4
  Grok-4.5
  Gemini 3.1 Pro
  Llama-3.3
  (each model that materially contributed)
```

- Name the **specific** models actually used (e.g. the Pool A/B/C fan-out; the
  sovereign-council speakers + judge), never a generic "AI". The names in the
  example block above are illustrative; where re-runnability matters, pin the exact
  model ID / version (e.g. `claude-opus-4-8`, not just "Claude Opus 4.8") — a named
  model with no version pin cannot be re-run. Where a signed record exists
  (IDR leaf / council seal), cite it — provenance you can re-verify.
- **Commits / PRs**: provenance is now **encouraged, not banned** — name the
  assisting models (a `Provenance:` / `Co-Authored-By:` trailer is fine). The old
  "NEVER Co-Authored-By: Claude" ban is **reversed**.
- Attribute truthfully in **both** directions: never claim solely-human authorship
  of AI-assisted work, and never claim an AI did work a human did.

## Why

Provenance is the moat. GRIP's records are valuable precisely because they state
truthfully *what* decided and *who/what* contributed. Hiding AI authorship is the
same class of dishonesty as hiding a source. Naming the models also makes the work
**auditable and falsifiable** — a named model's contribution can be re-run and
checked.

## Falsification

Wrong if naming models on deliverables causes a concrete harm the old
no-attribution rule prevented, over the next ~10 deliverables (then narrow scope to
internal-only). Track: substantive deliverables shipped **without** a provenance
block (target 0).

## Scope

Substantive deliverables (papers, reports, generated docs/PDFs, public artefacts)
and commits/PRs. A trivial one-line reply needs no block. Reverses the
no-attribution lines in `commit-standards.md` and CLAUDE.md. Mirror into
`~/.hal/rules/`.

Narrative (origin, incidents, measurements): `docs/doctrine/provenance-name-the-authors.md`.
