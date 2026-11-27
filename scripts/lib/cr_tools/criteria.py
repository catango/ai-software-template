"""scripts/cr-criteria: the acceptance criteria of a change request as JSON, for a self-review.

    cr-criteria [--offline] [--tree DIR] CR        CR is `CR-<n>` or `<n>`

Reads `<root>cr/CR-<n>/acceptance-criteria.md` of the tree (the first record root of `config/project.yaml` that has it) and prints one
JSON object: the file, and for every table row whose first cell is an ID (`T-12`, `AC3-7`): the ID, the criterion, and the evidence
that the table names. The Skill returns one verdict per ID, posted as ONE pull request comment. A field longer than 400
characters is cut and the item carries `"cut": true`; read the file by range for the rest. The script reads local files only: the
flag --offline is accepted so that every Skill script answers the same flag, and it changes nothing.

Exit status: 0 printed, 1 refused (no such change request, no table, or no criterion), 2 usage error.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from pr_contract import config as policy
from pr_contract.errors import ConfigError, InputError
from pr_contract.sanitize import clean

from . import common

SCRIPT = "cr-criteria"
FIELD_MAX = 400
ITEMS_MAX = 200
FILE_MAX = 262144
ID = re.compile(r"[A-Z][A-Z0-9]{0,9}-[0-9]{1,4}")
CELL_SPLIT = re.compile(r"(?<!\\)\|")


def cells(line):
    """The cells of a Markdown table row (`\\|` stays inside a cell), or None when the line is not a row."""
    line = line.strip()
    if not line.startswith("|"):
        return None
    parts = CELL_SPLIT.split(line)
    return [part.strip().replace("\\|", "|") for part in parts[1:-1]] if line.endswith("|") and len(parts) >= 3 else None


def parse_table(text):
    """({id, criterion, evidence, cut} items, problems) of the first table whose header names ID, Criterion and Evidence."""
    items, problems, seen = [], [], set()
    columns = None
    for line in text.split("\n"):
        row = cells(line)
        if row is None:
            if columns is not None and line.strip() == "":
                break
            continue
        names = [name.lower() for name in row]
        if columns is None:
            if len(row) >= 3 and names[0] == "id" and "criterion" in names and "evidence" in names:
                columns = (0, names.index("criterion"), names.index("evidence"))
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in row if cell != ""):
            continue
        if len(row) <= max(columns) or not ID.fullmatch(row[0]):
            problems.append("a table row has no ID in its first cell")
            continue
        if row[0] in seen:
            problems.append(f"the ID {row[0]} occurs more than once")
            continue
        seen.add(row[0])
        item = {"id": row[0], "criterion": common.plain(row[columns[1]], FIELD_MAX), "evidence": common.plain(row[columns[2]], FIELD_MAX)}
        if len(row[columns[1]]) > FIELD_MAX or len(row[columns[2]]) > FIELD_MAX:
            item["cut"] = True
        items.append(item)
    return items, problems


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("usage: cr-criteria [--offline] [--tree DIR] CR")


def main(argv=None):
    try:
        parser = _Parser(prog=SCRIPT, add_help=False)
        parser.add_argument("--offline", action="store_true")
        parser.add_argument("--tree", default=str(Path(__file__).resolve().parents[3]))
        parser.add_argument("cr")
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
        match = re.fullmatch(r"(?:CR-)?([1-9][0-9]{0,8})", args.cr)
        if not match:
            raise InputError("CR is CR-<n> or <n>")
        number = int(match.group(1))
        tree = Path(args.tree)
        try:
            config_file = tree / "config" / "project.yaml"
            text = config_file.read_text(encoding="utf-8")
            roots = policy.docs_roots(policy.parse_yaml(text))
        except (OSError, UnicodeDecodeError, ConfigError):
            print(common.summary(SCRIPT, "refused: the record roots cannot be read from config/project.yaml"))
            return 1
        for root in roots:
            path = tree / root / "cr" / f"CR-{number}" / "acceptance-criteria.md"
            if path.is_file() and not path.is_symlink():
                break
        else:
            print(common.summary(SCRIPT, f"refused: no acceptance-criteria.md for CR {number} under {', '.join(roots)}"))
            return 1
        data = path.read_bytes()
        if len(data) > FILE_MAX:
            print(common.summary(SCRIPT, "refused: acceptance-criteria.md is too large"))
            return 1
        items, problems = parse_table(data.decode("utf-8", "replace"))
        if not items:
            print(common.summary(SCRIPT, f"refused: no criterion table in CR {number}"))
            return 1
        kept = items[:ITEMS_MAX]
        if len(items) > ITEMS_MAX:
            problems.append(f"{len(items) - ITEMS_MAX} more criteria were left out")
        relative = path.relative_to(tree).as_posix()
        rows = ",\n".join(" " + json.dumps(item, ensure_ascii=True, separators=(",", ":")) for item in kept)
        print(f'{{"cr":{number},"file":{json.dumps(relative)},"count":{len(kept)},"problems":{json.dumps(problems)},"criteria":[\n{rows}\n]}}')
        print(common.summary(SCRIPT, f"CR {number}: {len(kept)} criteria" + (f", {len(problems)} problem(s)" if problems else "")))
        return 0
    except InputError as error:
        print(f"{SCRIPT}: error: {clean(error, 200)}", file=sys.stderr)
        return 2
