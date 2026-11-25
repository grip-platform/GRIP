# /local — Switch HAL routing to an on-device local model

Top-level shortcut for `/llm local`. Sets the active LLM to
`ollama/gemma4:26b` — Google Gemma 4 (26B MoE, 4B active) running on
the operator's hardware via Ollama. Zero network egress, zero per-request
cost, 256K context.

## Usage

```
/local
```

That's it. No arguments. Marker flips to bold-blue `Gemma 4 (local) 256K`
in the status line. Subsequent HAL calls without an explicit provider
default to Ollama.

## Direct CLI (equivalent)

```bash
python3 -m lib.active_llm local
```

## When to use

- Sensitive content that must NOT leave the network (legal, secrets,
  personal data)
- Offline work — train, plane, no-WiFi cafe
- Cost-sensitive long-running loops where token bills add up
- Vision/multimodal tasks via local MLX/Ollama paths

## When NOT to use

- Tasks needing frontier reasoning depth — Gemma 4 is good but not
  best-of-class for hard problems
- When Ollama isn't running (`ollama serve` must be up; check with
  `curl http://localhost:11434/api/tags`)

## Pre-flight

If Ollama isn't running or `gemma4:26b` isn't pulled, HAL will fail to
route. Pull once:

```bash
ollama pull gemma4:26b
```

Verify:

```bash
ollama list | grep gemma4
```

## Related

- `/llm` — full surface
- `/cheap` — fast cloud (Groq Llama) instead
- `/frontier` — frontier reasoning model instead
