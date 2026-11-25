# Personal Matters Stay Private (PARAMOUNT)

## The law

> **Work on the operator's personal matters lives only in his private guardian repository:
> encrypted with a key kept off GitHub, owned by his personal account, with no collaborators.
> Nothing from that work enters GRIP: no files, no test fixtures, no names, identifiers or
> quotes, and no commit messages or pull-request text that describe it. GRIP keeps only
> generic tools, and they read any personal value from the private repository or from a local
> file that git never tracks.**

The private repository's local path is in the gitignored `config/guardian.local.json`
(key `guardian_repo`). Its encryption key is a symmetric git-crypt key kept outside every
repository.

## Why GRIP cannot hold it, even encrypted

- **GRIP is shared.** Other accounts can read its files and its whole history. git-crypt hides
  file contents, but not file names, directory names or commit messages.
- **GRIP feeds a public surface.** The public decision chain is built from GRIP's commit
  messages. A redaction shield and a withheld list catch only what they already know about;
  wording that names no one, or a quote, passes both.
- **The only complete fix is that the material never arrives.** Everything downstream of GRIP
  is a net, and nets miss.

## What this means in practice

- A session working on a personal matter works in the private repository. Its commits,
  branches and pull requests never target GRIP.
- A generic GRIP tool may serve personal work, but it carries no personal values: no names,
  phone numbers, messaging IDs, ID numbers, matter terms or message text. Those live in the
  private repository's configuration, or in a gitignored local file.
- Tests in GRIP use values that are obviously fake (an all-zero phone number,
  `Jane Example`, `example.com`), never a real message or a real person's details.
- GRIP commit messages and pull-request text never describe a personal matter, even in
  general terms. A change made for personal work is described by what the code does.
- GRIP's history is left as it is: V>> chose to go forward only on 2026-10-05.

## Enforcement

Two separate things, both true at once. The rule is **binding**: every session and every
subagent must follow it, which is why it sits in the safety core. It is **enforced by review
and habit**: no hook yet refuses a personal value, and until a write-time check exists nothing
mechanical stops one. Other safety-core rules work the same way; a rule reaches a subagent so
the subagent knows it, whether or not anything enforces it. Moving the existing material out
of GRIP, and the write-time check itself, are separate changes.

One route has its write-time check since 2026-10-09. The memory kernel, `rules/HOLISTIC.md`,
is a tracked file that `lib/holistic_synthesiser.py` regenerates into the shared tree on every
Stop, and it folds in every memory record by name. Its scrub replaces denylisted names; it
cannot know that a record's title describes a personal matter, and on 2026-10-09 a record on a
personal matter of the operator's reached the shared tree's kernel that way. A record
whose frontmatter carries `private: true` (at the top level or under `metadata:`) is now left
out of the kernel entirely, and the R1 verifier does not demand it. Mark a personal-matter
record that way when it is written. Every other route (commit messages, pull-request text,
test fixtures, code) is still review and habit.

## Falsification

Wrong if, over the next ~20 sessions that touch personal work: generic tools whose personal
values live outside GRIP prove unworkable in practice (they break or drift), in which case the
tools move to the private repository too; or personal material reaches GRIP again, in which
case the write-time check is missing or too narrow and must be widened, never dropped. Track:
personal-matter items in the files on GRIP's main branch, not in its history (target 0 once the
existing material has been moved out).

## Scope

Every session and every harness, on every machine. Sits beside
`no-sensitive-info-in-grip.md` (financial, commercial and third-party data),
`operator-anonymity.md` (the operator's own identity), `proportionate-critique.md` (a
person's account of their own life is not an artefact) and the `guardian` skill, whose
private repository this rule makes the home of all personal work.

Narrative (origin, incidents, measurements): `docs/doctrine/personal-matters-stay-private.md`.
