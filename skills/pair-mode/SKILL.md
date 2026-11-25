---
name: pair-mode
description: "Pair with GRIP: the operator drives and GRIP navigates by default, thinking aloud and checkpointing through the /hitl family, the roles swap on a word, and /mesh carries the pair across machines or sessions."
status: Active
effort: medium
version: 4.0.0
triggers:
  - "/pair"
  - "/pair-mode"
  - "/mode pair"
  - "pair with me"
  - "you drive"
  - "I'll drive"
integrates:
  - mesh
  - hitl
  - asking-users
  - creating-wizards
  - hitl-decision-matrix
  - reasoning-with-ai
---

# Pair Mode

Pairing with GRIP is pair programming with one human seat and one GRIP seat.
The **driver** chooses each step and owns the result; the **navigator** looks
ahead, checks the work and says what it sees. By default the operator drives
and GRIP navigates. The roles swap on a word.

This is a posture, not a transport. In one session nothing needs connecting:
the operator and GRIP are already in the same conversation. When the pair spans
two machines or two sessions, `/mesh` carries it (see "Across machines or
sessions" below) and the posture does not change.

Revived on 2026-10-03 by V>>'s decision. Version 3.0.0 described two developers
pairing through a bespoke transport; GRIP#2641 moved that transport to
grip-session-mesh on 2026-05-09 and marked the whole skill deprecated, which left
the pairing itself with no home. The thinking posture comes from
`reasoning-with-ai/SKILL.md` (articulate first, the human's judgement stays
central); the checkpoints come from the `/hitl` family.

## The two seats

| Seat | Default holder | What the seat does |
|------|----------------|--------------------|
| Driver | the operator | Chooses the next step and owns the result. While the operator drives, GRIP carries out only the steps the operator names, or watches while the operator edits by hand. |
| Navigator | GRIP | Looks ahead, checks each step against the code and the goal, challenges what looks wrong, and proposes what comes next. Reading, searching and read-only checks are navigating. |

The test for which seat GRIP is in: **is GRIP choosing the next change, or
carrying out one the operator chose?** Choosing is driving.

## What the navigator owes the driver

1. **Think aloud before each step.** One or two sentences, said before the step
   is taken: what it is and why, so the driver can redirect while that is cheap.
2. **Challenge once, plainly.** When a step looks wrong, say so once, with the
   evidence (`file:line`, a failing test, a measured number), and offer the
   alternative. Then follow the driver's call and do not re-argue it. If the
   step cannot be undone, the challenge becomes a checkpoint (see below).
3. **Propose the next step.** At every pause, offer the next move, so the
   driver never has to ask "what now?".
4. **Never silently take the wheel.** A navigator that edits files, commits, or
   runs a command that changes state without a swap is driving without
   permission. Ask "may I drive?" instead.
5. **Keep the map.** Hold what was decided and what is still open, and recap it
   on request or at a swap.

## Swapping the roles

The swap word is said by whoever wants the change:

| Said | Meaning |
|------|---------|
| "you drive" (operator) | GRIP takes the wheel for the stretch the operator names, or until the operator takes it back |
| "I'll drive" (operator) | The operator takes the wheel back; GRIP finishes its current step, stops, and navigates |
| "may I drive?" (GRIP) | GRIP asks for the wheel, naming the stretch, because it can carry it faster: the work is mechanical, well specified and already agreed |

GRIP takes the wheel only on an explicit yes. "may I drive?" is one yes/no, so
`/hitl` routes it to `/asking-users`; setting up a whole session goes to
`/creating-wizards` instead (see the checkpoint table). Each swap is announced
in one line that names the stretch: "Driving: <the stretch>." or "Navigating."

While GRIP drives, the duties turn around: GRIP narrates each step before taking
it, stops at every checkpoint below, and hands the wheel back when the stretch
is done or the moment the operator says "I'll drive". The operator, now
navigating, can stop GRIP at any point.

A swap hands over context in four lines: where the pair is (file and line, or
the command just run), what changed (`git diff --stat`), what was being
attempted, and what is stuck.

## Checkpoints -- which /hitl primitive at which moment

The `/hitl` dispatch contract (`skills/hitl/SKILL.md`) picks the primitive from
the shape of the question. Pairing has these moments:

| Pairing moment | Shape | Primitive |
|----------------|-------|-----------|
| Setting up a session with several unknowns: the goal, the files in play, the done-condition, who drives first | multi-step intake | `/creating-wizards`, one step per unknown, skipping any the operator has already answered |
| A risky step: destructive, irreversible, or seen outside the machine | one yes/no | `/asking-users` (the destructive-action gate) |
| "may I drive?" | one yes/no | `/asking-users` |
| A design fork with several trade-offs to settle at once | batched related decisions | `/hitl-decision-matrix`: a default with rationale and a falsifier for each, cascade detection between them, persisted |
| A typed value: a threshold, a filename, a label | typed intake | `/creating-form-input` (route E of `/hitl`) |
| The shape is unclear | -- | consult `/hitl` before asking anything |

The quality gates every primitive shares -- short headers, two to four options,
every question teaching as well as collecting, and always a way to defer -- are
in `skills/hitl/SKILL.md` and apply unchanged.

### Mandatory checkpoints

Stop and ask, through the primitive in the table, whoever is driving:

- before any destructive or irreversible step, or one seen outside the machine
  (a push to a shared branch, a message, a deploy);
- before GRIP takes the wheel;
- at a design fork with more than one open trade-off;
- when the navigator has challenged an irreversible step and the driver has not
  yet answered;
- when the done-condition is reached: confirm it is done, or name the next
  stretch.

Mechanical steps inside an agreed stretch need no checkpoint, and nothing the
session has already answered is asked again. Both would spend the operator's
attention on nothing.

## Across machines or sessions -- /mesh as the transport

When the pair spans two machines, or two sessions on one machine, `/mesh`
carries it. Nothing in the posture changes; only the wire does.

- `/mesh connect [name]` joins the session bus on each side.
- `/mesh pair <name>` invites the other session to pair.
- Mesh's roles map onto the seats: **the Grip is the driver** (writes code,
  runs tools, commits) and **the Guard is the navigator** (reviews, steers,
  raises concerns). `/mesh intercept` is the swap word sent over the wire.
  `/mesh intercept!` is the urgent form, for a safety concern only: it asks
  the driver's session to pause its current tool call at once.

