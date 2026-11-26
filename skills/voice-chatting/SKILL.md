---
name: voice-chatting
description: Activates GRIP's real-time voice chat mode. Every GRIP response is automatically spoken aloud through grip-speak.py using Google's Gemini 3.8 Flash TTS endpoint directly via the Interactions API (not through HAL), every reply passed through lib/redaction_shield first; zero marginal cost on the free tier. Supports voice profiles — default (grip/Charon), child (Iapetus + cool-older-brother style for a young child), and warm (Charon + warm reverent style). Pairs with Claude Code's native /voice command so users can speak to GRIP and GRIP speaks back; a two-sentence reply takes about 6 s to generate (measured 2026-11-24). Use when user invokes /chat, /chat child, "voice chat", "start chat mode", "talk to me", or any request for bidirectional voice conversation.
status: Active
version: 1.1.0
created: 2026-04-10
triggers:
  - /chat
  - "voice chat"
  - "start chat mode"
  - "talk to me"
  - "chat mode"
integrates:
  - asking-users
  - creating-wizards
  - hitl
metadata:
  voice_engine: scripts/grip-speak.py
  tts_model: gemini-3.8-flash-tts
  profiles: [grip, child, warm]
  default_profile: grip
  original_author: V>>
---

# Voice Chatting

Real-time voice conversation with GRIP. Every response is spoken aloud through `grip-speak.py` using Gemini 3.8 Flash TTS (the Interactions API). Pairs with Claude Code's native `/voice` command for bidirectional speech — a child speaks to GRIP, GRIP speaks back.

## Activation

```bash
/chat              # default profile (GRIP = Charon deep voice, no style)
/chat child         # a child profile (Iapetus + cool-older-brother style)
/chat warm          # a family member profile (Charon + warm reverent style)
/chat "opening message"         # activate with a custom opening line
/chat child "hey buddy!"         # child profile + custom opener
```

On activation, GRIP:
1. Speaks a warm greeting through the selected voice profile
2. Tells the user: "You can press `/voice` now to talk to me"
3. Enters chat mode — every subsequent response is automatically spoken
4. Stays in chat mode until exited

## Profiles

Voice profiles are defined in `scripts/grip-speak.py` under the `PROFILES` dict. Each profile bundles a Gemini voice name with a short `speech_metadata.style` phrase — a structured annotation sent beside the text, never inside it — that shapes prosody, emotional register, and pace.

| Profile | Voice | Style | Use case |
|---|---|---|---|
| **grip** (default) | Charon | none (empty) | V>> talks to GRIP normally; deep authoritative register |
| **child** | Iapetus | Warm, cool-older-brother tone, gentle and unhurried | V>>'s five-year-old son a child |
| **warm** | Charon | Warm, sincere, measured, slight reverence | Messages to/about a family member |

Add a new profile by editing the `PROFILES` dict in `grip-speak.py`. Keep the style phrase short — Google's own guidance for Gemini 3.8 TTS: a long-form "Audio Profile" or multi-bullet "Director's Notes" block is the most common cause of voice drift, and most requests need no style at all.

## Response Register per Profile

### Default (grip)
- GRIP's normal voice — direct, sardonic, helpful
- Charon deep male voice, no style
- Full-length responses, technical content fine

### a child Mode
When profile is `child`:
- Keep responses short — 1 to 3 sentences max (5yo attention span)
- Simple words only — "cool", "awesome", "silly", "magic", "build", "boom"
- Cool older-brother register — warm, curious, subtle edge, not cutesy
- Drop Italian Brainrot references when natural (Tralalero Tralala, Bombardiro Crocodilo, Lirili Larila, Tung Tung Tung Sahur)
- Honour the a child + the parent bond — "show the parent", "build that with the parent"
- Match a child's energy; if he's excited, amp slightly; if quiet, go gentle
- **Never** talk down, correct grammar, or use baby voice

### a family member Mode
When profile is `warm`:
- Warm and sincere, never performative
- Measured pacing, slight reverence
- For farewell messages, appreciation, team updates relayed to a family member

## Auto-Speak Protocol

**Every single response** while chat mode is active MUST be spoken aloud via:

```bash
python3 ~/.claude/scripts/grip-speak.py --profile <profile> "<response text>"
```

The script plays audio through Mac speakers via `afplay` and saves the WAV to `~/.claude/state/voice/`. Displayed text accompanies every speak so V>> can see what GRIP said.

Example turn in a child mode:
```bash
python3 ~/.claude/scripts/grip-speak.py --profile child "Hey a child! Whoa, a dinosaur drawing? Was it a big one or a sneaky small one?"
```

## Real-Time Conversation Loop

```
1. V>> invokes /chat or /chat child
2. GRIP speaks opening greeting automatically
3. GRIP tells user: "press /voice to talk to me"
4. User presses /voice → speaks → Claude Code transcribes to input
5. GRIP receives the words as a regular user message
6. GRIP composes response (register per profile)
7. GRIP speaks it via grip-speak.py --profile <name>
8. User hears it through Mac speakers
9. Loop back to step 4
```

