"""Static part of the gate check `operator-contract` (used by operator-contract.sh). Standard library only.

Usage: operator_contract.py [--config FILE | --config-unreadable]   (tracked paths on stdin; exit 1 when there are findings).

The policy comes from `workflow.operator_paths` and `workflow.operator_commands` of `config/project.yaml`, read with the strict
reader of pr_contract.config from the text that operator-contract.sh took from the BASE commit, never from the head that is judged.
Failing closed: a configuration that is missing, unreadable, or lacks `operator_paths` is a finding. An explicit empty
`operator_paths` makes the check inactive.

Rules for a Python file under `operator_paths` (the syntax tree; nothing is run):
  - no `shell=True`, `executable=`, `os.system`, `os.popen`, `os.exec*`, `os.spawn*`, `pty`, `eval`, `exec`, or dynamic import;
  - a subprocess has a literal argument list whose program is listed in `operator_commands` (none is allowed by default);
  - no token read from the command line (an option or a variable named like a credential) or from the environment (a key named
    like a credential, a key that is not a literal, or the whole environment);
  - an entry point defines `--apply` as a flag that is off by default: the dry run is the default.
A shell script under `operator_paths` is a finding: the rules above cannot be checked in shell. Other files are not judged.
Static checks cannot see values built at run time, so the rules reject what they cannot read. Findings are "path:line: message".
"""
import ast
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

from pr_contract import config as policy  # noqa: E402
from pr_contract.errors import ConfigError  # noqa: E402

CONFIG_PATH = "config/project.yaml"
MAX_BYTES = 1_000_000
# Names that hold a credential. `pat` and `auth` count as words of their own (GH_PAT, AUTH_HEADER), not inside path or author.
SENSITIVE = re.compile(r"(?i)token|secret|passw|credential|api[_-]?key|access[_-]?key|private[_-]?key|bearer|oauth|authorization"
                       r"|(?<![a-z])(?:pat|auth)(?![a-z])")
SUBPROCESS_CALLS = frozenset({"run", "call", "check_call", "check_output", "Popen"})
SUBPROCESS_SHELL_CALLS = frozenset({"getoutput", "getstatusoutput"})
ENV_NAMES = ("os.environ", "os.environb")
ENV_READS = frozenset({"os.getenv", "os.getenvb", "os.environ.get", "os.environb.get"})
DYNAMIC_IMPORTS = frozenset({"__import__", "importlib.import_module", "importlib.__import__"})


def is_python(path, text):
    first = text.split("\n", 1)[0]
    return path.endswith(".py") or (first.startswith("#!") and "python" in first)


def is_shell(path, text):
    first = text.split("\n", 1)[0]
    return path.endswith((".sh", ".bash")) or (first.startswith("#!") and re.search(r"[/ ](?:ba|da|z|k)?sh\b", first) is not None)


def literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class Scanner(ast.NodeVisitor):
    def __init__(self, tree, allowed):
        self.allowed = allowed
        self.found = []
        self.handled = set()
        self.names = {}  # a local name -> the dotted name it stands for
        self.apply_ok = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.names[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
                    if alias.name.split(".")[0] == "pty":
                        self.add(node, "pty is not allowed")
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                if node.module.split(".")[0] == "pty":
                    self.add(node, "pty is not allowed")
                for alias in node.names:
                    self.names[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def add(self, node, message):
        self.found.append((getattr(node, "lineno", 1), message))

    def canon(self, node):
        if isinstance(node, ast.Name):
            return self.names.get(node.id, node.id if node.id in ("eval", "exec", "__import__") else None)
        if isinstance(node, ast.Attribute):
            base = self.canon(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    # -- calls
    def visit_Call(self, node):
        name = self.canon(node.func)
        for keyword in node.keywords:
            if keyword.arg == "shell":
                value = keyword.value
                if isinstance(value, ast.Constant) and value.value is True:
                    self.add(node, "shell=True is not allowed")
            elif keyword.arg == "executable":
                self.add(node, "executable= is not allowed: it starts a program other than the one that is checked")
        if isinstance(node.func, ast.Attribute) and node.func.attr == "set_defaults":
            for keyword in node.keywords:
                if keyword.arg == "apply" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value in (False, None)):
                    self.add(node, "set_defaults must not turn --apply on: the dry run is the default")
        if name in ("eval", "exec", "builtins.eval", "builtins.exec"):
            self.add(node, f"{name.split('.')[-1]} is not allowed")
        elif name in DYNAMIC_IMPORTS:
            self.add(node, "a dynamic import is not allowed")
        elif name and name.startswith("os."):
            tail = name.split(".")[1]
            if tail in ("system", "popen", "fork", "forkpty") or tail.startswith(("exec", "spawn", "posix_spawn")):
                self.add(node, f"os.{tail} is not allowed")
        elif name and name.startswith("pty."):
            self.add(node, "pty is not allowed")
        elif name and name.split(".")[0] == "subprocess" and name.count(".") == 1:
            self.subprocess_call(node, name.split(".")[1])
        elif name == "asyncio.create_subprocess_shell":
            self.add(node, "a command through a shell is not allowed")
        elif name == "asyncio.create_subprocess_exec":
            self.program(node, node.args[0] if node.args else None)
        elif name == "getattr" or (isinstance(node.func, ast.Name) and node.func.id == "getattr"):
            if node.args and self.canon(node.args[0]) in ("os", "subprocess", "sys", "importlib", "builtins"):
                self.add(node, "getattr on os, subprocess, sys, or importlib is not allowed")
        if name in ENV_READS:
            self.handled.add(id(node.func))
            if isinstance(node.func, ast.Attribute):
                self.handled.add(id(node.func.value))
            self.env_key(node, node.args[0] if node.args else None)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            self.option(node)
        self.generic_visit(node)

    def subprocess_call(self, node, function):
        if function in SUBPROCESS_SHELL_CALLS:
            self.add(node, "a command through a shell is not allowed")
        elif function in SUBPROCESS_CALLS:
            argument = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "args"), None)
            for keyword in node.keywords:
                if keyword.arg == "shell" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value in (False, None)):
                    if not (isinstance(keyword.value, ast.Constant) and keyword.value.value is True):
                        self.add(node, "shell must be the literal False")
            if isinstance(argument, (ast.List, ast.Tuple)):
                argument = argument.elts[0] if argument.elts else None
            elif literal(argument) is None:
                argument = None  # a command that is not a literal list or string
            self.program(node, argument)

    def program(self, node, argument):
        name = literal(argument)
        if name is None:
            self.add(node, "a subprocess needs a literal argument list that starts with a literal program name")
        elif name not in self.allowed:
            self.add(node, "a subprocess of a program that operator_commands does not list")

    def env_key(self, node, key):
        value = literal(key)
        if value is None:
            self.add(node, "the environment is read with a key that is not a literal")
        elif SENSITIVE.search(value):
            self.add(node, "a token is read from the environment")

    def option(self, node):
        names = [literal(a) for a in node.args] + [literal(k.value) for k in node.keywords if k.arg in ("dest", "metavar")]
        if any(n and SENSITIVE.search(n) for n in names):
            self.add(node, "a token is taken from the command line")
        if "--apply" in names:
            action = next((k.value for k in node.keywords if k.arg == "action"), None)
            default = next((k.value for k in node.keywords if k.arg == "default"), None)
            on_by_default = isinstance(default, ast.Constant) and bool(default.value)
            if literal(action) == "store_true" and not on_by_default:
                self.apply_ok = True
            else:
                self.add(node, "--apply must be a store_true flag that is off by default")
                self.apply_ok = True  # reported once, here

    # -- environment and command line
    def visit_Subscript(self, node):
        if self.canon(node.value) in ENV_NAMES:
            self.handled.add(id(node.value))
            self.env_key(node, node.slice)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        name = self.canon(node)
        if name in ENV_NAMES and id(node) not in self.handled:
            self.add(node, "the whole environment is read")
        self.generic_visit(node)

    def visit_Name(self, node):
        name = self.canon(node)
        if name in ENV_NAMES and id(node) not in self.handled:
            self.add(node, "the whole environment is read")
        elif name in ENV_READS and id(node) not in self.handled and not isinstance(node.ctx, ast.Store):
            self.add(node, "an environment reader is used other than as a direct call")

    def visit_Assign(self, node):
        self.assignment(node, node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.assignment(node, [node.target], node.value)
        self.generic_visit(node)

    def assignment(self, node, targets, value):
        names = [t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else "" for t in targets]
        if any(SENSITIVE.search(n) for n in names) and any(self.canon(n) == "sys.argv" for n in ast.walk(value)):
            self.add(node, "a token is taken from the command line")


def scan_python(path, text, allowed):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [(1, "the file cannot be parsed, so the rules cannot be checked")]
    scanner = Scanner(tree, allowed)
    scanner.visit(tree)
    entry = text.startswith("#!") or any(
        isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__"
        for n in tree.body)
    if entry and not scanner.apply_ok:
        scanner.found.append((1, "an entry point must define --apply as a flag that is off by default (dry run is the default)"))
    return scanner.found


def scan_file(path, allowed):
    if os.path.islink(path):
        return [(1, "a symbolic link in the operator paths is not checked; use a regular file")]
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError:
        return []
    if len(data) > MAX_BYTES:
        return [(1, f"the file is larger than {MAX_BYTES} bytes and is not checked")]
    if b"\0" in data:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [(1, "the file is not UTF-8 text and is not checked")]
    if is_python(path, text):
        return sorted(set(scan_python(path, text, allowed)))
    if is_shell(path, text):
        return [(1, "a shell script cannot be checked against the operator contract; write the operator script in Python")]
    return []


def load_policy(config_file, unreadable):
    """(paths, commands, problem). `problem` is a finding text: the check fails closed."""
    if unreadable:
        return (), (), "the base commit cannot be read; the check fails closed"
    if config_file is None:
        return (), (), "the configuration is missing; operator_paths cannot be read and the check fails closed"
    try:
        with open(config_file, encoding="utf-8") as handle:
            text = handle.read(policy.MAX_TEXT + 1)
        parsed = policy.parse_yaml(text)
        paths = policy.operator_paths(parsed)
        commands = policy.operator_commands(parsed) if paths else ()
    except (OSError, UnicodeDecodeError, ConfigError) as error:
        detail = f" ({error})" if isinstance(error, ConfigError) else ""
        return (), (), f"operator_paths or operator_commands cannot be read from the configuration{detail}; the check fails closed"
    return paths, commands, None


def main(argv, stdin=sys.stdin, out=sys.stdout):
    config_file, unreadable = None, False
    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag == "--config" and args:
            config_file = args.pop(0)
        elif flag == "--config-unreadable":
            unreadable = True
        else:
            out.write("usage: operator_contract.py [--config FILE | --config-unreadable]\n")
            return 2
    paths, commands, problem = load_policy(config_file, unreadable)
    if problem:
        out.write(f"{CONFIG_PATH}:1: {problem}\n")
        return 1
    bad = False
    for raw in stdin:
        path = raw.rstrip("\n")
        if not path or not policy.is_operator_path(path, paths) or not os.path.lexists(path):
            continue
        for line, message in scan_file(path, frozenset(commands)):
            out.write(f"{path}:{line}: {message}\n")
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
