"""Static part of the gate check `forbidden-api` (used by forbidden-api.sh). Standard library only.

Deny rules match only the commands of the AI session, not what a script runs. This check therefore reads every script and
rejects calls that merge, submit a review, enable auto-merge, or change rulesets, collaborators, repository settings,
commit statuses, check runs, alert states, push protection, branch protection, secrets, variables, environments, webhooks,
deploy keys, Actions permissions and runners, security features, releases, or tag refs. The list is data:
forbidden-api.toml next to this file. A finding names the class and never repeats script text.

Constraint: an endpoint and a GraphQL document must be string literals, for any client (`gh api`, `curl`, `urllib`,
`requests`). A variable may stand only for one whole path segment (an owner, a repository, a number); anything else is a
finding, because the check cannot see what a built string contains. `-f query=@file` is accepted only when that file is a
tracked file, and it is then scanned too.

Usage: forbidden_api.py DATA_TOML [TRACKED_FILE_LIST]   (script paths on stdin; findings "path:line: message" on stdout;
exit 1 when there are findings).
"""
import ast
import os
import re
import sys
import tomllib

WRITE_METHODS_OK = ("GET", "HEAD")
SHELL_EXT = (".sh", ".bash")
PY_EXT = (".py",)
SHEBANG = re.compile(r"^#!.*[/\s](?P<interp>bash|sh|dash|ksh|zsh|python[0-9.]*)(\s|$)")
VAR_MARK = "\x00"
SEGMENT_OK = re.compile(r"^[A-Za-z0-9._~%+@:=-]+$")
HOSTS = ("https://api.github.com/", "http://api.github.com/")
FIRST_SEGMENTS = ("repos", "orgs", "user", "users", "graphql", "search", "rate_limit", "app", "installation", "enterprises", "gists", "notifications", "teams", "licenses", "emojis", "meta", "markdown", "organizations", "repositories", "applications", "marketplace_listing")
KEYWORDS = {"if", "then", "elif", "else", "fi", "while", "until", "do", "done", "for", "in", "case", "esac", "select", "function", "time", "!", "{", "}", "coproc"}
PREFIXES = {"command", "exec", "env", "builtin", "nohup", "sudo", "timeout", "nice", "stdbuf", "xargs"}


