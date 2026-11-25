ULTRAPROMPT: land your own uncommitted work, then record what you did (fleet run {{RUN_ID}}, {{DATE}})

Do this at your next safe stopping point, not mid-edit. It is for every running GRIP session: Claude Code, Codex, OpenCode, Kiro or any other harness. Use plain git and gh yourself; no council or subagent is needed.

WHY
The shared tree {{GRIP_HOME}} is what ~/.claude points to, and launchd agents run from it. Each session lands ITS OWN work on a branch with a PR, then writes one record, so the operator can inspect every session's work in one page.

WHERE THINGS STAND (probe before you trust)
- How far the shared tree is behind main: `git -C {{GRIP_HOME}} rev-list --count HEAD..origin/main`.
- Its uncommitted paths: `git -C {{GRIP_HOME}} status --short`. Ownership: `{{GRIP_HOME}}/bin/grip-ownership --tree {{GRIP_HOME}}`. UNATTRIBUTED means only YOU know what you changed.
- Staged changes already in the shared index (any commit made there would sweep them in): `git -C {{GRIP_HOME}} diff --cached --name-only`.
- {{GRIP_HOME}}/settings.json is the operator's live config: leave it alone.
- Notes from the operator for this run:
{{NOTES}}

THE SHARED TREE IS READ-ONLY FOR YOU, WITH TWO EXCEPTIONS
In {{GRIP_HOME}} you may run only status, diff, show, log, ls-files, rev-parse and fetch. This prompt asks for exactly two writes there, and nothing else:
- creating your worktree with lib/grip-worktree.sh (task 3B). This writes only git's worktree records, not anyone's files.
- your ONE record file under {{RECORDS_DIR}}/ (task 5). That folder is gitignored.
If a fetch hits a lock, wait and retry; never delete a lock file. Run every git command that writes as `git -C <your worktree> ...`, never relying on your current directory. If `git -C <dir> rev-parse --show-toplevel` prints the same path as `git -C {{GRIP_HOME}} rev-parse --show-toplevel`, STOP: that is the shared tree.

