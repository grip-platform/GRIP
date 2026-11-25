# Review record for docs/ULTRAPROMPT-fleet-collation.md

> **Status.** The merged brief applied R0 #1-#4 and #6-#8, B1, B3 (in part), B4, C1, C3, C5 and C7. Not applied, because the brief was superseded by grip-fleet (GRIP#7053): R0 #5, #9 and #10, B2, C2, C4, C6 and C8-C11. The checks worth keeping are carried in GRIP#7077.

This file holds the review behind the brief: the orchestrator's R0 findings, then every council concern with its tested verdict. Items marked NOT yet folded in the brief's self-review section are the open work.

## Orchestrator R0 findings

R0 findings on ultraprompt v1, from the orchestrating session (Claude Opus 5.5).
Each finding was checked against code or live state at about 02:30 UTC unless it says otherwise.

1. BLOCKER -- task 4's stranded-commit test cannot be trusted.
   `git log origin/<branch> --not origin/main` is non-empty for EVERY squash-merged PR. A squash-merge leaves the branch's own commits unreachable from main, so every merged PR would be reported as having stranded commits. This is the squash trap the brief warns about two paragraphs earlier.
   Replace it. For a merged PR, `gh pr view --json headRefOid` is the head it merged at; compare that with the branch tip now, `git ls-remote origin refs/heads/<branch>`.
   - If the branch still exists and its tip differs from headRefOid, then `git log --oneline <headRefOid>..<tip>` lists the commits that never reached main. That is the GRIP#6959 class.
   - If the branch is gone from origin, check any local branch or worktree of that name for commits beyond headRefOid.

2. BLOCKER -- task 3's fallback for a branch with no upstream has the same trap.
   "origin/main..HEAD with no upstream" reports every commit of a squash-merged branch as unpushed. The fallback should be:
   - With no upstream, run `git ls-remote origin refs/heads/<br>`.
   - If that branch is absent, run `gh pr list -R grip-platform/GRIP --head <br> --state all --json number,state,headRefOid` and compare HEAD with that PR's headRefOid.
   - Count as unlanded only commits after a merged PR's head, or commits on a branch that has neither a PR nor a remote.

3. SHOULD -- "read-only" is not quite true across live worktrees.
   Plain `git status` may refresh and rewrite another worktree's index: it takes index.lock opportunistically, and that can collide with a live session's own git command. Use `git --no-optional-locks -C <wt> status --porcelain` (or GIT_OPTIONAL_LOCKS=0) everywhere, including the shared tree in task 7.
   Also carry the broadcast's rule: if a fetch hits a lock, wait and retry, and never delete a lock file.

4. SHOULD -- "every required check" is the wrong filter.
   main requires exactly one check, leak-check, set by two rulesets (verified via `gh api repos/grip-platform/GRIP/rules/branches/main`). `gh pr checks --required` therefore never shows Forward Anchors or AGORA. The merge rule should read: every check reported by `gh pr checks <n>` (not `--required`) has finished green, Forward Anchors included.

5. SHOULD -- the brief is twice its budget.
   It measures 15,554 bytes; the design notes say ~10 KB, and the skill's budget is ~8 KB. Most of the excess is the per-record map (WHERE WE ARE, the eleven suffix lines). It restates what task 2 re-derives at run time, and it goes stale as records change: one record already grew "Update" sections after it was written. Either cut the map to one line per record (suffix, harness, the single claim to check first), or move it into a companion file linked from the brief.

6. SHOULD -- the brief carries security specifics it does not need.
   One line names a keychain item and says a secret's value reached a transcript; another points at keys in a machine's shell history. The run reads these from the records anyway. The brief should say "security items first (see record ...-97b0 and the the second Mac item)" and name nothing, so that the plan file carries no security pointers if it is ever pasted elsewhere.

7. NIT -- task 8's two commands need correcting.
   - `prove_it.render()` takes the whole spec, {title, response, sources, citations}, so call the file spec.json rather than citations.json.
   - `lib.redaction_shield verify` takes ONE target (`verify [--profile {public,llm_egress}] [--strict] [target]`), so call it once per file.

8. NIT -- task 1 uses `$SCRATCH`, which is not a standard variable. Say "your session's scratchpad directory (Claude Code names it at session start), else `mktemp -d`".

9. NIT -- `bin/grip-pr-monitor:100` is the verdict EXTRACTOR: it takes the latest "**Verdict: ...**" string. It does not apply MERGE or DEFER itself. Cite it as the extractor.

