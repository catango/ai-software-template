"""Read `.template/manifest.toml` and classify paths (standard library only).

A path has one class: `owned`, `seeded`, or `template-only`. The manifest lists patterns
per class; the pattern syntax is described at the top of the manifest.
"""

import os
import re
import tomllib
from dataclasses import dataclass, field

CLASSES = ("owned", "seeded", "template-only")
SCHEMA_VERSION = 1
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


class ManifestError(ValueError):
    """The manifest is malformed."""


def compile_pattern(pattern):
    """Compile a manifest pattern into a regular expression for `fullmatch`."""
    if not isinstance(pattern, str) or not pattern:
        raise ManifestError("pattern must be a non-empty string")
    if "\\" in pattern or pattern.startswith("/") or "//" in pattern:
        raise ManifestError(f"pattern {pattern!r} must be a relative path with '/' separators")
    prefix = pattern.endswith("/")
    segments = pattern[:-1].split("/") if prefix else pattern.split("/")
    for seg in segments:
        if seg in ("", ".", ".."):
            raise ManifestError(f"pattern {pattern!r} has an empty, '.', or '..' segment")
        if "**" in seg and seg != "**":
            raise ManifestError(f"pattern {pattern!r}: '**' must be a whole segment")
    if prefix and segments[-1] == "**":
        raise ManifestError(f"pattern {pattern!r}: a directory pattern cannot end in '**'")

    regex = ""
    last = len(segments) - 1
    for index, seg in enumerate(segments):
        if seg == "**":
            regex += ".+" if index == last else "(?:.+/)?"
            continue
        for char in seg:
            if char == "*":
                regex += "[^/]*"
            elif char == "?":
                regex += "[^/]"
            else:
                regex += re.escape(char)
        if index != last:
            regex += "/"
    if prefix:
        regex += "/.+"
    return re.compile(regex)


@dataclass
class Manifest:
    version: str
    classes: dict
    _compiled: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._compiled = {
            cls: [compile_pattern(p) for p in patterns] for cls, patterns in self.classes.items()
        }

    def classify(self, path):
        """Classes whose patterns match `path`, in the order of CLASSES, each once."""
        return [
            cls for cls in CLASSES
            if any(regex.fullmatch(path) for regex in self._compiled[cls])
        ]


def parse_manifest(text):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"not valid TOML: {exc}") from exc
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"schema_version must be {SCHEMA_VERSION}")
    version = data.get("version")
    if not isinstance(version, str) or not _VERSION.match(version):
        raise ManifestError("version must be a string like 1.2.3")
    unknown_top = set(data) - {"schema_version", "version", "classes"}
    if unknown_top:
        raise ManifestError(f"unknown keys: {sorted(unknown_top)}")
    classes = data.get("classes")
    if not isinstance(classes, dict):
        raise ManifestError("[classes] is missing")
    if set(classes) != set(CLASSES):
        raise ManifestError(f"[classes] must have exactly {list(CLASSES)}, found {sorted(classes)}")
    for cls, patterns in classes.items():
        if not isinstance(patterns, list):
            raise ManifestError(f"classes.{cls} must be a list")
        for pattern in patterns:
            try:
                compile_pattern(pattern)
            except ManifestError as exc:
                raise ManifestError(f"classes.{cls}: {exc}") from exc
    return Manifest(version=version, classes={c: list(classes[c]) for c in CLASSES})


def load_manifest(path):
    with open(path, encoding="utf-8") as handle:
        return parse_manifest(handle.read())


def check_paths(manifest, paths):
    """Report paths without a class and paths with more than one.

    Returns {"unclassed": [path, ...], "multiple": [(path, [class, ...]), ...]}.
    """
    unclassed, multiple = [], []
    for path in paths:
        matched = manifest.classify(path)
        if not matched:
            unclassed.append(path)
        elif len(matched) > 1:
            multiple.append((path, matched))
    return {"unclassed": unclassed, "multiple": multiple}


def walk_files(root):
    """Sorted repository-relative paths of every file below root, without `.git`."""
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            full = os.path.join(base, name)
            found.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return sorted(found)
