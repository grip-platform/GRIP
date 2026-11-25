---
name: osa
description: Dispatch macOS automation scripts via the HAL osascript control layer. Tier-1 auto, tier-2 token-gated, tier-3 always confirms.
status: Active
effort: low
version: 1.0.0
triggers:
  - "/osa"
  - "mute my mac"
  - "unmute"
  - "lock my screen"
  - "what's my battery"
  - "what app is in focus"
  - "send imessage"
  - "run shortcut"
  - "what's playing"
  - "play music"
  - "pause music"
  - "next track"
  - "previous track"
  - "notify me"
  - "set clipboard"
  - "wake screen"
  - "sleep now"
  - "shut down"
  - "restart mac"
integrates:
  - bin/osa
  - lib/hal/osa/dispatch.py
  - lib/hal/osa/registry.json
see_also:
  - caffeinate
---

# /osa — HAL osascript dispatch skill

Routes macOS automation requests through the HAL vetted script registry.
No ad-hoc AppleScript is ever generated at runtime — every script is reviewed
and registered with typed parameter guards before it can be dispatched.

## Tier model

| Tier | Examples | Gate |
|------|----------|------|
| 1 | `battery_get`, `volume_get`, `frontmost_app_get`, `music_now_playing` | Auto — read-only, idempotent |
| 2 | `mute`, `notification`, `imessage_send`, `music_play`, `shortcut_run` | HMAC capability token required |
| 3 | `sleep_now`, `shutdown`, `restart` | AskUserQuestion always fires |

## Usage

```bash
/osa list                              # list all registered scripts
/osa info battery_get                  # show schema as JSON
/osa battery_get                       # run tier-1 script
/osa notification title=GRIP body=Done # run tier-2 (token minted automatically)
/osa shutdown                          # tier-3: asks for confirmation
```

## Natural-language trigger mapping

| User says | Script dispatched |
|-----------|------------------|
| mute / unmute my Mac | `mute` / `unmute` |
| what's the volume? | `volume_get` |
| set volume to 50 | `volume_set level=50` |
| lock my screen | `screen_lock` |
| wake screen | `screen_wake` |
| what's my battery? | `battery_get` |
| what app is in focus? | `frontmost_app_get` |
| notify me: title X, body Y | `notification title=X body=Y` |
| speak "message" | `speak text=message` |
| send iMessage to +27… saying … | `imessage_send handle=+27… text=…` |
| run Shortcut NAME | `shortcut_run name=NAME` |
| what's playing? | `music_now_playing` |
| play / pause / next / previous | `music_play` / `music_pause` / `music_next_track` / `music_previous_track` |
| copy TEXT to clipboard | `clipboard_set text=TEXT` |
| sleep now | `sleep_now` (tier-3: confirms) |
| shut down / restart | `shutdown` / `restart` (tier-3: confirms) |

## Implementation protocol

When `/osa` is invoked, the agent follows this protocol:

1. **Detect HAL**: check if `$HOME/.hal/bin/osa` is executable.
   - If missing: print install instructions and stop.

2. **Map intent to script name** using the trigger table above.
   - Unrecognised intent: run `$HOME/.hal/bin/osa list` and ask the user which script.

3. **Dispatch**:

```bash
HAL_OSA="$HOME/.hal/bin/osa"

if [[ ! -x "$HAL_OSA" ]]; then
    echo "HAL not installed at $HOME/.hal. Install: git clone https://github.com/CodeTonight-SA/hal.git $HOME/.hal"
    exit 1
fi

# Tier-1 (no token needed):
"$HAL_OSA" run battery_get

# Tier-2 (mint token, then run):
TOKEN=$("$HAL_OSA" mint notification --ttl 60)
"$HAL_OSA" run notification title=GRIP body="Build done" --token "$TOKEN"

# Tier-3 (user confirmation fires inside dispatch):
"$HAL_OSA" run shutdown
```

4. **Remote dispatch** (Tailscale paired Mac):
   - Append `--host <tailscale_ip>` to the run command.
   - Requires `HAL_OSA_REMOTE_DISPATCH=1` in environment.
   - Tier-3 scripts are capped at tier-2 on remote hosts by default.

5. **Audit log**: every dispatch appends one JSONL entry to `~/.claude/logs/osa.log`.

## Fallback (HAL unavailable)

If `$HOME/.hal` is not installed, tier-1 scripts can be dispatched directly:

```python
import sys, os
sys.path.insert(0, os.path.expanduser("~/.hal"))
from lib.hal.osa import osa, ProvenanceType
result = osa("battery_get", provenance=ProvenanceType.HUMAN_TYPED)
print(result.stdout)
```

This fallback covers read-only operations only. Tier-2+ require the full HAL
capability-token infrastructure.
