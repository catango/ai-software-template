"""The policy inputs that the pr-contract rules read from the configuration of the BASE commit.

Only text of the base commit reaches this module: the clients fetch `config/project.yaml` at the base SHA and never the head.
There is no YAML library and no process: `parse_yaml` is a strict reader of THREE keys, in the subset of YAML that
`scripts/initialize-project` writes, and everything else in the file is not interpreted. Anything of those keys outside the
subset raises ConfigError, so the rules that need them fail closed instead of guessing. The subset:

    workflow:                         # a block mapping; keys other than the three below are skipped
      design_repos: []                # or a block list of strings (owner/name), the only two forms
      docs_roots:                     # the same two forms: directories ending in `/` that hold the records (PC-4, PC-5)
        - "docs/"
      operator_paths:
        - "scripts/operator/"         # strings plain, "double" or 'single' quoted, of letters, digits, `.`, `_`, `-`, `/`, `@`
        - ansible/roles/operator_*/   # and `*` as the last two characters `*/` of a path; no escapes, no flow style

    architectures:                    # a block list of mappings, the only form; `[]` is also read (an empty list)
      - name: "arch-a"                # keys name, runner, runner_private; values plain, "double" or 'single' quoted,
        runner: "runner-a"            # of letters, digits, `.`, `_`, `-` only (no escapes); an omitted value is null
        runner_private: "runner-a"

The three lists may appear in any order, each at most once. Comments, blank lines, and any other top-level key are skipped.
Flow style, anchors, aliases, tags, tabs, block scalars, a repeated key, a quoted key, and a second YAML document are errors
inside the keys that are read. Plain values that YAML would not read as a string (numbers, true, false, null) are errors; quote
them.

`docs_roots` lists the directories that hold the change requests (`<root>cr/`) and the ADRs (`<root>adr/`): a design PR may change
only paths under them (PC-4) and an implementation or change PR may not change the approved records in them (PC-5). The owned
templates `cr/_template/` and `adr/_template.md` are not approved records. Absent key: `docs/`. An empty list, or one that is not
a list of distinct relative directories ending in `/`, is an error.
`design_repos` lists the repositories (owner/name) whose merged design PRs an implementation PR may reference (rule PC-3).
`operator_paths` is the operator set of rule PC-8: an entry matches a path as an exact path, or as a directory prefix when it
ends with `/`, or as the prefix left when a final `*/` loses its `*`. Case-sensitive. An explicit `[]` makes PC-8 inactive.
`architectures` is a list of mappings with `name`, `runner` (the label for a public repository) and `runner_private` (the label
for a private one). An empty label means the repository has no runner for that architecture (rule PC-9). There is no default:
a missing key fails closed.
"""
import re
from dataclasses import dataclass

from .errors import ConfigError

MAX_TEXT = 65536
MAX_ENTRY = 200
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,19}")  # the pattern of the configuration schema
_LABEL = re.compile(r"[A-Za-z0-9_.-]{0,64}")
_KEYS = frozenset({"name", "runner", "runner_private"})
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}")
_PATH_ENTRY = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*(?:\*/)?")
_ROOT_ENTRY = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)*/")

_PLAIN = r"[A-Za-z0-9_.][A-Za-z0-9_.-]*"
_VALUE = rf"(?:{_PLAIN}|\"[A-Za-z0-9_.-]*\"|'[A-Za-z0-9_.-]*')"
_COMMENT = r"(?: +#.*)?"
_LIST_PLAIN = r"[A-Za-z0-9_.][A-Za-z0-9_./@*-]*"
_LIST_QUOTED = r"\"[A-Za-z0-9_./@*-]*\"|'[A-Za-z0-9_./@*-]*'"
_ITEM = re.compile(rf"(?P<indent> *)- +(?P<val>{_LIST_PLAIN}|{_LIST_QUOTED}){_COMMENT}")
_TOP_ARCH = re.compile(r"architectures:(?P<rest>(?: .*)?)")
_TOP_WORKFLOW = re.compile(r"workflow:(?P<rest>(?: .*)?)")
_ODD_TOP = re.compile(r"""(?:["'](?:architectures|workflow)|\? |<<|(?:architectures|workflow)(?![A-Za-z0-9_.-]))""")
_CHILD_KEY = re.compile(r"(?P<key>design_repos|operator_paths|docs_roots):(?P<rest>(?: .*)?)")
_ODD_CHILD = re.compile(r"""(?:["'](?:design_repos|operator_paths|docs_roots)|\? |<<|(?:design_repos|operator_paths|docs_roots)(?![A-Za-z0-9_.-]))""")
_EMPTY_LIST = re.compile(rf" +\[\]{_COMMENT}")
_NOTHING = re.compile(_COMMENT)
_FIRST = re.compile(rf"(?P<indent> *)-(?P<gap> +)(?P<key>[a-z_]+):(?: +(?P<val>{_VALUE}))?{_COMMENT}")
_NEXT = re.compile(rf"(?P<indent> +)(?P<key>[a-z_]+):(?: +(?P<val>{_VALUE}))?{_COMMENT}")
_NOT_A_STRING = re.compile(r"(?i)(?:true|false|null|~|yes|no|on|off|y|n)|[-+.]?[0-9].*")