YOUR TASKS
1. Stop safely. Finish the edit or test in hand, and start nothing new. Leave long-running background jobs running, and land what is already finished.
2. List what YOU changed this session, repo by repo, from your own record of your edits. If another session may have edited the same file, you must be able to vouch for every line you carry. If you cannot, leave it and name it in your record.
3. Land it.
   A. You are in your own worktree on a feature branch (call it <wt>, on branch <br>):
      `git -C <wt> add -- <each path>`
      `git -C <wt> commit -m "<type>: <what>" -m "<why, tests run, Provenance: <your model>>"` (always use -m, because a bare commit opens an editor and hangs)
      `git -C <wt> push -u origin <br>`
      If no PR exists for <br>, open one:
      `gh pr create -R grip-platform/GRIP --base main --head <br> --title "<type>: <what>" --body "<plain-English why, test plan, provenance>"`
      Add --draft if the work is unfinished. If a PR already exists, get its URL with `gh pr view <br> -R grip-platform/GRIP --json url`.
   B. Your changes sit in {{GRIP_HOME}} itself. Make a uniquely named worktree from fresh main:
      `N="land-<your harness>-$(date -u +%Y%m%d%H%M)-$(openssl rand -hex 2)"`
      `git -C {{GRIP_HOME}} fetch -q origin main && (cd {{GRIP_HOME}} && bash lib/grip-worktree.sh add {{WORKTREES}}/$N fix/$N --base origin/main)`
      If the name exists, make a new N; never reuse or delete anyone's worktree. Then carry your work across:
      - Tracked files: `git -C {{GRIP_HOME}} diff HEAD --binary -- <paths> | git -C {{WORKTREES}}/$N apply`. One exception: if a path of yours appears in `git -C {{GRIP_HOME}} diff --cached --name-only` and you did NOT stage it yourself, carry it with `git -C {{GRIP_HOME}} diff --binary -- <path>` (no HEAD), which leaves the other session's staged lines behind. The apply is all-or-nothing. If it refuses, make ONE honest manual redo of your own edit, or leave the path and record it.
      - New files: `mkdir -p "$(dirname {{WORKTREES}}/$N/<path>)" && cp {{GRIP_HOME}}/<path> {{WORKTREES}}/$N/<path>`
      - git-crypt paths (drafts/**, facts/*.md, rules/operational-context.md, data/genomes/*.json): first check the file reads as plain text in the new worktree, then redo your edit by hand. Never pipe or copy an encrypted file's diff.
      - Check before you commit: `git -C {{WORKTREES}}/$N status --short` lists only your files, and `git -C {{WORKTREES}}/$N diff HEAD` shows only your lines. Run the relevant tests there, then follow 3A with <wt> = {{WORKTREES}}/$N.
   C. Another repo you changed (HAL in ~/.hal, a product repo): follow 3A in that repo, with `-R <owner>/<repo>`, and that repo's own PR rules.
4. Verify each landing. `git -C <wt> ls-remote origin refs/heads/<br>` must equal `git -C <wt> rev-parse HEAD`, and `gh pr view <PR url> --json state,url` must answer.
5. Write your record. `mkdir -p {{RECORDS_DIR}}`, then write ONE new file, `{{RECORDS_DIR}}/$N.md` (if you made no N, `<harness>-<UTC yyyymmddHHMM>-<4 hex>.md`). Use exactly these six headings, in this order, in plain English:
   ## who
   - harness: ...
   - model: ...
   - session: <your session id>
   - cwd: <working directory>
   ## landed
   One bullet per repo: branch, SHA and the PR URL, or "nothing to land".
   ## work this session
   One bullet per PR, commit or deliverable, each with its link. Name PRs as GRIP#123 or by URL.
   ## unfinished
   What is still open, and why.
   ## needs V>>
   One bullet per decision or action only he can take, or the single word "nothing".
   ## left uncommitted
   Paths, and why, or "nothing".
   A personal or client matter gets one neutral line and no details. No secrets. Never edit or delete another session's record.
6. Print the landed line(s) in your own session too, then stop and wait for V>>.

HELD FOR V>> (do not do these)
- Anything that writes in {{GRIP_HOME}}: commit, add, stash, reset, checkout, restore, clean, rm, pull, merge, branch switch, grip-tree-sync. If a stash entry you did not create appears, do not touch it.
- Pushing to main directly, from any harness. Every change goes through a PR.
- Merging, unless AGORA says MERGE AND every required check has finished green. Never use --admin past a red or still-running check. A PR that changes merge authority stays V>>'s.
- Force-pushing or rewriting history.

GUARDRAILS
- Name every path you add. Never `git add .` or `-A`, and never add a directory you did not create in full.
- Machine-written files are not yours unless you deliberately authored the change: rules/HOLISTIC.md, data/genome-shared.json, apps/grip-web/public/snapshot.json, settings.json, state/, learning/, skills/synced/, *.bak, *.lock, daemon-auth-*.
- No secrets, keys, client data or personal-matter content in commits, PRs or records.
- This is a git-crypt repo, so make worktrees only with lib/grip-worktree.sh. If you touch an encrypted path, write "includes a git-crypt path" in the PR body, because AGORA cannot read it.
- Push every commit before AGORA finishes: PRs here can merge within about a minute of its verdict. If one merged without your later commit, cherry-pick it onto a fresh branch from origin/main and open a follow-up PR.
- Run GRIP code with `{{GRIP_HOME}}/bin/grip-py`, never with system python.
- If GitHub reports a merge conflict, record it; do not force a resolution.

PROVENANCE OF THIS PROMPT
Rendered by `grip-fleet prompt` from config/fleet-landing-prompt.md. The template's first version was reviewed by the sovereign council over four rounds on 2026-10-02 with zero Anthropic models (seals daf78395f8b2bf70, 8563462cac4e6601, 79b0d5b9a23f293d, 52bb1dc3963abaf5; re-verify with `grip-sovereign-council --verify <seal>`). Any edit to the template voids that review until it is re-run.
