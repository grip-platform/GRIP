# /llm — Switch the active LLM (any HAL provider/model)

Drives the GRIP↔HAL bridge. The status line reflects whichever model is
active; HAL's `/api/query` reads the marker so subsequent calls default
to it (no provider needed in the request body).

## Usage

```
/llm                              # Show current marker (alias for /llm show)
/llm set xai/grok-4.3             # Mark a specific provider/model active
/llm set ollama/gemma4:26b        # Local model
/llm set anthropic/claude-opus-4-7
/llm pin moonshot/kimi-k3         # Like set, but session-lifetime (no silent 30-min revert)
/llm cheap                        # Tier alias -> groq/openai/gpt-oss-120b
/llm local                        # Tier alias -> ollama/gemma4:26b
/llm frontier                     # Harness-aware: moonshot/kimi-k3 inside Claude Code, else anthropic/claude-opus-5
/llm council A B C [--mode moe|fallback]
/llm once <address>               # Use this model for the next HAL call only, then revert
/llm undo                         # Pop the previous marker off the history stack
/llm stack                        # Show the marker history stack (depth <= 5)
/llm clear                        # Revert to CC default (removes the session AND global markers)
/llm show                         # Print marker JSON + freshness
/llm list                         # Print catalogue grouped by tier
```

## Direct CLI (equivalent)

```bash
python3 -m lib.active_llm set xai/grok-4.3
python3 -m lib.active_llm pin moonshot/kimi-k3
python3 -m lib.active_llm cheap
python3 -m lib.active_llm council xai/grok-4.3 ollama/gemma4:26b gemini/gemini-2.5-flash
python3 -m lib.active_llm once xai/grok-4.3
python3 -m lib.active_llm undo
python3 -m lib.active_llm stack
python3 -m lib.active_llm clear
python3 -m lib.active_llm list
python3 -m lib.active_llm show
```

## What gets written

`~/.claude/state/session/active-llm.json` — solo or council shape, depending
on the subcommand. Contract documented in `lib/active_llm.py`. The status
line reads it via `statusline-command.sh:row2_model()`.

## What HAL does with it

`~/.hal/lib/hal/server.py:_api_query` reads this marker for the default
provider when no `provider` field is supplied in the POST body. So once
you've said `/llm cheap`, every subsequent HAL call without an explicit
provider routes through Groq GPT-OSS until the marker expires (default
30 min) or you `/llm clear`.

This is the **bidirectional** half — HAL writes the marker to advertise
"I just routed to X", AND reads it to default future routes to X. The
status line is the visible side; the routing default is the hidden side.

## Aliases (top-level shortcuts)

For the three most common cases there are top-level commands:
- `/cheap` — same as `/llm cheap`
- `/local` — same as `/llm local`
- `/frontier` — same as `/llm frontier`

## Falsification

This command is wrong if any of the following hold after four weeks of use:

- Operators consistently use the bare `python3 -m lib.active_llm` invocation
  rather than the slash commands (the slash layer adds no value)
- The status line and HAL's actual routing diverge — operator says `/llm
  cheap`, HAL routes to something else (the contract is broken)
- The 30-minute TTL turns out to be the wrong default (status line over- or
  under-claims for typical operator workflow)

Track via: hook log of `/llm` invocations vs direct CLI; spot-check council
runs to confirm the marker matches what HAL actually invoked.

## Marker history stack

Every `/llm set`, `/llm once`, and `/llm council` call captures the prior
marker as a `previous` field in the new payload. The chain is bounded at
depth 5 — the oldest entry is silently dropped when the limit is reached.

```
/llm stack     # numbered list, newest first
1. Grok 4.3 (xai/grok-4.3)  <- active
2. Sonnet (anthropic/claude-sonnet-4-6)
3. [council] Opus · Gemma · Grok
```

**undo** pops one entry: the previous marker becomes active again.

```
/llm undo      # restore whatever was active before the last set/once/council
```

**once** pushes a temporary override that reverts automatically after the
next HAL call. HAL calls `consume_once_marker()` after dispatching; when
`revert_after_consumes` reaches 0 the prior marker is restored. The
statusline prefixes the model name with `⏳` while a once-marker is active.

```
/llm once xai/grok-4.3   # next HAL call uses Grok 4.3, then reverts
```

Wiring HAL to call `consume_once_marker()` automatically is tracked as a
follow-up task. Until then, use `/llm undo` to revert manually after a
once call.

## Related

- `lib/active_llm.py` — helper module + CLI; cooked-payload contract
- `~/.hal/lib/hal/server.py` — HAL server reads/writes the marker
- `statusline-command.sh:row2_model()` — renders the marker in the status bar
- `rules/syscall-doctrine.md` — `lib/active_llm.py` is registered as a syscall shim
