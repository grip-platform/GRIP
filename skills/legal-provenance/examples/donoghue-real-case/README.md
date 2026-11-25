# Real-case certification — *Donoghue v Stevenson* [1932] UKHL 100

The teaching sample (`../legal-hallucination-modes.json`) proves the floor on a
clearly-labelled fiction. This example certifies it on **genuine law**: the most
famous case in the common-law world, the snail in the ginger-beer bottle, the
birth of the modern law of negligence.

## Provenance chain

| Step | Detail |
|------|--------|
| Case | *Donoghue v Stevenson* [1932] UKHL 100; [1932] AC 562 (26 May 1932) |
| Canonical source | BAILII — `bailii.org/uk/cases/UKHL/1932/100.html` (returns HTTP 403 to non-browser fetchers) |
| Mirror used | University of Trier PDF mirror of the BAILII text (`uni-trier.de/...Donoghue_v_Stevenson__1932__UKHL_100__26_May_1932_.pdf`, 25 pp) |
| Extraction | `pdftotext -layout`, fetched 2026-07-03 |
| Full-text sha256 | `e704212059bd9dd2116ae36b90dcda287d2bdff615305f1056775e63f31a2925` |
| Excerpt | Three passage ranges inlined verbatim into both specs (OCR artefacts preserved — e.g. "the consumers life") |

To re-derive: download the mirror PDF, run `pdftotext -layout`, hash the output,
and compare the excerpt lines. The floor verifies against the supplied text **as
it actually is** — source authenticity (is this a certified law report?) remains
the lawyer's responsibility, exactly per the skill's honesty boundary.

## The two specs

- **`clean.json`** — a case-note citing four real passages: Lord Atkin's
  neighbour principle, the manufacturer ratio, his "no one who was not a lawyer
  would for one moment doubt" remark, and Lord Macmillan's "categories of
  negligence are never closed". Expected: **0 NOT-FOUND → SAFE TO FILE (exit 0)**.
- **`poisoned.json`** — the same four real quotes **plus three planted controls**:
  - `p1` the textbook misquote — one word ("my") inserted into the neighbour
    principle (embellished quote, mode 4);
  - `p2` a phantom holding — "liability is absolute where the product is sold in
    a sealed container" (mode 1);
  - `p3` a phantom authority — a floodgates quote pinned to a source not in the
    record (mode 5).

  Expected: **exactly 3 NOT-FOUND → DO NOT FILE (exit 1)**. A floor that cannot
  fail is not a floor.

## Run it

```bash
python3 -m lib.legal_receipt skills/legal-provenance/examples/donoghue-real-case/clean.json \
  --no-record --out /tmp/donoghue-clean.receipt.json --html /tmp/donoghue-clean.html
# → SAFE TO FILE, exit 0

python3 -m lib.legal_receipt skills/legal-provenance/examples/donoghue-real-case/poisoned.json \
  --no-record --out /tmp/donoghue-poisoned.receipt.json --html /tmp/donoghue-poisoned.html
# → DO NOT FILE (3 NOT FOUND), exit 1
```

## The second leg — `misattribution.json` (a real quote, wrong job)

The floor proves a quote is *present*. It cannot prove the quote *supports* the
claim — a citation can be 100% verbatim-real and still be cited for a proposition
it does not bear, or the exact opposite. That is legal judgement, so GRASP does
not fake it with arithmetic: the **L2 external-model council** (run with
`--council`) sends each *(claim, quote)* pair to a panel of models that did NOT
draft the memo, and flags — advisory, never gating the floor — any real quote the
panel judges unsupported.

`misattribution.json` is a memo where **every quote is verbatim-real** (so it is
SAFE TO FILE on the floor, 100% grounding) but two citations are misused:

- `m2` — Lord Atkin's ratio, which says the manufacturer owes a duty to take
  *"reasonable care"*, cited for the claim that liability is **absolute**;
- `m3` — Lord Macmillan's *"the categories of negligence are never closed"*,
  cited for the claim that they are **fixed and closed** — the exact opposite.

```bash
python3 -m lib.legal_receipt skills/legal-provenance/examples/donoghue-real-case/misattribution.json \
  --no-record --council
# → SAFE TO FILE on the floor (all quotes real), but:
#   External council (L2): 3 reviewed · 2 unsupported · 0 partial
#   ⚠ Attorney review required — m2, m3 are real quotes cited for claims they refute
```

The council is advisory and monotone toward doubt (`llm-augmentation-pattern`):
it can flag a real quote as unsupported for attorney review, but it never
overrides the L1 filing gate, and a `not_found` quote is never reviewed (the
floor already refused it). Hermetic tests: `tests/test_legal_receipt_council.py`
(injected panel, monotonicity + fail-open pinned). The verdicts above were
produced live by external non-Anthropic models (Llama 3.3 70B, Qwen3 32B,
Llama 4 Scout) via HAL on 2026-07-04.

## What the clean run's "fuzzy" tally means

The mirror text hard-wraps its lines, so single-line quotes match on the
whitespace-flexible tier (`fuzzy` = same words, different whitespace) rather
than byte-exact. The certification boundary is proven by the controls: a quote
differing by **one word** falls to NOT-FOUND while whitespace-differing quotes
pass. Formatting is forgiven; words never are.

Certification tests: `tests/test_legal_provenance_real_case.py` (green path,
red path, tolerance boundary, fold idempotence — each mutation-mapped).

First run of this certification: live on the CC+ team call, 2026-07-03 —
receipts signed as IDR `sha256:1c443e0ba…` (clean) and `sha256:984a5d8bd…`
(poisoned control).
