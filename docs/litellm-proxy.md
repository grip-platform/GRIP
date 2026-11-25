# LiteLLM → Gemini 2.5 Flash proxy

A rate-limit workaround that lets Claude Code keep running when the CodeTonight
org Anthropic subscription is throttled. A local [LiteLLM](https://github.com/BerriAI/litellm)
proxy accepts the Anthropic API surface on `127.0.0.1:4000` and routes calls
to `gemini/gemini-2.5-flash` (free tier, 1M context). Claude Code connects via
`ANTHROPIC_BASE_URL`.

This is temporary infrastructure. When the Anthropic subscription recovers,
unset `ANTHROPIC_BASE_URL` and you are back on Claude.

## Security — LiteLLM version floor

LiteLLM `1.82.7` and `1.82.8` shipped credential-stealing malware (supply chain
attack, March 2026). This setup requires `>= 1.82.9`. The `grip-gemini`
wrapper refuses to start if the installed LiteLLM is below that floor. Never
downgrade to pin around those versions.

The recommended install is whatever LiteLLM is in your primary `pip3`
environment — the bundled macOS machine at CodeTonight uses the Homebrew
Python 3.11 install at `/opt/homebrew/bin/litellm`. Python `3.14` currently
lacks an `orjson` wheel, so LiteLLM will not install into a 3.14 interpreter;
either use Python 3.13 or the Homebrew 3.11 environment.

## macOS — recommended (grip-gemini wrapper)

### One-time setup

1. Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).
2. Store it in the macOS keychain under service `grip-gemini`:
   ```bash
   security add-generic-password -U \
     -s grip-gemini -a "$USER" -w '<your-gemini-api-key>'
   ```
3. Verify LiteLLM is installed at a sufficient version:
   ```bash
   /opt/homebrew/bin/litellm --version
   # LiteLLM: Current Version = 1.83.8  (anything >= 1.82.9 is fine)
   ```
   If missing or too old:
   ```bash
   pip3 install --upgrade 'litellm[proxy]>=1.82.9'
   ```

### Day-to-day

```bash
grip-gemini                       # start proxy + launch claude, tear down on exit
grip-gemini --keep-alive          # leave proxy running after claude exits
grip-gemini --port 4001           # override port (default 4000)
grip-gemini -- grip latest        # launch a different target instead of claude
grip-gemini proxy                 # start proxy in the foreground (Ctrl-C to stop)
grip-gemini status                # show proxy pid / port / health
grip-gemini stop                  # kill the background proxy if one is running
```

Everything else works the same as a normal Claude Code session. Tool use
(Read, Edit, Bash) is routed through the proxy, which forwards to Gemini and
back-translates the response into Anthropic Messages format.

Environment overrides (for tinkering):

| Variable | Default | Purpose |
|----------|---------|---------|
| `LITELLM_BIN` | `/opt/homebrew/bin/litellm` | Path to the LiteLLM CLI |
| `LITELLM_PORT` | `4000` | Proxy port |
| `GRIP_GEMINI_KEYCHAIN_SERVICE` | `grip-gemini` | Keychain service name |
| `GRIP_GEMINI_CONFIG` | `~/.claude/config/litellm-config.yaml` | Model-mapping YAML |

## Windows (Andre)

Windows does not have macOS Keychain, so the Gemini key lives in an
environment variable. Run the proxy and Claude Code in two separate
terminals.

**Terminal 1 — proxy:**
```cmd
set GEMINI_API_KEY=your-key-here
litellm --config %USERPROFILE%\.claude\config\litellm-config.yaml --port 4000
```

**Terminal 2 — Claude Code:**
```cmd
set ANTHROPIC_BASE_URL=http://localhost:4000
claude
```

If `pip install 'litellm[proxy]>=1.82.9'` fails on Python 3.14 with an
`orjson` build error, create a dedicated Python 3.13 venv and use its
`litellm` binary:
```cmd
py -3.13 -m venv C:\tools\litellm-venv
C:\tools\litellm-venv\Scripts\pip install "litellm[proxy]>=1.82.9"
C:\tools\litellm-venv\Scripts\litellm --config ... --port 4000
```

## Model mapping

`~/.claude/config/litellm-config.yaml` maps three Claude model IDs to
`gemini/gemini-2.5-flash`:

| Claude model ID | Proxied backend |
|-----------------|-----------------|
| `claude-opus-4-7` | `gemini/gemini-2.5-flash` |
| `claude-sonnet-4-6` | `gemini/gemini-2.5-flash` |
| `claude-haiku-4-5-20251001` | `gemini/gemini-2.5-flash` |

If Claude Code reports an unmapped model, add the exact ID to `model_list`
in that YAML. Keep the mapping target as `gemini/gemini-2.5-flash` unless
there is a documented reason to switch.

`drop_params: true` silently discards Claude-specific request parameters
that Gemini does not understand (`metadata.user_id`, some stop-sequence
shapes). The proxy never forwards them to the upstream API.

## Retry policy — rate-limit handling

Gemini 2.5 Flash on the free tier caps requests at ~10 RPM, with a 60s
reset window. The proxy config ships with a retry policy tuned to absorb
those throttles transparently instead of surfacing 429s to Claude Code.

