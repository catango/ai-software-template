"""TC-1 (template-sync PRs) and TC-2 (init PRs): which paths such a PR may touch, by the file classes of the manifest.

The classes come from `.template/manifest.toml`. The BASE manifest says what a path was before the PR; for the paths a
template-sync PR adds, the HEAD manifest says what the new template version makes of them (the version being applied is the
only place that knows its new files). A sync PR may add a new `owned` path, or a new `seeded` path that is absent from the base
commit and listed in the body under "Changed files by class". Nothing else is read from the head except `.template/state.toml`,
whose version must equal the branch version. A manifest or state file that cannot be read or parsed is a violation, never a pass.

An init PR may append entries to `permissions.deny` of the owned `.claude/settings.json`, if the head's `.claude/deny.platform.txt`
lists every one and nothing else in the file changes (decision of the approver, 2026-11-27). That is the only read of the head
by TC-2, and it happens only when the PR modifies that file.
"""
import json
import tomllib
import unicodedata

import template_manifest

from . import rules
from .sanitize import clean

MANIFEST = ".template/manifest.toml"
STATE = ".template/state.toml"
OVERRIDES = ".template/overrides.toml"
SETTINGS = ".claude/settings.json"
DENY_LIST = ".claude/deny.platform.txt"
CLASS_SECTION = "Changed files by class"
REMOVING = ("removed",)
WRITING = ("added", "modified", "changed", "copied")


def _load_manifest(client, sha):
    try:
        text = client.file_text(sha, MANIFEST)
        if text is None:
            return None
        return template_manifest.parse_manifest(text)
    except template_manifest.ManifestError:
        return None


def _only(manifest, path, wanted):
    return manifest.classify(path) == [wanted]


def _entries(files):
    """(path, how) for every path a PR touches: how is 'removed', 'written', or 'unknown' (a status this check does not know).
    A rename removes the old path and writes the new one."""
    for item in files:
        status = item.status
        if status == "renamed" and item.previous:
            yield item.previous, "removed"
            yield item.path, "written"
        elif status in REMOVING:
            yield item.path, "removed"
        elif status in WRITING:
            yield item.path, "written"
        else:
            yield item.path, "unknown"


def check_init(pr, files, client):
    """TC-2: an init PR deletes only template-only paths and writes only seeded files (the configuration file and the state file
    are seeded); it fails when the base branch already has `.template/state.toml`."""
    out = []
    if client.file_text(pr.base_sha, STATE) is not None:
        out.append(rules.Violation("TC-2", "the base branch already has .template/state.toml"))
    manifest = _load_manifest(client, pr.base_sha)
    if manifest is None:
        return out + [rules.Violation("TC-2", "the manifest of the base commit is missing or invalid")]
    bad, settings_changed = [], False
    for path, how in _entries(files):
        if how == "removed" and not _only(manifest, path, "template-only"):
            bad.append(f"deleted path is not template-only: {clean(path)}")
        elif how == "written" and path == SETTINGS and _status_of(files, path) in ("modified", "changed") \
                and _only(manifest, path, "owned"):
            settings_changed = True  # the one exception; judged below
        elif how == "written" and not _only(manifest, path, "seeded"):
            bad.append(f"written path is not seeded: {clean(path)}")
        elif how == "unknown":
            bad.append(f"change of unknown kind: {clean(path)}")
    out.extend(rules.Violation("TC-2", message) for message in bad[:rules.PATH_CAP])
    if len(bad) > rules.PATH_CAP:
        out.append(rules.Violation("TC-2", f"and {len(bad) - rules.PATH_CAP} more paths"))
    if settings_changed:
        out.extend(_check_settings(pr, client))
    return out


def _no_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _refuse_constant(name):
    raise ValueError(name)


def _settings(client, sha):
    """(the parsed file, its permissions.deny list) or (None, reason). Strict: valid JSON without duplicate keys or NaN, an object,
    and `permissions.deny` a list of strings."""
    text = client.file_text(sha, SETTINGS)
    if text is None:
        return None, "is missing"
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        return None, "is not valid JSON"
    permissions = data.get("permissions") if isinstance(data, dict) else None
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    if not isinstance(deny, list) or not all(isinstance(entry, str) for entry in deny):
        return None, "has no permissions.deny list of strings"
    return data, deny


def _without_deny(data):
    rest = {key: value for key, value in data.items() if key != "permissions"}
    rest["permissions"] = {key: value for key, value in data["permissions"].items() if key != "deny"}
    return json.dumps(rest, sort_keys=True)  # JSON text: True, 1 and 1.0 stay different


def _listed_entries(text):
    """The entries of a deny list file: one per line, blank lines and `#` comments skipped. Lines are split with `str.splitlines()`,
    exactly as `merge_platform_deny` of `scripts/initialize-project` splits them (it also breaks at the control characters 0B, 0C,
    1C to 1E, 85 and at U+2028 and U+2029), so every line it appends to the settings file is a line here."""
    return {line.strip() for line in (text or "").splitlines() if line.strip() and not line.strip().startswith("#")}


