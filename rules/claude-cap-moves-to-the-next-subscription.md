# A Claude Cap Moves to the Next Subscription, Never to the Paid Key

- When a Claude account reaches its usage cap, let the HAL wire move the session to the next pooled subscription account, because the wire does this per request with no paid key.
- When every pooled subscription is full, wait for the earliest reset and say when it is, because the operator ruled the paid API key out at a cap.
- Never engage the paid Anthropic API key unless the operator has set `GRIP_CLAUDE_ALLOW_PAID=1` (not by `--engage`, the `--note-429` threshold, cap policy `engage`, `~/.zshrc` or the relay), because a usage limit must never turn into per-token spend he did not approve.
- Launch every Claude session and spawn through the wire when it answers, because only a wired session can be switched while it runs; a direct session stops at the cap.

Narrative (origin, incidents, measurements): `docs/doctrine/claude-cap-moves-to-the-next-subscription.md`.
