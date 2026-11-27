"""Static part of the gate check `deny-list` (used by deny-list.sh). Standard library only.

Usage: deny_list.py ROOT   (exit 1 when there are findings).

The deny syntax is the one of Claude Code's permission rules: `Tool` or `Tool(specifier)`, compared here as exact strings.
Findings are "path:line: message". A missing entry is named by its line number in the list that requires it, never by its text.
"""
import json
import os
import re
import sys

sys.dont_write_bytecode = True

SETTINGS = ".claude/settings.json"
LISTS = (".claude/required-deny.txt", ".claude/deny.platform.txt")
MAX_BYTES = 262144
ENTRY = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\([^\n]+\))?")


def read_text(root, rel, findings):
    path = os.path.join(root, rel)
    if os.path.islink(path) or not os.path.isfile(path):
        findings.append(f"{rel}:1: the file is missing or is not a regular file")
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError
        return data.decode("utf-8")
    except (OSError, ValueError):
        findings.append(f"{rel}:1: the file cannot be read as text within {MAX_BYTES} bytes")
        return None


def entries(text):
    """[(line number, entry)] of a list file."""
    out = []
    for number, line in enumerate(text.split("\n"), 1):
        entry = line.strip()
        if entry and not entry.startswith("#"):
            out.append((number, entry))
    return out


def check(root):
    findings = []
    deny = None
    text = read_text(root, SETTINGS, findings)
    if text is not None:
        try:
            settings = json.loads(text)
            deny = settings["permissions"]["deny"]
            if not isinstance(deny, list) or not all(isinstance(item, str) for item in deny):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            findings.append(f"{SETTINGS}:1: permissions.deny is missing, or is not valid JSON, or is not a list of strings")
            deny = None
    present = set(deny or ())
    for rel in LISTS:
        text = read_text(root, rel, findings)
        if text is None:
            continue
        for number, entry in entries(text):
            if not ENTRY.fullmatch(entry):
                findings.append(f"{rel}:{number}: the entry is not a deny rule of the form Tool or Tool(specifier)")
            elif deny is not None and entry not in present:
                findings.append(f"{rel}:{number}: this entry is missing from permissions.deny in {SETTINGS}")
    return findings


def main():
    findings = check(sys.argv[1] if len(sys.argv) > 1 else ".")
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