@dataclass(frozen=True)
class Arch:
    name: str
    runner: str = ""
    runner_private: str = ""

    def runner_for(self, private):
        return self.runner_private if private else self.runner


def _scalar(text):
    if text is None:
        return None
    if text[0] in "\"'":
        return text[1:-1]
    if _NOT_A_STRING.fullmatch(text):
        raise ConfigError("architectures value is not a string")
    return text


def _skipped(line):
    return line.strip(" ") == "" or line.lstrip(" ").startswith("#")


def parse_yaml(text):
    """{"architectures": [...], "workflow": {"design_repos": [...], "operator_paths": [...]}} with only the keys the file has in
    the subset above, else {}. Everything else is not read."""
    if not isinstance(text, str) or len(text.encode("utf-8", "replace")) > MAX_TEXT or "\x00" in text:
        raise ConfigError("configuration unreadable")
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    seen_content = False
    keys = {}
    for number, line in enumerate(lines):
        if line[:1] in ("", " ", "#") or line.startswith("- ") or line == "-":
            seen_content = seen_content or not _skipped(line)
            continue
        if re.match(r"(?:---|\.\.\.)(?: |$)", line):
            if line.startswith("---") and not seen_content:
                continue
            raise ConfigError("more than one YAML document")
        seen_content = True
        if _ODD_TOP.match(line):
            shape = _TOP_ARCH if line.startswith("architectures") else _TOP_WORKFLOW
            if not shape.fullmatch(line):
                raise ConfigError("a supported key is not in the supported form")
            name = line.split(":", 1)[0]
            if name in keys:
                raise ConfigError(f"{name} key is repeated")
            keys[name] = number
    out = {}
    if "architectures" in keys:
        out["architectures"] = _read_architectures(lines, keys["architectures"])
    if "workflow" in keys:
        out["workflow"] = _read_workflow(lines, keys["workflow"])
    return out


def _read_architectures(lines, index):
    rest = _TOP_ARCH.fullmatch(lines[index]).group("rest")
    if _EMPTY_LIST.fullmatch(rest):
        return []
    if not _NOTHING.fullmatch(rest):
        raise ConfigError("architectures is not a block list or []")
    items, current, column, item_indent = [], None, None, None
    for line in lines[index + 1:]:
        if _skipped(line):
            if "\t" in line:
                raise ConfigError("tab in architectures")
            continue
        if line[0] == "\t":
            raise ConfigError("tab in architectures")
        if line[0] not in " -":
            break
        first = _FIRST.fullmatch(line)
        if first:
            indent = len(first.group("indent"))
            if item_indent not in (None, indent):
                raise ConfigError("architectures list indentation changes")
            item_indent, column = indent, indent + 1 + len(first.group("gap"))
            current = {}
            items.append(current)
            match = first
        else:
            match = _NEXT.fullmatch(line)
            if not match or current is None or len(match.group("indent")) != column:
                raise ConfigError("architectures is outside the supported form")
        key = match.group("key")
        if key in current:
            raise ConfigError("architectures key is repeated in an entry")
        current[key] = _scalar(match.group("val"))
    return items or None


def _read_workflow(lines, index):
    rest = _TOP_WORKFLOW.fullmatch(lines[index]).group("rest")
    if not _NOTHING.fullmatch(rest):
        raise ConfigError("workflow is not a block mapping")
    found, child = {}, None
    for position in range(index + 1, len(lines)):
        line = lines[position]
        if _skipped(line):
            if "\t" in line:
                raise ConfigError("tab in workflow")
            continue
        if line[0] == "\t":
            raise ConfigError("tab in workflow")
        if line[0] not in " -":
            break
        indent = len(line) - len(line.lstrip(" "))
        if child is None:
            if line.lstrip(" ").startswith("-"):
                raise ConfigError("workflow is not a block mapping")
            child = indent
        if indent < child:
            raise ConfigError("workflow indentation changes")
        if indent != child:
            continue
        body = line[child:]
        if _ODD_CHILD.match(body):
            match = _CHILD_KEY.fullmatch(body)
            if not match:
                raise ConfigError("a supported key is not in the supported form")
            key = match.group("key")
            if key in found:
                raise ConfigError(f"{key} key is repeated")
            found[key] = _read_list(lines, position, child, key, match.group("rest"))
    return found


