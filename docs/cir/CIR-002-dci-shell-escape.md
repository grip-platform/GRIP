# CIR-002: DCI Shell Escape — No Python f-strings with ${} in !`command`

**Date:** 2026-03-23

## Intent

Ensure DCI `!`command`` blocks work reliably across all shells (zsh, bash).

## Behaviour

- **Given** a DCI block containing `!`python3 -c "print(f'${d[\"key\"]}')"``
- **When** the skill is invoked
- **Then** zsh interprets `${d[...]}` as shell variable expansion BEFORE Python runs
- **Result** "bad math expression" error — the DCI block fails

## Constraints

- DCI commands run through the user's shell (zsh on macOS)
- Shell variable expansion happens before command execution
- `$` followed by `{` is ALWAYS shell expansion in zsh

## Decisions

1. **Use %-format strings**: `print('%.2f' % (d['key'],))` instead of f-strings with `${}`. Rejected: escaping `$` with `\$` — fragile, breaks in some shell contexts.
2. **Avoid complex Python in DCI**: Keep DCI commands simple. If complex logic needed, write a helper script in `lib/` and call it from DCI. Rejected: inline complex Python (maintenance nightmare).
3. **`$HOME` is safe**: Shell expands `$HOME` correctly — this is desired behaviour. Only `${var}` inside Python strings is problematic.

## Status

Active — applies to ALL future DCI skill development.