10. NOTE -- since this draft was written, the worktree count moved from 132 to 133. The figure is a dated snapshot, as the brief already says; keep the probe and drop the hard number, or mark it as dated.


## Council concerns and verdicts

REVIEW DIGEST for ultraprompt v1, compiled by the orchestrating session (Claude Opus 5.5) at about 02:35 UTC.
Every council concern below was tested against the records or the code. "Holds" means fold it; "does not hold" means reject it, for the reason given.

A. Orchestrator R0 findings: opus-r0-findings.md, in this folder. All ten hold; read that file.

B. Moat council, round 1. Seal b561a9b4a39e4da4. Only DeepSeek V4 Pro answered, and it was cut off at its token budget; xAI timed out and Groq was rate-limited. The judge was Gemini 3.1 Pro. Raw output: council-r1.out.
 B1 holds. The stranded-commit test fails outright when a merged PR's branch was deleted, because origin/<branch> is missing. Same fix as R0 #1.
 B2 holds. Task 5 says "apply the merge rule with gh pr checks and the verdict comment" without the exact commands. Give them: the latest verdict through the same jq bin/grip-pr-monitor uses, and the head marker matched by its string, "<!-- agora-head-sha: ".
 B3 holds. Shorthand such as "merge-base --is-ancestor;" with no arguments is a silent command error. Every command must be complete and runnable as written.
 B4 holds. Security details must not reach the brief or the briefing. Same as R0 #6.
 B5 does not hold. "gh pr checks cannot show non-required checks" is true only of `gh pr checks --required`; plain `gh pr checks` lists every check run. Keep R0 #4's wording.

C. Grounded pass (/ultrareason), which escalated to its own council. Seal 8025f3050a86880d; 4 moat facts; satisfied=false. Grok 4.5 and DeepSeek V4 Pro answered, both cut off. The Astra API seat answered HTTP 429 "no credits"; Groq was rate-limited. The judge was Gemini 3.1 Pro and rated Grok best. Raw output: ultrareason.out, lines 294-560.
 C1 partly holds. Grok calls "#6993" a typo for #6995. It is NOT one: record ...-97b0 says GRIP#6993 merged while Forward Anchors was red. But a hand-typed list can miss PRs: record ...-3015 says two of GRIP#6991/#6994/#6995 merged past a red or running check and does not name which two. So derive the list from the records at run time; do not hardcode it.
 C2 holds. The broadcast went to Codex sessions too, and there are zero Codex records. Add a recipient roll call covering every harness's live and recent sessions, not only worktrees.
 C3 holds. Freeze every input together in task 1: the records, the worktree lists (GRIP and HAL) and the open-PR lists. Otherwise the briefing mixes moments.
 C4 holds. Late or changed records: the briefing uses the frozen set only. A record that arrives or changes after the freeze is listed as "after freeze", with its sha256, and is not merged into the counts.
 C5 holds. Security items appear in outputs only as record id + line numbers + a probe of their current state, never their substance.
 C6 holds. Redact at parse time: records.json keeps line references for personal, client or email lines, never the text.
 C7 holds, and is the most valuable finding. Split the merge queue into two columns:
   (a) SWEEP-WILL-MERGE: what bin/grip-pr-monitor will actually merge (it merges on the verdict alone, because only leak-check is required);
   (b) POLICY-OK: what the stated rule allows (every check green, Forward Anchors included, head marker equals headRefOid).
   Label every PR in (a) but not (b) "POLICY-GAP": that is exactly the past-red-check class the records report.
 C8 holds. Line anchors drift: resolve the merge rule by symbol or string (VERDICT_JQ, HEAD_SHA_MARKER_PREFIX), and cite file:line only as a dated hint.
 C9 holds. Deduplicate landed PRs and SHAs across records: key them by repo#n, or by commit for a direct push, and list every record that claims each one.
 C10 holds. One record says an AFK marker is still active. Give it a probe and an output row.
 C11 holds. The the second Mac item has no handle: bind it to its record id + line range.

D. Astra (GPT-6 via the Codex CLI on the ChatGPT subscription; the operator wants the subscription route) is PENDING. A probe through ~/.codex at 02:05 UTC answered "Your workspace is out of credits". ~/.codex-ct's 5-hour window was 96% used and resets at about 03:23 UTC. Another session's KERN v3.4 watcher is also queued on that window at about 03:24 UTC, so Astra's cross-check is one modest call after that, not a max-effort run. Leave it as pending in the self-review section.
