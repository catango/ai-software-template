"""The plan of a sync: what each path of the tag does to the project, decided before anything is written.

For every path of the tag that its own manifest classes `owned`, the project's file is overwritten (also when it was changed locally
and no entry of `.template/overrides.toml` names it), added, or kept as it is because an entry names it. A path recorded in `state.toml`
that the tag no longer ships as owned is removed. A `seeded` file is never changed or removed; a seeded path that is new to the project
and absent from the tree is added once. "New" means that the project's own manifest has no seeded pattern for the path, or lists it
by its exact name and the template commit recorded in `state.toml` did not have the file (a manifest names a file before a release
ships it). `.github/CODEOWNERS` is written from the `approver_login` of the project's own `config/project.yaml`, never from the tag's
file, which names the template's approver. Template-only paths never arrive, and neither does
`.template/state.toml` or `.template/overrides.toml` from the tag. A change that rule TC-1 would reject is refused, not made.
"""
import hashlib
import os
import re
import stat
from collections import namedtuple
from pathlib import Path

from cr_tools.identity import read_identities
from pr_contract.errors import ConfigError

from . import fsops, settings, state as state_module, uses_drift
from .errors import Refused

STATE = ".template/state.toml"
OVERRIDES = ".template/overrides.toml"
SETTINGS = ".claude/settings.json"
DENY_LIST = ".claude/deny.platform.txt"
CODEOWNERS = ".github/CODEOWNERS"
PROJECT_CONFIG = "config/project.yaml"
# The login form of the gate check `codeowners` (it also ends in a letter or digit).
CODEOWNERS_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
WORKFLOW_PREFIXES = (".github/workflows/", ".github/actions/")
REGULAR = ("100644", "100755")
CAP = 10

Plan = namedtuple("Plan", "changes writes removals honoured unused notes new_state")
Change = namedtuple("Change", "path kind")

KINDS = ("added", "changed", "drift", "uses", "removed", "seeded")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _many(paths):
    shown = ", ".join(paths[:CAP])
    return shown + (f" and {len(paths) - CAP} more" if len(paths) > CAP else "")


def _local(tree, path):
    """(bytes, executable) of a regular file of the project, None when there is none; Refused for a link or a directory."""
    target = Path(tree) / path
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise Refused(f"{path} is a symbolic link or a directory in the project; the template ships a file there")
    return target.read_bytes(), bool(info.st_mode & 0o111)


def _codeowners(tree):
    """The bytes of the one CODEOWNERS line for the approver of the project's configuration, or None when it names no valid one."""
    local = _local(tree, PROJECT_CONFIG)
    if local is None:
        return None
    try:
        login = read_identities(local[0].decode("utf-8")).approver_login
    except (ConfigError, UnicodeDecodeError):
        return None
    if not CODEOWNERS_LOGIN.fullmatch(login):
        return None
    return f"* @{login}\n".encode()


def _is_new_seeded(path, local_manifest, base_paths):
    """A seeded path of the tag that the project lacks is added when its manifest has no seeded pattern for it, or lists it by
    its exact name while the recorded template commit (`base_paths`, None when unknown) did not have the file."""
    if local_manifest.classify(path) != ["seeded"]:
        return True
    return base_paths is not None and path in local_manifest.classes.get("seeded", ()) and path not in base_paths


