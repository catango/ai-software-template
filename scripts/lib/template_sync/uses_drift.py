"""The reading of `uses:` drift. THE ONE PLACE TO SWITCH IT.

Design section 8 ("my reading, to confirm", section 14, plan row 8): a change limited to `uses:` lines in an owned workflow (a Dependabot
pin bump) is allowed by the gate check `owned-drift`, consistent with `template-sync` overwriting and listing such drift.

    OWNED_DRIFT_ALLOWS_USES_ONLY = True    the design's reading: owned-drift lets such a change pass without an override entry
    OWNED_DRIFT_ALLOWS_USES_ONLY = False   the other reading: owned-drift treats a pin bump like any other change to an owned file and
                                           wants an entry in .template/overrides.toml (until the next sync overwrites the file)

`template-sync` does not read the constant: it overwrites such drift either way and lists it under "Drift limited to uses lines".

Where the design is silent the strictest sound reading applies (`SAME_ACTION_ONLY`): a `uses:` line may change only its pin and its
trailing comment, never the action it names, its indent, or its `- ` marker; a local action (no `@`) has no pin to bump; the number of
lines, every other line, and the line endings stay the same.
"""
import re

OWNED_DRIFT_ALLOWS_USES_ONLY = True
SAME_ACTION_ONLY = True

_WORKFLOW = re.compile(r"\.github/workflows/[^/]+\.ya?ml")
# `[ \t]` and not `\s` after the pin: a line ending that differs is a difference of its own.
_USES = re.compile(r"(?P<lead>[ \t]*(?:-[ \t]+)?)uses:[ \t]+(?P<ref>[^\s#]+)(?P<tail>[ \t]+#[^\n]*)?[ \t]*")


def applies_to(path):
    """Only a workflow file has `uses:` lines to bump."""
    return _WORKFLOW.fullmatch(path) is not None


def is_uses_only(reference, changed):
    """True when `changed` differs from `reference` and every difference is a `uses:` line of the same action."""
    if reference == changed:
        return False
    old, new = reference.split("\n"), changed.split("\n")
    if len(old) != len(new):
        return False
    for before, after in zip(old, new):
        if before == after:
            continue
        a, b = _USES.fullmatch(before), _USES.fullmatch(after)
        if not a or not b or a.group("lead") != b.group("lead"):
            return False
        if "@" not in a.group("ref") or "@" not in b.group("ref"):
            return False
        if SAME_ACTION_ONLY and a.group("ref").rsplit("@", 1)[0] != b.group("ref").rsplit("@", 1)[0]:
            return False
    return True
