"""The countable core of ASD-STE100, "80% of the way", for the output of /clarify.

Andrej Karpathy (2026-10-02, https://x.com/karpathy/status/2105819303471976479)
suggested asking a model to explain things in ASD-STE100, the controlled English
of aircraft maintenance manuals, and softening it to "80% of the way to
ASD-STE100" because the full specification is stringent. GRIP reads that as:
enforce the rules a program can count, and leave the rules that need judgement to
the writer. This module is the counting half. It reads text and writes nothing.

ERRORS (exit 1):

- sentence-length: a descriptive sentence over 25 words, or an instruction (a
  sentence that starts with a command such as "Run", "Set" or "Do not") over 20.
- paragraph-length: a paragraph of more than 6 sentences. Each list item is its
  own paragraph.
- unapproved-word: a word the operator's STE reference sheet marks as not
  approved, with the approved word to use (commence -> start, ensure -> make
  sure, replenish -> fill, utilize -> use, prior to ->
  before, in order to -> to). The sheet lists examples. The full STE dictionary
  has about 900 approved words and is not encoded here.
- determiner-floor: a prose paragraph of 40 words or more with fewer than 4
  determiners (a, an, the, this, that, these, those, its, their, his, her, our,
  your, my) per 100 words. That is telegram style: STE's own rule is "Do not
  leave out words like 'the', 'a' and 'this'", and GRIP's output style calls the
  result laborious. Calibrated on 2026-10-03 against GRIP's own prose: across
  rules/canonical-output-style.md, rules/proportionate-critique.md,
  rules/verify-canonical-not-claimed.md and docs/cos-w0-constraint-lock.md, no
  plain paragraph fell below 5.9 (medians 12.7 to 15.1), the two telegram
  paragraphs found there scored 2.1 and 3.4, and the same prose with its
  determiners removed scored 0. The 40-word gate counts every word. The rate
  counts the plain words only, so a technical name can neither fail a
  paragraph nor take one out of the rule.
- semicolon: a semicolon in prose (Rule 8.1).

WARNINGS (exit 0):

- progressive: "is closing" and the like. STE uses the simple tense.
- passive-descriptive: a passive in a description. Keep it only when necessary.
- passive-instruction: an instruction in the passive: "must", "should" or
  "shall", then "be", then a past participle ("The cover must be removed.").
  Say who does it, or use a command. A state in a command sentence is not one:
  "Make sure that the door is locked." is correct STE (a past participle as an
  adjective) and gets no finding.
- one-instruction: a second command after "and" or "then" in a command
  sentence, except simultaneous actions. The second command is known by its
  object: a word, then "the", "a", "this", "it" and the like ("Remove the
  cover and clean the surface."). "Open the log and test files." has no such
  object and gets no finding.
  Both rules are warnings until their precision is measured on real turns.
- ing-form: an -ing verb with a clear verbal context, apart from a progressive.
- phrasal-verb: a soft phrasal verb with a simpler replacement (Rule 9.3).
- noun-cluster: four or more known engineering nouns without a separator.

NOT CHECKED: one-word-one-meaning, warning-command-first and vertical-list.
The full dictionary still needs the operator's own source. The grammar checks
are conservative heuristics, not a parser. Epistemic modal phrases and sentences
marked UNVERIFIED or "it is not established" keep their verb forms and passives.
A score names each unchecked rule and never counts it as a pass.

TECHNICAL NAMES are never judged as words. A token is a technical name when it
has a slash (a path or a slash command), is a file name with its extension or a
dotfile, has an underscore or a camelCase join, is a PR or issue id such as
GRIP#7207, a commit SHA, a number or another value, a command-line flag, or a
word in capitals of two letters or more, such as GRIP or PRs. A technical name
is never an unapproved word, and the dictionary counts it as approved.

THE SCORE. score(text) measures two halves. The structural pass rate is the
share of prose sentences with no sentence-level error from the rules that it
checks. The dictionary coverage is the share of prose word occurrences that the
STE dictionary approves or that are technical names, each technical name
counting once. The coverage is None until the operator's own dictionary is
built with lib/ste_lexicon.py: GRIP's seed alone is a short list, not a
dictionary, and the score then names its source as "seed". The same text and
the same dictionary always give the same score, and the score names the scorer
version that made it.

THE COMMAND reads each FILE, or standard input when there is no FILE. It prints
each finding and a count. With --score, it prints one row of rates for each
input, a total row for two inputs or more, and the count of each rule. With
--json, it prints the same data as JSON. Exit 0 means no errors, exit 1 means
errors, and exit 2 means that an input cannot be read.

YAML frontmatter, fenced code, links, headings and table rows are never judged
as prose. An inline code span of up to 6 words counts as one word, as a
technical name does in STE. A longer span is prose in backticks, and is judged
as prose. A finding names the line its paragraph starts on.

usage: grip-py lib/ste_lint.py [--json] [--score] [FILE ...]     (reads standard input without FILE)
"""
from __future__ import annotations

