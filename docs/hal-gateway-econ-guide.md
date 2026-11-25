# HAL Gateway Econ — turn it ON to save our Claude weekly limit

**Why this exists:** our shared Claude weekly limit hit **86%** (resets Sunday
03:00). Every GRIP session burns it — and so does every *internal* AI call GRIP
makes for you: AGORA councils, summaries, classification, knowledge absorption,
evo. Those internal calls don't need Claude. Now they don't use it.

**What shipped (2026-06-11, GRIP #3526):** when GRIP makes an internal AI call
without asking for a specific model, it now goes to a **free model**
(`groq/llama-3.3-70b-versatile`) by default instead of Claude. Those calls stop
counting against the Claude weekly limit. If the free model is busy it cascades
through six more cheap/free providers (gemini → xai → gemma → qwen → deepseek →
local ollama) — it **never silently falls back to Claude**.

## It's already ON — just pull main

The econ default is in the code. After `git pull` on GRIP `main`, it's active.
There is no toggle to flip. Any internal GRIP call that doesn't name a model
routes to the free model automatically.

## Check it's working (30 seconds)

```bash
bin/grip-llm-usage
```

Look at the **By provider** table. If you see `groq` (or `gemini`/`qwen`/etc.)
calls, econ routing is landing. If you see **only** `anthropic-cch`, your econ
calls aren't reaching a cheap provider — fix it under "What you need" below.

## What you need

At least **one** cheap provider keyed in HAL. The easiest is a free groq key:

1. Free key: [console.groq.com](https://console.groq.com) → API Keys.
2. Store it in the macOS keychain (HAL reads it from there — never paste it into
   a file or a shell script):

   ```bash
   security add-generic-password -a "$USER" -s grip-groq -w "PASTE_YOUR_KEY"
   ```

   HAL finds it via `config/providers.yaml` → `api_key_source: keychain:grip-groq`.
3. Re-run `bin/grip-llm-usage` — `groq` calls should now appear.

Already have gemini / qwen / xai / deepseek keyed? Econ routes through those too
— groq is just the fastest default. You don't need all of them, just one.

## Honest scope — what it saves and what it doesn't

- **Saves:** GRIP's *internal* AI fan-out (councils, broly, summaries,
  classification, absorption, evo). Across a team that's a lot of calls now off
  the Claude limit.
- **Does NOT save:** the main Claude Code *coding session itself* — that's the
  harness, it's Claude by definition.
- **For the coding sessions, the bigger lever:** the weekly screen shows a
  **separate "Sonnet only" pool sitting at ~3%** while "All models" is at 86%.
  For routine coding, switch your model to **Sonnet** (`/model` → Sonnet, or the
  model picker). It draws from the barely-touched Sonnet pool instead of the
  nearly-empty premium one. This is the single biggest thing you can do for the
  weekly limit.

## Turn it off or retune (rarely needed)

```bash
export GRIP_ECON_DEFAULT_MODEL=off                       # back to the Claude ladder
export GRIP_ECON_DEFAULT_MODEL=gemini/gemini-2.5-flash   # or a different cheap model
```

Accepted "off" values: `off`, `none`, `false`, `0`, or empty. Any other value is
treated as the model address to use.

## Proof it's live (V>>'s machine, 2026-06-11)

- **Signed usage chain** (`bin/grip-llm-usage`, tamper-evident / `VERIFIED`):
  `groq` = 27 calls on `llama-3.3-70b-versatile`, last call 11:21Z. The provider
  shifted to groq the moment #3526 merged (00:17Z) — `anthropic-cch` went quiet
  at 23:49Z the night before.
- **Live test:** a no-model `infer()` call routed to
  `provider=groq, model=llama-3.3-70b-versatile` and wrote a usable sentence —
  zero Claude limit spent.

---
*Questions → ask in the team channel. Source: GRIP #3526, `lib/hal_llm_adapter.py`.*