class Data:
    def __init__(self, path):
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
        # "{}" in a path pattern stands for any one path segment (an owner, a repository, a number, or a variable).
        self.rest = [(entry["class"], [re.compile(p.replace(r"\{\}", "[^/]+")) for p in entry["paths"]]) for entry in raw.get("rest", [])]
        self.graphql = []
        for entry in raw.get("graphql", []):
            for name in entry["names"]:
                self.graphql.append((entry["class"], re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")))
        command = raw.get("command", {})
        self.gh_groups = set(command.get("gh_groups", []))
        self.gh_pr = set(command.get("gh_pr", []))
        self.push_flags = set(command.get("git_push_flags", []))
        # Files that may pass a non-literal URL to a Python HTTP client: reviewed in the owned data file, one reason each.
        self.literal_exempt = {entry["path"] for entry in raw.get("literal_exempt", [])}

    def classify_path(self, normalised):
        for name, patterns in self.rest:
            if any(p.match(normalised) for p in patterns):
                return name
        return None

    def graphql_classes(self, text):
        return sorted({name for name, pattern in self.graphql if pattern.search(text)})


# ------------------------------------------------------------------ endpoints

def normalise(endpoint):
    """(path, dynamic_error): the path with variables as {}, and a message when the endpoint is not a literal enough."""
    value = endpoint
    for host in HOSTS:
        if value.startswith(host):
            value = value[len(host):]
    value = value.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    segments = value.split("/")
    out = []
    previous_variable = False
    for index, segment in enumerate(segments):
        has_var = VAR_MARK in segment
        if has_var:
            if segment.replace(VAR_MARK, "") != "":
                return None, "an endpoint part is built from a variable and text"
            if index == 0:
                return None, "an endpoint starts with a variable"
            if previous_variable and not (index <= 2 and segments[0] in ("repos", "orgs")):
                return None, "an endpoint has two variable parts in a row"
            out.append("{}")
        else:
            if segment and not SEGMENT_OK.match(segment):
                return None, "an endpoint is not a plain path"
            out.append(segment)
        previous_variable = has_var
    if out and out[0] not in FIRST_SEGMENTS and not has_var_first(segments):
        return None, "an endpoint does not start with a known API collection"
    return "/".join(out), None


def has_var_first(segments):
    return bool(segments) and VAR_MARK in segments[0]


def looks_like_endpoint(text):
    value = text
    for host in HOSTS:
        if value.startswith(host):
            return True
    value = value.lstrip("/")
    return value.split("/", 1)[0] in ("repos", "orgs", "user") and "/" in value or value == "graphql"


# ------------------------------------------------------------------ shell reading

class Word:
    __slots__ = ("text", "dynamic", "literal_quote", "line")

    def __init__(self):
        self.text = ""
        self.dynamic = False
        self.literal_quote = True
        self.line = 1

    def value(self):
        return self.text


def split_commands(source, depth=0, base=1):
    """Commands (lists of Word) of a shell text. Command substitutions are read as commands of their own."""
    commands, words, word = [], [], None
    pos, length = 0, len(source)

    def flush_word():
        nonlocal word
        if word is not None:
            words.append(word)
            word = None

    def flush_command():
        nonlocal words
        flush_word()
        if words:
            commands.append(words)
        words = []

    def need():
        nonlocal word
        if word is None:
            word = Word()
            word.line = base + source.count("\n", 0, pos)
        return word

    def read_substitution(start):
        """start is just after '$('. Returns the index after the matching ')'."""
        level, i, quote = 1, start, None
        while i < length:
            ch = source[i]
            if quote:
                if ch == "\\" and quote == '"':
                    i += 1
                elif ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "\\":
                i += 1
            elif ch == "(":
                level += 1
            elif ch == ")":
                level -= 1
                if level == 0:
                    if depth < 8:
                        commands.extend(split_commands(source[start:i], depth + 1, base + source.count("\n", 0, start)))
                    return i + 1
            i += 1
        return length

    heredocs = []
    while pos < length:
        ch = source[pos]
        if ch == "\n":
            flush_command()
            pos += 1
            while heredocs:
                tag, strip = heredocs.pop(0)
                while pos < length:
                    end = source.find("\n", pos)
                    line = source[pos:end if end != -1 else length]
                    pos = end + 1 if end != -1 else length
                    if (line.lstrip("\t") if strip else line) == tag:
                        break
            continue
        if ch in " \t":
            flush_word()
            pos += 1
            continue
        if ch == "\\":
            if source[pos + 1:pos + 2] == "\n":
                pos += 2
            else:
                need().text += source[pos + 1:pos + 2]
                pos += 2
            continue
        if ch == "#" and word is None:
            end = source.find("\n", pos)
            pos = length if end == -1 else end
            continue
        if ch in ";&|(){}":
            if ch in "{}" and (word is not None or source[pos + 1:pos + 2] not in (" ", "\t", "\n", "")):
                need().text += ch
                pos += 1
                continue
            flush_command()
            pos += 1
            while pos < length and source[pos] in ";&|":
                pos += 1
            continue
        if ch == "<" and source.startswith("<<", pos) and not source.startswith("<<<", pos):
            match = re.match(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", source[pos:])
            if match:
                heredocs.append((match.group(3), bool(match.group(1))))
                pos += match.end()
                flush_word()
                continue
        if ch == "'":
            end = source.find("'", pos + 1)
            end = length if end == -1 else end
            need().text += source[pos + 1:end]
            pos = end + 1
            continue
        if ch == '"':
            pos += 1
            target = need()
            while pos < length and source[pos] != '"':
                c = source[pos]
                if c == "\\":
                    target.text += source[pos + 1:pos + 2]
                    pos += 2
                elif c == "$" and source.startswith("$(", pos):
                    target.dynamic = True
                    target.text += VAR_MARK
                    pos = read_substitution(pos + 2)
                elif c == "$" and re.match(r"\$(\{|[A-Za-z_0-9@*#?!$-])", source[pos:pos + 2]):
                    target.dynamic = True
                    target.text += VAR_MARK
                    if source[pos + 1] == "{":
                        end = source.find("}", pos)
                        pos = length if end == -1 else end + 1
                    else:
                        m = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*|\$.", source[pos:])
                        pos += len(m.group(0))
                elif c == "`":
                    end = source.find("`", pos + 1)
                    end = length if end == -1 else end
                    if depth < 8:
                        commands.extend(split_commands(source[pos + 1:end], depth + 1, base + source.count("\n", 0, pos)))
                    target.dynamic = True
                    target.text += VAR_MARK
                    pos = end + 1
                else:
                    target.text += c
                    pos += 1
            pos += 1
            continue
        if ch == "$" and source.startswith("$(", pos):
            need().dynamic = True
            word.text += VAR_MARK
            pos = read_substitution(pos + 2)
            continue
        if ch == "$" and re.match(r"\$(\{|[A-Za-z_0-9@*#?!$-])", source[pos:pos + 2]):
            target = need()
            target.dynamic = True
            target.text += VAR_MARK
            if source[pos + 1] == "{":
                end = source.find("}", pos)
                pos = length if end == -1 else end + 1
            else:
                m = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*|\$.", source[pos:])
                pos += len(m.group(0))
            continue
        if ch == "`":
            end = source.find("`", pos + 1)
            end = length if end == -1 else end
            if depth < 8:
                commands.extend(split_commands(source[pos + 1:end], depth + 1, base + source.count("\n", 0, pos)))
            need().dynamic = True
            word.text += VAR_MARK
            pos = end + 1
            continue
        need().text += ch
        pos += 1
    flush_command()
    return commands


def strip_prefix(words):
    """Drop assignments, keywords, and wrapper commands in front of the command word."""
    i = 0
    while i < len(words):
        text = words[i].text
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", text) or text in KEYWORDS:
            i += 1
        elif text in PREFIXES and not words[i].dynamic:
            i += 1
            while i < len(words) and (words[i].text.startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[i].text)):
                i += 1
        else:
            break
    return words[i:]


def request_flags(args, kind):
    """(endpoint Word or None, method literal or None, has_body, method_dynamic, query words) from the arguments of gh api or curl."""
    endpoint, method, body, method_dynamic, queries = None, None, False, False, []
    i = 0
    while i < len(args):
        word = args[i]
        text = word.text
        nxt = args[i + 1] if i + 1 < len(args) else None
        if text in ("-X", "--request", "--method"):
            if nxt is None or nxt.dynamic:
                method_dynamic = True
            else:
                method = nxt.text.upper()
            i += 2
            continue
        if text.startswith("--method=") or text.startswith("--request="):
            value = text.split("=", 1)[1]
            if word.dynamic:
                method_dynamic = True
            else:
                method = value.upper()
            i += 1
            continue
        if re.match(r"^-X.+", text) and kind == "curl":
            method = text[2:].upper()
            i += 1
            continue
        if text in ("-f", "-F", "--field", "--raw-field", "--form", "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--json", "-T", "--upload-file", "--input"):
            body = True
            if nxt is not None:
                queries.append(nxt)
            i += 2
            continue
        if re.match(r"^--(field|raw-field|data|data-raw|data-binary|data-urlencode|json|form|input)=", text):
            body = True
            queries.append(word)
            i += 1
            continue
        if text in ("-H", "--header", "-q", "--jq", "-t", "--template", "--cache", "-o", "--output", "-u", "--user", "--hostname", "--paginate", "-i", "--include", "--silent"):
            i += 2 if text in ("-H", "--header", "-q", "--jq", "-t", "--template", "--cache", "-o", "--output", "-u", "--user", "--hostname") else 1
            continue
        if text.startswith("-"):
            i += 1
            continue
        if endpoint is None and (kind == "gh" or looks_like_endpoint(text) or VAR_MARK in text or text.startswith("http")):
            endpoint = word
        i += 1
    return endpoint, method, body, method_dynamic, queries


class Scanner:
    def __init__(self, data, tracked):
        self.data = data
        self.tracked = tracked
        self.findings = []
        self.scanned = set()

    def add(self, path, line, message):
        self.findings.append((path, line, message))

    # ---- text level (every script and workflow)
    def text_scan(self, path, text, with_paths=False):
        for number, line in enumerate(text.split("\n"), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for name in self.data.graphql_classes(line):
                self.add(path, number, f"forbidden GraphQL mutation ({name})")
            if with_paths:
                line = re.sub(r"\$\{\{.*?\}\}", VAR_MARK, line)
                for match in re.finditer(r"(?:https?://api\.github\.com)?/?(?:repos|orgs|user)/[^\s\"'`)>,;]*", line):
                    token = re.sub(r"\$\{[^}]*\}|\$\([^)]*\)|\$[A-Za-z_][A-Za-z0-9_]*|\{[^}]*\}", VAR_MARK, match.group(0))
                    normalised, error = normalise(token)
                    name = None
                    if normalised:
                        # a workflow expression such as github.repository stands for owner and repository together
                        for candidate in (normalised, normalised.replace("repos/{}/", "repos/{}/{}/", 1)):
                            name = name or self.data.classify_path(candidate)
                    if name:
                        self.add(path, number, f"forbidden REST endpoint ({name})")

    def judge(self, path, line, endpoint, method, has_body, method_dynamic, kind):
        """endpoint: text with VAR_MARK; returns nothing, adds findings."""
        normalised, error = normalise(endpoint)
        if error:
            self.add(path, line, f"the endpoint is not a string literal ({error}); write the endpoint out")
            return
        if normalised == "graphql":
            return
        name = self.data.classify_path(normalised)
        if name is None:
            return
        if method_dynamic:
            self.add(path, line, f"forbidden REST endpoint ({name}) with a method that is not a literal")
            return
        effective = method or ("POST" if has_body else "GET")
        if effective not in WRITE_METHODS_OK:
            self.add(path, line, f"forbidden REST call ({name}, {effective})")

    # ---- shell
    def shell(self, path, text):
        self.text_scan(path, text)
        offsets = []
        for words in split_commands(text):
            words = strip_prefix(words)
            if not words:
                continue
            command = words[0]
            line = command.line
            if command.dynamic:
                # a command named by a variable that is given an API verb cannot be judged
                if any(w.text in ("api", "graphql") for w in words[1:]):
                    self.add(path, line, "the command word is not a literal and is given an API call")
                continue
            name = os.path.basename(command.text)
            args = words[1:]
            if name == "gh":
                self.gh(path, line, args)
            elif name == "curl":
                self.http(path, line, args, "curl")
            elif name == "wget":
                self.http(path, line, args, "wget")
            elif name == "git":
                self.git(path, line, args)
        return offsets

    def gh(self, path, line, args):
        plain = [w for w in args if not w.text.startswith("-")]
        if not plain:
            return
        first = plain[0]
        if first.dynamic:
            self.add(path, line, "the gh subcommand is not a literal")
            return
        if first.text in self.data.gh_groups:
            self.add(path, line, f"forbidden gh command (gh {first.text})")
        elif first.text == "pr" and len(plain) > 1 and not plain[1].dynamic and plain[1].text in self.data.gh_pr:
            self.add(path, line, f"forbidden gh command (gh pr {plain[1].text})")
        elif first.text == "api":
            self.http(path, line, args[[w.text for w in args].index("api") + 1:], "gh")

    def http(self, path, line, args, kind):
        endpoint, method, body, method_dynamic, queries = request_flags(args, kind)
        if endpoint is None:
            if kind == "gh":
                self.add(path, line, "gh api without a literal endpoint")
            return
        if endpoint.text == VAR_MARK:
            self.add(path, line, "the endpoint is not a string literal (the whole endpoint is a variable); write the endpoint out")
            return
        target = endpoint.text
        if kind != "gh" and not looks_like_endpoint(target):
            return
        self.judge(path, line, target, method, body, method_dynamic, kind)
        if target.rstrip("/").split("?", 1)[0].endswith("graphql") or target == "graphql":
            self.graphql_body(path, line, queries)

    def graphql_body(self, path, line, queries):
        for word in queries:
            value = word.text
            key, _, rest = value.partition("=")
            if key in ("query", "q") or value.startswith("query="):
                if rest.startswith("@"):
                    target = rest[1:]
                    if VAR_MARK in target or target not in self.tracked:
                        self.add(path, line, "the GraphQL document is read from a file that is not a tracked literal path")
                    elif target not in self.scanned:
                        self.scan_file(target, from_query=True)
                elif VAR_MARK in rest or word.dynamic:
                    self.add(path, line, "the GraphQL document is not a string literal (it contains a variable)")
                else:
                    for name in self.data.graphql_classes(rest):
                        self.add(path, line, f"forbidden GraphQL mutation ({name})")

    def git(self, path, line, args):
        plain = [w for w in args if not w.text.startswith("-")]
        subcommand = None
        i = 0
        while i < len(args):
            text = args[i].text
            if text in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
                i += 2
                continue
            if text.startswith("-"):
                i += 1
                continue
            subcommand = args[i]
            break
        if subcommand is None:
            return
        if subcommand.dynamic:
            self.add(path, line, "the git subcommand is not a literal")
            return
        rest = [w.text for w in args[i + 1:]]
        if subcommand.text == "tag":
            self.add(path, line, "forbidden git command (git tag)")
        elif subcommand.text == "push" and (any(t in self.data.push_flags for t in rest) or any("refs/tags/" in t for t in rest)):
            self.add(path, line, "forbidden git command (a push that carries tags)")
        elif subcommand.text == "update-ref" and any("refs/tags/" in t for t in rest):
            self.add(path, line, "forbidden git command (a tag ref)")

    # ---- python
    def python(self, path, text):
        self.text_scan(path, text)
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            self.add(path, 1, "the Python file could not be parsed; its calls were not checked")
            return
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)):
                self.py_argv(path, node)
            elif isinstance(node, ast.Call):
                self.py_call(path, node)

    @staticmethod
    def py_text(node):
        """(text with VAR_MARK, literal) of a string expression, or (None, False) when it is not string-like."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value, True
        if isinstance(node, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else VAR_MARK for v in node.values), False
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            left, _ = Scanner.py_text(node.left)
            right, _ = Scanner.py_text(node.right)
            if left is None and right is None:
                return None, False
            return (left if left is not None else VAR_MARK) + (right if right is not None else VAR_MARK), False
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("format", "join"):
            base, _ = Scanner.py_text(node.func.value)
            if base is not None:
                return re.sub(r"\{[^}]*\}|%[sd]", VAR_MARK, base) if node.func.attr == "format" else VAR_MARK, False
        return None, False

    def py_argv(self, path, node):
        items = []
        for element in node.elts:
            text, literal = self.py_text(element)
            items.append((text, literal, element))
        texts = [t for t, _, _ in items]
        line = node.lineno
        if len(texts) >= 2 and texts[0] == "gh" and texts[1] == "api":
            self.py_cli(path, line, items[2:], "gh")
        elif texts and texts[0] == "api" and len(texts) >= 2 and not any(t == "gh" for t in texts):
            self.py_cli(path, line, items[1:], "gh")
        elif texts and texts[0] == "curl":
            self.py_cli(path, line, items[1:], "curl")
        elif len(texts) >= 2 and texts[0] == "gh" and texts[1] in self.data.gh_groups:
            self.add(path, line, f"forbidden gh command (gh {texts[1]})")
        elif len(texts) >= 3 and texts[0] == "gh" and texts[1] == "pr" and texts[2] in self.data.gh_pr:
            self.add(path, line, f"forbidden gh command (gh pr {texts[2]})")
        elif len(texts) >= 2 and texts[0] == "git" and texts[1] == "tag":
            self.add(path, line, "forbidden git command (git tag)")
        elif len(texts) >= 2 and texts[0] == "git" and texts[1] == "push" and any(t in self.data.push_flags or (t and "refs/tags/" in t) for t in texts[2:]):
            self.add(path, line, "forbidden git command (a push that carries tags)")

    def py_cli(self, path, line, items, kind):
        words = []
        for text, literal, _ in items:
            word = Word()
            word.text = text if text is not None else VAR_MARK
            word.dynamic = not literal
            words.append(word)
        self.http(path, line, words, kind)

    def py_call(self, path, node):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
        verbs = ("get", "post", "put", "patch", "delete", "head")
        is_request = (name in ("Request", "urlopen") or (owner in ("requests", "httpx", "session", "client", "http") and name in verbs + ("request",)))
        if not is_request:
            return
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        url_node = node.args[0] if node.args else keywords.get("url")
        if name == "request" and len(node.args) >= 2:
            url_node = node.args[1]
        if url_node is None:
            return
        if isinstance(url_node, ast.Call) and getattr(url_node.func, "attr", getattr(url_node.func, "id", "")) == "Request":
            return  # urlopen(Request(...)): the Request call is judged on its own
        text, literal = self.py_text(url_node)
        if text is None or text == VAR_MARK or (not literal and VAR_MARK in text and not text.replace(VAR_MARK, "")):
            if path in self.data.literal_exempt:
                return
            self.add(path, node.lineno, "the endpoint is not a string literal; write the endpoint out")
            return
        if text.startswith(("http://", "https://")) and not text.startswith(HOSTS):
            return  # a literal URL of another host is not the GitHub API
        if not looks_like_endpoint(text) and not text.startswith("http"):
            return
        method, dynamic = None, False
        if name in verbs:
            method = name.upper()
        elif "method" in keywords:
            value, lit = self.py_text(keywords["method"])
            method, dynamic = (value.upper(), False) if lit else (None, True)
        elif name == "request" and node.args:
            value, lit = self.py_text(node.args[0])
            method, dynamic = (value.upper(), False) if lit else (None, True)
        body = "data" in keywords or "json" in keywords or (name == "Request" and len(node.args) > 1)
        self.judge(path, node.lineno, text, method, body, dynamic, "py")

    # ---- driver
    def scan_file(self, path, from_query=False):
        if path in self.scanned:
            return
        self.scanned.add(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            return
        if from_query:
            self.text_scan(path, text)
            return
        base = os.path.basename(path)
        if path.startswith(".github/workflows/") and base.endswith((".yml", ".yaml")) or path.startswith(".github/actions/"):
            self.text_scan(path, text, with_paths=True)
            return
        first = text.split("\n", 1)[0]
        match = SHEBANG.match(first)
        interp = match.group("interp") if match else ""
        if path.endswith(PY_EXT) or interp.startswith("python"):
            self.python(path, text)
        elif path.endswith(SHELL_EXT) or interp:
            self.shell(path, text)
        elif path.startswith(".github/") or base.endswith((".graphql", ".gql")):
            self.text_scan(path, text, with_paths=True)


def main(argv):
    if len(argv) < 2:
        print("usage: forbidden_api.py DATA_TOML [TRACKED_LIST]", file=sys.stderr)
        return 2
    data = Data(argv[1])
    tracked = set()
    if len(argv) > 2:
        with open(argv[2], encoding="utf-8") as handle:
            tracked = {line.rstrip("\n") for line in handle if line.strip()}
    paths = [line.rstrip("\n") for line in sys.stdin if line.strip()]
    scanner = Scanner(data, tracked or set(paths))
    for path in paths:
        if os.path.isfile(path):
            scanner.scan_file(path)
    for path, line, message in sorted(set(scanner.findings)):
        print(f"{path}:{line}: {message}")
    return 1 if scanner.findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