def build(tree, state, entries, local_manifest, release, base_paths=None):
    """The Plan for syncing the project in `tree` (recorded `state`, `entries` of overrides.toml, `local_manifest` of the project) to
    `release` (a source.Release). `base_paths` is the set of paths of the template commit recorded in `state`, None when it is not
    known. Raises Refused."""
    target = release.manifest
    classes, bad_class, bad_type = {}, {"none": [], "many": []}, []
    for path, (mode, kind, _) in release.entries.items():
        try:
            fsops.check_path(path)
        except ValueError:
            raise Refused(f"the tag has a path that is not a plain relative path: {path.encode('ascii', 'replace').decode()[:60]}") from None
        found = target.classify(path)
        if not found:
            bad_class["none"].append(path)
        elif len(found) > 1:
            bad_class["many"].append(path)
        classes[path] = found
    if bad_class["none"]:
        raise Refused(f"no class in the manifest of the tag: {_many(sorted(bad_class['none']))}")
    if bad_class["many"]:
        raise Refused(f"more than one class in the manifest of the tag: {_many(sorted(bad_class['many']))}")
    for path, (mode, kind, _) in release.entries.items():
        if classes[path][0] in ("owned", "seeded") and mode not in REGULAR:
            what = "a symbolic link" if mode == "120000" else "a submodule" if mode == "160000" else "an entry that is not a regular file"
            raise Refused(f"the tag has {what} at {path}, which the manifest classes {classes[path][0]}")
    owned_target = sorted(p for p, c in classes.items() if c == ["owned"])
    seeded_target = sorted(p for p, c in classes.items() if c == ["seeded"])

    blobs = release.reader.read_many(owned_target + [p for p in seeded_target if p not in (STATE, OVERRIDES)])
    deny_platform = ""
    local_deny = _local(tree, DENY_LIST)
    if local_deny is not None:
        deny_platform = local_deny[0].decode("utf-8", "replace")
    if SETTINGS in blobs and SETTINGS not in entries:
        try:
            blobs[SETTINGS] = settings.regenerate(blobs[SETTINGS], deny_platform)
        except ValueError as error:
            raise Refused(str(error)) from None

    changes, writes, removals, collisions = [], {}, [], []
    new_owned, new_overrides, honoured, notes = {}, {}, [], []
    for path in owned_target:
        data = blobs[path]
        executable = release.entries[path][0] == "100755"
        digest = sha256(data)
        new_owned[path] = digest
        if path in entries:
            honoured.append(path)
            new_overrides[path] = state_module.OVERRIDE
            continue
        local = _local(tree, path)
        if local is None:
            changes.append(Change(path, "added"))
        else:
            if local_manifest.classify(path) != ["owned"]:
                collisions.append(path)
                continue
            if sha256(local[0]) == digest and local[1] == executable:
                continue
            if state.owned.get(path) == sha256(local[0]) or sha256(local[0]) == digest:
                kind = "changed"  # only the template changed it (or only its executable bit differs)
            elif uses_drift.applies_to(path) and _uses_only(local[0], data):
                kind = "uses"
            else:
                kind = "drift"
            changes.append(Change(path, kind))
        writes[path] = (data, executable)
    if collisions:
        raise Refused(f"{_many(collisions)}: the project has this file, its manifest does not class it owned, and the tag ships it as owned")

    for path in sorted(state.owned):
        if path in classes and classes[path] == ["owned"]:
            continue
        if local_manifest.classify(path) != ["owned"]:
            notes.append(f"{path} is recorded as owned but its manifest does not class it owned: left alone")
            continue
        if path in entries:
            honoured.append(path)
            new_overrides[path] = state_module.OVERRIDE
        elif classes.get(path) == ["seeded"]:
            notes.append(f"{path} is a seeded file in the tag: kept, no longer synced")
        elif _local(tree, path) is not None:
            changes.append(Change(path, "removed"))
            removals.append(path)

    for path in seeded_target:
        if path in (STATE, OVERRIDES) or _local(tree, path) is not None or not _is_new_seeded(path, local_manifest, base_paths):
            continue
        data = blobs[path]
        if path == CODEOWNERS:
            data = _codeowners(tree)
            if data is None:
                notes.append(f"{path} is new in the tag but was not added: {PROJECT_CONFIG} has no valid approver_login to write it from")
                continue
        writes[path] = (data, release.entries[path][0] == "100755")
        changes.append(Change(path, "seeded"))

    unused = sorted(path for path in entries if path not in honoured)
    new_text = state_module.render(state.source, _version_text(release), release.commit, new_owned, new_overrides)
    return Plan(sorted(changes, key=lambda c: (KINDS.index(c.kind), c.path)), writes, removals, sorted(honoured), unused, notes, new_text)


def _uses_only(local_bytes, target_bytes):
    try:
        return uses_drift.is_uses_only(local_bytes.decode("utf-8"), target_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        return False


def _version_text(release):
    return release.tag[1:]


def changed_paths(plan):
    return [change.path for change in plan.changes]


def workflow_paths(plan):
    return [p for p in changed_paths(plan) if p.startswith(WORKFLOW_PREFIXES)]
