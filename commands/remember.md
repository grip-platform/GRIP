# /remember — Remember a rule/fact/lesson forever

Encode something so GRIP **never forgets it** — write it to the tier re-loaded
every session. Named for Mufasa: *"Remember who you are."*

## Usage

```
/remember <the rule, preference, or lesson to keep forever>
/remember paramount: <non-overridable doctrine>
/remember rule: <standing doctrine>
/remember memory: <a lesson or fact>
```

No explicit tier → the skill classifies it (an operator-stated durable rule →
paramount; a discovered lesson → memory).

## What it does

Invokes `skills/remember/SKILL.md`, whose deterministic backend
`lib/remember.py` writes:

- **paramount** → `rules/<slug>.md`, routed by its channel, + one line in
  `CLAUDE.md` under "Remembered laws"
- **rule** → `rules/<slug>.md`, routed by its channel
- **memory** → `memory/<slug>.md` + a `MEMORY.md` pointer

A rule's **channel** says who reads it and when: `floor` (every session and
subagent), `main` (the main session only), or `file` (when a matching file is
read or written). Its **readers** say who needs it (`makes-claims`, `writes-code`,
`runs-shell`, `writes-repo`, `sends-outside`, `dispatches`, `handles-personal-data`,
`talks-to-operator`, `orchestrates`). The skill asks for both. The rule is listed in
`config/rules-scope.json` to match, its narrative goes to
`docs/doctrine/<slug>.md`, and the backend prints how the always-loaded total
changed. Then it commits (Commit-Often) and prints the Mufasa confirmation.
Idempotent.

## Implementation

```bash
# a rule (the skill drafts the operative text and the narrative to files, then:)
grip-py lib/remember.py --tier <paramount|rule> \
  --slug <kebab-slug> --title "<Title>" --desc "<one-line>" \
  --body-file <operative.md> --repo "$PWD" \
  --channel <floor|main|file> [--paths "<glob>"]... --readers <reader>... \
  [--narrative-file <narrative.md>]

# a memory record: no routing flags or readers (the backend refuses them)
grip-py lib/remember.py --tier memory \
  --slug <kebab-slug> --title "<Title>" --desc "<one-line>" \
  --body-file <body.md> --kind <user|feedback|project|reference>
```

See `skills/remember/SKILL.md` for the full procedure.
