"""Tags and versions: `v<MAJOR>.<MINOR>.<PATCH>` and `<MAJOR>.<MINOR>.<PATCH>`, numbers without leading zeros, compared as numbers."""
import re

_NUMBER = r"(0|[1-9][0-9]{0,8})"
_TAG = re.compile(rf"v{_NUMBER}\.{_NUMBER}\.{_NUMBER}")
_VERSION = re.compile(rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}")


def parse_tag(text):
    """(major, minor, patch) of a tag name; ValueError for anything else (a branch, a commit, `1.2.3`, a suffix, a line break)."""
    match = _TAG.fullmatch(text) if isinstance(text, str) else None
    if not match:
        raise ValueError("not a tag name of the form v<MAJOR>.<MINOR>.<PATCH>")
    return tuple(int(part) for part in match.groups())


def parse_version(text):
    match = _VERSION.fullmatch(text) if isinstance(text, str) else None
    if not match:
        raise ValueError("not a version of the form <MAJOR>.<MINOR>.<PATCH>")
    return tuple(int(part) for part in match.groups())


def format_tag(version):
    return "v" + format_version(version)


def format_version(version):
    return ".".join(str(part) for part in version)


def lag_line(recorded, tags):
    """The template lag in plain words, as the first line of `template-sync --check`. `recorded` and `tags` are version tuples."""
    have = format_tag(recorded)
    if not tags:
        return f"no release tag found in the template (recorded {have})"
    newest = max(tags)
    newer = {tag for tag in tags if tag > recorded}
    if newer:
        count = len(newer)
        return f"behind by {count} release{'' if count == 1 else 's'} (recorded {have}, newest {format_tag(newest)})"
    if recorded == newest:
        return f"up to date at {have}"
    return f"up to date at {have} (the newest tag of the template is {format_tag(newest)})"
