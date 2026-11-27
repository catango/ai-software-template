"""Sanitizing of untrusted values before they are printed or written to a job summary."""
import re
import unicodedata

_DROP = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def clean(value, limit=80):
    """Drop control, format (zero-width, bidirectional), and unassigned characters; neutralize
    the workflow-command marker; limit the length."""
    text = "".join(ch for ch in str(value) if unicodedata.category(ch) not in _DROP)
    text = text.replace("::", ": :")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def span(value, limit=80):
    """A value for a Markdown code span: sanitized, with characters that could end the span,
    start HTML, or break a table replaced."""
    return re.sub(r"[`<>&|\\]", "'", clean(value, limit))
