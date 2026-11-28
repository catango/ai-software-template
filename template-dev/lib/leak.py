"""Scan files for identifier patterns and platform references (the `leak` test).

Findings carry the pattern name and the location only, never the matched text, so a
failing run does not copy an identifier into a log.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

GROUPS = ("identifier", "platform", "arch", "placeholder")
SCOPES = ("both", "generated")

# Files that carry configuration data by design and may therefore hold architecture names and runner labels: the configuration,
# and the CI manifest, which declares the architectures it has artifacts for (the install engine itself names none).
ARCH_DATA_FILES = frozenset({"config/project.yaml", "toolchain/ci-manifest.toml"})

# The template's own seeded configuration lists the repository that holds its design records under `design_repos` (rule PC-3
# needs it to accept the template's own implementation PRs). That one pattern is skipped for that one file, in the payload scan
# only: a generated tree is written from the configuration it was given and gets no exemption.
PAYLOAD_NAME_EXEMPT = {"config/project.yaml": frozenset({"platform-repository-name"})}


class PatternError(ValueError):
    """The pattern file is malformed."""


@dataclass(frozen=True)
class Pattern:
    name: str
    group: str
    scope: str
    regex: re.Pattern


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    pattern: str

    def __str__(self):
        return f"{self.path}:{self.line}: {self.pattern}"


def load_patterns(path):
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PatternError(f"not valid TOML: {exc}") from exc
    entries = data.get("pattern")
    if not isinstance(entries, list) or not entries:
        raise PatternError("no [[pattern]] entries")
    patterns, seen = [], set()
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str) or not name or name in seen:
            raise PatternError(f"pattern name missing or duplicated: {name!r}")
        seen.add(name)
        if entry.get("group") not in GROUPS:
            raise PatternError(f"{name}: group must be one of {GROUPS}")
        if entry.get("scope") not in SCOPES:
            raise PatternError(f"{name}: scope must be one of {SCOPES}")
        flags = re.IGNORECASE if entry.get("ci") is True else 0
        try:
            regex = re.compile(entry.get("regex", ""), flags)
        except re.error as exc:
            raise PatternError(f"{name}: invalid regex: {exc}") from exc
        patterns.append(Pattern(name, entry["group"], entry["scope"], regex))
    return patterns


def scan(root, paths, patterns, scope, arch_exempt=frozenset(), name_exempt=None):
    """Scan `paths` (relative to `root`).

    scope "both" applies only the patterns of scope "both" (a payload scan); scope
    "generated" applies every pattern (a generated-tree scan). Files in `arch_exempt` are
    skipped for the group "arch" only: they carry configuration data by design. `name_exempt` maps a path to the pattern names
    that are skipped for that file alone (the seeded configuration of the template itself names the repository of its design
    records); every other pattern still applies to it.
    """
    name_exempt = name_exempt or {}
    active = [p for p in patterns if scope == "generated" or p.scope == "both"]
    findings = []
    for rel in paths:
        if not (Path(root) / rel).is_file():
            continue
        raw = (Path(root) / rel).read_bytes()
        if b"\0" in raw:
            continue
        lines = raw.decode("utf-8", errors="replace").splitlines()
        for number, line in enumerate(lines, start=1):
            for pattern in active:
                if pattern.group == "arch" and rel in arch_exempt:
                    continue
                if pattern.name in name_exempt.get(rel, ()):
                    continue
                if pattern.regex.search(line):
                    findings.append(Finding(rel, number, pattern.name))
    return findings