import functools
import itertools
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

#: The version of the rules that made a score. Change it with any rule.
#: 3 (2026-10-10): GRIP's declared technical names no longer form a noun cluster,
#: and "approximately" is no longer swapped for "about". Neither rule moves the
#: structural pass rate, so a version-2 rate and a version-3 rate of one text agree.
#: 4 (2026-11-25): a sentence that opens with a quotation in lower case is a new
#: sentence (GRIP#9181). This one can move the structural pass rate, so compare a
#: version-4 rate only with another version-4 rate. The STE trial that pinned
#: version 3 ended on 2026-11-08.
#: 5 (2026-11-25): "Fig." and "et al." are abbreviations, and a quotation that
#: opens with an accented letter starts a sentence too (GRIP#9762). Compare a
#: version-5 rate only with another version-5 rate.
SCORER_VERSION = "5"

DESCRIPTIVE_MAX, INSTRUCTION_MAX, PARAGRAPH_MAX = 25, 20, 6
DETERMINER_FLOOR, FLOOR_MIN_WORDS = 4.0, 40
#: An inline code span of more words than this is prose in backticks.
SPAN_MAX_WORDS = 6

#: The rules this module counts. A score lists each one, even at 0.
RULES_CHECKED = ("sentence-length", "paragraph-length", "unapproved-word", "determiner-floor", "progressive",
                 "one-instruction", "passive-instruction", "passive-descriptive", "ing-form",
                 "semicolon", "phrasal-verb", "noun-cluster")
#: The rules of the register that this module does not count. A score lists each
#: one as "unchecked", and a sentence never passes one of them by default. The
#: remaining three are the writer's half of skills/clarify/SKILL.md.
RULES_UNCHECKED = ("one-word-one-meaning", "warning-command-first", "vertical-list")
UNCHECKED = "unchecked"
#: The rules whose error fails a sentence, and so moves structural_pass_rate.
#: Every other rule is a warning or judges a paragraph or a word; it counts in
#: per_rule and never in the rate. tests/test_ste_lint.py pins this tuple to
#: the behaviour of score(), and lib/ste_hook.py names the same two rules.
SENTENCE_ERROR_RULES = ("sentence-length", "semicolon")
#: The source name of a lexicon that holds GRIP's seed and no dictionary.
SEED_ONLY = "seed"
#: The source name of a lexicon built from the operator's own copy of the standard.
SEED_AND_LOCAL = "seed+local"
#: The smallest built dictionary that counts as one: half of each total the
#: standard publishes for itself (asd-ste100.org: about 900 approved and about
#: 1,200 non-approved words). A local lexicon that read a sliver of a badly laid
#: out export would otherwise report a coverage that reads as a number about the
#: standard, which is the route lib/ste_lexicon.py's own check line only warns about.
DICTIONARY_MIN = {"approved": 450, "unapproved": 600}
#: What a score says when it has no dictionary coverage, by lexicon source.
NOT_LOADED = {
    "none": "dictionary: not loaded",
    SEED_ONLY: "dictionary: not loaded (the seed alone is not a dictionary; "
               "build one with lib/ste_lexicon.py)",
    SEED_AND_LOCAL: "dictionary: built but too small to trust (fewer than 450 approved or "
                    "600 non-approved words read; lib/ste_lexicon.py status shows the counts)",
}

#: The not-approved words on the operator's STE sheet, each inflection with the
#: approved word in the same form.
UNAPPROVED = {
    "commence": "start", "commences": "starts", "commenced": "started", "commencing": "starting",
    "ensure": "make sure", "ensures": "makes sure", "ensured": "made sure", "ensuring": "making sure",
    "replenish": "fill", "replenishes": "fills", "replenished": "filled", "replenishing": "filling",
    "utilize": "use", "utilizes": "uses", "utilized": "used", "utilizing": "using",
    "utilise": "use", "utilises": "uses", "utilised": "used", "utilising": "using",
}
# "approximately" -> "about" was the eighth swap until 2026-10-10. The standard's own
# FAQ (asd-ste100.org) says ABOUT is approved only in the sense "concerned with" and
# cannot mean "approximately", so the swap recommended the unapproved sense. Removed.
UNAPPROVED_PHRASES = {"prior to": "before", "in order to": "to"}

#: Words that make a sentence an instruction when they come first.
COMMAND_VERBS = frozenset(
    "add check click close copy delete do don't enter fill install keep make merge move never "
    "open paste press pull push put read remove replace restart run save select set sign start "
    "stop test turn type use wait write".split())

