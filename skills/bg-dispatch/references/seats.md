# Seats: the Shape D launch script

`/bg-dispatch` reads this file only when a launch seats outside models.

Write the brief to `$BRIEF`, choose `$OUT`, set `experts` and `advisor`, and run this with the Bash tool's
`run_in_background: true`:

```bash
G="${CLAUDE_DIR:-$HOME/.claude}"
PYTHONPATH="$G" "$G/bin/grip-py" -P - "$BRIEF" > "$OUT" 2>&1 <<'PY'
import pathlib, sys
from lib.decision_privacy import may_send
from lib.roundtable import assert_disjoint
from lib.sovereign_council import (DEFAULT_JUDGE, DEFAULT_SPEAKERS, fan_out, judge,
                                   no_quorum_verdict, roundtable, seal)

experts = {}    # seat -> its expert role. Other seats answer without a role.
advisor = None  # "codex" makes this a Roundtable. It may not also judge.
speakers = list(dict.fromkeys([*DEFAULT_SPEAKERS, *experts]))  # Edit to seat another panel.
prompt = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
if experts:  # In the prompt, because the seal does not cover system prompts.
    prompt += "\n\nPanel roles:\n" + "\n".join(f"- {s}: {r}" for s, r in experts.items())
if len(speakers) < 2:
    sys.exit("bg-dispatch: a sitting needs at least two seats")
assert_disjoint(DEFAULT_JUDGE, speakers + ([advisor] if advisor else []))
allowed, why = may_send(prompt)
if not allowed:
    sys.exit(f"bg-dispatch: not sent to outside seats: {why}")
if advisor:
    out = roundtable(prompt, advisor, speakers, DEFAULT_JUDGE, system_prompts=experts)
    results, verdict, sha = out["results"], out["verdict"], out["sha"]
else:
    results, _ = fan_out(prompt, speakers, experts)
    answered = any(r["ok"] for r in results)
    verdict = judge(prompt, results, DEFAULT_JUDGE) if answered else no_quorum_verdict()
    _, sha = seal(prompt, results, verdict)
print(sha)
sys.exit(0 if verdict["judge_ok"] and sum(r["ok"] for r in results) >= 2 else 1)
PY
```

`$OUT` ends with the seal. The script exits 1 unless the judge answered and at least two seats did: a sitting below that
floor has ratified nothing. A panel of one or a vendor clash stops it before any paid call. An HTTP seat that returns an
empty 200 still counts as answered (GRIP#8921), so read the sitting before trusting the exit code.

## Reading the settings

- `experts` empty is a plain sitting. Each seat answers the same brief and another vendor judges. That is a Council.
- `experts` set gives each named seat its own system instruction, and the rest of the panel answers without a role.
  Edit `speakers` to seat a different panel of two or more. Whether roles make the answers better is not measured yet,
  so use them when the brief has clearly different angles.
- `advisor` set makes it a Roundtable: the advisor frames the question, and its brief rides inside the sealed prompt.
- A long brief costs seats. On 2026-11-01 `groq` answered HTTP 413 to a 33,141-character brief, and `deepseek` reached
  its token cap before stating findings, at both 3,000 and 8,000 tokens (`GRIP_COUNCIL_SPEAKER_MAX_TOKENS`). The other
  seats answered, so the sitting still passed its floor.
- A refusal from `may_send` is final. That text stays home (`rules/personal-matters-stay-private.md`).
- `may_send` checks the brief and nothing else. `astra`, `codex` and `openai` run Codex in a read-only sandbox, which can
  read local files (GRIP#8920). On a brief carrying third-party text, edit `speakers` to HTTP seats only, such as
  `["deepseek", "xai", "groq"]` (`rules/untrusted-content-capability-lock.md`).
- Seat names come from `REGISTRY` and `SUBPROCESS_SPEAKERS` in `lib/sovereign_council.py`. `fable` is the one that
  spends the weekly limit, because it runs through `claude -p`.

## Why this script and not another tool

| Tool | What it does | Why not here |
|---|---|---|
| `bin/grip-sovereign-council` | The same council, with `--speakers`, `--judge` and `--advisor` | It has no per-seat roles and no `may_send` check. Use it to read a sitting back |
| `lib/roundtable.py` | Frontier advisors design a judging plan | Its default rotation seats an Anthropic advisor through `claude -p`, which spends the weekly limit |
| `bin/grip-ultrathink` | Three seats that debate and falsify each other | Its default seats include Fable through `claude -p` |
| `lib/broly_council.py` | Weighted votes per broly role | The role name never reaches the model (GRIP#8908), so it is not an expert panel yet |
| `bin/grip-council --roster open` | Outside models through the HAL gateway | A fair alternative when the gateway is up, with no per-seat roles |

A seat that needs tools of its own (reading files, editing) is not a council seat. Use `bin/grip-opencode run` or
`bin/grip-codex-ladder`, and give it the smallest capability that does the job
(`rules/untrusted-content-capability-lock.md`).