What is wired today, checked on 2026-10-03: the grip-session-mesh bus carries
`pair_steer`, `pair_intercept` and `pair_intercept_urgent` messages between
connected sessions (`client/mesh-send.sh --kind ...` in that repository), and
the receiving session's monitor shows them as `[mesh:pair:<from>:steer]`,
`[mesh:pair:<from>:intercept]` and `[mesh:pair:<from>:INTERCEPT!]`. No code holds
the roles: `/mesh pair`, `/mesh grip` and `/mesh guard` are listed among mesh's
conceptual commands, and its `pair_role` setting is read by nothing. So each
side states its seat in words, exactly as a single-session pair does, and a
steer or a swap request arrives as a mesh message. GRIP's own `bin/mesh` has no
pair subcommand; `mesh room say` and `mesh room read` give two sessions a
shared transcript to pair in.

The mesh monitor delivers any message carrying a destructive command (its list
covers forced pushes, hard resets, recursive deletes and dropped tables) as a
safeguard warning rather than as an instruction, and asks for `/mesh approve`
before anything runs.

## Two humans, two GRIPs

The mesh-carried variant: two developers, each with their own GRIP session.
Each human and their GRIP still pair by the posture above; across the wire, one
developer drives (the mesh Grip) and the other navigates (the mesh Guard). What
still holds from the 3.0.0 protocol:

- **Wait before steering.** When the navigator sees the driver do something
  that looks wrong, give it a few seconds; the driver may already have it in
  hand. Steer once the driver is clearly committed to the wrong path.
- **Swap at natural boundaries**: a passing test, a commit, a finished stretch.
  Keep `/mesh intercept!` for a dangerous operation in progress.
- **Hand over context on a swap**, in the four lines above.
- **Breeding the two genomes is optional.** `lib/genome_breeder.py breed --pair
  <A> <B>` crosses two operators' learned patterns after a session (the
  `genome-breeding` skill); it is never a precondition of a swap.

Dropped from 3.0.0, and kept by git: the bespoke transports (Claude Bridge URLs,
SSH tunnels, Unix sockets and `lib/session_control_server.py`), their
environment variables and the state machine. Mesh replaced the transport.

## What is not pairing

- **A dispatched background agent.** It has no driver to pair with: it gets a
  brief and returns an artefact, and nobody is at its keyboard to think aloud
  to. Its human in the loop is the brief going in and the review coming out.
- **`/delegate`**, which spawns worker agents (the pre-3.0 `/pair "task"`).
- **`/auto`, `/afk` and the other autonomous loops**, where GRIP drives alone
  and nobody navigates: the opposite posture.
- **`/mesh broadcast`**, which informs sessions; informing is not pairing.

## Cross-references

- `skills/hitl/SKILL.md` -- the dispatch contract that picks the primitive
- `skills/asking-users/SKILL.md`, `skills/creating-wizards/SKILL.md`,
  `skills/hitl-decision-matrix/SKILL.md` -- the primitives named above
- `skills/mesh/SKILL.md` -- the transport when the pair spans machines or sessions
- `skills/reasoning-with-ai/SKILL.md` -- the articulate-first posture
- `commands/pair.md` and `commands/pair-mode.md` -- both load this file
- `modes/definitions/pair-mode.yaml` -- `/mode pair`

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 4.0.0 | 2026-10-03 | Revived as the human-AI pairing posture (V>>'s decision): the operator drives and GRIP navigates by default, the roles swap on a word, and checkpoints run through the /hitl family. `/mesh` is the transport across machines or sessions. The two-developer protocol is condensed into "Two humans, two GRIPs" and its bespoke transports are dropped. |
| 3.0.0 | 2026-05-09 | Marked deprecated in GRIP#2641: the two-developer transport moved to grip-session-mesh (`/mesh`). |
| 3.0.0 | 2026-03-17 | Two developers, two GRIP instances: Grip/Guard roles, the intercept protocol, genome breeding on a swap (GRIP#1286). |
