"""Static part of the gate check `cr-layout` (used by cr-layout.sh). Standard library only.

Usage: cr_layout.py [--config FILE | --config-unreadable] ROOT   (exit 1 when there are findings).

The record roots are `workflow.docs_roots` of the configuration, read with the strict reader of pr_contract.config from the text
that cr-layout.sh took from the BASE commit. A configuration that is missing, unreadable, or malformed is a finding (fail closed).

For every `<root>cr/CR-<n>/` and for `<root>cr/_template/`:
  - the five files of the skeleton exist as regular files and can be read as UTF-8 text;
  - acceptance-criteria.md has a table whose first column is `ID`, with at least one row, every ID of the form `AC-1` (letters,
    a hyphen, digits), and no ID twice;
  - implementation-plan.md has a table with an `Acceptance criteria` column, and every row of it names at least one ID that
    acceptance-criteria.md defines. Only a token whose prefix acceptance-criteria.md uses counts as a criterion reference (`PC-8`
    next to `AC-1` is prose); a defined prefix with an undefined number (`AC-99`) is a finding;
  - every Markdown file of the directory, and every Markdown file directly under `<root>adr/`, writes a reference to another
    repository in a qualified form. Accepted: `#<n>`, `<owner>/<repo>#<n>`, `<repo>:ADR-<n>`. Rejected: `<repo>#<n>` and
    `<repo>/ADR-<n>` (and `<repo>/CR-<n>`), which name a repository without saying whose it is. Link targets, URLs, and fenced
    code are not read. A bare `ADR-<n>` or `CR-<n>` is a record of this repository. At most 200 Markdown files are read per
    directory; more than that is a finding (the check does not pass on files it did not read).
Any other entry under `<root>cr/` that is a directory or a link is a finding. Findings are "path:line: message".
"""
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

from pr_contract import config as policy  # noqa: E402
from pr_contract.errors import ConfigError  # noqa: E402

