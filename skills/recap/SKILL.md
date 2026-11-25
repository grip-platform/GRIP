---
name: recap
description: Plain-language summary of what shipped in a sprint or time window — from git history, formatted for client updates, sprint reviews, or weekly reports. Optionally renders a designed PDF and delivers it to named recipients.
version: 1.1.0
status: Active
triggers:
  - "/recap"
  - "what did we ship"
  - "sprint summary"
  - "what shipped this week"
  - "sprint recap"
  - "what have we built"
  - "client update"
---

# /recap — Sprint Summary

A plain-language summary of what shipped in a time window, grouped by theme,
suitable for clients, sprint reviews, or the weekly Slack update.

## Usage

```
/recap                  # Last 7 days (default)
/recap --days 14        # Last 2 weeks
/recap --since "Apr 14" # Since a specific date
/recap --brief          # One bullet per group (stakeholder mode)
/recap --slack          # Post to #grip-updates after generating
/recap --pdf            # Also render a designed PDF (does not send it)
/recap --send V>>,A>>   # Render and deliver to named recipients
```

## Output Example

```
GRIP Sprint Recap — Week of 14 Apr 2026

FEATURES SHIPPED
• Google Calendar integration (/gcal) — fetch any workspace user's calendar
  via DWD; morning brief and standup-ready output
• Skill catalog (/check-reachable) — all 242 GRIP capabilities now browsable;
  202 previously invisible skills surfaced
• Daily brief (/morning) — one command for calendar, remaining work, and Slack
• Standup prep (/standup) — generates standup bullets from git log in seconds
• Gemini 2.5 Flash — multi-model analysis now available (zero new dependencies)

BUG FIXES
• Skill routing false positive — "padding" was triggering the "add" tier;
  fixed with word-boundary regex guards across all tier patterns
• WeasyPrint PDF render path — fixed for latest HAL provider builds

SECURITY
• protobufjs ACE (CVE-2023-36665) — patched via npm overrides
• sqlite-lembed supply chain — dormant PyPI package replaced by vendored binary

QUALITY
• evolve.py DIP compliance — injectable callable defaults eliminate
  4 hardwired concrete imports (sys.path manipulation removed)
• 68 new tests added across routing and calendar modules

5 features · 2 bug fixes · 2 security patches · 1 refactor
```

## Execution Protocol

### Step 1 — Fetch commits

```bash
git log -n 10000 --since="N days ago" --format="%s|%h|%ad" \
    --date=short --no-merges 2>/dev/null
git rev-list --count --no-merges --since="N days ago" HEAD
```

