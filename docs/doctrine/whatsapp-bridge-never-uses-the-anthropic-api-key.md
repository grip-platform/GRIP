# The WhatsApp Bridge Never Uses the Anthropic API Key: annex

The narrative behind `rules/whatsapp-bridge-never-uses-the-anthropic-api-key.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

## Origin

On 2026-11-11 at about 07:00 UTC V>> asked whether anything was running on an Anthropic API key. A process-environment scan (`ps -AEww` on macOS; `ps -e` is not the environment flag there) found one holder: the WhatsApp bridge, PID 32570, `node .../mcp-servers/grip-channel/dist/index.js`. `scripts/load-env.sh` loaded the key from the keychain item `grip-anthropic-external` and exported it, and `scripts/run-daemon.sh` passed it to node.

The bridge used it in five places, each calling Claude Haiku 4.5 directly through `@anthropic-ai/sdk`, outside HAL's spend guard: the Discord research council, the Discord channel summary and `/challenge` (`src/discord.ts`), the running Discord summary (`src/running-summary.ts`), and the auto-responder's last rung, which also received every image message (`src/autonomous-responder.ts`). At the time Discord was off on this Mac (WhatsApp-only mode), so the key sat idle there; no connection to api.anthropic.com was open.

V>>'s words, 2026-11-11 at 09:14 UTC: "It must NOT ever be used for the WhatsApp bridge. /recall CCH @~/.hal -- CCH. Use that -- never the API key."

## What enforces it

GRIP#9170: `load-env.sh` no longer loads or exports the key, `run-daemon.sh` unsets it before `exec node`, `index.ts` removes it at boot for any other launcher (`src/no-anthropic-key.ts`), and `askClaude` in `src/hal-client.ts` replaces the five SDK calls. `src/__tests__/no-anthropic-api-key.test.ts` (9 tests) fails on a run-time SDK import, a `new Anthropic(`, a read of the key, a key load in `load-env.sh`, a missing `unset` before `exec node`, or an `askClaude` that stops asking `/api/infer` on the CCH model. Four deliberate breaks each turned it red on 2026-11-11.

## Measurements

- HAL's server on port 3850, unpinned request for `anthropic/claude-haiku-4-5`: `{"ok":true,"provider":"anthropic-cch","model":"claude-haiku-4-5-20251001"}` in 0.98 s. The compiled `askClaude` gave the same through the same server in 1,183 ms.
- A pinned request for the same model was served through CCH in 1.03 s and then refused by HAL with `provider_pin_violation`, because CCH reports its provider as `anthropic-cch`. So the bridge does not pin. Unpinned, a failed CCH call can fall over only to HAL's other ladder rungs, none of which is the Anthropic key: HAL's spend guard leaves `anthropic-api-key` out of `paid_opt_in` with a $0 cap.
- HAL refused images for Claude ("not known to support audio/image content_parts"), so the bridge's image turns answer that they cannot look at images until HAL carries them to Claude. That limit is temporary and tracked apart from this rule, in HAL#797; the pin defect that keeps `askClaude` unpinned is HAL#591.
- The VPS bridge held no key: 0 `ANTHROPIC_API_KEY` lines in its env file and 0 in its live process environment; its HAL answered on port 3850.

## How to check it

Count, never print: on macOS `ps -wwE -p <pid> -o command= | tr ' ' '\n' | grep -c '^ANTHROPIC_API_KEY='`; on Linux `tr '\0' '\n' < /proc/<pid>/environ | grep -c '^ANTHROPIC_API_KEY='`. The target is 0.

## Falsification

Wrong if CCH proves unable to serve the bridge's Claude features often enough that V>> chooses to restore paid use; then he changes this rule, and no session does it for him. Also wrong if the key reaches the bridge's environment by a route the scripts and the boot scrub do not cover: widen the guard, never drop it. Track the key's presence in the bridge's live environment (target 0) and run-time SDK imports in the bridge (target 0, the guard test).