FILES = ("README.md", "impact-analysis.md", "design.md", "acceptance-criteria.md", "implementation-plan.md")
MAX_BYTES = 1_000_000
MAX_MARKDOWN_FILES = 200
CR_DIR = re.compile(r"CR-[1-9][0-9]*")
ID = re.compile(r"[A-Z][A-Za-z0-9]*-[0-9]+")
ID_ANYWHERE = re.compile(r"(?<![A-Za-z0-9-])[A-Z][A-Za-z0-9]*-[0-9]+(?![A-Za-z0-9-])")
SEPARATOR = re.compile(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?")
FENCE = re.compile(r"\s{0,3}(```|~~~)")
LINK_TARGET = re.compile(r"\]\([^)]*\)")
URL = re.compile(r"<?[A-Za-z][A-Za-z0-9+.-]*://\S+")
# <repo>#<n> without an owner: the name is not preceded by a path character, so `<owner>/<repo>#<n>` never matches.
UNOWNED = re.compile(r"(?<![\w./&-])([A-Za-z0-9_][A-Za-z0-9_.-]*)#([0-9]+)(?![\w-])")
UNQUALIFIED_RECORD = re.compile(r"(?<![\w./-])([A-Za-z][A-Za-z0-9_.-]*)/((?:ADR|CR)-[0-9]+)(?![\w/-]|\.\w)")


def read_text(path):
    """The text of a regular file, or None (not a regular file, too large, or not UTF-8)."""
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return None
        return data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def lines_outside_fences(text):
    """[(line number, line)] of the text that is not inside a fenced code block."""
    out, fence = [], None
    for number, line in enumerate(text.split("\n"), 1):
        match = FENCE.match(line)
        if match:
            if fence is None:
                fence = match.group(1)
            elif match.group(1) == fence:
                fence = None
            continue
        if fence is None:
            out.append((number, line))
    return out


def cells(line):
    """The cells of a table row: split on a pipe that no backslash escapes, without the outer pipes."""
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def tables(text):
    """[(header cells, [(line number, row cells)])] of the pipe tables outside fenced code."""
    found, current = [], []
    for number, line in lines_outside_fences(text) + [(0, "")]:
        if line.strip().startswith("|"):
            current.append((number, line))
            continue
        if len(current) >= 2 and SEPARATOR.fullmatch(current[1][1].strip()):
            found.append((cells(current[0][1]), [(n, cells(row)) for n, row in current[2:]]))
        current = []
    return found


def plain(cell):
    return cell.replace("`", "").replace("*", "").strip()


def shown(value):
    return re.sub(r"[^\x20-\x7e]", "?", value)[:40]


def criteria(path, text, findings):
    """{ID: line} of the criteria table of acceptance-criteria.md; findings for a missing or malformed table."""
    ids = {}
    table = next((t for t in tables(text) if t[0] and plain(t[0][0]).lower() == "id"), None)
    if table is None or not table[1]:
        findings.append(f"{path}:1: no table with the columns ID, Criterion, and Evidence and at least one criterion")
        return ids
    for number, row in table[1]:
        value = plain(row[0])
        if not ID.fullmatch(value):
            findings.append(f"{path}:{number}: criterion ID \"{shown(value)}\" is not of the form AC-1")
        elif value in ids:
            findings.append(f"{path}:{number}: duplicate criterion ID {value} (first at line {ids[value]})")
        else:
            ids[value] = number
    return ids


def plan(path, text, ids, findings):
    table = None
    for header, rows in tables(text):
        names = [plain(c).lower() for c in header]
        if "acceptance criteria" in names:
            table = (names.index("acceptance criteria"), rows)
            break
    if table is None or not table[1]:
        findings.append(f"{path}:1: no PR table with an Acceptance criteria column and at least one row")
        return
    column, rows = table
    # A token is a criterion reference when its prefix is one that acceptance-criteria.md uses (`AC-` in `AC-1`); an ID-shaped word
    # with another prefix (`PC-8`, `ADR-5`) is prose. A defined prefix with an undefined number (`AC-99`) is still a finding.
    prefixes = {value.rsplit("-", 1)[0] for value in ids}
    for number, row in rows:
        tokens = ID_ANYWHERE.findall(plain(row[column]) if column < len(row) else "")
        named = [value for value in tokens if value.rsplit("-", 1)[0] in prefixes]
        label = shown(plain(row[0])) if row else "?"
        if not named:
            hint = f" ({shown(tokens[0])} has a prefix that acceptance-criteria.md does not use)" if tokens else ""
            findings.append(f"{path}:{number}: plan row {label} names no criterion ID{hint}")
            continue
        for value in dict.fromkeys(named):
            if value not in ids:
                findings.append(f"{path}:{number}: plan row {label} names {value}, which acceptance-criteria.md does not define")


def references(path, text, findings):
    for number, line in lines_outside_fences(text):
        line = URL.sub("", LINK_TARGET.sub("]()", line))
        for match in UNOWNED.finditer(line):
            findings.append(f"{path}:{number}: unqualified reference to another repository \"{shown(match.group(0))}\": "
                            f"write <owner>/{shown(match.group(1))}#{match.group(2)}")
        for match in UNQUALIFIED_RECORD.finditer(line):
            findings.append(f"{path}:{number}: unqualified reference to another repository \"{shown(match.group(0))}\": "
                            f"write {shown(match.group(1))}:{match.group(2)}")


def markdown_files(directory):
    """([paths], capped): at most MAX_MARKDOWN_FILES paths; `capped` is true when there are more (bounds the work, never silently)."""
    out = []
    for base, dirs, names in os.walk(directory, followlinks=False):
        dirs.sort()
        for name in sorted(names):
            if name.endswith(".md"):
                if len(out) == MAX_MARKDOWN_FILES:
                    return out, True
                out.append(os.path.join(base, name))
    return out, False


def check_cr(root, rel, findings):
    directory = os.path.join(root, rel)
    texts = {}
    for name in FILES:
        text = read_text(os.path.join(directory, name))
        if text is None:
            findings.append(f"{rel}/{name}:1: the change request has no readable regular file {name}")
        else:
            texts[name] = text
    ids = {}
    if "acceptance-criteria.md" in texts:
        ids = criteria(f"{rel}/acceptance-criteria.md", texts["acceptance-criteria.md"], findings)
    if "implementation-plan.md" in texts and "acceptance-criteria.md" in texts:
        plan(f"{rel}/implementation-plan.md", texts["implementation-plan.md"], ids, findings)
    paths, capped = markdown_files(directory)
    if capped:
        findings.append(f"{rel}:1: more than {MAX_MARKDOWN_FILES} Markdown files; not all were checked")
    for path in paths:
        text = read_text(path)
        if text is not None:
            references(os.path.relpath(path, root).replace(os.sep, "/"), text, findings)


def check_root(root, record_root, findings):
    cr = record_root + "cr"
    cr_path = os.path.join(root, cr)
    if os.path.islink(cr_path):
        findings.append(f"{cr}:1: {cr}/ is a symbolic link")
    elif os.path.isdir(cr_path):
        for name in sorted(os.listdir(cr_path)):
            entry = os.path.join(cr_path, name)
            if os.path.islink(entry):
                findings.append(f"{cr}/{name}:1: a symbolic link under {cr}/")
            elif os.path.isdir(entry):
                if name == "_template" or CR_DIR.fullmatch(name):
                    check_cr(root, f"{cr}/{name}", findings)
                else:
                    findings.append(f"{cr}/{name}:1: not a change request directory (CR-<n> with the issue number, or _template)")
    adr_path = os.path.join(root, record_root + "adr")
    if os.path.isdir(adr_path) and not os.path.islink(adr_path):
        for name in sorted(os.listdir(adr_path)):
            text = read_text(os.path.join(adr_path, name)) if name.endswith(".md") else None
            if text is not None:
                references(f"{record_root}adr/{name}", text, findings)


def load_roots(config_file, unreadable):
    """(roots, problem). `problem` is a finding text: the check fails closed."""
    if unreadable:
        return (), "the base commit cannot be read; the check fails closed"
    if config_file is None:
        return (), "the configuration is missing; docs_roots cannot be read and the check fails closed"
    try:
        with open(config_file, encoding="utf-8") as handle:
            text = handle.read(policy.MAX_TEXT + 1)
        return policy.docs_roots(policy.parse_yaml(text)), None
    except (OSError, UnicodeDecodeError, ConfigError) as error:
        detail = f" ({error})" if isinstance(error, ConfigError) else ""
        return (), f"docs_roots cannot be read from the configuration{detail}; the check fails closed"


def main(argv, out=sys.stdout):
    config_file, unreadable, args = None, False, list(argv)
    root = None
    while args:
        flag = args.pop(0)
        if flag == "--config" and args:
            config_file = args.pop(0)
        elif flag == "--config-unreadable":
            unreadable = True
        elif root is None and not flag.startswith("-"):
            root = flag
        else:
            out.write("usage: cr_layout.py [--config FILE | --config-unreadable] ROOT\n")
            return 2
    if root is None:
        out.write("usage: cr_layout.py [--config FILE | --config-unreadable] ROOT\n")
        return 2
    roots, problem = load_roots(config_file, unreadable)
    findings = []
    if problem:
        findings.append(f"config/project.yaml:1: {problem}")
    for record_root in roots:
        check_root(root, record_root, findings)
    for finding in findings:
        out.write(finding + "\n")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
