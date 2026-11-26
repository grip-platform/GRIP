# /ultraread

**Multi-model comprehension council** — N models read a target in parallel,
structured analyses synthesised with cross-model agreement scores.

Part of the `ultra*` family: `/ultrathink`, `/ultraplan`, `/ultrareview`, `/ultraread`, `/ultrajudge`.

## Quick usage

```bash
/ultraread path/to/file.py
/ultraread path/to/pr-2435-diff.md   # save the PR diff/body to a file first -- a bare URL is refused
/ultraread --models deepseek/deepseek-v4-pro,xai/grok-4.6 path/to/spec.md
```

## What you get

A Markdown deliverable at `state/ultraread/<slug>-<timestamp>.md` with:

- Cross-model agreement score (% of claims every model supports; claims match
  on the identifiers they cite, so differently worded claims about the same
  function agree, and a claim that names no identifier matches a claim that
  shares enough of its uncommon words, so paraphrases about a prose target
  agree too)
- Structural map per model
- Key claims tagged by how many models support them (`[unanimous]`, `[3/4]`, `[2/4]`)
- Risks, gaps, and questions (union across all models)
- Per-model confidence scores
- Dissents (claims not every model supports, naming which models do and which do not)

## Options

| Flag | Description |
|---|---|
| `--models` | Comma-separated model list (default: deepseek/deepseek-v4-pro,xai/grok-4.6,gemini/gemini-4-carbon,qwen/qwen3.7-plus -- none Anthropic, none OpenAI). `opus`/`sonnet` are requestable by name but spend the weekly Claude limit. |

## Full documentation

See `skills/ultraread/SKILL.md` for the complete protocol, per-model schema,
HAL integration details, and falsification conditions.
