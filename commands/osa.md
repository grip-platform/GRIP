---
description: Dispatch a named macOS AppleScript from the HAL vetted registry
disable-model-invocation: false
---

# /osa Command

Runs macOS automation via the HAL osascript control layer. Library-only —
no ad-hoc scripts. Tier-1 runs automatically; tier-2 requires a capability
token; tier-3 always asks for confirmation.

## Usage

```bash
/osa list                              # list all registered scripts
/osa info <name>                       # show one script's JSON schema
/osa <name> [key=value ...]            # dispatch a script
/osa mint <name> [--ttl N]             # mint a tier-2 capability token
```

## Implementation

```bash
HAL_OSA="$HOME/.hal/bin/osa"

if [[ ! -x "$HAL_OSA" ]]; then
    echo "HAL not installed. Clone: git clone https://github.com/CodeTonight-SA/hal.git $HOME/.hal"
    exit 1
fi

SUBCMD="${1:-list}"

case "$SUBCMD" in
    list)
        "$HAL_OSA" list
        ;;
    info)
        "$HAL_OSA" info "${2:?usage: /osa info <script_name>}"
        ;;
    mint)
        shift
        "$HAL_OSA" mint "$@"
        ;;
    *)
        # Bare script name with optional key=val pairs
        # Tier-1: dispatches immediately
        # Tier-2: mint a 60s token, then run
        NAME="$SUBCMD"
        shift
        TIER=$("$HAL_OSA" info "$NAME" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tier'])" 2>/dev/null || echo "1")
        if [[ "$TIER" -ge 2 ]]; then
            TOKEN=$("$HAL_OSA" mint "$NAME" --ttl 60)
            "$HAL_OSA" run "$NAME" "$@" --token "$TOKEN"
        else
            "$HAL_OSA" run "$NAME" "$@"
        fi
        ;;
esac
```

## Notes

- Audit log: every dispatch appends to `~/.claude/logs/osa.log`
- Remote: `HAL_OSA_REMOTE_DISPATCH=1 /osa <name> --host 100.x.x.x`
- Skills doc: `~/.claude/skills/osa/SKILL.md`
