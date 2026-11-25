---
name: api-status
description: Show auth health, rate-limit state, and per-provider availability from local files only (no browser, no OAuth). Use when something feels slow, a loop is stalling, or an error needs diagnosing before blaming the network.
---

# /api-status — API Connectivity Health Check

Shows auth health, rate limit state, and per-provider availability from local files.
No browser. No OAuth. All data lives locally.

Use when: something feels slow, a loop is stalling, you hit an error and want to know
if the API layer is the problem — or simply to confirm everything is clean.

## What This Checks

1. **Auth** — `claude auth status` (JSON, no browser)
2. **CC Rate Limiter** — `~/.claude/state/rsi/rate-limit-state.json` (RSI adaptive backoff)
3. **HAL Sentinel** — `~/.hal/state/hal/limits.json` (per-provider utilisation: anthropic, openai, gemini, groq, deepseek, openrouter, ollama)
4. **Environment** — presence of `ANTHROPIC_API_KEY`, `ANTHROPIC_RATE_LIMITED`, `ANTHROPIC_RATE_LIMIT_RETRY_AFTER`

## Execution Protocol

Run the following in sequence. Do NOT open a browser or initiate any OAuth flow.

### Step 1: Auth Health

```bash
claude auth status --output-format=json 2>/dev/null || claude auth status 2>/dev/null
```

Report: logged in (yes/no), auth method (oauth_token / api_key / none).

If not logged in:
```
AUTH REQUIRED: run `claude auth login` in a terminal (outside Claude Code CLI).
This is an auth issue, not a rate limit issue.
```

### Step 2: CC Rate Limiter State

```bash
test -f "$HOME/.claude/state/rsi/rate-limit-state.json" \
  && cat "$HOME/.claude/state/rsi/rate-limit-state.json" \
  || echo '{"status": "clean", "message": "No rate limit state file — not currently rate limited"}'
```

Key fields: `rate_limited`, `retry_after`, `backoff_delay`, `consecutive_failures`.

If `rate_limited: true`:
```
RATE LIMITED (cc_rate_limiter): retry after <timestamp>
Backoff: <N>s | Consecutive failures: <N>
Clear: rm ~/.claude/state/rsi/rate-limit-state.json
```

### Step 3: HAL Sentinel Provider Limits

```bash
test -f "$HOME/.hal/state/hal/limits.json" \
  && cat "$HOME/.hal/state/hal/limits.json" \
  || echo '{"status": "clean", "message": "No HAL limit state found"}'
```

Summarise each provider as: **OK** / **WARNING** (utilisation ≥ 70%) / **LIMITED** (≥ 85%).

Key fields: `utilisation` (0.0–1.0), `status`, `cooldown_until`, `consecutive_429s`.

### Step 4: Environment Variables

NEVER echo variable values. Use conditional presence check only:

```bash
echo "ANTHROPIC_RATE_LIMITED=${ANTHROPIC_RATE_LIMITED:-<not set>}"
echo "ANTHROPIC_RATE_LIMIT_RETRY_AFTER=${ANTHROPIC_RATE_LIMIT_RETRY_AFTER:-<not set>}"
test -n "${ANTHROPIC_API_KEY:-}" && echo "ANTHROPIC_API_KEY=present (masked)" || echo "ANTHROPIC_API_KEY=<not set>"
```

The `test -n ... && echo present || echo absent` pattern is the only safe form for secrets.
Never use `${VAR:-fallback}` for API keys — it exposes the value when set.

### Step 5: Output Summary

```
API STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Auth:          <HEALTHY (oauth_token) / NOT LOGGED IN>
CC Limiter:    <CLEAN / RATE LIMITED (retry after <time>)>
HAL Sentinel:
  anthropic:   <OK / WARNING / LIMITED> (<N>% utilisation)
  openai:      <OK / WARNING / LIMITED>
  gemini:      <OK / WARNING / LIMITED>
  groq:        <OK / WARNING / LIMITED>
  deepseek:    <OK / WARNING / LIMITED>
  openrouter:  <OK / WARNING / LIMITED>
  ollama:      <OK / WARNING / LIMITED>
Env vars:      ANTHROPIC_API_KEY <present (masked) / absent>
               ANTHROPIC_RATE_LIMITED <value / not set>

ACTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<If CC rate limited>
  • Wait <N> seconds, OR clear: rm ~/.claude/state/rsi/rate-limit-state.json
  • RSI loops auto-recover via cc_rate_limiter.py exponential backoff

<If HAL provider limited>
  • HAL auto-routes to next available provider
  • Clear all HAL limits: rm ~/.hal/state/hal/limits.json

<If auth issue>
  • Run in a terminal (NOT in CC CLI): claude auth login

<If all clean>
  • No issues detected. If errors persist:
    - Anthropic status: https://status.anthropic.com
    - Claude Code billing: claude billing
```

## Why This Skill Exists

Without this skill, typing `/api-status` (or the prior name `/rate-limit-options`) in
the CC CLI triggers remote command resolution via OAuth browser flow — causing
"Login interrupted" errors. This skill intercepts the command and reads local state only.

## Parameters

None. Always reads current local state.

## Related Files

| File | Purpose |
|------|---------|
| `~/.claude/lib/cc_rate_limiter.py` | RSI adaptive backoff (BASE=2s, MAX=120s) |
| `~/.claude/state/rsi/rate-limit-state.json` | CC limiter state (absent = clean) |
| `~/.hal/lib/hal/sentinel.py` | HAL LimitSentinel (warn ≥70%, reroute ≥85%) |
| `~/.hal/state/hal/limits.json` | Per-provider utilisation state |
