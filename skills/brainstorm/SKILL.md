---
name: brainstorm
description: "Runs a whole brainstorm in one command, with the operator deciding at every gate. SEED, then DIVERGE and CONVERGE through /ultraideate (outside models, ranked by Jev, with the operator's own seeds scored alongside), CLUSTER, the operator's pick, ATTACK on the reasoning-lenses ladder, DECIDE through /hitl-decision-matrix, and DEVELOP. Each result is shown through /simple. Use when the operator invokes /brainstorm or asks for a full brainstorm, end to end, of ideas that must survive an attack before anyone builds them."
user_invocable: true
command: brainstorm
status: Active
version: 1.1.0
created: 2026-11-24
triggers:
  - /brainstorm
  - run a full brainstorm
  - brainstorm this end to end
integrates:
  - brainstorm-mode
  - ultraideate
  - hitl
  - asking-users
  - creating-wizards
  - hitl-decision-matrix
  - simple
  - clarify
  - reasoning-with-ai
  - productise
---

# /brainstorm: the whole brainstorm, from seed to plan

`/brainstorm` runs a brainstorm from start to end in one command. It walks the phases of `skills/brainstorm-mode/SKILL.md` in order, and it calls the skill that owns each phase. It does no phase's work itself. The operator decides at every gate, and the machine does the slow work between the gates.

## When to use it

Use it when ideas must survive an attack before anyone builds them. The operator must be in the session, because each gate asks him a question.

- For a quick "five ideas for X", the Quick workflow of brainstorm-mode is enough.
- A background agent cannot run it, because a background agent cannot ask the operator anything.
- The data limit of `/ultraideate` applies: internal and public text only. A personal matter, a client document, WhatsApp content, a child's data or a secret never goes into the topic or a seed. Personal work belongs in the operator's private guardian repository (`rules/personal-matters-stay-private.md`).

## The phases

| # | Phase | Who | Primitive | Writes |
|---|---|---|---|---|
| 1 | SEED | operator | `/asking-users`, or `/creating-wizards` for several unknowns | `topic.txt`, `seeds.json` |
| 2 | DIVERGE and CONVERGE | machine, no Claude limit | `/ultraideate` with `rank --seeds-file` | `rank.json` |
| 3 | CLUSTER | session | none | `options.json` |
| 4 | PICK | operator | `AskUserQuestion` with `multiSelect`, one question for each theme | a picks row |
| 5 | ATTACK | machine | the ladder in `rules/reasoning-lenses.md` | a fate for each pick |
| 6 | DECIDE | operator | `/hitl-decision-matrix`, batched | locked decisions and the release order |
| 7 | DEVELOP | session | the idea's own repository, and `/productise` for a product | a plan for each kept idea |

brainstorm-mode clusters before it converges. Here `rank` runs DIVERGE and CONVERGE in one call, so CLUSTER groups the ranked shortlist, and that is what the pick needs. The `/hitl` contract chose each primitive in the table. Every result that the operator reads goes through `/simple` first, as the section "Show every result with /simple" says.

Keep the files of one run in one folder of the session's scratchpad, such as `<scratchpad>/brainstorm-lunch-orders/`. Never put them in a repository, because the topic holds the operator's own words. The commands below call that folder `$RUN`.

## 1. SEED

Ask only for what is still unknown. Ask for the operator's own ideas before you show any machine idea, so that his intuition comes first (`skills/reasoning-with-ai/SKILL.md`). One unknown takes `/asking-users`, and several take `/creating-wizards`, as the gate table in brainstorm-mode says.

Collect these, and skip each one that the session already holds:

- the challenge, in one sentence
- the constraints
- what success looks like
- his own ideas, which become the seeds
- for a product, the buyer and the job in one sentence, which is step 1 of `/productise`

Then write the two input files:

- `topic.txt` holds his words, the constraints and what success looks like. Name his seeds in it as "seeds to build on, not limits", so that the council can combine them. Leave them out of the topic when he wants ideas that his own ideas do not shape.
- `seeds.json` holds `{"ideas": [{"text": "...", "proposed_by": ["operator"]}]}`. Give every seed its `proposed_by`. A seed without one is credited to the file, never to the operator, because nothing shows who wrote it. The session may add three ideas of its own at most, after his, with its model id in `proposed_by`. It adds them only when it knows something that the council does not, because every seed takes a place on the pick list.

Before the run, say which criterion weights are in force. This command prints them:

```bash
~/.grip/venv/bin/python -c "from lib.ultraideate import operator_weights; print(operator_weights())"
```