def _read_list(lines, index, child, key, rest):
    """The block list of strings (or `[]`) under the key on lines[index]."""
    if _EMPTY_LIST.fullmatch(rest):
        return []
    if not _NOTHING.fullmatch(rest):
        raise ConfigError(f"{key} is not a block list or []")
    items, item_indent = [], None
    for line in lines[index + 1:]:
        if _skipped(line):
            if "\t" in line:
                raise ConfigError(f"tab in {key}")
            continue
        if line[0] == "\t":
            raise ConfigError(f"tab in {key}")
        if len(line) - len(line.lstrip(" ")) <= child:
            break
        item = _ITEM.fullmatch(line)
        if not item:
            raise ConfigError(f"{key} is outside the supported form")
        indent = len(item.group("indent"))
        if item_indent not in (None, indent):
            raise ConfigError(f"{key} list indentation changes")
        item_indent = indent
        value = item.group("val")
        if value[0] in "\"'":
            value = value[1:-1]
        elif _NOT_A_STRING.fullmatch(value):
            raise ConfigError(f"{key} value is not a string")
        items.append(value)
    if not items:
        raise ConfigError(f"{key} is not a block list or []")
    return items


def _workflow_list(config, key):
    """The tuple of strings under `workflow.<key>`; a missing file, mapping, or key and anything that is not a list of
    distinct strings raise ConfigError."""
    if config is None:
        raise ConfigError(f"{key} is missing")
    if not isinstance(config, dict):
        raise ConfigError("configuration is not a mapping")
    workflow = config.get("workflow")
    if not isinstance(workflow, dict) or key not in workflow:
        raise ConfigError(f"{key} is missing")
    items = workflow[key]
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ConfigError(f"{key} is not a list of strings")
    if len(set(items)) != len(items):
        raise ConfigError(f"{key} has a repeated entry")
    return tuple(items)


def _path_entry_valid(entry):
    if len(entry) > MAX_ENTRY or not _PATH_ENTRY.fullmatch(entry):
        return False
    body = entry[:-2] if entry.endswith("*/") else entry[:-1] if entry.endswith("/") else entry
    return all(part not in ("", ".", "..") for part in body.split("/"))


def operator_paths(config):
    """The PC-8 operator set of a parsed base configuration, as a tuple (empty for an explicit `[]`). A missing file or key and
    anything malformed raise ConfigError: PC-8 fails closed, never passes."""
    paths = _workflow_list(config, "operator_paths")
    if not all(_path_entry_valid(entry) for entry in paths):
        raise ConfigError("operator_paths entry is not valid")
    return paths


def design_repos(config):
    """The repositories (owner/name) that PC-3 accepts for a qualified reference, as a tuple (empty for `[]`). A missing file or
    key and anything malformed raise ConfigError."""
    repos = _workflow_list(config, "design_repos")
    if not all(len(r) <= MAX_ENTRY and _REPO.fullmatch(r) for r in repos):
        raise ConfigError("design_repos entry is not valid")
    return repos


DEFAULT_DOCS_ROOTS = ("docs/",)


def docs_roots(config):
    """The directories that hold the records, as a tuple. An absent key (in a configuration that was read) is the default
    `docs/`; a missing file and anything malformed raise ConfigError, so PC-4 and PC-5 fail closed."""
    if config is None:
        raise ConfigError("configuration is missing")
    if not isinstance(config, dict):
        raise ConfigError("configuration is not a mapping")
    workflow = config.get("workflow", {})
    if not isinstance(workflow, dict):
        raise ConfigError("workflow is not a mapping")
    if "docs_roots" not in workflow:
        return DEFAULT_DOCS_ROOTS
    roots = _workflow_list(config, "docs_roots")
    if not roots or not all(len(r) <= MAX_ENTRY and _ROOT_ENTRY.fullmatch(r) and
                            all(part not in (".", "..") for part in r[:-1].split("/")) for r in roots):
        raise ConfigError("docs_roots entry is not valid")
    return roots


def is_operator_path(path, entries):
    """Whether `path` matches an entry: the exact path, or the prefix of an entry ending `/`, or of an entry ending `*/` (the
    star dropped). Case-sensitive; an entry never matches by any other rule."""
    for entry in entries:
        if entry.endswith("*/"):
            if path.startswith(entry[:-2]):
                return True
        elif entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif path == entry:
            return True
    return False


def architectures(config):
    """The architectures of a parsed base configuration. A missing file or key and anything malformed raise ConfigError."""
    if config is None:
        raise ConfigError("configuration is missing")
    if not isinstance(config, dict):
        raise ConfigError("configuration is not a mapping")
    if "architectures" not in config:
        raise ConfigError("architectures is missing")
    items = config["architectures"]
    if not isinstance(items, list):
        raise ConfigError("architectures is not a list")
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) - _KEYS:
            raise ConfigError("architectures entry is not valid")
        name = item.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in seen:
            raise ConfigError("architectures name is not valid")
        labels = []
        for key in ("runner", "runner_private"):
            value = item.get(key)
            value = "" if value is None else value
            if not isinstance(value, str) or not _LABEL.fullmatch(value):
                raise ConfigError("architectures runner is not valid")
            labels.append(value)
        seen.add(name)
        out.append(Arch(name, *labels))
    return tuple(out)
