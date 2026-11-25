# Auth-bucket failover

When Claude's subscription is being rate limited, GRIP can move new Claude sessions onto a separate, pay-as-you-go API key, and move them back once the subscription serves again. The API key draws on a different rate-limit pool, so it keeps work moving through a throttle. It is billed per token, so an engagement that outlives the throttle costs money. That is why it now ends itself.

**Since 2026-10-12 nothing reaches the key without the operator's opt-in.** That night the HAL wire moved five capped sessions to another pooled subscription, while this tool moved every new shell onto the paid key. V>> ruled: "This is the way it should work!! Not via paid API KEY". So every path below that writes the marker does nothing unless `GRIP_CLAUDE_ALLOW_PAID` is exactly `1`. A capped session that rides the wire moves to the next pooled subscription; anything else waits for the reset (`rules/claude-cap-moves-to-the-next-subscription.md`).

A usage cap, such as the subscription's 5-hour session limit, is different: it ends at a time the message states. By default GRIP tells you when it resets and waits, and does not switch to the key (see [Cap policy](#cap-policy)).

The tool is `bin/grip-auth-failover`. Its state is one marker file. While that file exists and says `"bucket": "api-key"`, new sessions use the key.

## How it switches on

| Trigger | Command | Notes |
|---|---|---|
| By hand | `GRIP_CLAUDE_ALLOW_PAID=1 grip-auth-failover --engage [reason]` | Refuses, writing nothing, without the opt-in or if the API-key keychain entry is missing. Only `--reset` ends a hand engagement. |
| Sustained rate limiting | `grip-auth-failover --note-429 [msg]` | With `GRIP_CLAUDE_ALLOW_PAID=1`, engages after 3 transient 429s within 300 seconds (`NOTE_429_THRESHOLD`, `NOTE_429_WINDOW_SECS`). Without it, it counts and says it did not engage. A single 429 never engages it. |
| A usage cap | `grip-auth-failover --note-cap [reset or message]` | By default never engages: it notifies with the reset time and waits (see [Cap policy](#cap-policy)). Under the cap policy `engage` it fails over at once if the API-key keychain entry exists, and otherwise notifies and pauses until the reset. It never stops silently (`rules/rate-limit-never-blocks.md`). It accepts the reset time, or the harness's whole message, from which it reads the time. |
| One model's allowance used up | `grip-auth-failover --note-model-cap [msg]` | Never engages. "You're out of usage credits. Run /usage-credits to keep using Fable 5.1", often followed by "or /model to switch models", means one model is spent while the others still run on the subscription, so it only notifies, naming the model. `/model` switches. It is recognised by "out of usage credits" together with a model name that carries a version (such as "Fable 5.1") or an offer to switch models. |

Interactive sessions feed `--note-429`, `--note-cap` and `--note-model-cap` through `hooks/repl-429-autodetect-hook.py`, which reads each session's transcript for API errors. Since GRIP#7343 it trusts the HTTP status the harness records: only a 429 counts as a throttle. Before that, a 31-minute outage on 2026-10-03, whose 502 errors end with "try again in a moment", was read as rate limiting and kept new sessions on the key for eight hours (GRIP#7274). A usage cap is still recognised by its wording at any status, because "credit balance is too low" arrives as a 400, and the hook passes its text to `--note-cap` so the tool can read the reset time. So is one model's allowance running out, which at status 429 used to count as a throttle: three such messages inside five minutes would have moved every model onto the key. The subscription's session limit ("You've hit your session limit · resets 7:20am (UTC)") was recognised by no wording rule until GRIP#7274, so at status 429 it counted as a throttle too; it is now a usage cap. The relay has its own detector, `_is_transient_429` in `lib/relay_dispatcher.py`, which matches rate-limit wording only. It sends caps to `--note-cap` with no message, so its notices give no reset time.

`grip-auth-failover --classify [msg]` tells the error kinds apart: exit 0 means transient (retry), exit 4 means a usage cap, and exit 5 means one model's allowance is used up (switch model with `/model`; the API key is not engaged).

## Cap policy

The cap policy decides what an account-wide usage cap does. Examples are the 5-hour session limit and the weekly limit.

- **`notify`, the default.** `--note-cap` records the cap, sends one notification saying when the subscription resets, prints one line saying the paid key was not engaged, and waits. It never creates the marker.
- **`engage`.** `--note-cap` engages the API-key bucket at once, which every cap did before GRIP#7274. It needs `GRIP_CLAUDE_ALLOW_PAID=1` as well; without it the policy reads as `notify`, and `--status` says the engage was refused.

**One notice per cap, not per message.** Every session that hits a cap writes its own error record, so one cap can arrive as dozens of messages, and a burst of identical notices teaches the operator to ignore them. Under `notify` the notice goes out when none has gone out for this cap, when the message names a different reset time, or when the last notice is more than 15 minutes old (`NOTE_OK_COOLDOWN_SECS`). Any other repeat only prints its line, which says the notice already went out. A cap that names no reset time, such as every cap the relay reports, therefore repeats its notice every 15 minutes. A notice counts as sent only when the push succeeded, so a failed push is retried on the next message instead of hiding the cap.

To opt in, set `GRIP_CLAUDE_ALLOW_PAID=1` and `GRIP_AUTH_FAILOVER_ON_CAP=engage`, or put `{"on_cap": "engage"}` in `config/auth-failover.local.json` under the GRIP directory. That file is gitignored, because it is a choice about one machine's spending. The environment wins over the file, and any value other than `notify` or `engage` is ignored. `grip-auth-failover --status` shows the policy in force and where it was set.

The tracked `settings.json` sets `GRIP_AUTH_FAILOVER_ON_CAP=notify` in its `env` block and never sets `GRIP_CLAUDE_ALLOW_PAID` (V>> 2026-10-12, reversing the `engage` he chose on 2026-10-11); `tests/test_grip_auth_failover.py::test_tracked_settings_never_select_the_paid_key` pins both. Installs made from `config/settings.reference.json` do not carry the variable and keep `notify`. The settings merge (`lib/settings_merge.py`) keeps the variable through install updates and post-merge syncs, as it keeps the model variables, and the machine's own value wins; without that, every regeneration dropped it, because the rest of `env` is GRIP-managed. To opt in on one machine, set both variables in that machine's environment: the local file cannot override the environment.

Why waiting is the default: the operator's standing rules are subscription first and no new paid spend. A replay of 13 days of real error records (GRIP#7274) found the session-limit message engaging the paid key six times, because no wording rule recognised it and at status 429 it counted as a throttle. GPT-6 Astra and DeepSeek v4 Pro, reviewing separately, both recommended notify-and-wait as the default. Both also warned that the two halves must change together. Recognising the session limit without changing what a cap does would have engaged the key on the first message instead of the third.

Where a cap is recorded: under `notify` it goes in `auth-cap.json` beside the marker, as `last_cap`, `cap_reset` and, once a notice for it was delivered, `notified_at`, never in the marker itself. `~/.zshrc` exports the paid key whenever the marker file exists, whatever it contains, so a cap written there would engage the key after all.

If the key is already engaged (by hand, by a run of throttles, or by an earlier cap under `engage`), a cap is recorded in the marker as before, under either policy, and the notification says no fallback is left.

## The marker

`state/session/auth-failover.json` under the GRIP directory. Its fields:

- `bucket`: `"api-key"` while engaged.
- `by`: who engaged it. `"grip-auth-failover"` for `--engage`; values ending in `(auto)` for `--note-429` and `--note-cap`. Only automatic engagements end themselves.
- `engaged_at`, `reason`, `entry` (the keychain item, `grip-anthropic-external`), and `last_cap` and `cap_reset` after a usage cap that engaged the key or arrived while it was engaged.

The 429 counter lives beside it in `auth-429-window.json`, and a cap waited out under the `notify` policy in `auth-cap.json`. Neither is an engagement.

The tool and every reader resolve the path the same way: `$GRIP_AUTH_FAILOVER_MARKER` if set, else `<claude dir>/state/session/auth-failover.json`. A test checks the tool against one of the readers, `lib/ultraplan_remote.default_marker_path`.

## What it changes while engaged

- **New shells.** `~/.zshrc` tests for the marker and, while it exists, exports `ANTHROPIC_API_KEY` from the keychain item `grip-anthropic-external`. Every `claude` started from such a shell bills the key.
- **Relay spawns.** `lib/relay_dispatcher.py` re-reads the marker on every spawn, so `--engage` and `--reset` take effect at once with no daemon restart. While engaged, the relay keeps the API key, overriding its usual stripping of it (GRIP#2164); otherwise the relay would keep riding the throttled subscription.
- **The InfiniteMachine proxy.** `lib/grip_infinite.py` keeps `ANTHROPIC_API_KEY` while the marker selects the key, and says so on a visible line, so the paid key is never carried silently.
- **Cloud launches.** `lib/ultraplan_remote.py` refuses to launch while the marker exists, because an API credential takes precedence over the subscription there; `--unset-api-key` launches without it.

What it cannot do: change the credentials of a session that is already running. The harness owns a live session's auth.

## How it switches off

- **By hand**, always: `grip-auth-failover --reset`.
- **Automatically, for automatic engagements** (GRIP#7343). When a session that still bills the subscription gets a real model reply, the hook sends `grip-auth-failover --note-ok`. That ends an engagement whose `by` ends in `(auto)`, once the newest sign of throttling (the engagement itself, the last 429 or the last cap) is at least 15 minutes old (`NOTE_OK_COOLDOWN_SECS`). The recovery is announced the same way the engagement was.
- A quiet period alone never ends an engagement (AGORA #3662): ending one needs positive evidence that the subscription serves again.

Recovery needs a session that started before the engagement. A session started while engaged carries the key, so its replies prove nothing about the subscription and the hook sends no `--note-ok`. If no such session is running, use `--reset`.

## Diagnosing

1. `grip-auth-failover --status` shows the active bucket and the marker.
2. If the engagement followed a stall, check the gateway audit log (`~/.hal/logs/gateway-audit.jsonl`, UTC): `event: unreachable` means Anthropic could not be reached at all, which the API key cannot fix either.
3. If it engaged on 429s, `auth-429-window.json` beside the marker shows how many arrived and when the last one came; `--note-ok` measures its cool-down from that time too.
4. If a session stopped on a usage cap and nothing engaged, `auth-cap.json` beside the marker shows when the last cap arrived, the reset time it named, and when its notice went out (`notified_at`; absent if the push failed).
5. A `claude -p` that fails on credit, or an `ANTHROPIC_API_KEY` in an environment nobody set, usually traces back to an engaged marker.

## Known limits

- **One global marker.** A sustained throttle on one model still moves every session to the key. One model's allowance running out is recognised and never engages: "You're out of usage credits … keep using Fable 5.1", with or without "or /model to switch models", feeds `--note-model-cap`, which only notifies.
- **Repeat suppression remembers one cap.** `auth-cap.json` holds only the latest cap. Messages that alternate between two reset times each count as a new cap and send a notice. One example is a session message naming "7:20am (UTC)" alternating with a relay message naming none. If the record cannot be written, the next message sends again: a lost record can repeat a notice, never hide one.
- **The no-fallback message assumes both buckets are capped.** When the key is already engaged, a cap is reported as "both the subscription and the API-key bucket are usage-capped". The tool cannot tell which bucket the message came from: a session limit can only come from a session still on the subscription, and "credit balance is too low" only from one on the key.

## Tests

`tests/test_grip_auth_failover.py` covers the tool, including recovery, its cool-down, one model's allowance, the cap policy, and the repeat suppression of cap notices through a stub notify script. `tests/test_repl_429_autodetect_hook.py` covers the hook's status gating, its recovery signal, the routing of model allowances and session limits, and two runs of the real hook into the real tool with every path under a temporary directory. `tests/test_rate_limit_classifier.py` covers the wording and the reset time. All three run in CI.
