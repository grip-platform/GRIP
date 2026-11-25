# /chat

Real-time voice chat with GRIP. Every response is spoken aloud through `grip-speak.py` using Gemini 3.8 Flash TTS (the Interactions API). Pairs with Claude Code's native `/voice` command for bidirectional conversation.

## Usage

```bash
/chat                         # default profile (grip = Charon, no style)
/chat child                   # child profile (Iapetus + cool-older-brother style)
/chat warm                    # warm profile (Charon + warm reverent style)
/chat "opening message"       # activate with custom opener
/chat child "hey buddy!"      # child profile + custom opener
/chat off                     # exit chat mode, restore default register
```

## What happens on activation

1. GRIP speaks a warm greeting through the chosen profile's voice
2. GRIP tells the user: "press `/voice` to talk to me"
3. GRIP enters chat mode — **every response is automatically spoken**
4. User talks via Claude Code `/voice` → GRIP hears → GRIP responds in voice
5. Stays in chat mode until `/chat off` or equivalent exit phrase

## Profiles

| Profile | Voice | For |
|---|---|---|
| `grip` (default) | Charon | Normal GRIP conversation |
| `child` | Iapetus | A young child (cool-older-brother register, Italian Brainrot refs) |
| `warm` | Charon | Warm farewell/appreciation messages to a family member |

Switch profile mid-chat: `/chat child`, then later `/chat warm`, then `/chat` for default.

Add new profiles by editing the `PROFILES` dict in `~/.claude/scripts/grip-speak.py`.

## The stack

```
User speaks → Claude Code /voice → transcribed to GRIP input
                                         ↓
                                  GRIP composes response
                                         ↓
                             grip-speak.py --profile <name>
                                         ↓
                             Gemini 3.8 Flash TTS + style
                                         ↓
                                   afplay → speakers
                                         ↓
                                     User hears
```

Per-turn latency, measured 2026-11-24: a two-sentence reply takes about **6 seconds** to generate with `gemini-3.8-flash-tts` (median 6.12 s; the retired 2.5 preview model took 5.78 s on the same text), and playback starts only when the whole clip has arrived. True sub-500ms streaming is a future upgrade via Gemini Live WebSocket (`gemini-3.1-flash-live-preview`).

## Cost

**Zero** for personal use. Uses Google AI Studio free tier via `grip-gemini` keychain entry; Google's pricing page, read 2026-11-24, lists `gemini-3.8-flash-tts` as free of charge on that tier. The rate limits recorded here (~10-15 RPM, 1,500/day) were for the retired 2.5 preview model and have not been re-checked for 3.8.

## Exiting

```bash
/chat off                     # clean exit
exit chat                     # natural language exit
end chat                      # same
bye child                     # child-mode-specific exit
```

## Related

- `/voice` — Claude Code's native speech-to-text input (users press this to talk to GRIP)
- `scripts/grip-speak.py` — TTS engine with profile system
- `skills/voice-chatting/SKILL.md` — full skill documentation
- `drafts/research-zero-cost-socket-frontend.md` — research backlog for exposing this as a zero-cost socket to external frontends
