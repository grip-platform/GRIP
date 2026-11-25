# /org -- Organisation Switcher

Manage the active GRIP organisation, switch context, and run onboarding convergence.

## Usage

```bash
/org                    # Show active org + consciousness level
/org list               # List all orgs with W-axis status
/org <slug>             # Switch org for this session
/org guest              # Activate guest/incognito session
/org onboard <slug>     # Run converge() onboarding loop
/org verify             # Run isolation check
/org brain status       # Show namespace + active merges
/org brain merge request <ns>   # Request brain merge (READ by default)
/org brain merge approve <id>   # Approve incoming request
/org brain merge list           # All merge states with weights
/org gui                # Open HTML dashboard in browser
```

## Implementation

All subcommands delegate to `lib/org_switcher.py`:

```bash
python3 ~/.claude/lib/org_switcher.py status [slug]
python3 ~/.claude/lib/org_switcher.py onboard <slug>
python3 ~/.claude/lib/org_switcher.py inject-env [slug]
python3 ~/.claude/lib/org_switcher.py verify
python3 ~/.claude/lib/org_switcher.py list
```

Brain subcommands delegate to `lib/brain_merge.py`:
```bash
python3 ~/.claude/lib/brain_merge.py status
python3 ~/.claude/lib/brain_merge.py request <target_namespace> [--type read_merge|full_merge|copy_merge]
python3 ~/.claude/lib/brain_merge.py approve <request_id>
python3 ~/.claude/lib/brain_merge.py list
```

GUI subcommand:
```bash
python3 ~/.claude/lib/org_dashboard.py
```

## Consciousness Levels (W-axis)

| Level | Name | Condition |
|-------|------|-----------|
| W=0 | LATENT | Profile exists, nothing wired |
| W=1 | DETECTED | GRIP_ORG or git config detected |
| W=2 | UNDERSTOOD | Profile valid + team field present |
| W=3 | CODIFIED | GitHub + Slack connectors both active |
| W=4 | EMBODIED | GRIP_AUTONOMOUS >= 1 AND both connectors active |

## Guest Mode

`/org guest` activates incognito mode with full isolation:
- No KONO persistence
- No external API calls
- No RSI loops
- No worker spawning
- 60-minute session TTL
