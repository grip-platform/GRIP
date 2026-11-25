"""KERN — a low-token notation, with a LEXICON, for GRIP's instruction files.

GRIP's always-loaded instruction files (both CLAUDE.md files and the core rules)
passed 290k characters against Claude Code's 150k warning. KERN rewrites each
rule as a short KERNEL of operative clauses, keeps the full doctrine in an ANNEX
under docs/doctrine/, and defines its shared vocabulary once, in a LEXICON at
rules/00-lexicon.md. This package is the tooling that measures and proves that
rewrite; it changes no loaded text itself.

One job per module:

  surfaces    which files a session loads, by tier, and their length in
              Claude Code's own unit (UTF-16 code units)
  tokencount  pluggable token counters (the Tokenizer Tribunal), each labelled
              exact / proxy / estimate, with a content-addressed cache
  measure     per-file and per-tier figures against their budgets
  lexicon     parse and normalise the LEXICON file
  kernel      parse a kernel file: header, clause lines, command spans
  codec       encode and expand between defined terms and their meanings
  ledger      the clause ledger tying each kernel line to its annex section
  lint        every mechanical check, returned as a list of findings

The command-line entry point is bin/grip-lexicon.
"""
