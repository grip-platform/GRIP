# GPT Runs on the ChatGPT Subscription, Never on Credits

Origin: V>> 2026-10-05, during the GRIP#7460 decision: "use subscription not credits!!!" and "you should /remember to always use the subscription for GPT". GPT-6 Astra and GPT-5.6 Sol had been called through Codex without the subscription login flag.

## The rule

> **Every call to an OpenAI model through Codex -- GPT-6 Astra, GPT-5.6 Sol or any other -- runs on the ChatGPT subscription: through `bin/grip-codex-ladder` rung 1, or as `CODEX_HOME=<a home from config/codex-accounts.json> OPENAI_API_KEY= CODEX_API_KEY= codex exec -c model_provider=openai -c forced_login_method=chatgpt ...`, with the `-c` flags after the subcommand. Never on workspace credits, never on the OpenAI API key. When every subscription window is full, wait for the reset and say when it is.**

## Why: one command, two billing paths

Which path a Codex call takes depends on where its `-c` flags sit, and the error text alone does not say which path ran.

**Codex reads `-c` overrides at one level only.** Measured on codex-cli 0.156.1 on 2026-10-05, on `~/.codex-ct`, with the same model and prompt each time:

| Flags | `provider:` line | Answer |
|---|---|---|
| `codex -c model_provider=openai -c forced_login_method=chatgpt exec ...` | `openai` | "Your workspace is out of credits. Add credits to continue." |
| the same, plus one `-c model_reasoning_effort=low` after `exec` | `hal-gateway` | "Quota exceeded. Check your plan and billing details." |
| every `-c` after `exec` | `openai` | "Your workspace is out of credits. Add credits to continue." |

Once the subcommand carries a `-c` of its own, Codex drops the root-level ones and the home's `config.toml` default takes over. On every subscription home here that default is `hal-gateway`. HAL routes the `gpt-` family to its `openai` provider (`lib.hal.model_route.route_model`), which calls `api.openai.com` with the metered key from the keychain service `grip-openai` (`~/.hal/lib/hal/gateway_egress.py`). That is the API-key path this rule forbids, and it cost nothing that day only because the call was refused for quota. The same behaviour was measured again with zero spend, against a provider that cannot connect. That run also showed two more things: a repeated key resolves to the last one, and `-m` beats `-c model=` in either order.