#: Words ending in -ing after "is" that are not a progressive verb.
NOT_PROGRESSIVE = frozenset(
    "thing nothing something anything everything during morning evening string ceiling "
    "building meaning wording setting heading warning ring king spring sibling".split())

#: A short irregular list complements the regular past participles.
PARTICIPLE = r"(?:[a-z]+ed|been|built|brought|bought|broken|chosen|done|drawn|driven|found|given|gone|held|kept|known|left|lost|made|put|read|run|seen|sent|set|shown|shut|taken|told|torn|worn|written)"
PASSIVE = re.compile(r"\b(?:am|is|are|was|were|be|been|being)\s+(?:(?:not|never|already)\s+)?(" + PARTICIPLE + r")\b", re.I)
NOT_PARTICIPLE = frozenset("red hundred indeed sacred naked wicked beloved".split())
EPISTEMIC = re.compile(r"\b(?:may|might|could|must|should)\s+(?:(?:not|never)\s+)?have\s+(?:been\s+(?:being\s+)?(?:[a-z]+ing|" + PARTICIPLE + r")|" + PARTICIPLE + r")\b", re.I)
UNCERTAIN = re.compile(r"\bUNVERIFIED\b|\bit is not established\b", re.I)
#: A second command is known by its object, so no list of verbs is needed.
COMMAND_JOIN = re.compile(
    r"\b(?:and(?:\s+then)?|then)\s+([a-z]+)\s+(?:the|a|an|this|that|these|those|it|them|your|its)\b", re.I)
#: Words that can stand before "the" after a joiner and are not a command.
NOT_A_COMMAND = frozenset(
    "all both each every any some only also not never then if when with for to in on at of by from into "
    "onto after before until while that which as than or nor but so there here one two half even just "
    "is are was were be has have had do does did can will may must should".split())
#: An instruction in the passive: a modal of duty, "be", a past participle.
PASSIVE_DUTY = re.compile(r"\b(?:must|should|shall)\s+(?:not\s+)?be\s+(?:not\s+)?(" + PARTICIPLE + r")\b", re.I)
SIMULTANEOUS = re.compile(r"\b(?:while|at the same time)\b", re.I)

#: Require an object and a verbal context rather than guessing from the suffix.
ING_VERB = re.compile(r"(?:^|\b(by|before|after|without|while|when|avoid|keep|continue)\s+)([a-z]+ing)\s+(?:the|a|an|this|that|these|those|it|them|your|its)\b", re.I)
#: Words in -ing that are never a verb. After a preposition every other -ing word
#: before an object is a verb, also "setting" and "building".
NEVER_A_VERB = frozenset("thing nothing something anything everything during morning evening string ceiling "
                         "ring king spring sibling".split())

PHRASAL_VERBS = {
    "set up": "INSTALL or CONFIGURE", "carry out": "DO", "figure out": "FIND",
    "roll back": "REVERT", "turn on": "START", "turn off": "STOP",
    "look into": "INVESTIGATE", "kick off": "START", "spin up": "START",
    "wrap up": "FINISH", "pick up": "COLLECT", "come up with": "DEVISE",
    "end up": "BECOME", "go ahead": "PROCEED", "hold off": "WAIT",
    "sort out": "RESOLVE", "work out": "CALCULATE",
}
PHRASAL = re.compile(r"\b(?:" + "|".join(PHRASAL_VERBS) + r")\b", re.I)

#: Known nouns avoid treating every unknown word as a noun. Ambiguous command
#: verbs are absent. An unfamiliar noun ends the run, so recall is limited.
CLUSTER_NOUNS = frozenset(
    "access account action agent air application approval assembly audit battery body cable "
    "chain chamber circuit client code command component computer configuration connection "
    "context control coolant data database decision device disk document door engine error "
    "event evidence failure file filter flow fluid fuel gate hardware history hose input "
    "instruction interface knowledge line log machine memory message model module motor "
    "network oil operation operator output panel path pipe pipeline platform policy power "
    "pressure process program project protocol pump quality queue record reference request "
    "reservoir response review risk role rule safety schema security sensor server service "
    "session signal software source state status storage stream surface system task temperature "
    "token tool trace unit user valve version voltage water wire worker "
    "analysis branch buffer call capacity change check count design domain feature function "
    "head key language layer length level limit load manager number object packet port "
    "range rate relief report result return sample size step table test time type value".split())
CLUSTER_TOKEN = re.compile(r"[A-Za-z]+|[^\w\s]|\d+|_+")
NOUN_SUFFIX = re.compile(r"[a-z]{3,}(?:tion|ment|ness|ity|ance|ence|ism|ship)s?\Z")

