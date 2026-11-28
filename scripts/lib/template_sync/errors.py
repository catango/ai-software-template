"""Exit codes of scripts/template-sync: 0 done, 1 refused (a rule says stop), 2 usage, 3 error (the template source could not be read, or a
write failed). A message is fixed text plus values that were checked or sanitized; no text of the template or of git is repeated."""


class Refused(Exception):
    """A rule says stop. Nothing was changed."""


class UsageError(Exception):
    """The command line is wrong."""


class SourceError(Exception):
    """The template source could not be read, or a step failed for a reason that is not a rule."""
