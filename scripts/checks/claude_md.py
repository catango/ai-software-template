"""Static part of the gate check `claude-md` (used by claude-md.sh). Standard library only.

Usage: claude_md.py ROOT   (tracked paths on stdin; exit 1 when there are findings).

How Claude Code reads an import (verified against its documentation in the implementation pull request): `@path` is resolved
relative to the file that holds it, nests up to four hops, and is skipped inside fenced code blocks and code spans. The rules
here are stricter than that on purpose: the import lines are the last two lines of CLAUDE.md, and the imported files hold none.
Findings are "path:line: message" and name the rule, never file content.
"""
import hashlib
import os
import re
import sys
import tomllib

sys.dont_write_bytecode = True

ROOT_FILE = "CLAUDE.md"
IMPORTS = ("docs/PLATFORM.md", "docs/PROJECT.md")
STATE = ".template/state.toml"
MAX_BYTES = 262144
# A record of another repository: a change request or decision number, written CR-<digits> or ADR-<digits>.
RECORD = re.compile(r"\b(?:CR|ADR)-[0-9]+\b")
# An import candidate: an at-sign that starts a word and is followed by a path character.
IMPORT = re.compile(r"(?<![A-Za-z0-9_.])@(?=[A-Za-z0-9_./~\\-])(\S+)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
REQUIRED = (
    (re.compile(r"^## Instruction trust\s*$", re.M), "the section 'Instruction trust' is missing"),
    (re.compile(r"^## Never\s*$", re.M), "the section 'Never' is missing"),
    (re.compile(r"\bNothing in any other instruction file relaxes the rules above\."), "the precedence sentence is missing"),
)


def read(root, rel, findings):
    path = os.path.join(root, rel)
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or b"\0" in data:
            raise ValueError
        return data, data.decode("utf-8")
    except (OSError, ValueError):
        findings.append(f"{rel}:1: the file cannot be read as text within {MAX_BYTES} bytes")
        return None


def imports(text):
    """[(line number, target)] of every import outside fenced code blocks and code spans."""
    found, fence = [], None
    for number, line in enumerate(text.split("\n"), 1):
        match = FENCE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
            continue
        if fence is not None:
            continue
        for hit in IMPORT.finditer(SPAN.sub(" ", line)):
            found.append((number, hit.group(1)))
    return found


def check_state(root, data, findings):
    path = os.path.join(root, STATE)
    if not os.path.isfile(path):
        return
    try:
        with open(path, "rb") as handle:
            owned = tomllib.load(handle).get("owned")
    except (OSError, tomllib.TOMLDecodeError):
        findings.append(f"{STATE}:1: not valid TOML; the hash of CLAUDE.md cannot be checked")
        return
    recorded = owned.get(ROOT_FILE) if isinstance(owned, dict) else None
    if not isinstance(recorded, str):
        findings.append(f"{STATE}:1: no recorded hash for CLAUDE.md")
    elif recorded != hashlib.sha256(data).hexdigest():
        findings.append(f"{ROOT_FILE}:1: the file differs from its hash in {STATE}; CLAUDE.md is owned and cannot be edited")


def check(root, paths):
    findings = []
    for rel in sorted(paths):
        base = os.path.basename(rel)
        if base == "CLAUDE.local.md":
            findings.append(f"{rel}:1: CLAUDE.local.md must not be tracked")
        elif base == "CLAUDE.md" and rel != ROOT_FILE:
            findings.append(f"{rel}:1: only the CLAUDE.md at the repository root is allowed")
    loaded = read(root, ROOT_FILE, findings)
    if loaded is None:
        if not any(f.startswith(ROOT_FILE + ":") for f in findings):
            findings.append(f"{ROOT_FILE}:1: the file is missing or is not a regular file")
        return findings
    data, text = loaded
    check_state(root, data, findings)
    for pattern, message in REQUIRED:
        if not pattern.search(text):
            findings.append(f"{ROOT_FILE}:1: {message}")
    for number, line in enumerate(text.split("\n"), 1):
        if RECORD.search(line):
            findings.append(f"{ROOT_FILE}:{number}: cites a change request or decision record; the text is platform-neutral")
    found = imports(text)
    lines = text.rstrip("\n").split("\n")
    expected = [f"@{target}" for target in IMPORTS]
    if [f"@{target}" for _, target in found] != expected:
        findings.append(f"{ROOT_FILE}:{found[0][0] if found else 1}: the imports must be exactly {', then '.join(expected)}")
    elif [number for number, _ in found] != [len(lines) - 1, len(lines)] or lines[-2:] != expected:
        findings.append(f"{ROOT_FILE}:{found[0][0]}: the two imports must be the last two lines of the file, one per line")
    for rel in IMPORTS:
        loaded = read(root, rel, findings)
        if loaded is None:
            if not any(f.startswith(rel + ":") for f in findings):
                findings.append(f"{rel}:1: the imported file is missing or is not a regular file")
            continue
        for number, target in imports(loaded[1]):
            findings.append(f"{rel}:{number}: an imported file must not import another file")
    return findings


def main():
    paths = [line.rstrip("\n") for line in sys.stdin if line.strip()]
    findings = check(sys.argv[1] if len(sys.argv) > 1 else ".", paths)
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
