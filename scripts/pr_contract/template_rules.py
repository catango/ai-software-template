"""TC-1 (template-sync PRs) and TC-2 (init PRs): which paths such a PR may touch, by the file classes of the manifest.

The classes come from `.template/manifest.toml`. The BASE manifest says what a path was before the PR; for the paths a
template-sync PR adds, the HEAD manifest says what the new template version makes of them (the version being applied is the
only place that knows its new files). Nothing else is read from the head except `.template/state.toml`, whose version
must equal the branch version. A manifest or state file that cannot be read or parsed is a violation, never a pass.
"""
import tomllib

import template_manifest

from . import rules
from .sanitize import clean

MANIFEST = ".template/manifest.toml"
STATE = ".template/state.toml"
OVERRIDES = ".template/overrides.toml"
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
    bad = []
    for path, how in _entries(files):
        if how == "removed" and not _only(manifest, path, "template-only"):
            bad.append(f"deleted path is not template-only: {clean(path)}")
        elif how == "written" and not _only(manifest, path, "seeded"):
            bad.append(f"written path is not seeded: {clean(path)}")
        elif how == "unknown":
            bad.append(f"change of unknown kind: {clean(path)}")
    out.extend(rules.Violation("TC-2", message) for message in bad[:rules.PATH_CAP])
    if len(bad) > rules.PATH_CAP:
        out.append(rules.Violation("TC-2", f"and {len(bad) - rules.PATH_CAP} more paths"))
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


def check_template_sync(pr, files, client):
    """TC-1: a template-sync PR changes or removes only paths that are owned in the base manifest, adds only paths that are new
    and owned in the head manifest, updates `.template/state.toml` with a version equal to the branch version, and never touches
    `.template/overrides.toml`."""
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
        elif not _only(head, path, "owned"):  # added, copied, or the new name of a rename
            bad.append(f"added path is not owned in the head manifest: {clean(path)}")
    if not state_written:
        bad.append(f"{STATE} is not updated")
    out.extend(rules.Violation("TC-1", message) for message in bad[:rules.PATH_CAP])
    if len(bad) > rules.PATH_CAP:
        out.append(rules.Violation("TC-1", f"and {len(bad) - rules.PATH_CAP} more findings"))
    version = rules.branch_version(pr.head_ref)
    if state_written and _state_version(client, pr.head_sha) != version:
        out.append(rules.Violation("TC-1", f"the version in {STATE} does not equal the branch version"))
    return out


def _status_of(files, path):
    for item in files:
        if item.path == path:
            return item.status
    return ""
