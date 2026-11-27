"""Static part of the gate check `size-budget` (used by size-budget.sh). Standard library only.

Reads `.template/budgets.toml`, applies the per-key raises of `.template/overrides.toml` (`[[budget]]` entries), and compares
the lines, bytes, or characters of the matching tracked files with the limits. Findings are "path:line: message"; they name
the class and the numbers, never file content. The active raises are printed on stderr, one line each:
`raise: <class>.<key> <old> -> <new> (<pr>)`.

Usage: size_budget.py ROOT   (tracked paths on stdin; exit 1 when there are findings).
"""
import os
import re
import sys
import tomllib

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))

import template_manifest  # noqa: E402

BUDGETS = ".template/budgets.toml"
OVERRIDES = ".template/overrides.toml"
KEYS = ("max_lines", "max_bytes", "max_chars")


def load(root, rel):
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle), None
    except (OSError, tomllib.TOMLDecodeError):
        return None, f"{rel}:1: not valid TOML"


def is_posint(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def parse_classes(raw, findings):
    classes = {}
    if raw.get("schema") != 1:
        findings.append((BUDGETS, 1, "schema must be 1"))
    for name, entry in (raw.get("class") or {}).items():
        label = f"class.{name}"
        if not isinstance(entry, dict):
            findings.append((BUDGETS, 1, f"{label}: must be a table"))
            continue
        paths = entry.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
            findings.append((BUDGETS, 1, f"{label}.paths: must be a non-empty list of patterns"))
            continue
        try:
            patterns = [template_manifest.compile_pattern(p) for p in paths]
        except template_manifest.ManifestError as exc:
            findings.append((BUDGETS, 1, f"{label}.paths: {exc}"))
            continue
        limits = {}
        for key, value in entry.items():
            if key == "paths":
                continue
            if key not in KEYS or not is_posint(value):
                findings.append((BUDGETS, 1, f"{label}.{key}: unknown key or not a positive integer"))
            else:
                limits[key] = value
        if not limits:
            findings.append((BUDGETS, 1, f"{label}: no limit (one of {', '.join(KEYS)})"))
        classes[name] = {"patterns": patterns, "limits": limits}
    if not classes:
        findings.append((BUDGETS, 1, "no class defined"))
    return classes


def whole_file_override(node):
    """True when the overrides name the budgets file as a key or a value anywhere (a whole-file override)."""
    if isinstance(node, dict):
        return any(key == BUDGETS or whole_file_override(value) for key, value in node.items())
    if isinstance(node, list):
        return any(whole_file_override(item) for item in node)
    return node == BUDGETS


def apply_raises(raw, classes, findings, notes):
    if raw is None:
        return
    if whole_file_override(raw):
        findings.append((OVERRIDES, 1, f"{BUDGETS} cannot be overridden as a whole file; raise single values with [[budget]] entries"))
    entries = raw.get("budget", [])
    if not isinstance(entries, list):
        findings.append((OVERRIDES, 1, "budget must be an array of tables ([[budget]])"))
        return
    for index, entry in enumerate(entries, 1):
        label = f"budget entry {index}"
        if not isinstance(entry, dict) or set(entry) != {"class", "key", "value", "reason", "pr"}:
            findings.append((OVERRIDES, 1, f"{label}: needs exactly class, key, value, reason, and pr"))
            continue
        cls, key, value = entry["class"], entry["key"], entry["value"]
        if cls not in classes or key not in classes[cls]["limits"]:
            findings.append((OVERRIDES, 1, f"{label}: no such budget (class and key must exist in {BUDGETS})"))
            continue
        if not is_posint(value) or not all(isinstance(entry[k], str) and entry[k].strip() for k in ("reason", "pr")):
            findings.append((OVERRIDES, 1, f"{label}: value must be a positive integer, reason and pr non-empty strings"))
            continue
        old = classes[cls]["limits"][key]
        if value <= old:
            findings.append((OVERRIDES, 1, f"{label}: the value must raise the budget ({old}); lower it in {BUDGETS} instead"))
            continue
        classes[cls]["limits"][key] = value
        notes.append(f"raise: {cls}.{key} {old} -> {value} ({' '.join(entry['pr'].split())[:80]})")


def frontmatter_description(text):
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            block = lines[1:end]
            break
    else:
        return None
    for i, line in enumerate(block):
        match = re.match(r"^description:\s*(.*)$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if value in ("", ">", "|", ">-", "|-", ">+", "|+"):
            parts = []
            for follow in block[i + 1:]:
                if follow.startswith((" ", "\t")):
                    parts.append(follow.strip())
                else:
                    break
            return " ".join(parts)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return None


def main(argv):
    root = argv[1] if len(argv) > 1 else "."
    paths = [line.rstrip("\n") for line in sys.stdin if line.strip()]
    findings, notes = [], []
    raw, error = load(root, BUDGETS)
    if error:
        findings.append((BUDGETS, 1, "not valid TOML"))
    elif raw is None:
        findings.append((BUDGETS, 1, "missing: size-budget has no budgets to apply"))
    if raw is not None:
        classes = parse_classes(raw, findings)
        overrides, oerror = load(root, OVERRIDES)
        if oerror:
            findings.append((OVERRIDES, 1, "not valid TOML"))
        apply_raises(overrides, classes, findings, notes)
        for name, entry in classes.items():
            for path in paths:
                if not any(p.fullmatch(path) for p in entry["patterns"]):
                    continue
                full = os.path.join(root, path)
                if not os.path.isfile(full):
                    continue
                with open(full, "rb") as handle:
                    data = handle.read()
                limits = entry["limits"]
                if "max_lines" in limits and data.count(b"\n") > limits["max_lines"]:
                    findings.append((path, 1, f"{data.count(chr(10).encode())} lines > {limits['max_lines']} ({name})"))
                if "max_bytes" in limits and len(data) > limits["max_bytes"]:
                    findings.append((path, 1, f"{len(data)} bytes > {limits['max_bytes']} ({name})"))
                if "max_chars" in limits:
                    description = frontmatter_description(data.decode("utf-8", errors="replace"))
                    if description is None:
                        findings.append((path, 1, f"no description in the frontmatter ({name})"))
                    elif len(description) > limits["max_chars"]:
                        findings.append((path, 1, f"description of {len(description)} characters > {limits['max_chars']} ({name})"))
    for note in notes:
        print(note, file=sys.stderr)
    for path, line, message in findings:
        print(f"{path}:{line}: {message}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
