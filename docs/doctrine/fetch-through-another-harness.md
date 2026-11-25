# When Claude Code Cannot Fetch a Page, Another Harness Fetches It: annex

The narrative behind `rules/fetch-through-another-harness.md`: its origin, incidents and measurements. The rule holds the operative text; nothing here loads at startup.

## Origin

V>>, 2026-11-05, while a session was fact-checking a blog post and said it had no direct WebFetch tool:
"use another model to do web fetches if Claude Code can't do it", corrected a minute later to "I meant use another **HARNESS**".

## Measurements, 2026-11-05 about 11:00 UTC, one VentureBeat article

| Route | Result |
|---|---|
| `curl -sSL` with a browser user agent | HTTP 429, exit 0 |
| `opencode run --auto`, WebFetch tool, deepseek-v4-pro | HTTP 429, reported honestly as an error |
| `codex exec --search` | refused: `unexpected argument '--search'`, because the flag belongs to the root command |
| `bin/grip-codex-ladder --subscription-only -- exec ... -c web_search=live ...` | opened the page on gpt-5.6-sol, `provider: openai`; gave headline, byline and date, summarised the paragraphs and declined to reproduce them verbatim |
| `gemini -y -p`, web_fetch tool | no answer after more than 15 minutes, stopped by hand |

Curl and OpenCode both left from this machine and got the same 429. Codex's search may run from OpenAI's side rather than from this machine, which would explain why it got through; that cause was not tested. One article is one instance, so the table is evidence for this route on that page and no more.

## What the first draft got wrong

The first draft of this rule told sessions to run the Codex command above directly. Codex's own review of that pull request (GRIP#8951) raised four findings, and each one held when tested:

1. The command left Codex's local read and shell access on while it read an untrusted page, which breaks `rules/untrusted-content-capability-lock.md`.
2. The command used a path relative to the GRIP checkout, so it failed in any other project.
3. `curl -sSL` exits 0 on a 429 and prints the error page, so "when curl is refused" might never be noticed. The first measurement in the table above shows exactly this.
4. "Never report a page as unreadable" together with "send only public URLs" left a private page with no way to end.

So the route became a tool, `bin/grip-web-fetch` over `lib/web_fetch_harness.py`. A rule cannot carry 25 flags reliably, and a test can hold them where prose cannot.

## The lock, as measured

Measured on codex-cli 0.156.1. With nine features off (`shell_tool`, `unified_exec`, `code_mode`, `multi_agent`, `apps`, `plugins`, `view_image`, `image_generation`, `skill_search`), a canary file in the working folder was not read, and the run still searched the web. Adding the rest of the list kept that, with one exception: `features.code_mode_host=false` also switches off web search ("code-mode host is disabled"), so that one stays on. The first shim run with it off exited 0 with an answer saying it could not open the page, which is why a run that did not open the page now counts as a failure.

A second Codex review of GRIP#8951 raised five more findings, and each one held when tested. The printed `web search:` line does not prove a page was opened; a rotated account prints two provider lines; a tool word in the answer read as a tool; a capability link can sit in a path or a `code=` parameter; and a timeout left Codex and `tee` running after the ladder died. So the tool now reads Codex's JSON events (`exec --json`) and stops the whole process group on a timeout. Measured on both a real page and a missing one: a completed `web_search` item carries `action.type: open_page` and the URL, a good open has a result with a `url`, and a failed open has a result titled `Internal Error` with no `url`.

Codex has no allowlist for tools, so the feature list is a denylist in practice, which `rules/untrusted-content-capability-lock.md` warns against. The tool closes that gap the other way, with an allowlist over what a run shows: any event kind other than `agent_message`, `reasoning`, `web_search` or `error` withholds the answer (exit 5), so a tool a later release adds is caught without being named. JSON mode prints no `provider:` line, so the subscription check is the ladder's own `rung 1 ... ChatGPT subscription` line on stderr, with `--subscription-only` stopping it from going further. The model's own statement that it has no file tool is a self-report; the evidence is the canary and the event log. The URL gate is a net for the obvious private forms and cannot prove a page is public, so the duty not to send a private page stays with the caller.

A third round added three more findings. Numeric spellings of loopback (`127.1`, `0x7f.0.0.1`) passed the gate, and curl had no time or size bound; both held and are fixed. The third held in part: curl-first puts raw page text into a session that holds real tools. That is not new with this rule, since `rules/prove-it-claims-canonical.md` asks for the raw source because a summary is a claim about the source, and a verbatim quote cannot come through a reader that summarises. So the rule now says to read a page as data, in excerpts, and the mechanical answer, a locked reader with later privileged actions gated, is tracked as its own issue. One finding did not hold: that the tool must launch through the GRIP venv. `rules/never-system-python-for-grip.md` draws its line at reaching a record writer, and an import walk over this tool's 14 in-tree modules found none naming `append_idr`.

## Why a rule

Reporting a page as unreadable is a negative claim, and `rules/prove-it-claims-canonical.md` already says a search that never reads the source is a missing search. This rule adds the concrete next route, so the failure no longer ends at the first refusal.

## Falsification

Wrong if, over the next ~10 refused fetches, another harness adds nothing that curl and a search result did not already give. Then the rule narrows to pages that gate a decision. Also wrong if a harness summary is ever quoted as verbatim text because of this route: then the labelling clause needs a mechanical check. Also wrong if the withheld-answer check fires on a clean run more than once in ten: then the tool-line pattern is mis-tuned, and it is tightened rather than dropped. Track: pages reported unreadable with no other harness tried (target 0).

## Scope

The main session, and every fleet it briefs. A subagent reaches this only through its brief until the task-brief channel lands. Siblings: `rules/prove-it-claims-canonical.md`, `rules/gpt-on-the-subscription-never-credits.md`, `rules/untrusted-content-capability-lock.md`, and the memory `feedback_redaction_is_paramount_shield_by_default`.
