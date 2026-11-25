# Bug B — LiteLLM fallback for `claude-opus-4-7`

Documents a missing fallback edge in V>>'s local LiteLLM router config that
causes `claude-opus-4-7` calls to fail outright when every Opus-tier
deployment is exhausted, instead of degrading gracefully to the
Sonnet tier.

This is a **doc-only fix.** The bug lives in V>>'s LOCAL
`~/.litellm/config.yaml` — runtime code in GRIP/HAL is correct.

## Symptom

When the LiteLLM proxy is running and Claude Code calls
`claude-opus-4-7`, V>> sees one of these failure modes after every
Opus-tier deployment has been tried:

- `litellm.RateLimitError: All deployments returned 429`
- `litellm.APIError: No more retries available`
- Claude Code surfaces an empty response and the turn dies

This happens during sustained `/auto`, `/rsi-optimal`, or `/ultrado`
loops where Opus-tier providers (Gemini 3.1 Pro preview, DeepSeek
Reasoner, Groq Qwen3-32b, OpenAI o3-mini, OpenRouter DeepSeek R1,
Moonshot v1, Ollama gemma4) all hit a free-tier ceiling at roughly
the same time. The proxy has plenty of healthy Sonnet-tier
deployments waiting — but it never tries them, because no fallback
edge tells it to.

The complementary edge already exists: when the **Sonnet** tier is
exhausted, the router escalates UP to **Opus**. The downward edge
(Opus → Sonnet) is missing.

## Root cause

`config/litellm-config.yaml.example` line 342–345 ships this
`router_settings.fallbacks` block:

```yaml
router_settings:
  routing_strategy: latency-based-routing
  fallbacks:
    - "claude-haiku-4-5-20251001": ["claude-sonnet-4-6"]
    - "claude-sonnet-4-6": ["claude-opus-4-7"]
```

The chain reads: Haiku → Sonnet → Opus. There is no entry for
`claude-opus-4-7`, so when Opus is exhausted the router has no
next tier to try. The chain dead-ends at the top.

This was a sensible default when Opus was the highest-quality tier
and "fall back to nothing" meant "give up because there is no
better option." It is the wrong default for a `/ultrado` loop
where degrading to Sonnet quality is strictly preferable to
emitting no response at all.

## Fix (paste-apply)

Open `~/.litellm/config.yaml` and add ONE line to the existing
`router_settings.fallbacks` block.

### Existing block (V>>'s current state, inferred from the example)

```yaml
router_settings:
  routing_strategy: latency-based-routing
  fallbacks:
    - "claude-haiku-4-5-20251001": ["claude-sonnet-4-6"]
    - "claude-sonnet-4-6": ["claude-opus-4-7"]
```

### Patched block (one new line)

```yaml
router_settings:
  routing_strategy: latency-based-routing
  fallbacks:
    - "claude-haiku-4-5-20251001": ["claude-sonnet-4-6"]
    - "claude-sonnet-4-6": ["claude-opus-4-7"]
    - "claude-opus-4-7": ["claude-sonnet-4-6"]
```

### Unified diff

```diff
 router_settings:
   routing_strategy: latency-based-routing
   fallbacks:
     - "claude-haiku-4-5-20251001": ["claude-sonnet-4-6"]
     - "claude-sonnet-4-6": ["claude-opus-4-7"]
+    - "claude-opus-4-7": ["claude-sonnet-4-6"]
```

After saving the file, restart the proxy:

```bash
grip-gemini stop && grip-gemini --keep-alive
```

Or if running LiteLLM directly:

```bash
pkill -f 'litellm.*--port 4000' && grip-gemini proxy &
```

## Verification steps

1. **Confirm the patch landed.** Check the file shows three fallback
   entries, not two:
   ```bash
   rg -A 5 'router_settings:' ~/.litellm/config.yaml
   ```
   You should see all three keys: `claude-haiku-4-5-20251001`,
   `claude-sonnet-4-6`, AND `claude-opus-4-7`.

2. **Optional — read-only check.** Run
   `scripts/verify-litellm-fallback.sh` from this repo. It prints
   `OK` if the descent edge is present, `MISSING` otherwise.

3. **Live verify under simulated rate-limit.** With the proxy
   running, force every Opus deployment to fail by temporarily
   unsetting their keys:
   ```bash
   GEMINI_API_KEY= DEEPSEEK_API_KEY= GROQ_API_KEY= OPENAI_API_KEY= \
   OPENROUTER_API_KEY= MOONSHOT_API_KEY= \
     curl -s -X POST http://127.0.0.1:4000/v1/messages \
       -H 'content-type: application/json' \
       -H 'x-api-key: local-proxy' \
       -H 'anthropic-version: 2023-06-01' \
       -d '{"model":"claude-opus-4-7","max_tokens":50,
            "messages":[{"role":"user","content":"Reply: SONNET-FALLBACK-OK"}]}'
   ```
   With the patch: response routes through the Sonnet tier and
   returns content. Without the patch: response is an error or
   empty.

4. **Falsifier — check the response model.** LiteLLM stamps the
   actually-used backend in the response metadata. If the Sonnet
   tier served the request, the response shows `gemini-2.5-flash`
   or `groq/llama-3.3-70b-versatile` (or another Sonnet
   deployment), confirming the descent edge fired.

## Why this is a doc-only fix

The bug is in V>>'s LOCAL `~/.litellm/config.yaml`. That file is:

- Gitignored — never committed to GRIP/HAL repos
- Per-machine — every developer's copy is independent
- Bootstrapped from `config/litellm-config.yaml.example` on first
  run of `grip-gemini`

GRIP/HAL runtime code is correct. No Python, no TypeScript, no
shell scripts need to change. The fix is one line of YAML in a
file the repo has no authority to edit.

The example template in this repo (`config/litellm-config.yaml.example`)
SHOULD also gain the descent line, so that future fresh installs
include it. That is a small follow-up PR — orthogonal to the
local-config patch documented here. (V>> can make the call.)

## Hypothesis H369 + falsifier

**Claim**: Documenting the 2-line LiteLLM config patch as a
doc-only PR with paste-apply instructions resolves Bug B for V>>
without runtime code changes.

**Falsifier (any one of):**

- V>> applies the patch and sustained `/auto` runs still die when
  Opus is exhausted (suggests runtime code ALSO needs changes —
  e.g. Claude Code's retry loop is the bottleneck, not the proxy
  router)
- The YAML diff doesn't apply cleanly to V>>'s actual file
  (suggests the inferred existing-block doesn't match reality)
- V>> reads this doc and is unable to paste-apply confidently
  (suggests the doc is unclear and needs a wizard or script)
- The descent edge fires but produces lower-quality results that
  break downstream callers (suggests Sonnet quality is
  insufficient for Opus-tier reasoning workloads — needs a
  different mitigation)

Verification deadline: 2026-05-12 (one week of normal usage).
Confirmed if no Opus-exhaustion failures land in
`logs/litellm-proxy.log` after the patch over a 7-day window.