**Latency per turn**, measured 2026-11-24: TTS generation alone takes about 6 seconds for a two-sentence reply with `gemini-3.8-flash-tts` (median 6.12 s), the same as the retired 2.5 preview model on that text (5.78 s); `gemini-3.8-flash-lite-tts` took 4.88 s. Transcription and afplay add to that, and playback starts only when the whole clip has arrived. True sub-500ms streaming is a future W4 upgrade via Gemini Live WebSocket API (`gemini-3.1-flash-live-preview` or `gemini-2.5-flash-native-audio-latest`).

## Exiting Chat Mode

User ends with any of:
- "exit chat" / "end chat" / "stop chat"
- "bye child" / "child has to go" (a child mode specifically)
- `/chat off`

On exit:
1. GRIP speaks a closing line through the active profile
2. Switches back to default voice (Charon) and normal register
3. In a child mode: summarises the conversation for V>> (what a child talked about, notable moments)

## Profile Switching Mid-Chat

```
/chat child        # start in a child mode
... conversation ...
/chat warm         # switch profile mid-conversation
/chat             # switch back to default
```

Each switch changes the voice profile for subsequent responses without ending chat mode.

## Cost Profile

Uses Google AI Studio **free tier** via `grip-gemini` keychain entry; Google's pricing page, read 2026-11-24, lists `gemini-3.8-flash-tts` as free of charge on that tier. For personal conversation volume (10-100 turns/day), effectively free. The rate limits recorded here (~10-15 RPM, 1,500 requests/day) were for the retired 2.5 preview model and have not been re-checked for 3.8.

If billing is ever enabled, Google's pricing page (read 2026-11-24) puts `gemini-3.8-flash-tts` on the paid Standard tier at 0.50 US dollars per million text tokens in and 9.00 per million audio tokens out through 31 December 2026, doubling to 1.00 and 18.00 US dollars per million from 1 January 2027. The free tier itself stays free of charge either side of that date.

True billing visibility: `aistudio.google.com` → API keys → billing. Enable billing only if you hit free tier limits at scale.

## Zero-Cost Socket Pattern (Research Backlog)

This skill is local-only — runs inside one Claude Code session. The **research question** for future sprints: how to expose this capability as a zero-cost socket so external frontends (web, mobile, your-gateway.example.com) can consume it without re-paying. See `drafts/research-zero-cost-socket-frontend.md` for the research brief.

## Safety + Trust

- **a child profile**: never speak anything scary or upsetting. If a child says something that suggests distress, stay calm, acknowledge, invite the parent into the conversation.
- **Never** store audio of user voice input (Claude Code `/voice` handles STT via the harness, not GRIP). Only GRIP's spoken output is saved to `state/voice/` for replay.
- **Every spoken reply is redacted first.** `scripts/grip-speak.py` passes the text through `lib/redaction_shield` (llm_egress profile) before Google's TTS endpoint sees it: people, phone numbers, emails, ID numbers and denylisted names become pinned pseudonyms. The reply text goes to Google directly, not through HAL; the keychain key is `grip-gemini`. A shield failure stops the send.
- V>> is always the ambient supervisor for a child sessions. If V>> interrupts, stop and listen.
- Profiles are scoped — `child` profile should only be used for actual a child conversations.

## Success Criteria

This skill is working when:
1. `/chat` invocation immediately triggers a spoken greeting in the chosen profile
2. Every subsequent response is automatically spoken via `grip-speak.py --profile <name>`
3. Response register matches the profile (short for child, normal for grip, reverent for warm)
4. Profile switching mid-chat takes effect on the next response
5. Latency per turn stays under 4 seconds end-to-end. Not met on 2026-11-24 for a two-sentence reply, with either model (TTS alone takes about 6 s and returns the whole clip at once); meeting it needs streaming playback
6. V>> sees the text of every spoken response alongside the audio
7. Exit phrase cleanly switches back to default mode and restores normal register

## Falsification

This skill is wrong if:
- GRIP forgets to auto-speak a response while chat mode is active
- a child profile responses use words a 5yo wouldn't understand
- Profile switching requires restarting chat mode
- Italian Brainrot references in child mode feel forced or tokenistic
- Exit doesn't restore default voice + register
- Audio saves to the wrong directory or stomps existing files

## Changelog

| Version | Date | Changes |
|---|---|---|
| 1.1.0 | 2026-11-24 | GRIP#9447. Google shuts `gemini-2.5-flash-preview-tts` down 18 December 2026; moved to `gemini-3.8-flash-tts` through the Interactions API. The new model reads an inline directive aloud instead of parsing it silently, so each profile's directive became a short `speech_metadata.style` annotation sent beside the text, never inside it. |
| 1.0.0 | 2026-04-10 | Initial release. Gen 509 ultrado W2. Iapetus voice for a child confirmed by V>>. Built on `grip-speak.py` profile system. Supports grip/child/warm profiles. Pairs with Claude Code native /voice for bidirectional conversation at ~2-3s per-turn latency. Zero-cost socket pattern deferred to research backlog. |
