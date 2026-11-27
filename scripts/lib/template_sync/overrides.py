"""`.template/overrides.toml`: the divergences of a project from owned files, one `[[override]]` table each with exactly `path`, `reason`, and
`pr`. The file belongs to the project: template-sync reads it and never writes it. Other tables of the file (`[[budget]]` raises, read by
the gate check size-budget) are left to their readers. The reader is shared by template-sync and the gate check owned-drift."""
import re
import tomllib
import unicodedata
from collections import namedtuple

from . import fsops

BUDGETS = ".template/budgets.toml"
MAX_REASON = 300
MAX_ENTRIES = 500
# a pull request reference of the project: #12 or owner/repo#12
PR = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100})?#[1-9][0-9]{0,8}")

Entry = namedtuple("Entry", "reason pr")


class OverridesError(ValueError):
    """The overrides file is malformed. The message names the entry."""


def parse(text):
    """{path: Entry} in file order."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise OverridesError("not valid TOML") from None
    raw = data.get("override", [])
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise OverridesError("override must be an array of tables ([[override]])")
    if len(raw) > MAX_ENTRIES:
        raise OverridesError("too many entries")
    found = {}
    for number, entry in enumerate(raw, start=1):
        label = f"entry {number}"
        if set(entry) != {"path", "reason", "pr"}:
            raise OverridesError(f"{label}: needs exactly path, reason, and pr")
        path, reason, pr = entry["path"], entry["reason"], entry["pr"]
        try:
            fsops.check_path(path)
        except ValueError:
            raise OverridesError(f"{label}: path must be a relative path with '/' separators inside the repository") from None
        if path == BUDGETS:
            raise OverridesError(f"{label}: {BUDGETS} cannot be overridden as a whole file; raise single values with [[budget]] entries")
        if (not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON
                or any(unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"} for ch in reason)):
            raise OverridesError(f"{label}: reason must be a non-empty string of one line, at most {MAX_REASON} characters")
        if not isinstance(pr, str) or not PR.fullmatch(pr):
            raise OverridesError(f"{label}: pr must be a reference like #12 or owner/repo#12")
        if path in found:
            raise OverridesError(f"{label}: path is listed twice")
        found[path] = Entry(" ".join(reason.split()), pr)
    return found
