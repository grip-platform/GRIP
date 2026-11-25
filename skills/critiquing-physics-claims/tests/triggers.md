# Trigger Test Suite

Per Anthropic best practices guide (chapter 3: Testing and Iteration) and GRIP hypothesis-driven-development. This file is not loaded at runtime — it exists for manual and automated validation.

---

## Hypothesis

**Claim**: The `critiquing-physics-claims` skill triggers on at least 90% of physics-critique paraphrases and triggers on fewer than 5% of unrelated queries.

**Falsification deadline**: 2026-05-10 (30 days post-creation).

**How to verify**: Run the trigger test cases below via manual invocation or the skill-creator validation harness. Record pass/fail per case.

---

## Should Trigger (Positive Cases)

1. "Give me a bullshit meter rating on this paper"
2. "Review this physics theory Hossenfelder-style"
3. "Is this crackpot or legit? [pastes abstract]"
4. "Stress-test the falsifiability of my theory"
5. "Rate my theory on the bullshit meter"
6. "Is this not even wrong?"
7. "I wrote a theory of everything — tear it apart"
8. "Check my physics paper for rigour issues"
9. "Can you do a Hossenfelder review on this?"
10. "How skeptical can you be about this quantum gravity proposal?"
11. "Is this paper testable at all?"
12. "Rip apart my unified field theory draft"
13. "I want brutal critique on my physics claim"
14. "Falsifiability check on this framework please"

## Should NOT Trigger (Negative Cases)

1. "What is the weather in Geneva?"
2. "Help me write a Python function"
3. "Review my pull request"
4. "Explain general relativity to me" (teaching, not critique)
5. "I love this physics paper" (encouragement request, not critique)
6. "What is the current state of string theory research?" (research query)
7. "Review my novel's plot" (non-physics)
8. "Is this code correct?" (non-physics)
9. "Summarise this physics paper" (summarisation, not critique)
10. "What did Hossenfelder say about superdeterminism?" (biographical query)
11. "Fix this LaTeX error" (formatting, not physics)

## Ambiguous Cases (Document Behaviour)

1. "What do you think of this physics paper?" → Should ask clarifying question: critique or summary?
2. "Is this good physics?" → Should trigger (implicit critique)
3. "Review this" (no domain specified) → Should NOT trigger without physics context

---

## Functional Tests

These validate skill output quality, not just trigger behaviour.

### Bar Rendering

- [ ] Score 0 renders as `[░░░░░░░░░░] 0/10` (zero filled, ten empty)
- [ ] Score 1 renders as `[█░░░░░░░░░] 1/10`
- [ ] Score 5 renders as `[█████░░░░░] 5/10`
- [ ] Score 7 renders as `[███████░░░] 7/10`
- [ ] Score 10 renders as `[██████████] 10/10`
- [ ] Total character count is always exactly 10 (filled + empty)
- [ ] Never copies a static template — always regenerates per score

### Section Presence

- [ ] Every output contains all four sections: What Works, What Doesn't, What Would Lower the Score, Verdict
- [ ] "What Works" is never empty, even at score 10
- [ ] "What Would Lower the Score" contains at least one actionable derivation step
- [ ] Verdict is 1-2 sentences, not a paragraph

### Attribution Correctness

- [ ] Output never claims first-person identity as Sabine Hossenfelder
- [ ] "Not even wrong" is attributed to Pauli when used
- [ ] Any real quote is either cited correctly or removed
- [ ] No invented quotes attributed to real people

### Anti-Templating

- [ ] Feed three different physics ideas (good, mediocre, bad). Verdicts differ in score AND in prose AND in specific references.
- [ ] Score distribution across a diverse input batch is not concentrated on a single value.

### Input Reading

- [ ] When input references a file path, the skill opens the file before critiquing
- [ ] Critique references specific passages from the input, not just generic phrases
- [ ] If the input is genuinely unclear, the reviewer says so rather than bluffing

---

## Calibration Test Inputs

Use these as regression inputs to ensure scoring stability over time.

| Input | Expected score range | Notes |
|-------|---------------------|-------|
| Standard Model cross-section calculation | 0-1 | Well-formalised, testable, honest scope |
| New BSM model with LHC-reachable predictions | 2-4 | Promising, gaps in derivation |
| String theory landscape multiverse paper | 6-7 | Philosophy with equations |
| Consciousness-as-quantum-collapse proposal | 7-9 | (see `references/examples.md`) |
| Pure numerology linking physical constants | 9-10 | Not even wrong |

Scoring must remain within the expected range across sessions. Drift of more than 2 points indicates the rubric has been corrupted.

---

## Iteration Signals

Per Anthropic guide chapter 3, watch for:

**Undertriggering signals**:
- Users manually invoking instead of natural language triggering
- Support questions about when to use it
- Skill not loading on obvious physics-critique requests

**Overtriggering signals**:
- Skill loading on general physics questions (not critique)
- Users disabling it
- Confusion about scope

**Fix direction**: adjust the `description` field in SKILL.md frontmatter — add keywords for undertriggering, add negative triggers for overtriggering.
