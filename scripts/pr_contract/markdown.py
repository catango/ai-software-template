"""A line scanner for PR bodies: fences, HTML comments, level-2 headings.

The scanner is a small state machine over lines, so there is no regular expression with
nested quantifiers and the work is linear in the length of the body.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional

MAX_BODY = 65536
MAX_LINES = 5000

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MARKER = re.compile(r"\s*PR type: ([a-z-]{1,20})(?![a-z-])")
_REPO = r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}"
_DIGITS = "[0-9]" + "{1,9}"
_REF = rf"(?P<repo>{_REPO})?#(?P<number>{_DIGITS})"
_IMPLEMENTS = re.compile(rf"Implements: {_REF}[ \t]*")
_DESIGN = re.compile(rf"Design: {_REF}[ \t]*")
_ISSUE_REF = re.compile(rf"(?<![A-Za-z0-9_.&/-]){_REF}(?![0-9])")


@dataclass(frozen=True)
class Ref:
    """An issue or PR reference: `#<n>` (repo is None, the repository of the text) or `<owner>/<repo>#<n>`."""
    repo: Optional[str]
    number: int


@dataclass
class Parsed:
    marker: Optional[str] = None
    sections: Dict[str, str] = field(default_factory=dict)
    implements: Optional[Ref] = None
    design: Optional[Ref] = None


_MARKERS = re.compile(r"(?:>|[-*+]|[0-9]{1,9}[.)]|\[[ xX]\])(?:[ \t]+|$)")


def has_content(text):
    """True when the text holds at least one letter or digit outside the list, quote, and checkbox markers that start its lines.
    Punctuation and symbols alone, and an empty list or checklist, are not content."""
    for line in text.split("\n"):
        line = line.lstrip(" \t")
        while True:
            match = _MARKERS.match(line)
            if not match:
                break
            line = line[match.end():]
        if any(unicodedata.category(ch)[0] in "LN" for ch in line):
            return True
    return False


def same_text(a, b):
    """Whether two blocks of text are the same lines, ignoring blank lines, line ends, and spaces at the ends of a line. Case,
    order, and checkbox state are compared."""
    def lines(text):
        return [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
    return lines(a) == lines(b)


def _ref(match):
    return Ref(match.group("repo"), int(match.group("number")))


def references(text):
    """References written as #<digits> or <owner>/<repo>#<digits> in a block of text, in order."""
    return [_ref(m) for m in _ISSUE_REF.finditer(text)]


def parse(body):
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    parsed = Parsed()
    fence = None  # (character, length) while inside a fenced code block
    in_comment = False
    comment = []
    heading = None
    buffer: List[str] = []

    def close_section():
        if heading is not None and heading not in parsed.sections:
            parsed.sections[heading] = "\n".join(buffer)

    def finish_comment():
        if parsed.marker is None:
            match = _MARKER.match("".join(comment))
            if match:
                parsed.marker = match.group(1)

    for line in text.split("\n"):
        if not in_comment:
            if fence is not None:
                match = _FENCE.match(line)
                closing = bool(match) and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1]
                if closing and line.strip().strip(fence[0]) == "":
                    fence = None
                buffer.append(line)
                continue
            match = _FENCE.match(line)
            if match:
                fence = (match.group(1)[0], len(match.group(1)))
                buffer.append(line)
                continue
        visible = []
        position = 0
        while position <= len(line):
            if in_comment:
                end = line.find("-->", position)
                if end < 0:
                    comment.append(line[position:])
                    comment.append("\n")
                    break
                comment.append(line[position:end])
                finish_comment()
                comment = []
                in_comment = False
                position = end + 3
            else:
                start = line.find("<!--", position)
                if start < 0:
                    visible.append(line[position:])
                    break
                visible.append(line[position:start])
                in_comment = True
                comment = []
                position = start + 4
        shown = "".join(visible)
        if shown.startswith("## "):
            close_section()
            heading = shown[3:].strip()
            buffer = []
            continue
        buffer.append(shown)
        if parsed.implements is None:
            match = _IMPLEMENTS.fullmatch(shown)
            if match:
                parsed.implements = _ref(match)
        if parsed.design is None:
            match = _DESIGN.fullmatch(shown)
            if match:
                parsed.design = _ref(match)
    close_section()
    return parsed