```yaml
litellm_settings:
  drop_params: true

  # Six attempts total; LiteLLM applies tenacity-backed exponential
  # backoff with jitter. Worst-case wall time ≈ 63s — crosses the 60s
  # Gemini reset window.
  num_retries: 6
  request_timeout: 600

  retry_policy:
    RateLimitErrorRetries: 6              # 429s — the headline case.
    TimeoutErrorRetries: 3                # Transient network hiccups.
    InternalServerErrorRetries: 3         # 5xx from Gemini.
    ContentPolicyViolationErrorRetries: 0 # Deterministic; retry cannot help.
    AuthenticationErrorRetries: 0         # Fix the key, do not burn quota.
    BadRequestErrorRetries: 0             # Caller error; same response next call.

  # Per-deployment circuit breaker.
  allowed_fails: 10
  cooldown_time: 60
```

**What actually happens on a 429.** LiteLLM reads Gemini's `Retry-After`
header when present and sleeps exactly that long before the next attempt.
If no header is returned, it falls back to `wait_exponential_jitter`
(~1s, 2s, 4s, 8s, 16s, 32s with jitter). After 6 attempts, the failure
bubbles up to Claude Code — at which point you have likely hit the daily
1500-RPD free-tier ceiling and should top up or wait out the quota.

**Non-retryable errors are fast-fail.** Authentication errors, bad
requests, and content-policy refusals all retry zero times. Burning
retries on deterministic rejections wastes quota and the response never
changes.

**Upgrading from an older config.** The YAML is gitignored and
bootstrapped on first run of `grip-gemini`. Existing installs ship with
`num_retries: 2` and no `retry_policy` — upgrade by deleting the stale
file and letting the wrapper regenerate it:

```bash
rm ~/.claude/config/litellm-config.yaml
# The next `grip-gemini` or `grip-gemini proxy` invocation re-seeds the
# default template. `grip-gemini status` does NOT re-seed — it only
# reports proxy state.
```

If you have hand-edited the config (added models, changed port, etc.),
merge the `retry_policy` and `request_timeout` / `allowed_fails` /
`cooldown_time` blocks from `bin/grip-gemini` lines 79-117 into your
existing file instead of deleting it.

## Limitations

- **Extended thinking / adaptive reasoning is disabled.** Those are
  Claude-specific capabilities. The proxy drops the `thinking` parameter
  via `drop_params: true`, so `[thinking]` blocks will not appear in
  responses.
- **Prompt caching is off.** Anthropic's cache breakpoints have no meaning
  for Gemini. Expect higher per-turn latency and cost.
- **Token accounting is Gemini-native.** Claude Code's context meter and
  cost tracker use Gemini token counts that only approximate Claude's
  tokenisation. Treat reported numbers as indicative, not precise.
- **Rate limits are per-API-key.** The free Gemini tier has daily request
  and token caps. A single day of GRIP work usually fits comfortably, but
  heavy `/ultrado` / `/rsi-optimal` loops may exhaust it.
- **Some GRIP hooks log warnings.** Non-blocking. They expect
  Anthropic-only response shapes for side metadata that Gemini does not
  populate.

## Smoke test

Quick verification without touching Claude Code. Requires the keychain
entry and config in place.

```bash
# Start the proxy in the foreground (separate terminal, or run `grip-gemini proxy &`)
grip-gemini proxy &
LIT_PID=$!

# Wait a beat, then probe
curl -s http://127.0.0.1:4000/v1/models | head -c 400
curl -s -X POST http://127.0.0.1:4000/v1/messages \
  -H 'content-type: application/json' \
  -H 'x-api-key: local-proxy' \
  -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"claude-opus-4-7","max_tokens":50,
       "messages":[{"role":"user","content":"Reply with exactly: GEMINI-OK"}]}'

kill "$LIT_PID"
```

A healthy response ends with something like:
```json
{"content":[{"type":"text","text":"GEMINI-OK"}],"stop_reason":"end_turn"}
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Keychain entry 'grip-gemini' not found` | Key not stored in Keychain | Run the `security add-generic-password` command from "One-time setup" |
| `LiteLLM <ver> < 1.82.9 (CVE-tainted)` | Install is below the safety floor | `pip3 install --upgrade 'litellm[proxy]>=1.82.9'` |
| `Port 4000 already in use` | Stale proxy, or another service on 4000 | `grip-gemini stop`, or `grip-gemini --port 4001` |
| `LiteLLM binary not found at /opt/homebrew/bin/litellm` | Homebrew Python does not have it | Install there (`/opt/homebrew/opt/python@3.11/bin/pip3 install 'litellm[proxy]>=1.82.9'`) or set `LITELLM_BIN=/path/to/your/litellm` |
| Claude replies "model not found" | Model ID not mapped | Add the exact ID under `model_list` in `config/litellm-config.yaml` |
| `orjson` build error during pip install | Python 3.14, no wheel yet | Use Python 3.13 venv or Homebrew Python 3.11 |

## Reverting

When the Anthropic subscription recovers:

```bash
grip-gemini stop                      # if proxy was --keep-alive
unset ANTHROPIC_BASE_URL              # in any shell that has it exported
```

No GRIP state needs unwinding — the wrapper only touches environment
variables for its own subshell, plus a PID file at
`~/.claude/state/litellm-proxy.pid` that it cleans up itself.

## Files

- `bin/grip-gemini` — the wrapper script (this document's reference implementation)
- `config/litellm-config.yaml` — Anthropic → Gemini model map
- `logs/litellm-proxy.log` — stdout + stderr of the background proxy
- `state/litellm-proxy.pid` — tracks the background proxy for `stop` / `status`
