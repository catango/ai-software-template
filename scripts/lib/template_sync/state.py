"""`.template/state.toml`: the template version and tag commit last synced, the SHA-256 of every owned file as the template ships it, and
the entries recorded as `override`. Written by scripts/initialize-project and by template-sync only (the format is the one of
`toml_state` in initialize-project; a test compares them). The reader is strict: anything it does not know is an error."""
import json
import re
import tomllib
from collections import namedtuple

from . import fsops, versions

SCHEMA_VERSION = 1
MAX_OWNED = 5000
SOURCE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}")  # `template.source` of the configuration schema
_COMMIT = re.compile(r"[0-9a-f]{40}")
_HASH = re.compile(r"[0-9a-f]{64}")
OVERRIDE = "override"
HEADER = "# Written by scripts/initialize-project and by template-sync only. Do not edit by hand.\n"

State = namedtuple("State", "source version commit owned overrides")


class StateError(ValueError):
    """The state file is not what initialize-project or template-sync write."""


def _table(data, key, required=True):
    value = data.get(key, {} if not required else None)
    if not isinstance(value, dict):
        raise StateError(f"[{key}] is missing or is not a table")
    return value


def parse(text):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise StateError("not valid TOML") from None
    if set(data) - {"schema_version", "template", "owned", "overrides"}:
        raise StateError("unknown top-level key")
    if data.get("schema_version") != SCHEMA_VERSION or isinstance(data.get("schema_version"), bool):
        raise StateError(f"schema_version must be {SCHEMA_VERSION}")
    template = _table(data, "template")
    if set(template) - {"source", "version", "commit"}:
        raise StateError("unknown key in [template]")
    source, version, commit = template.get("source"), template.get("version"), template.get("commit")
    if not isinstance(source, str) or not SOURCE.fullmatch(source):
        raise StateError("template.source must be owner/name")
    try:
        versions.parse_version(version)
    except ValueError:
        raise StateError("template.version must be MAJOR.MINOR.PATCH") from None
    if commit is not None and (not isinstance(commit, str) or not _COMMIT.fullmatch(commit)):
        raise StateError("template.commit must be 40 lowercase hex digits")
    owned = _table(data, "owned")
    if len(owned) > MAX_OWNED:
        raise StateError("too many owned files")
    for path, digest in owned.items():
        try:
            fsops.check_path(path)
        except ValueError:
            raise StateError("a path in [owned] is not a relative path") from None
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise StateError("a hash in [owned] is not a lowercase SHA-256")
    marked = _table(data, "overrides", required=False)
    for path, value in marked.items():
        try:
            fsops.check_path(path)
        except ValueError:
            raise StateError("a path in [overrides] is not a relative path") from None
        if value != OVERRIDE:
            raise StateError(f"a value in [overrides] must be \"{OVERRIDE}\"")
    return State(source, version, commit, dict(owned), dict(marked))


def render(source, version, commit, owned, overrides):
    out = [HEADER, f"schema_version = {SCHEMA_VERSION}\n\n", "[template]\n", f"source = {json.dumps(source)}\n", f"version = {json.dumps(version)}\n"]
    if commit:
        out.append(f"commit = {json.dumps(commit)}\n")
    out.append("\n[owned]\n")
    for path in sorted(owned):
        out.append(f"{json.dumps(path)} = {json.dumps(owned[path])}\n")
    out.append("\n[overrides]\n")
    for path in sorted(overrides):
        out.append(f"{json.dumps(path)} = {json.dumps(overrides[path])}\n")
    return "".join(out)