Keep the explicit `-n`. The RTK hook rewrites a plain `git log` to
`rtk git log`, which stops at 50 commits when no `-n` is given and says
nothing about it (GRIP#9179: 50 lines against 208 commits for one week).
An explicit `-n` is kept, byte for byte, and works on a machine without
rtk, where `rtk proxy` would fail. The log's line count must equal the
`rev-list` count. If it does not, the list is short: raise the `-n` above
the count, run it again, and never send a recap built on the short list.
(`rev-list --count` is safe through the hook: it gave 208 both ways.) For
a recap across several repositories, compare the counts per repository.

Exclude chore/sync commits unless they describe meaningful state changes
(e.g. "sync learning state" → skip; "sync evo + genome after sprint" → include
as "Genome evolved to Gen N").

### Step 2 — Group by theme

Classify each commit into one of these buckets using the conventional
commit type and subject:

| Prefix | Group |
|--------|-------|
| `feat` | Features Shipped |
| `fix` | Bug Fixes |
| `fix(security)`, `security` | Security |
| `refactor`, `perf`, `style` | Quality |
| `docs` | Documentation |
| `test` | Testing |
| `build(deps)` | Security when an advisory shows it patched a vulnerability (see below); otherwise skip |
| `chore` | (skip unless notable) |

A dependency bump can fix a security advisory even when its subject does not
say so. Its Dependabot pull request body may not name the advisory either.
Before you write a security bullet, read each alert fixed in the window, once
per repository:

```bash
gh api --paginate 'repos/<owner>/<repo>/dependabot/alerts?state=fixed&per_page=100' \
  --jq '.[] | select(.fixed_at >= "<window start, UTC: YYYY-MM-DDTHH:MM:SSZ>") | "\(.fixed_at)|\(.dependency.package.name)|\(.security_advisory.ghsa_id)|\(.security_advisory.severity)|\(.security_advisory.summary)"'
```

The filter compares text, not dates, so write the window start in UTC, as
`YYYY-MM-DDTHH:MM:SSZ`. Name the advisory ID. State the risk in plain words
from the advisory's summary, not from memory.

Dependabot can miss a vulnerability, or raise its alert late. So an advisory
you have read, which shows the old version affected and the new one patched,
also counts. A bump with neither is a dependency update, not a security patch.

### Step 3 — Translate to plain language

For each commit, write ONE bullet that:
- States what the user gets (not what changed in code)
- Adds a parenthetical with the user-facing command or entry point where applicable
- Stays under 15 words

Examples:
- `feat(gcal): Google Calendar via DWD` → "Google Calendar integration (/gcal) — fetch any workspace user's events"
- `fix(routing): word-boundary regex guards` → "Skill routing false positive fixed — 'padding' no longer triggers 'add' tier"
- `fix(hal): cap max_tokens for DeepSeek` → "DeepSeek and Ollama token cap fixed — prevents API errors on long prompts"

### Step 4 — Add summary line

Tally the groups: "N features · M fixes · P security patches · Q refactors"

### Step 5 — Format

```
GRIP Sprint Recap — Week of [start date]

[GROUP]
• [bullet]
• [bullet]

[GROUP]
• [bullet]

N features · M fixes · ...
```

Plain text, copy-pasteable. No markdown symbols unless `--slack` is passed,
in which case use Slack bold (*...*) for group headers.

### Step 6 — Sweep, then deliver (only when asked)

Steps 1 to 5 produce the recap. This step runs only if the recap is leaving the
session: `--slack`, `--send`, or a non-empty `default_recipients`. If none of
those apply, the recap is printed and the skill is finished.

Anything about to go outward is swept first — the text for `--slack`, the
rendered PDF for `--send`. See **Designed PDF Delivery** below for the sweep and
for how recipients are resolved.

## Brief Mode

`/recap --brief` condenses to one line per group:

```
Features: gcal + skill catalog + morning brief + standup + Gemini (5 shipped)
Fixes: routing false positive, WeasyPrint render (2 fixed)
Security: protobufjs CVE, sqlite-lembed supply chain (2 patched)
```

## Slack Mode

`/recap --slack` generates the full recap and posts it to #grip-updates
(C0EXAMPLE01) via the Slack MCP tool. Use after sprint reviews or on Fridays.

## Designed PDF Delivery

`--pdf` renders the recap as a designed document; `--send` renders it and
delivers it. They are separate on purpose: rendering is cheap and reversible,
sending is neither.

### Why this is a design step, not a print step

A recap rendered straight from the plain-text output reads as a wall of
bullets. The step that makes it worth sending is choosing a **subject** — the
single most interesting thing the window revealed — and letting the layout
argue it. Invoke `/artifact-design` for the visual treatment and
`/generate-pdf` for the render. The bullets remain the evidence; the design
decides what the reader takes away.

Keep the finished PDF somewhere durable. The session scratchpad is cleared by
the operating system, so a deliverable written only there will not survive the
week it describes.

### Recipient resolution

Recipients resolve through the same ladder `/generate-pdf` uses for branding,
so there is one pattern to learn rather than two:

| Order | Source | Effect |
|-------|--------|--------|
| 1 | `--send <names>` argument | Explicit; taken as given, still allowlist-checked |
| 2 | `config/recap-delivery.json` → `default_recipients` | Operator's persisted choice |
| 3 | Neither present | **No delivery.** The recap is still generated and printed |

Names are signatures (`V>>`, `A>>`), never raw phone numbers. Each name is
resolved against `config/channel-allowlist.json`, which is the canonical store
for who may be contacted on which platform. A name absent from that allowlist
is refused rather than guessed at — two people can share a first initial, and a
document sent to the wrong person cannot be recalled.

**If the allowlist cannot be read, refuse to send and say so.** This is a state
that really occurs: the file is git-crypt encrypted, so on a machine without the
key it decrypts to nothing. An unreadable allowlist means the identity of the
recipient is unknown, which is not the same as the recipient being fine — so it
fails closed. Never fall back to a number found elsewhere.

### Configuration

`config/recap-delivery.json`:

```json
{
  "default_recipients": [],
  "auto_pdf": false,
  "pdf_variant": null
}
```

| Field | Meaning |
|-------|---------|
| `default_recipients` | Signatures to deliver to when `--send` is omitted. Empty means never send by default |
| `auto_pdf` | When true, `/recap` renders the PDF without `--pdf`. Rendering only — it does not imply sending |
| `pdf_variant` | Branding variant passed to `/generate-pdf`. Null uses that skill's own resolution |

### Sending is opt-in, and stays opt-in

Delivery reaches a human outside the session and cannot be undone, so the
default is off and an empty recipient list is honoured as "do not send" rather
than treated as a missing value to fill in. `auto_pdf` deliberately does not
imply delivery: a config that quietly turned recaps into outbound messages
would be the wrong thing to be able to switch on by accident.

### Before anything leaves: sweep it

Sweep whatever is about to go outward for real hostnames, internal IPs, client
names, credentials, and personal contact details.

The rule is keyed on **what is leaving**, not on which flag triggered it — a
delivery configured through `default_recipients` sends exactly what `--send`
sends, and must be swept identically:

| What is leaving | What to sweep |
|-----------------|---------------|
| A PDF (from `--send` or `default_recipients`) | The text extracted from the finished PDF, then the rendered pages themselves |
| Recap text (from `--slack`) | The text as it will be posted |

The text path needs this as much as the PDF path does. Recap bullets are written
from commit subjects, and a commit subject is perfectly capable of naming a
staging host or a client. `--slack` was an outbound path before this change and
was never swept; that gap is closed here.

The PDF needs the extra step of looking at the rendered pages because a PDF is a
surface no secrets scanner reads — see
`rules/visual-artifact-leak-prevention.md`. A render that exits cleanly proves
the file was produced, not that its contents are safe to send.

Confirm the sweep actually read something. A sweep over an empty extraction
reports no matches and is indistinguishable from a clean one, so check the
character count before trusting the result.

**A zero character count is a failed sweep, not a clean one. Refuse to send and
say so.** This is the same rule as the unreadable allowlist above: no evidence is
not evidence of safety. It really happens — a PDF whose pages are images carries
no text layer, so extraction returns nothing while the pages themselves may show
anything at all. Re-render, extract again, or check the pages by eye; never treat
the empty result as permission to send.

### Example

```
/recap --days 7 --send V>>,A>>

  → 75 commits across 2 repositories
  → subject chosen: the self-improvement loop was scoring itself with
    three measurements that had never been taken
  → rendered: ~/CodeTonight/milestones/grip-calibration-report-2026-08-02.pdf
  → leak sweep: 8,660 characters extracted, 0 matches across 5 categories
  → delivered to V>> and A>>
```

## Falsification

This skill fails if:
- Bullet language contains commit hashes, file names, or function names
  (those belong in a diff, not a recap)
- A "security" item omits its CVE or advisory ID, omits the risk in plain
  English, or states a risk that the advisory does not support
- The feature count in the summary line doesn't match the bullets above it
- A recap is delivered to anyone without `--send` or a non-empty
  `default_recipients` — delivery must never happen by default
- A recipient is resolved to a phone number or handle that is not in
  `config/channel-allowlist.json`
- A PDF is sent without its extracted text having been swept, or the sweep is
  trusted despite extracting nothing
- The PDF is written only to the session scratchpad, where it will not survive