def _check_settings(pr, client):
    """TC-2 exception: `.claude/settings.json` may differ from the base only by entries appended to `permissions.deny`, each listed
    in the head's `.claude/deny.platform.txt`. Every other key and every existing entry, in order, stays; no duplicate is added."""
    messages = []
    base, base_deny = _settings(client, pr.base_sha)
    head, head_deny = _settings(client, pr.head_sha)
    if base is None:
        messages.append(f"{SETTINGS} of the base commit {base_deny}")
    if head is None:
        messages.append(f"{SETTINGS} at the head {head_deny}")
    if base is None or head is None:
        return [rules.Violation("TC-2", message) for message in messages]
    if _without_deny(base) != _without_deny(head):
        messages.append(f"{SETTINGS} changes something other than appended permissions.deny entries")
    if head_deny[:len(base_deny)] != base_deny:
        messages.append("an existing permissions.deny entry is removed, changed, or reordered")
        appended = []
    else:
        appended = head_deny[len(base_deny):]
    listed = _listed_entries(client.file_text(pr.head_sha, DENY_LIST))
    seen = set(base_deny)
    entries = []
    for entry in appended:
        if entry in seen:
            entries.append(f"appended deny entry duplicates another entry: {clean(entry)}")
        elif entry not in listed:
            entries.append(f"appended deny entry is not listed in {DENY_LIST}: {clean(entry)}")
        seen.add(entry)
    messages.extend(entries[:rules.PATH_CAP])
    out = [rules.Violation("TC-2", message) for message in messages]
    if len(entries) > rules.PATH_CAP:
        out.append(rules.Violation("TC-2", f"and {len(entries) - rules.PATH_CAP} more entries"))
    return out


def _state_version(client, sha):
    try:
        text = client.file_text(sha, STATE)
        if text is None:
            return None
        version = tomllib.loads(text).get("template", {}).get("version")
    except (tomllib.TOMLDecodeError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def _continues_a_name(char):
    """A letter, digit, or combining mark of any script, or an underscore: it would make a longer name of a path next to it."""
    return char.isalnum() or char == "_" or unicodedata.category(char)[0] == "M"


def _listed(section, path):
    """Whether the path appears in the text of a section as a whole token: not as part of a longer path or name. The characters next
    to it decide: a path character (`/`, `.`, `-`, `_`), or a letter, digit or combining mark of any script, makes it part of
    something else; a space or punctuation of any script does not. A `.` after it ends a sentence unless a name character follows."""
    start = 0
    while True:
        at = section.find(path, start)
        if at < 0:
            return False
        before = section[at - 1] if at else ""
        after = section[at + len(path):at + len(path) + 2]
        glued_before = bool(before) and (_continues_a_name(before) or before in "./-")
        glued_after = bool(after) and (_continues_a_name(after[0]) or after[0] in "/-"
                                       or (after[0] == "." and len(after) > 1 and _continues_a_name(after[1])))
        if not glued_before and not glued_after:
            return True
        start = at + 1


def check_template_sync(pr, files, client, parsed=None):
    """TC-1: a template-sync PR changes or removes only paths that are owned in the base manifest, adds only paths that are new
    in the head manifest and owned or seeded there, updates `.template/state.toml` with a version equal to the branch version,
    and never touches `.template/overrides.toml`. A seeded path is only added: it must be absent from the base commit and listed
    in the body under "Changed files by class" (`parsed` is the parsed body, None when it could not be read)."""
    base = _load_manifest(client, pr.base_sha)
    head = _load_manifest(client, pr.head_sha)
    out = []
    if base is None:
        out.append(rules.Violation("TC-1", "the manifest of the base commit is missing or invalid"))
    if head is None:
        out.append(rules.Violation("TC-1", "the manifest of the head commit is missing or invalid"))
    bad, state_written = [], False
    for item in files:
        for path in (item.path, item.previous):
            if path == OVERRIDES:
                bad.append(f"{OVERRIDES} is changed")
    for path, how in _entries(files):
        if path == OVERRIDES:
            continue
        if path == STATE:
            state_written = state_written or how == "written"
            if how != "written" or _status_of(files, path) == "added":
                bad.append(f"{STATE} is not an update")
            continue
        if base is None or head is None:
            continue
        status = _status_of(files, path)
        if how == "unknown":
            bad.append(f"change of unknown kind: {clean(path)}")
        elif how == "removed" or (how == "written" and status in ("modified", "changed")):
            if not _only(base, path, "owned"):
                bad.append(f"path is not owned in the base manifest: {clean(path)}")
        else:  # added, copied, or the new name of a rename: judged by the class of the head manifest
            classes = head.classify(path)
            if len(classes) > 1:
                bad.append(f"added path has more than one class in the head manifest: {clean(path)}")
            elif classes == ["seeded"]:
                bad.extend(_seeded_addition(path, parsed, pr, client))
            elif classes != ["owned"]:
                bad.append(f"added path is neither owned nor seeded in the head manifest: {clean(path)}")
    if not state_written:
        bad.append(f"{STATE} is not updated")
    out.extend(rules.Violation("TC-1", message) for message in bad[:rules.PATH_CAP])
    if len(bad) > rules.PATH_CAP:
        out.append(rules.Violation("TC-1", f"and {len(bad) - rules.PATH_CAP} more findings"))
    version = rules.branch_version(pr.head_ref)
    if state_written and _state_version(client, pr.head_sha) != version:
        out.append(rules.Violation("TC-1", f"the version in {STATE} does not equal the branch version"))
    return out


def _seeded_addition(path, parsed, pr, client):
    if client.file_text(pr.base_sha, path) is not None:
        return [f"added seeded path exists on the base commit: {clean(path)}"]
    section = parsed.sections.get(CLASS_SECTION, "") if parsed is not None else ""
    if not _listed(section, path):
        return [f"added seeded path is not listed under '{CLASS_SECTION}': {clean(path)}"]
    return []


def _status_of(files, path):
    for item in files:
        if item.path == path:
            return item.status
    return ""