#: Whole-token patterns for a technical name. STE counts a technical name as one
#: word, and no dictionary judges it.
TECHNICAL_NAMES = tuple(re.compile(pattern) for pattern in (
    r".*/.*",                                             # a path, or a slash command such as /eli5
    r"[\w-]{2,}(?:\.[\w-]+)*\.[A-Za-z][A-Za-z0-9]{0,4}",  # a file name with its extension
    r"\.[A-Za-z][\w.-]*",                                 # a dotfile, such as .env
    r".*[A-Za-z0-9]_[A-Za-z0-9].*",                       # an identifier with an underscore
    r".*[a-z][A-Z].*",                                    # an identifier in camelCase
    r"[\w.-]*#\d+",                                       # a PR or issue id, such as GRIP#7207
    r"(?=.*\d)[0-9a-f]{7,40}",                            # a commit SHA
    r"v?[^A-Za-z]*\d[^A-Za-z]*",                          # a number, a date, a version or a value
    r"--?[A-Za-z][\w-]*",                                 # a command-line flag
    r"(?=(?:[^A-Za-z]*[A-Z]){2})[A-Z0-9-]+s?",            # a word in capitals, such as GRIP or PRs
))
TOKEN = re.compile(r"\S+")
POSSESSIVE = re.compile(r"['’]s\Z")
EDGE_LEAD, EDGE_TRAIL = "\"'‘“([{<*_", "\"'’”)]}>*_,.;:!?"
#: Quoted text inside a finding's detail: the words it echoes from the prose.
QUOTED = re.compile(r"'[^']*'")

PLACEHOLDER = "CODE"
DOT = "․"
ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.", "Mr.", "Mrs.", "Ms.", "Dr.", "St.", "No.",
                 "Fig.", "et al.")

FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")
SKIPPED_LINE = re.compile(r"^(#|\||---|===)")
QUOTE_MARKER = re.compile(r"^(>\s?)+")
LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
#: The quote marks that open and close a quotation, named once for SENTENCE_END.
OPEN_QUOTES, CLOSE_QUOTES = "\"'‘“", "\"'’”"
#: A sentence ends at . ! or ? when the next one starts, behind optional quotes
#: and brackets, with a capital, a digit or a slash command, or with an opening
#: quote before any letter, an accented one included (a quotation that opens in
#: lower case, GRIP#9181 and GRIP#9762); or with markdown markup (bold, italics,
#: a code span) whatever follows it.
SENTENCE_END = re.compile(
    rf"(?<=[.!?])[{CLOSE_QUOTES})\]*_]*\s+"
    rf"(?=[{OPEN_QUOTES}(\[]*(?:[A-Z0-9/]|[{OPEN_QUOTES}][^\W\d_])|[*_`]+\S)")
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")
DETERMINER = re.compile(r"\b(a|an|the|this|that|these|those|its|their|his|her|our|your|my)\b", re.I)
PHRASE = re.compile(r"\b(prior to|in order to)\b", re.I)
PROGRESSIVE = re.compile(r"\b(?:am|is|are|was|were|be|been|being)\s+([A-Za-z]+ing)\b", re.I)


@dataclass(frozen=True)
class Finding:
    rule: str
    line: int
    severity: str
    detail: str


@dataclass(frozen=True)
class STEScore:
    """How close a text is to ASD-STE100, in two halves: its structure and its words."""
    sentences: int                      # prose sentences judged
    instructions: int                   # sentences judged as instructions
    structural_errors: int              # sentences with at least one error from a checked sentence rule
    structural_pass_rate: float         # 1 - structural_errors / sentences; 1.0 with no sentences
    dictionary_coverage: float | None   # approved or technical word occurrences / prose word occurrences
    per_rule: dict[str, int | str]      # rule -> findings, or "unchecked"; every rule is present
    rules_checked: tuple[str, ...]      # the rules behind the pass rate
    rules_unchecked: tuple[str, ...]    # the rules of the register that no count stands for
    lexicon_source: str                 # "none", or the source of the loaded dictionary
    scorer_version: str                 # SCORER_VERSION of the rules that made this score
    exceptions: int                     # findings whose own wording, outside quotes, says "technical"
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class _Tally:
    """The counts behind a score. Tallies add count by count, so a total over many
    files is a true total, not an average of rates."""
    findings: tuple[Finding, ...] = ()
    sentences: int = 0
    instructions: int = 0
    failing: int = 0
    covered: int | None = None
    words: int = 0

    def score(self, source: str) -> STEScore:
        """The score of these counts. ``source`` names the dictionary, or is "none"."""
        per_rule = (dict.fromkeys(RULES_CHECKED, 0) | Counter(finding.rule for finding in self.findings)
                    | dict.fromkeys(RULES_UNCHECKED, UNCHECKED))
        rate = 1 - self.failing / self.sentences if self.sentences else 1.0
        coverage = None if self.covered is None else (self.covered / self.words if self.words else 1.0)
        exceptions = sum("technical" in QUOTED.sub("", finding.detail).lower() for finding in self.findings)
        return STEScore(self.sentences, self.instructions, self.failing, rate, coverage, per_rule,
                        RULES_CHECKED, RULES_UNCHECKED, source, SCORER_VERSION, exceptions, self.findings)


