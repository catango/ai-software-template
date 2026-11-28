"""`.claude/settings.json` after a sync: the tag's file plus the entries of the project's seeded `.claude/deny.platform.txt` that it lacks
(design section 4, step 3, and the decision Q16): a function of the core list and the seeded list, so no override entry is needed."""
import json
import re

# the form of a deny rule, as scripts/initialize-project checks it before it appends an entry
DENY_ENTRY = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\([^\n]+\))?")


def entries_of(deny_platform_text):
    """The entries of a deny list file: one per line, blank lines and `#` comments skipped; lines are split with `str.splitlines()`,
    as initialize-project splits them. ValueError for a line that is not a deny rule."""
    entries = []
    for line in deny_platform_text.splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            if not DENY_ENTRY.fullmatch(entry):
                raise ValueError("an entry of .claude/deny.platform.txt is not a deny rule")
            entries.append(entry)
    return list(dict.fromkeys(entries))


def regenerate(tag_settings, deny_platform_text):
    """The bytes of settings.json: those of the tag, or, when the seeded list has entries the tag's list lacks, the tag's file with
    them appended to `permissions.deny` (in file order; the format of initialize-project). ValueError when it cannot be done."""
    entries = entries_of(deny_platform_text)
    if not entries:
        return tag_settings
    try:
        settings = json.loads(tag_settings.decode("utf-8"))
        deny = settings["permissions"]["deny"]
        if not isinstance(deny, list) or not all(isinstance(item, str) for item in deny):
            raise TypeError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("the tag's .claude/settings.json has no permissions.deny list to add the platform entries to") from None
    missing = [entry for entry in entries if entry not in deny]
    if not missing:
        return tag_settings
    deny.extend(missing)
    return (json.dumps(settings, indent=2) + "\n").encode()