When he wants other weights, run `/hitl-decision-matrix` with one decision for each of the five criteria. Then write all five numbers, each above 0, to `state/decision-weights/ultraideate.json` under the GRIP root. COMBINE reads that file during the run, so a weight that changes after the run changes nothing.

## 2. DIVERGE and CONVERGE

Run `rank` from the GRIP repository root, in the foreground, with a ten-minute timeout. The second line prints a short view of its result:

```bash
~/.grip/venv/bin/python -m lib.ultraideate rank "$(cat "$RUN/topic.txt")" --seeds-file "$RUN/seeds.json" --top 40 --json > "$RUN/rank.json"
jq -r '"note: \(.note)", "run id: \(.run_id)", (.ranked[] | [.rank, .id, (if .seed then "seed" else "-" end), "\(.scores | length) of 5 settled", (.scores | to_entries | map("\(.key) \(.value * 100 | round / 100)") | join(", ")), .idea] | @tsv)' "$RUN/rank.json"
```

The council has no Anthropic seat, so DIVERGE spends nothing from the Claude weekly limit. A quiet seat is normal. On run b58e370627d1 three of the four seats answered, and the Astra seat was the one missing. The value 40 for `--top` leaves room to fold duplicates and still fill sixteen options. On that run, sixteen options stood for 25 ideas. Every seed is in the output too, whatever its rank.

- **An empty `run_id` stops the brainstorm.** Jev scored nothing, and the note says why. Tell the operator in one line, and offer a rerun when the cause clears. Show no ranking, and ask for no pick.
- **Thin coverage is said out loud.** Count the settled readings before you show anything. On run b58e370627d1, Jev settled 332 of 2,075 readings, 151 of 415 ideas had no settled reading, and no idea had all five. Give the coverage in one line when it is that thin.

## 3. CLUSTER

CLUSTER turns the ranked candidates into the pick list. It works on the shortlist only, never on the whole run.

1. Fold near-duplicates into one option. The option keeps every id that it stands for in `options.json`. A pick of it records only its best-ranked id, so that one choice counts once in precision@5.
2. Build the list. Every seed gets a place first, and the best-ranked distinct ideas fill it up to sixteen options. Seeds always stay, so more than sixteen seeds make a longer list.
3. Group the options into themes. A theme name has 12 characters at most, because the name becomes the question header.
4. Give each theme two to four options. A theme with one option joins its nearest theme, and a theme with five or more splits in two. One call holds four themes at most, so a list of more than sixteen options takes more calls. A list of one option only becomes one question with `multiSelect: false` and two options, "Take it forward" and "Leave it". The first maps to that option's best-ranked id, and the second is the empty pick.
5. Write `options.json` with each theme, its options and the ids behind each option. The pick then maps back to ids with no guess.

Keep the rank order inside each theme. COMBINE's rank counts an unsettled criterion at the middle of the scale, so one high reading does not outrank several good ones. Do not sort by the `combined` number. It averages the settled readings only, so with thin coverage it can contradict the rank. On run b58e370627d1, rank 42 showed 0.882 from one reading, which is above the 0.783 that rank 6 showed from three.

## 4. PICK

Before the call, say in prose that Jev ranked these ideas and did not judge whether any of them is true or good. Say that each score is a machine reading with an unmeasured precision, and give the coverage line. Put these words before the question, never after it. The HITL quality-gate hook refuses a question that does not end with "?".

Then ask with `AskUserQuestion`, in as few calls as the list needs, under these rules:

- One question for each theme, with `multiSelect: true`, the theme name as its header, and question text that ends with "?". The one-option list of CLUSTER step 4 is the only exception.
- Each question holds two to four options, and a call holds four questions at most, so the call shows sixteen options at most. A longer list goes into more calls, and every question in those calls still holds two to four options. For a list of 17 options, use one call of 9 options and one of 8, never 16 and 1. No option drops out of the list.
- A short title as each option's label. The gate refuses an option named Other or Skip, because the dialog adds its own.
- A description that gives the idea in one sentence, then its rank, its ids and each settled score. It ends with the settled count, such as "2 of 5 settled", "your seed" for a seed, and the label "machine reading, precision unmeasured". The dialog may be all that the operator sees.
- The options of each question in rank order, with no option labelled Recommended. Jev ranks and does not judge, so its order is the only recommendation.