def _blank(match: re.Match) -> str:
    """The same number of line breaks as the text matched, so line numbers hold."""
    return "\n" * match.group(0).count("\n")


def _code_span(match: re.Match) -> str:
    """A short code span is one technical name. A long one is prose in
    backticks, so backticks cannot hide prose from the rules."""
    inner = match.group(0)[1:-1]
    return inner if len(inner.split()) > SPAN_MAX_WORDS else PLACEHOLDER


def _clean(text: str) -> str:
    """Prose only: YAML frontmatter at the very start and fenced code become
    blank lines, a short inline code span one placeholder word, a link its text,
    a bare URL nothing."""
    text = FENCE.sub(_blank, FRONTMATTER.sub(_blank, text))
    return URL.sub("", LINK.sub(r"\1", INLINE_CODE.sub(_code_span, text)))


def is_technical(token: str) -> bool:
    """True when ``token`` is a technical name, which no dictionary judges.
    Quotes, brackets, markup and end punctuation around it do not count."""
    core = POSSESSIVE.sub("", token.lstrip(EDGE_LEAD).rstrip(EDGE_TRAIL))
    return any(pattern.fullmatch(core) for pattern in TECHNICAL_NAMES)


def _masked(block: str) -> str:
    """``block`` with each technical name replaced by the placeholder word."""
    return TOKEN.sub(_mask_token, block)


def _mask_token(match: re.Match) -> str:
    token = match.group(0)
    if not is_technical(token):
        return token
    # A semicolon after a name still separates prose clauses.
    return PLACEHOLDER + (";" if token.endswith(";") else "")


def blocks_of(text: str) -> list[tuple[int, str]]:
    """Each paragraph and each list item, with the line it starts on.
    Headings, table rows and rules are left out."""
    blocks: list[list] = []
    current = None
    for number, raw in enumerate(_clean(text).splitlines(), 1):
        line = QUOTE_MARKER.sub("", raw.strip()).strip()
        if not line or SKIPPED_LINE.match(line):
            current = None
            continue
        if current is None or LIST_MARKER.match(line):
            current = [number, LIST_MARKER.sub("", line)]
            blocks.append(current)
            continue
        current[1] += " " + line
    return [(number, body) for number, body in blocks]


def sentences_of(block: str) -> list[str]:
    """The sentences of one paragraph. A full stop inside an abbreviation or a
    number does not end a sentence."""
    guarded = functools.reduce(lambda text, short: text.replace(short, short.replace(".", DOT)),
                               ABBREVIATIONS, block)
    return [part.replace(DOT, ".").strip() for part in SENTENCE_END.split(guarded) if part.strip()]


def _is_instruction(sentence: str) -> bool:
    first = WORD.findall(_masked(sentence).lower())[:1]
    return bool(first) and first[0] in COMMAND_VERBS


def _sentence_findings(line: int, sentence: str) -> list[Finding]:
    # A technical name counts as one word, as it does in STE. Counted unmasked,
    # the path lib/ste_lint.py would be four words and GRIP#7207 two.
    words = len(WORD.findall(_masked(sentence)))
    instruction = _is_instruction(sentence)
    limit = INSTRUCTION_MAX if instruction else DESCRIPTIVE_MAX
    kind = "instruction" if instruction else "sentence"
    findings = ([Finding("sentence-length", line, "error", f"{kind} of {words} words; the limit is {limit}")]
                if words > limit else [])
    prose = _masked(sentence)
    if ";" in prose:
        findings.append(Finding("semicolon", line, "error", "Split the prose at the semicolon (Rule 8.1)."))
    if instruction and _joined_commands(prose):
        findings.append(Finding("one-instruction", line, "warning", "Use one command per sentence."))
    verbs = _verb_prose(sentence)
    return findings + _passive_findings(line, verbs, instruction) + _progressive_findings(line, verbs) + _ing_findings(line, verbs)


def _joined_commands(prose: str) -> bool:
    """Only the main clause supplies sequential commands before a while clause."""
    if re.search(r"\bat the same time\b", prose, re.I):
        return False
    main = SIMULTANEOUS.split(prose, maxsplit=1)[0]
    return any(match.group(1).lower() not in NOT_A_COMMAND for match in COMMAND_JOIN.finditer(main))