**The ChatGPT path itself falls through to workspace credits.** With the provider and the login both forced where Codex reads them, an exhausted subscription answers "out of credits", and the session record carries `limit_id: "premium"` and `rate_limit_reached_type: "workspace_owner_credits_depleted"`. Codex went to the workspace credit pool. Another session measured the same at 19:16 UTC that day (a note on GRIP#7487). One account served the call from the subscription (`limit_id: "codex"`). The other, whose windows were full, went to the credit pool and billed nothing, because its balance was 0. No client flag measured here stops that fall-through. What keeps GPT off credits is a credit balance of zero, which is why credits are never added (`project_no_new_spend_until_2026_10_11`).

**A correction.** The first version of this rule (GRIP#7487) said the login flag was a spend guard. It claimed that without the flag a full window went to credits, and that with it the same window answered "Quota exceeded" from the subscription. Both halves were wrong. The "Quota exceeded" call had its flags at the root and an effort flag after `exec`, so it ran on HAL's gateway against the API key and never reached the subscription. The flag still matters: it stops an API key being used for the login. It does not keep a call off credits.

## How to apply

- Prefer `bin/grip-codex-ladder`. It rotates across the subscription accounts and blanks both keys. It also places its flags after the subcommand, and drops a caller's own value for any key it sets.
- A direct `codex` call carries `-c model_provider=openai -c forced_login_method=chatgpt` after the subcommand (`codex exec -c ...`). It also needs `OPENAI_API_KEY` and `CODEX_API_KEY` set blank, and a subscription `CODEX_HOME`.
- Read the `provider:` line Codex prints before trusting an answer. `openai` is the ChatGPT path. `hal-gateway` with an OpenAI model is the API key.
- Before declaring a GPT seat unavailable, read its windows. The newest record containing `"window_minutes"` under `$CODEX_HOME/sessions` gives `used_percent` and `resets_at` for the five-hour window (`300`) and the weekly window (`10080`). Then try the next account in `config/codex-accounts.json`.
- Read `limit_id` in the session record, not the error text. `codex` is the subscription. `premium` with `workspace_owner_credits_depleted` is the credit pool.
- Never suggest or add credits. Report "subscription window full, resets HH:MM UTC", and wait.

## Where it is bound

- **`bin/grip-codex-ladder`** forces the ChatGPT login and blanks both keys on rung 1. Until 2026-10-05 it placed its flags at the root. So a caller that added its own `-c` after `exec` was moved to HAL's gateway and the API key, without rung 2's opt-in. It now places them after the subcommand and drops the caller's competing values. Rung 2 runs only with `GRIP_CODEX_ALLOW_PAID=1`. Pinned by `tests/test_codex_billing_ladder.py`.
- **The council's Codex seat**, `lib/sovereign_council.py` `SUBPROCESS_SPEAKERS["codex"]`, is also the /ultrathink Astra rung. It passes every `-c` after `exec` and strips both keys. Pinned by `tests/test_council_subscription_seats.py` and `tests/test_ultrathink_trio.py`.
- **The /ultrathink Astra seat's `astra-hal` rung**, which bills the OpenAI API key through HAL, runs only on the same `GRIP_CODEX_ALLOW_PAID=1` as the ladder's rung 2. Without it the seat falls from the subscription straight to Grok, and the footer records the skip. Until 2026-10-05 it ran whenever an OpenAI key resolved. Pinned by `tests/test_ultrathink_trio.py`, which now runs in the CI unbound sweep.
- **Every send point, since GRIP#7577 (2026-10-06).** `lib/openai_billing.py` runs inside the redaction shield whenever a transport passes its destination (`to=`), which every pinned send point now does. Without `GRIP_CODEX_ALLOW_PAID=1` it refuses, before any socket opens or process starts: a request to `api.openai.com`; an OpenAI model sent to HAL, judged the way HAL itself routes: an explicit `openai/` prefix, a bare id in HAL's `gpt-`/`chatgpt`/`o1`/`o3`/`o4` family table, or a bare id the catalogue maps only to `openai/` (a model of any other family passes whether or not `config/model_catalog.yaml` lists it: the first cut refused every uncatalogued model and broke the HAL router's fallback test and real Groq and Qwen ids, fixed 2026-10-06); a HAPPI envelope naming an OpenAI provider; and a Codex argv that is not the subscription form above. The council's `astra` and `openai` seats, the Roundtable's `astra` advisor and the agentmail script are Codex subscription subprocesses under their old names; AGORA's GPT rung and the exogenous roster's OpenAI seat carry no auth without the opt-in; `bin/grip-opencode` reads the per-token key only through `lib/openai-key.sh`, which serves it only under the opt-in. Pinned by `tests/test_openai_billing.py` (CI unbound sweep), including a tripwire that fails any new file under `lib/`, `bin/`, `scripts/` or `hooks/` naming `api.openai.com`, `grip-openai` or `OPENAI_API_KEY` unless it is allowlisted there with a reason.
- **The seat's home is bound too, since GRIP#7577.** A council seat spawn with `CODEX_HOME` unset takes the first home in `config/codex-accounts.json`, and the policy refuses any home outside that file, so the default `~/.codex` and the client's home can no longer be used by accident. It still does not rotate across accounts as the ladder does. A machine with no accounts file (CI) has no subscription seat at all, and the seat says so (`CODEX_INVOCATION_UNSAFE`) rather than falling to a key. Only a zero balance keeps a full window off a credit pool.
- **HAL's own copy of the key is guarded too, since HAL#667 (2026-10-06).** HAL's spend guard (`lib/hal/spend_guard.py`, `require_openai_destination`) refuses OpenAI per-token billing at every HAL send path unless `GRIP_CODEX_ALLOW_PAID` is exactly `1`. The paths are gateway egress, both OpenAI-compatible providers, HAPPI, the tool runtime, the tool CLI integration and the realtime voice backend. It judges by where the request goes (`api.openai.com`), not by who made the model, so an OpenAI model served by another host (Groq's `openai/gpt-oss-120b`) still passes. A refusal is its own outcome, never a retryable transport error. A HAL tripwire test fails any new `lib/` file that names the key or host without an audit. On Codex's route (`/v1/responses`), a spent subscription now ends in one final message naming the reset time, never an HTTP 429. OpenCode and Hermes on `/v1/chat/completions` keep the 429 with `Retry-After`, which they wait out.
- **The shell export is gated too, since HAL#699 (2026-10-08).** HAL's `bin/grip-env` used to export the per-token key as `OPENAI_API_KEY` into any shell that loaded it, so a tool started from that shell could bill per token without asking either guard (HAL#668 item 3). V>> chose to gate it: `OPENAI_API_KEY` is now exported only when `GRIP_CODEX_ALLOW_PAID` is exactly `1`, whether it would arrive as the `GRIP_OPENAI` alias or as a line under its own name in the encrypted file. `GRIP_OPENAI` itself is still exported, and one stderr line says what was held back. Pinned by HAL's `tests/test_grip_env_paid_alias.py`, whose four mutations each turn it red.

## Falsification

Wrong if a call that prints `provider: openai`, with the ChatGPT login forced and both keys blank, is ever billed to the API key. Then the provider line is not enough, and every GPT call should go through the ladder with its billing outcome asserted. Also wrong if a later Codex release reads root-level and subcommand `-c` together. Then placement stops mattering, though the ladder still has to own its keys, because a repeated key resolves to the last one.

## Scope

Every harness, tool and probe that reaches an OpenAI model through Codex: councils, /ultrathink, the ladder, and ad-hoc probes. Siblings:

- `rules/pool-a-offload-routing.md` (Pool C)
- the memory `feedback_subscription_first_billing_default`
- the memory `project_no_new_spend_until_2026_10_11`
