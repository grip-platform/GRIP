---
name: prove-it
description: Turn any response into a viewable artifact where every claim is a clickable citation that opens the exact verbatim quote in its source document. Deterministic provenance — a hallucinated citation cannot pass (it renders RED as NOT-FOUND). Use when the operator invokes /prove-it, asks to "prove it / cite your sources / show me where that's from / make it verifiable", or retroactively on a past response. The L1 deterministic floor of GRIP's LLM falsification layer; ties into IDR/memory-chain provenance.
---

# /prove-it — Deterministic Citation Provenance

> A claim's citation is only *provable* if the quoted text actually exists,
> verbatim, in the source. That is a deterministic string-match — a quote is
> either in the source or it is not. `/prove-it` builds a self-contained HTML
> artifact where every claim carries a clickable citation; clicking it scrolls
> to and highlights the **exact quoted span** in the embedded source document.

This is the **L1 deterministic floor** of GRIP's falsification layer
(`rules/llm-augmentation-pattern.md`): the verbatim-grounding check is
un-fakeable. An optional **L2 multi-LLM council** (does the quote *support* the
claim?) layers on top, fail-open. Reuses the proven L1/L2 structural mapping —
not a new analogy.

## Usage

```bash
# Render a citations spec to a self-contained, offline HTML artifact
GRIP_DIR="${CLAUDE_DIR:-$HOME/.grip}"
grip-py "$GRIP_DIR/lib/prove_it.py" render --input spec.json --out artifact.html

# With the L2 multi-LLM support-check (fail-open)
grip-py "$GRIP_DIR/lib/prove_it.py" render --input spec.json --out artifact.html --council

# Record the provenance leaf onto a private chain instead of GRIP's shared one
grip-py "$GRIP_DIR/lib/prove_it.py" render --input spec.json --out artifact.html --chain-dir ~/private-matter/chain
```

On this install the canonical GRIP repo is `~/.grip`; `~/.claude` is a
compatibility symlink. `~/.Codex` is stale/wrong here.

Programmatic (and how a response is proven retroactively):

```python
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ.get("CLAUDE_DIR", Path.home() / ".grip")) / "lib"))
import prove_it as pi
html, prov = pi.render(spec)          # spec = {title, response, sources[], citations[]}
# prov["grounding_rate"], prov["tally"], per-source sha256 — the provenance record
```

## The spec format

```json
{
  "title": "...",
  "response": "markdown with [[cite:c1]] tokens at each claim",
  "sources":   [{"id": "doc", "label": "...", "path": "/abs/path"}],
  "citations": [{"id": "c1", "claim": "...", "source_id": "doc", "quote": "EXACT verbatim text from the source"}]
}
```

- `response` supports a minimal markdown subset: `#`/`##`/`###` headings,
  `**bold**`, `- ` lists, paragraphs, and `[[cite:ID]]` tokens.
- A source is loaded from `path` or passed inline as `text`.
- Each citation's `quote` is matched **exactly**, then whitespace- and
  typographic-flexibly against its source. Typography normalisation is
  length-preserving, so offsets still point at the original source text. No
  match → `not_found` (rendered RED).

## What the artifact shows

- Two panes: the **response** (left) with clickable `[n✓]` citation chips, and
  the **source documents** (right) with the quoted spans highlighted.
- Click a chip → scrolls to and flashes the exact span in the source.
- Status per citation: `✓` exact · `≈` whitespace-variant · `✗` **NOT in source
  (possible fabrication)** — flagged red, never silently dropped.
- A grounding-rate banner + per-source SHA-256 provenance footer (IDR-ready).

## The falsification guarantee (why this is real, not theatre)

`lib/prove_it.py::verify_quote` is the deterministic primitive; the
Goodhart-resistant anchor is `tests/test_prove_it.py::test_fabricated_quote_is_not_found`
— a quote not in the source MUST resolve to `not_found`. Mutate the verifier to
"always pass" and that test fails. A hallucinated citation cannot be proven.

## Provenance tie-in

`provenance()` emits per-source SHA-256 + per-citation status and character
offsets — a verifiable record that can be signed into the IDR forest / HAPPI
memory-chain (the offsets + source hash are exactly what an audit-chain leaf
needs). The artifact embeds this as `<script id="provenance">`.

## L2 multi-LLM council (BUILT — `lib/prove_it_council.py`)

`--council` runs each L1-located citation through a panel of external LLMs
(standard mode: Gemini 2.5 Flash, Groq Llama-3.3-70B, Qwen3-32B,
Llama-4 Scout, Anthropic Haiku; `proof` mode also adds DeepSeek R1) asking
"does the quote SUPPORT the claim?", takes a MoE majority (ties break toward
caution), and synthesises through a judge ladder (Gemini 4 Carbon → Groq → MoE
majority). Two invariants: **monotonicity** — L2 only annotates
citations L1 already located, so a `not_found` quote can never earn a support
badge — and **fail-open** — provider/judge/network failure degrades to majority
or "unavailable"; L1 always still renders. The artifact gains a "Citation
council" table. Gemini/Groq use provider shims; Anthropic uses a small direct
keychain-backed helper in `lib/prove_it_council.py`. Tests:
`tests/test_prove_it_council.py` (monotonicity + fail-open + aggregation).

> **Live caveat (load-bearing):** L2 is fallible AUGMENTATION, never the floor —
> read it as a second opinion (confidence = provider-count × agreement); trust L1
> (deterministic). The panel is HARDENED against single-provider outages: 3
> families (Gemini, Groq, Anthropic-external), the Groq trio alone keeping ≥3
> votes, plus a judge ladder (Gemini → Anthropic → Groq → MoE majority) so a
> synthesis always lands. Proof: the first 2026-06-19 run was degraded (Gemini
> down → 2 unavailable, wrong "unsupported" verdicts); after hardening the SAME
> 17 citations gave 0 unavailable, real multi-reviewer syntheses, and the wrong
> verdicts self-corrected to nuanced partial/supported.

## /witness rung 2 — and how every run closes (2026-08-13)

/prove-it IS rung 2 of `/witness` (`skills/witness/SKILL.md` — the one
gesture: see it, prove it, seal it). Every render closes with the see-it
line, one segment per line, plain paths, never `file://`:

```
See it:
open <artifact>.html
verify state/sovereign-council/<seal>.json    (omit when unsealed)
```

Invoked as `/witness` (or with `--seal`), rung 3 adds the fail-open IDR
leaf + memory-chain node and the boxless witness stanza closes the block
in place of the see-it line. The tally speaks for `[[cite:ID]]`-BOUND
claims only and prints its denominator (`N bound`) — material unbound
assertions are named in the block's limits clause, never covered by the ✓.

## Roadmap (earned by use, per knowledge-maturation-functor)

- **Deeper nesting**: `recurse` + AGORA + `gentner-enforcer` + `falsify-self`
  as a Coordinator-Pattern panel beyond the current MoE + judge.
- **Auto-citation authoring**: today the model authors claim→quote pairs and the
  engine VERIFIES them; a future pass proposes citations from the source set.

## First instance / dogfood

2026-06-19: dogfooded on the ExecIntent legal analysis — documented as
17 claims with 100% grounding, with the sensitive artifact outside the GRIP
repo. This checkout does not itself prove that external artifact exists.
Sibling: `rules/llm-augmentation-pattern.md`, `lib/idr_forest.py`
(provenance), and `lib/prove_it_council.py` (the L2 council layer).