def _verb_prose(sentence: str) -> str:
    """Keep uncertainty intact. A status marker scopes the whole sentence.

    A modal exempts its verb chain only, so another clause can still fail.
    Read status markers before masking technical names such as UNVERIFIED.
    """
    if UNCERTAIN.search(sentence):
        return ""
    return EPISTEMIC.sub(PLACEHOLDER, _masked(sentence))


def _passive_findings(line: int, prose: str, instruction: bool) -> list[Finding]:
    """A passive instruction anywhere, and any other passive in a description.
    A command sentence states conditions ("that the door is locked"), so its
    other passives are not flagged."""
    duty = [Finding("passive-instruction", line, "warning", f"'{match.group(0)}': say who does it, or use a command.")
            for match in PASSIVE_DUTY.finditer(prose) if match.group(1).lower() not in NOT_PARTICIPLE]
    if instruction:
        return duty
    rest = PASSIVE_DUTY.sub(PLACEHOLDER, prose)
    return duty + [Finding("passive-descriptive", line, "warning", f"'{match.group(0)}': Keep the passive only when necessary.")
                   for match in PASSIVE.finditer(rest) if match.group(1).lower() not in NOT_PARTICIPLE]


def _ing_findings(line: int, prose: str) -> list[Finding]:
    return [Finding("ing-form", line, "warning", f"'{match.group(2)}': use a simple verb form.")
            for match in ING_VERB.finditer(prose) if _is_ing_verb(match.group(1), match.group(2).lower())]


def _is_ing_verb(preposition, word: str) -> bool:
    """After a preposition only a word that is never a verb is exempt. At the
    start of a sentence the noun-like words of the progressive rule are exempt too."""
    return word not in (NEVER_A_VERB if preposition else NOT_PROGRESSIVE)


def _phrasal_findings(line: int, prose: str) -> list[Finding]:
    return [Finding("phrasal-verb", line, "warning",
                    f"'{match.group(0)}': use {PHRASAL_VERBS[match.group(0).lower()]} (Rule 9.3).")
            for match in PHRASAL.finditer(prose)]


def _noun_findings(line: int, prose: str) -> list[Finding]:
    """Quote maximal runs of known nouns. Punctuation and names end a run."""
    findings = []
    run = []
    for match in CLUSTER_TOKEN.finditer(prose + "."):
        word = match.group(0)
        if _is_cluster_noun(word):
            run.append(word)
            continue
        if len(run) >= 4:
            findings.append(Finding("noun-cluster", line, "warning",
                                    f"'{' '.join(run)}': separate the nouns with a preposition or a verb."))
        run = []
    return findings


def _is_cluster_noun(word: str) -> bool:
    lower = word.lower()
    if word == PLACEHOLDER or lower in COMMAND_VERBS or lower in _technical_names():
        return False
    return lower in CLUSTER_NOUNS or lower.removesuffix("s") in CLUSTER_NOUNS or bool(NOUN_SUFFIX.fullmatch(lower))


@functools.lru_cache(maxsize=1)
def _technical_names() -> frozenset[str]:
    """GRIP's declared technical names, in lower case: the seed's, and a local
    lexicon's when one is built. The standard lets an organisation declare its own
    technical nouns, and a declared noun never forms a cluster with the rest. Loaded
    once per process; the same set serves lint() and score(), so their findings agree."""
    lexicon = _load_lexicon()
    return frozenset(str(name).lower() for name in (getattr(lexicon, "technical_names", None) or ()))


def _paragraph_findings(line: int, sentences: list[str]) -> list[Finding]:
    count = len(sentences)
    return ([Finding("paragraph-length", line, "error", f"paragraph of {count} sentences; the limit is {PARAGRAPH_MAX}")]
            if count > PARAGRAPH_MAX else [])


def _unapproved(line: int, said: str, use: str) -> Finding:
    return Finding("unapproved-word", line, "error", f"'{said}' is not an approved word: use '{use}'")


def _word_findings(line: int, block: str) -> list[Finding]:
    prose = _masked(block)
    words = [_unapproved(line, word, UNAPPROVED[word.lower()]) for word in WORD.findall(prose)
             if word.lower() in UNAPPROVED]
    phrases = [_unapproved(line, match.group(0), UNAPPROVED_PHRASES[match.group(0).lower()])
               for match in PHRASE.finditer(prose)]
    return words + phrases


def _floor_findings(line: int, block: str) -> list[Finding]:
    # The gate counts every word, as the calibration did, so a paragraph cannot
    # leave the rule by being full of technical names. The rate counts the plain
    # words only: a technical name is no evidence of telegram style, and counted
    # as it is written one path is four words.
    if len([word for word in WORD.findall(block) if word != PLACEHOLDER]) < FLOOR_MIN_WORDS:
        return []
    prose = _masked(block)
    words = [word for word in WORD.findall(prose) if word != PLACEHOLDER]
    if not words:
        return []
    rate = 100 * len(DETERMINER.findall(prose)) / len(words)
    return [] if rate >= DETERMINER_FLOOR else [Finding(
        "determiner-floor", line, "error",
        f"determiners at {rate:.1f} per 100 words; plain prose stays above {DETERMINER_FLOOR:g} "
        "(STE: do not leave out 'the', 'a' and 'this')")]


