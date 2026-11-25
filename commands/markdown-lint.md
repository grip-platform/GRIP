# Markdown Lint Command

Scan markdown files for linting violations and offer auto-fix for common issues.

## What You Do

1. **Scan Current Directory** for markdown files:

   ```bash
   fd -e md
   ```

2. **Detect Violations** by checking for:
   - **MD040**: An opening code fence with no language tag (a closing fence is always bare)
   - **MD036**: Bold/italics used as headings (**Text:** pattern)
   - **MD022/MD031/MD032**: Missing blank lines around headings/code/lists
   - **MD-US**: A `snake_case` identifier, a file name, or a key written bare outside
     code (`per_person_sharing`, `build_handcheck.py`). Not a markdownlint rule —
     some Markdown viewers render the underscore as mid-word italics even though
     CommonMark and GitHub never do

3. **Report Findings** in this format:

   ```text
   ## Markdown Lint Report

   **Files Scanned:** X
   **Violations Found:** Y

   ### By Type:

   - MD040 (Missing code language): Z files
   - MD036 (Emphasis as heading): Z files
   - MD022/MD031/MD032 (Missing blank lines): Z files
   - MD-US (Bare underscore identifier): Z files

   ### Affected Files:

   - path/to/file1.md (MD040: 3, MD036: 1)
   - path/to/file2.md (MD031: 5)

   ```

4. **Offer Auto-Fix** for mechanical issues:
   - MD040: Add `text` to a bare opening fence only, never to a closing fence.
     CommonMark forbids an info string on a closing fence, so a tagged closer
     never closes its block and the rest of the file renders as code
   - MD022/MD031/MD032: Add blank lines
   - MD036: Requires manual review (semantic decision)
   - MD-US: Wrap the whole identifier, extension included, in backticks. Never
     inside a fenced code block, an inline code span, a URL, a link target, an
     image path, an HTML tag, front matter, an already escaped underscore
     (`per\_person`), or `_italic_`/`__bold__` emphasis

5. **Execute Fixes** (if user approves):
   - Use Python script from markdown-expert skill: `lib/markdown_underscore_check.py`
     implements MD-US detection and its fix (`find_identifiers`, `fix_text`) —
     the skill, the agent and this command all read the same module
   - Verify fixes with pattern matching
   - Report: "✅ Fixed X violations in Y files"

## When Auto-Fix Is Safe

- ✅ Adding `text` to a bare opening fence (conservative default)
- ❌ Adding anything to a closing fence (it stops closing the block)
- ✅ Adding blank lines around structures
- ❌ Changing bold to headings (needs semantic understanding)
- ✅ Wrapping a bare underscore identifier in backticks (MD-US — never inside
  code, a link target, an image path, an HTML tag, or front matter)

## Integration

- Uses patterns from `~/.claude/skills/markdown-expert/patterns.json`
- Can trigger markdown-expert agent for complex fixes
- Logs metrics to `~/.claude/metrics.jsonl`
- MD-US runs through `lib/markdown_underscore_check.py`, the one implementation
  this command, the skill and the agent all share

## Example Usage

```text
User: /markdown-lint
```
