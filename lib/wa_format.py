"""Convert model-written Markdown into WhatsApp markup, preserving code and links."""

import re

__all__ = ['to_whatsapp']

_BOLD_STARS = re.compile(r'\*\*(?=\S)(.+?)(?<=\S)\*\*')
_BOLD_UNDERSCORES = re.compile(r'(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)')
_STRIKE = re.compile(r'~~(?=\S)(.+?)(?<=\S)~~')
_LINK = re.compile(r'\[([^\]\n]+)\]\(([^)\s]+)\)')
# Up to three leading spaces, and an optional closing run of hashes after a space.
_HEADING = re.compile(r'^ {0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$')
_BULLET = re.compile(r'^([ \t]*)[*+](?=[ \t])')
# Four or more leading spaces make an indented code line, never a fence.
_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
# A code span closes on a backtick run exactly as long as the one that opened it. It may
# run across a single line break, but a blank line ends the paragraph and so the span.
_CODE_SPAN = re.compile(r'(`+)((?:[^\n]|\n(?![ \t]*\n))+?)(?<!`)\1(?!`)')
# Private-use characters mark the spans set aside while emphasis is converted.
_OPEN, _CLOSE = '', ''
_SLOT = re.compile(_OPEN + r'(\d+)' + _CLOSE)
_BARE_URL = re.compile(r'https?://[^\s<>()' + _OPEN + _CLOSE + r']+')
# Each pass can expose one more nested link, so the passes are capped: ordinary text settles
# in two or three, and a deeply nested input stays linear instead of quadratic.
_MAX_PASSES = 8


def _keep(kept, text):
    kept.append(text)
    return f'{_OPEN}{len(kept) - 1}{_CLOSE}'


def _put_back(s, kept):
    for _ in range(len(kept) + 1):  # a kept URL may hold a kept code span
        s = _SLOT.sub(lambda match: kept[int(match.group(1))], s)
    return s


def _link(kept, match):
    label, url = match.groups()
    return _keep(kept, url) if label.strip() == url else f'{label} ({_keep(kept, url)})'


def _bare_url(kept, match):
    """Set a URL aside, leaving outside it any emphasis marks that close right after it."""
    url = match.group(0).rstrip('*_~')
    return _keep(kept, url) + match.group(0)[len(url):]


def _inline(s, bold_repl, kept):
    s = _LINK.sub(lambda match: _link(kept, match), s)
    s = _BARE_URL.sub(lambda match: _bare_url(kept, match), s)
    s = _BOLD_STARS.sub(bold_repl, s)
    s = _BOLD_UNDERSCORES.sub(bold_repl, s)
    return _STRIKE.sub(r'~\1~', s)


def _body(body, kept):
    heading = _HEADING.match(body)
    if heading:
        return '*' + _inline(heading.group(1), r'\1', kept) + '*'
    return _inline(_BULLET.sub(r'\1-', body), r'*\1*', kept)


def _convert_block(text):
    """Convert lines outside any fence, after setting their code spans aside as a whole."""
    kept = []
    text = _CODE_SPAN.sub(lambda match: _keep(kept, match.group(0)), text)
    lines = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip('\r\n')
        lines.append(_body(body, kept) + line[len(body):])
    return _put_back(''.join(lines), kept)


def _fence_of(body):
    match = _FENCE.match(body)
    return (match.group(1)[0], len(match.group(1))) if match else None


def _closes(body, fence):
    """A closing fence repeats the opening character at least as many times, and nothing else."""
    found = _fence_of(body)
    return bool(found and found[0] == fence[0] and found[1] >= fence[1]
                and not body.strip().lstrip(fence[0]).strip())


def _one_pass(text):
    out, block, fence = [], [], None
    for line in text.splitlines(keepends=True):
        body = line.rstrip('\r\n')
        if fence:
            fence = None if _closes(body, fence) else fence
            out.append(line)
        elif _fence_of(body):
            out.append(_convert_block(''.join(block)))
            block, fence = [], _fence_of(body)
            out.append(line)
        else:
            block.append(line)
    out.append(_convert_block(''.join(block)))
    return ''.join(out)


def to_whatsapp(text: str) -> str:
    """Return stable WhatsApp markup while retaining original line endings.

    A message that already holds the private-use marks comes back unchanged, because the
    marks could not be told apart from its own text.
    """
    if _OPEN in text or _CLOSE in text:
        return text
    for _ in range(_MAX_PASSES):
        converted = _one_pass(text)
        if converted == text:
            return converted
        text = converted
    return text