def _progressive_findings(line: int, block: str) -> list[Finding]:
    return [Finding("progressive", line, "warning",
                    f"'{match.group(0)}': STE uses the simple tense ('it closes', not 'it is closing')")
            for match in PROGRESSIVE.finditer(block) if match.group(1).lower() not in NOT_PROGRESSIVE]


def _block_level(line: int, block: str, sentences: list[str]) -> list[Finding]:
    """The findings judged on the whole paragraph rather than one sentence."""
    return [*_paragraph_findings(line, sentences), *_word_findings(line, block),
            *_floor_findings(line, block), *_phrasal_findings(line, _masked(block)),
            *_noun_findings(line, _masked(block))]


def _load_lexicon():
    """The STE dictionary from lib/ste_lexicon.py, or None when that module is
    absent or has not loaded a dictionary."""
    try:
        if __package__:
            from . import ste_lexicon  # noqa: PLC0415
        else:
            import ste_lexicon  # noqa: PLC0415
    except ImportError:
        return None
    return ste_lexicon.load_lexicon()


def _source(lexicon) -> str:
    """The name of the dictionary's source, such as "seed", or "none" without one."""
    return "none" if lexicon is None else str(getattr(lexicon, "source", "unknown"))


def _dictionary(lexicon):
    """``lexicon`` when it holds the operator's own dictionary, else None.

    The seed alone is GRIP's own short list: a few approved words and its
    technical names. A coverage counted against it is a number about the seed,
    and it would read as a number about the standard (0.078 for a file that
    passes every sentence rule, measured 2026-10-05). So a score has no
    coverage until a dictionary is built, and its source still says "seed".

    A built dictionary that read too little of the standard is refused the same
    way (DICTIONARY_MIN): its source still says "seed+local", so the reader can
    see that a build happened, and the coverage stays None until the build reads
    enough.
    """
    if _source(lexicon) in ("none", SEED_ONLY) or _too_small(lexicon):
        return None
    return lexicon


def _too_small(lexicon) -> bool:
    """True when the built dictionary's own counts fall under DICTIONARY_MIN for any
    list. The LOCAL build's counts decide where the lexicon reports them: the seed's
    own entries (18 approved, 22 non-approved) must never pad a build towards the
    floor (a Codex finding on GRIP#7920). A lexicon without counts is too small by
    definition: it cannot show its size."""
    counts = getattr(lexicon, "local_counts", None) or getattr(lexicon, "counts", None) or {}
    return any(int(counts.get(name, 0) or 0) < floor for name, floor in DICTIONARY_MIN.items())


def _coverage(block: str, lexicon) -> tuple[int, int]:
    """The prose word occurrences of ``block`` that are approved or technical,
    and all of them. Each word goes to the dictionary as it is written."""
    words = WORD.findall(_masked(block))
    return sum(word == PLACEHOLDER or bool(lexicon.is_approved(word)) for word in words), len(words)


def _block_tally(line: int, block: str, lexicon) -> _Tally:
    sentences = sentences_of(block)
    groups = [_sentence_findings(line, sentence) for sentence in sentences]
    covered, words = _coverage(block, lexicon) if lexicon is not None else (None, 0)
    return _Tally(tuple(itertools.chain(*groups, _block_level(line, block, sentences))),
                  len(sentences), sum(map(_is_instruction, sentences)),
                  sum(any(finding.severity == "error" for finding in group) for group in groups),
                  covered, words)


def _combine(tallies: list[_Tally], counted: bool) -> _Tally:
    """One tally for many: each count added, the findings kept in order. The
    coverage is counted only when a dictionary was loaded for them all."""
    return _Tally(tuple(itertools.chain.from_iterable(tally.findings for tally in tallies)),
                  sum(tally.sentences for tally in tallies), sum(tally.instructions for tally in tallies),
                  sum(tally.failing for tally in tallies),
                  sum(tally.covered for tally in tallies) if counted else None,
                  sum(tally.words for tally in tallies))


def _tally(text: str, lexicon) -> _Tally:
    return _combine([_block_tally(line, block, lexicon) for line, block in blocks_of(text)], lexicon is not None)


def lint(text: str) -> list[Finding]:
    """Every finding in ``text``, in the order its paragraphs appear."""
    return list(_tally(text, None).findings)


