# /extend-context (Legacy)

Opus 4.7 defaults to 1M context since April 2026. This command is retained for switching to Sonnet 1M or reverting to Opus 1M.

## Usage

- `/extend-context` - Switch to Sonnet 1M (trades reasoning depth for cheaper per-token cost)
- `/extend-context revert` - Switch back to Opus 1M
- `/extend-context status` - Show current model state

## Protocol

### Extend

1. Run `python3 ~/.claude/lib/model_state_manager.py extend`
2. Issue `/model sonnet[1m]` to switch the active model
3. Confirm new thresholds: 70% = 700K, 80% = 800K tokens
4. Extended thinking remains active (Sonnet supports it too)

### Revert

1. Check current context usage - if over 800K tokens, warn that reverting may be close to the 80% block on 1M
2. Run `python3 ~/.claude/lib/model_state_manager.py revert`
3. Issue `/model opus` to switch back
4. Confirm thresholds reset: 70% = 700K, 80% = 800K tokens

### Status

1. Run `python3 ~/.claude/lib/model_state_manager.py status`
2. Display current model, context window, and switch history

## Cost Awareness

- Opus 1M: ~$15/MTok input, ~$75/MTok output (1M default)
- Sonnet 1M: ~$3/MTok input, ~$15/MTok output (1M max)
- Sonnet is cheaper per token but trades off reasoning depth

## Integration

- Context gate hook reads `.model-state.json` for dynamic thresholds
- Statusline shows current model state (opus:1M or son:1M)
- Broly PIPELINE/MESH levels no longer require model switching (Opus 1M is sufficient)
- Session start resets to opus/1M defaults
