---
paths:
  - "mcp-servers/grip-channel/**"
  - ".grip-channel.env"
---

# The WhatsApp Bridge Never Uses the Anthropic API Key

- When the WhatsApp bridge (`mcp-servers/grip-channel`, launchd job `com.grip.channel`) needs Claude, call `askClaude` in `src/hal-client.ts`, because it asks HAL's `/api/infer`, which serves Claude through CCH, HAL's Claude Code subscription signer (`~/.hal/lib/hal/utils/cch.py`), and so bills the subscription, never the key.
- Never load, export, pass or read `ANTHROPIC_API_KEY` in the bridge, its start-up scripts, its dotenv or any process it starts, because V>> ruled the paid key out for the bridge, and a key in its environment is one SDK call from per-token billing that HAL's daily spend ledger does not record.
- When CCH cannot answer, let the bridge's feature say it cannot answer now, because no failure of CCH justifies a fallback to the key; only V>> can change this rule.
- Import `@anthropic-ai/sdk` in the bridge for its types only, because a run-time import or `new Anthropic(` turns `src/__tests__/no-anthropic-api-key.test.ts` red.

Narrative (origin, incidents, measurements): `docs/doctrine/whatsapp-bridge-never-uses-the-anthropic-api-key.md`.