def score(text: str) -> STEScore:
    """The score of ``text``. Its findings are exactly those of ``lint(text)``."""
    lexicon = _load_lexicon()
    return _tally(text, _dictionary(lexicon)).score(_source(lexicon))


def report(findings: list[Finding]) -> dict:
    errors = sum(finding.severity == "error" for finding in findings)
    return {"errors": errors, "warnings": len(findings) - errors,
            "findings": [asdict(finding) for finding in findings]}


def _render(findings: list[Finding], summary: dict) -> str:
    rows = [f"line {finding.line}  {finding.rule}  {finding.detail}" for finding in findings]
    return "\n".join([*rows, f"{summary['errors']} errors, {summary['warnings']} warnings"])


def _lint_output(named: list[tuple[str, _Tally]], as_json: bool) -> str:
    """One input prints as it always has. Several print each finding with its
    file, then one count for them all."""
    if len(named) == 1:
        findings = list(named[0][1].findings)
        summary = report(findings)
        return json.dumps(summary, indent=1) if as_json else _render(findings, summary)
    total = report([finding for _, tally in named for finding in tally.findings])
    if as_json:
        files = [{"path": name, **report(list(tally.findings))} for name, tally in named]
        return json.dumps({"errors": total["errors"], "warnings": total["warnings"], "files": files}, indent=1)
    rows = [f"{name}  line {finding.line}  {finding.rule}  {finding.detail}"
            for name, tally in named for finding in tally.findings]
    return "\n".join([*rows, f"{total['errors']} errors, {total['warnings']} warnings in {len(named)} files"])


def _score_row(name: str, result: STEScore, width: int) -> str:
    coverage = "-" if result.dictionary_coverage is None else f"{result.dictionary_coverage:.3f}"
    return (f"{name:<{width}}  {result.sentences:>9}  {result.instructions:>12}  {result.structural_errors:>17}  "
            f"{result.structural_pass_rate:>15.3f}  {coverage:>10}")


def _score_table(named: list[tuple[str, STEScore]], total: STEScore) -> str:
    """A row for each input, a total row for two or more, then the count of each rule."""
    width = max(len(name) for name in ["input", "total", *(name for name, _ in named)])
    header = (f"{'input':<{width}}  {'sentences':>9}  {'instructions':>12}  {'structural errors':>17}  "
              f"{'structural pass':>15}  {'dictionary':>10}")
    rows = [_score_row(name, result, width) for name, result in named]
    if len(named) > 1:
        rows.append(_score_row("total", total, width))
    rule_width = max(map(len, total.per_rule))
    counts = [f"{rule:<{rule_width}}  {count:>9}" for rule, count in total.per_rule.items()]
    missing = ["", NOT_LOADED.get(total.lexicon_source, NOT_LOADED["none"])] if total.dictionary_coverage is None else []
    footer = f"scorer {total.scorer_version}, dictionary source {total.lexicon_source}"
    return "\n".join([header, *rows, "", f"{'rule':<{rule_width}}  {'count':>9}", *counts, *missing, "", footer])


def _score_output(named: list[tuple[str, _Tally]], lexicon, as_json: bool) -> str:
    source = _source(lexicon)
    scores = [(name, tally.score(source)) for name, tally in named]
    total = _combine([tally for _, tally in named], _dictionary(lexicon) is not None).score(source)
    if not as_json:
        return _score_table(scores, total)
    if len(scores) == 1:
        return json.dumps(asdict(total), indent=1)
    files = [{"path": name, **asdict(result)} for name, result in scores]
    summary = {key: value for key, value in asdict(total).items() if key != "findings"}
    return json.dumps({"files": files, "total": summary}, indent=1)


def _read(paths: list[str]) -> list[tuple[str, str]] | None:
    """Each input with its name, or None after a message when one cannot be read."""
    if not paths:
        return [("stdin", sys.stdin.read())]
    try:
        return [(path, Path(path).read_text(encoding="utf-8")) for path in paths]
    except OSError as exc:
        print(f"ste_lint: cannot read {exc.filename}: {exc.strerror}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    """Exit 0 with no errors, 1 with errors, 2 when an input cannot be read."""
    args = sys.argv[1:] if argv is None else argv
    if {"-h", "--help"} & set(args):
        print(__doc__.strip().splitlines()[-1])
        return 0
    inputs = _read([arg for arg in args if arg not in ("--json", "--score")])
    if inputs is None:
        return 2
    lexicon = _load_lexicon() if "--score" in args else None
    named = [(name, _tally(text, _dictionary(lexicon))) for name, text in inputs]
    as_json = "--json" in args
    print(_score_output(named, lexicon, as_json) if "--score" in args else _lint_output(named, as_json))
    return 1 if any(finding.severity == "error" for _, tally in named for finding in tally.findings) else 0


if __name__ == "__main__":
    sys.exit(main())
