# /prove-it — Deterministic Citation Provenance

Turn a response into a viewable artifact where every claim is a **clickable
citation** that opens the **exact verbatim quote** in its source document. A
hallucinated citation cannot pass — it renders RED as NOT-FOUND.

See `skills/prove-it/SKILL.md` for the full protocol. Engine: `lib/prove_it.py`.

## Quick use

```bash
GRIP_DIR="${CLAUDE_DIR:-$HOME/.grip}"
grip-py "$GRIP_DIR/lib/prove_it.py" render --input spec.json --out artifact.html [--council]
```

On this install `~/.grip` is canonical and `~/.claude` is a compatibility
symlink. `~/.Codex` is stale/wrong here.

`spec.json` = `{title, response (markdown with [[cite:ID]] tokens), sources[], citations[]}`.
Each citation = `{id, claim, source_id, quote}`; `quote` must be verbatim from the source.

## What it guarantees

- **L1 (deterministic, un-fakeable):** each quote is verified to exist in its
  source; the artifact highlights the exact span on click. Falsifier-anchored
  by `tests/test_prove_it.py`.
- **L2 (optional, fail-open):** `--council` asks the HAL multi-LLM reviewer
  whether the quote *supports* the claim. It uses provider shims plus an
  Anthropic keychain helper; it does not require the HAL gateway.
- **Provenance:** per-source SHA-256 + per-citation offsets, IDR/memory-chain ready.

Retroactive: pass any past response (as the `response` markdown) + its sources;
same effect.

## Closing (rung 2 of /witness)

/prove-it is rung 2 of `/witness` (`skills/witness/SKILL.md`). Every run
closes with the see-it line — `See it:` then `open <artifact>.html` and,
when sealed, `verify state/sovereign-council/<seal>.json` on its own line
(plain paths, never `file://`). `--seal` / invocation as `/witness` adds
the IDR leaf + memory-chain node and closes with the witness stanza instead.