```json
{
  "questions": [
    {
      "question": "Which ordering ideas go forward to the attack step?",
      "header": "Ordering",
      "multiSelect": true,
      "options": [
        {"label": "One shared order", "description": "Everyone adds a dish to one office order before 11:00. Rank 1, idea-7 and idea-19; delight 0.86, feasibility 0.74; 2 of 5 settled; machine reading, precision unmeasured."},
        {"label": "Repeat last Friday", "description": "One tap repeats last Friday's order. Rank 9, seed-1, your seed; delight 0.71; 1 of 5 settled; machine reading, precision unmeasured."}
      ]
    },
    {
      "question": "Which payment ideas go forward to the attack step?",
      "header": "Payment",
      "multiSelect": true,
      "options": [
        {"label": "Split the bill by dish", "description": "The app splits one bill by dish. Rank 2, idea-3; feasibility 0.81, data safety 0.92; 2 of 5 settled; machine reading, precision unmeasured."},
        {"label": "Monthly tab", "description": "Each person pays once a month. Rank 5, idea-11; no criterion settled; 0 of 5 settled; machine reading, precision unmeasured."}
      ]
    }
  ]
}
```

After the last question call, map each picked option to its best-ranked id through `options.json`. Then record and seal the whole pick once, with the ids from every call, because `picks` refuses a second row for the same run:

```bash
~/.grip/venv/bin/python -m lib.ultraideate picks <run_id> <one id for each picked option> --topic "$(cat "$RUN/topic.txt")" --k 5 --seal
```

`picks` copies the run's full ranked order from the shortlist log, and it refuses an id that the run does not hold. Its value 5 for `--k` is the one that hypothesis H823 registered. One id for each option keeps a folded cluster from counting several times in precision@5. With `--seal`, the pick also gets a signed IDR leaf, so ATTACK and DECIDE can show which choice started them. The picks log alone is plain text that anyone can edit.

When the operator picked nothing, the same call with no ids records the empty pick. That closes the shortlist, so the daily tick does not report it as abandoned. Run this call once for every run, and never a second time.

- **Everything picked is an answer.** Do not ask the same pick again. Go to ATTACK, and let DECIDE set the order in which the picks land. On run b58e370627d1 the operator picked all sixteen options.
- **Nothing picked, with free text that asks for more ideas,** reruns phase 2 with his new words, after the empty pick above. This is the readiness check of brainstorm-mode, moved here because `rank` runs DIVERGE and CONVERGE together. A rerun happens only when he asks for it.
- **Nothing picked, and no request for more,** ends the brainstorm after the empty pick above. Say that no idea went forward, and end with the RECOMMENDED NEXT STEP line. Do not ask the same pick again.

## 5. ATTACK

Climb the ladder in `rules/reasoning-lenses.md` for each pick, and stop at the first kill.

1. Write the load-bearing claim of the pick and the observation that would kill it. Register the claim with `lib.hypothesis_engine.register`, and start the claim with `[brainstorm <run_id>]`. The engine needs a metric, a prediction, a deadline and a measure plan. Write the prediction as a comparison on the metric, such as `>= 0.5` for a share of KEEP votes. `register` warns about free text, which the engine grades "inconclusive" (H1009 to H1020), so put the fact check in the kill clause. This is step 2 of the ladder, done first, so that a pick killed by the first count still leaves a record.
2. Run the cheap steps for every pick. They are a count against a control, the naive prior, and the analogy check when an analogy carries weight.
3. Send every pick that step 2 did not kill to the red team, in one framed prompt. Outside seats attack them through `bin/grip-sovereign-council`, which has no Anthropic seat and spends nothing from the weekly limit. An outside judge seals the result. Each seat gives one objection, a KEEP or KILL for every pick, and its best combination of at most three. Tally the KEEP votes per pick, next to the fate from step 2, and say how many seats voted. A seat can run out of budget on a long prompt and give no verdict, so the tally never stands for every seat.
4. Propose `.claude/workflows/attack-fanout.js` for a claim that gates an irreversible call. It still caps at four claims, with three lenses each. Launch it only when the operator asks for it or agrees, because it spends Claude agents. Arm `bin/grip-fleet-watch` when you launch it.

Each pick ends with one fate: KILLED, SURVIVED SO FAR or UNTESTABLE. Show the fates through `/simple`, with the evidence for each kill and the seat tally from step 3. SURVIVED SO FAR is not proof.

## 6. DECIDE

Hold one `/hitl-decision-matrix` sitting about what the plan still needs. It asks in batches of three, with eight decisions at most:

- **The order.** One decision for each release, or for each step, says which surviving picks land in it. The session proposes the default, with its reason and a falsifier. When the operator picked everything, this decision matters most.
- **The open forks.** One decision for each choice that the first release cannot start without.

