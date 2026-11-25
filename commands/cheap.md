# /cheap — Switch HAL routing to a cheap, fast cloud model

Top-level shortcut for `/llm cheap`. Sets the active LLM to
`groq/llama-3.3-70b-versatile` — Llama 3.3 70B served on Groq's LPU,
typically sub-second time-to-first-token at near-free per-token pricing.

## Usage

```
/cheap
```

That's it. No arguments. Marker is set, status line flips to green
`Llama 3.3 128K`, subsequent HAL `/api/query` calls without a `provider`
field route through Groq automatically.

## Direct CLI (equivalent)

```bash
python3 -m lib.active_llm cheap
```

## When to use

- Quick classification, routing, summarisation
- Drafting that you'll edit anyway
- High-volume jobs where latency matters more than depth
- "I want an answer in two seconds" tasks

## When NOT to use

- Tasks needing extended reasoning, long context, or strong coding
- Anything where wrong is worse than slow

For those, use `/frontier` (Grok / Gemini Pro / o3 tier) or stay on the
default Anthropic subscription.

## Related

- `/llm` — full surface (set, council, list, etc.)
- `/local` — flip to on-device Gemma 4 instead
- `/frontier` — flip to a frontier reasoning model
