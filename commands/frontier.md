# /frontier — Switch HAL routing to the harness-aware frontier model

Top-level shortcut for `/llm frontier`. Which model you get depends on what
you are running GRIP inside — the frontier seat must never share a vendor
with the driving harness (producer ≠ verifier,
`rules/exogenous-anchor-law.md`):

| Your harness | /frontier routes to |
|--------------|---------------------|
| **NOT Claude Code** (Pi, Codex, OpenCode, Aider, Cursor, plain shell — like now) | **Frontier Claude** — `anthropic/claude-opus-5` via CCH (Claude Premium subscription path, Opus 5) |
| **Claude Code** | **Kimi K3** — `moonshot/kimi-k3` (a Claude seat inside Claude Code would be same-vendor) |

## Usage

```
/frontier
```

No arguments. Marker flips in the status line. Subsequent HAL `/api/query`
calls without an explicit provider default to the resolved frontier model.

## Direct CLI (equivalent)

```bash
python3 -m lib.active_llm frontier
```

## The frontier roster

The council that backs /frontier work is: **GPT-5.6 Sol** (OpenAI),
**DeepSeek**, **Gemini 3.1 Pro** (judge — Pro, not Flash), plus the
harness-aware seat above (Claude outside Claude Code / Kimi K3 inside).

Kimi K3 was chosen by council investigation on 2026-08-26: GPT-5.6 Sol and
DeepSeek-chat were asked independently which non-Anthropic model should take
the Claude slot when the harness IS Claude Code. Both picked Kimi K3 —
Moonshot is a fifth distinct vendor, near-frontier reasoning at lower cost,
no collision with the Gemini judge, and no double-counting of DeepSeek.
Gemini Pro was the runner-up argument (frontier reasoning) but lost on the
judge-family collision: the council judge is already Gemini.

## When to use

- Hard reasoning tasks where quality matters more than speed
- Architectural decisions, complex debugging
- Council-grade exogenous review (the seat never grades its own vendor)

## When NOT to use

- Routine drafting / classification — use `/cheap` instead
- Sensitive content — use `/local` instead

## Related

- `/llm` — full surface (set, council, list, etc.)
- `/cheap` — fast Groq Llama instead
- `/local` — on-device Gemma 4 instead