A KILLED pick stays out unless the operator brings it back. Say so in the preamble of the matrix, and do not ask about each pick. Write the matrix spec as JSON, and dry-run it with `lib/hitl_decision_matrix.py spec.json --preamble --batches` before the sitting. Persist the outcome where the last phase of the matrix says. That is the project's own repository for a project decision, and never GRIP for a private matter. Give the fate of each pick in the `provenance` text of `persist`, because the metric below reads the fates from that record.

## 7. DEVELOP

Turn each kept idea of the first release into a concept. A concept says what the idea is, its first concrete step, its kill condition from ATTACK and the number to watch. For a product, hand the idea to `/productise`, which names the buyer, the proof and the cut. The build happens in the repository that the idea belongs to, never inside the brainstorm run. End with the RECOMMENDED NEXT STEP line.

## Show every result with /simple

Every result that the operator reads goes through the `simple` preset of `/clarify`. That preset gives one plain line first, and then the explanation for a bright beginner. It covers the seed summary, the coverage line, the fates, the preamble of the matrix and the plan. Each number, id, score and path stays exactly as written. Run `lib/ste_lint.py` on each summary before you show it, as `commands/clarify.md` shows.

## Budget and stop conditions

- DIVERGE and CONVERGE run on outside models, so they spend nothing from the Claude weekly limit. A Broly CLUSTER fan-out, the Deep workflow of brainstorm-mode, runs only when the operator asks for it.
- ATTACK sends every surviving pick to the council, because `bin/grip-sovereign-council` also spends nothing from the Claude weekly limit. `attack-fanout.js` still caps at four claims, and it runs only as step 4 of ATTACK says.
- Nothing reruns or loops on its own. Each rerun of phase 2 needs the operator's word.

## Honest limits

- Jev ranks, and it does not judge truth. Each score is the reading of one model, and its precision is unmeasured.
- The coverage can be thin. On run b58e370627d1, 16% of the readings settled.
- The ideas come from outside models, and a persuasive phrase can steer Jev. The honest limits of `/ultraideate` give the measured figure.
- A rerun of the same topic does not always give the same order, because the answers of Jev jitter a little.
- The operator's pick is the decision. The ranking is advice, and SURVIVED SO FAR is not proof.

## Falsifier and metrics

This command is wrong if the operator skips it in 5 or more of the next 10 brainstorms, and assembles the phases by hand. To count, take the session transcripts that changed after this skill merged. A brainstorm is a transcript with a `lib.ultraideate rank` call. A run of this command also holds its command tag before that call:

```bash
rg -l 'lib\.ultraideate rank' ~/.claude/projects/*/*.jsonl
rg -l '<command-name>/?brainstorm</command-name>' ~/.claude/projects/*/*.jsonl
```

Two numbers show whether the brainstorms work:

- precision@5 against the random baseline, from `~/.grip/venv/bin/python -m lib.ultraideate report`, which hypothesis H823 tracks
- the share of picks that survive ATTACK, from the DECIDE record of each brainstorm

If ATTACK kills nothing in 10 brainstorms, the red-team step adds no information here. Then cut ATTACK to its cheap steps, as `rules/reasoning-lenses.md` deletes a ladder step that kills nothing.

## Where it came from

V>> asked for it on 2026-11-24, in these words:

```text
/create `/brainstorm` that is a one-shot command that uses the full workflow of /brainstorm-mode and /brainstorm-mode-orchestration -- from the Divergent -> X -> Y -> Convergent ideation, which can maybe be combined with /hitl and /ultraideate and /hitl-decision-matrix and /simple to be the be-all-and-end-all BRAINSTORM command
```

His X and Y name the phases that he left unnamed: CLUSTER, and the pick and ATTACK steps of the full loop in brainstorm-mode. This command codifies two runs done by hand: run 9b6d07fc8c41 on 2026-11-11 and run b58e370627d1 on 2026-11-24. The rules for coverage, duplicates and an everything-picked answer come from the second run. The Codex review of GRIP#9364 added the one id for each option, the sealed pick and the closed shortlist before a rerun. GRIP#9525 removed the council's four-pick cap and added the seat tally. Claude Opus 5.5 (`claude-opus-5-5`) wrote this file.

## Changelog

| Version | Date | Changes |
|---|---|---|
| 1.0.0 | 2026-11-24 | First version: the seven phases, the 16-option pick list, the sealed picks record with one id for each option, the capped ATTACK and the falsifier |
| 1.1.0 | 2026-11-26 | ATTACK's council step sends every surviving pick, with a KEEP tally and a seat count, and `attack-fanout.js` keeps its own four-claim cap (GRIP#9525) |
