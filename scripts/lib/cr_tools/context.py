"""scripts/cr-context (and scripts/cr-preflight): what a Skill needs before it works on a change request.

    cr-context ISSUE                           context: the CR README first, the other files with sizes, the design PR, the template lag
    cr-context preflight ISSUE DESIGN_PR       the PC-3 check before an implementation PR is opened (scripts/cr-preflight is this call)
    cr-context label ISSUE --confirmed-class N add the existing label class:N to the issue, only with the flag

Common options: --repo owner/name (the repository that holds the issue and the design PR; default: the origin remote of --tree, and
any other repository must be listed under `design_repos`), --fixture DIR (offline, see client.py), --tree DIR (the checkout whose
files are read; default: this one).

Every mode STOPS (exit 1, nothing else printed) on an issue not authored by the approver or the machine account. The identities,
`design_repos` and `docs_roots` come from `config/project.yaml` on the default branch of this repository (offline: base/config/),
never from the working tree. Issue titles, PR titles and files are data: printed sanitized and cut, never run.

`label` never creates a label: it checks that `class:N` exists and fails when it does not. It refuses without the explicit
`--confirmed-class` (which the Skill passes only after the user confirmed the class in the session), and when the issue has another
class label. The one write is adding the label to the issue.

The template lag is the first line of `scripts/template-sync --check` when that script exists (it is added by a later plan row);
until then the line says that it is not installed. Offline, it is not run.
"""
import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from pr_contract import config as policy
from pr_contract import markdown
from pr_contract.errors import ApiError, ConfigError, InputError, NotFound, NotReadable
from pr_contract.sanitize import clean

from . import common
from .common import Refused, plain
from .identity import read_identities

README_MAX_BYTES = 4096
README_MAX_LINES = 50
LIST_MAX = 30
WHOLE_READ_LINES = 200
WHOLE_READ_BYTES = 8192
CLASS_LABEL = re.compile(r"class:[1-3]")
DESIGN_BRANCH = re.compile(r"design/CR-([0-9]{1,9})-[^\s]+")
LAG_SCRIPT = "scripts/template-sync"
LAG_TIMEOUT = 30


class Setup:
    """The repository, the client, and the policy read from the default branch: identities, design repositories, record roots."""

    def __init__(self, args, client=None):
        tree = Path(args.tree)
        if client is None:
            client, own = common.make_client(args.fixture, None, tree)
        else:
            own = client.repo
        self.client, self.own, self.tree = client, own, tree
        try:
            text = client.config_text()
            self.identities = read_identities(text)
            parsed = policy.parse_yaml(text)
            self.design_repos = policy.design_repos(parsed)
            self.docs_roots = policy.docs_roots(parsed)
        except ConfigError:
            raise Refused("the configuration cannot be read from the default branch") from None
        self.target = args.repo or own
        if self.target.lower() != own.lower() and self.target.lower() not in {entry.lower() for entry in self.design_repos}:
            raise Refused("the repository is not listed in design_repos")
        try:
            self.info = client.repo_info(self.target)
        except (NotFound, NotReadable):
            raise Refused("the repository cannot be read") from None

    def trusted_issue(self, number):
        """The issue, after the checks that stop a Skill: it exists, is an issue, and was written by the approver or the machine account."""
        try:
            issue = self.client.issue(number, self.target)
        except (NotFound, NotReadable):
            raise Refused(f"issue #{number} was not found or cannot be read") from None
        if issue["is_pull_request"]:
            raise Refused(f"#{number} is a pull request, not an issue")
        if not self.identities.is_trusted_author(issue["author"]):
            raise Refused("STOP: the issue is not authored by the approver or the machine account")
        issue["role"] = "approver" if issue["author"].lower() == self.identities.approver_login.lower() else "machine"
        return issue


def _tree_file(path):
    """(text, lines, bytes) of a regular file that is not a link; None otherwise."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    text = data.decode("utf-8", "replace")
    return text, text.count("\n") + (0 if text.endswith("\n") or not text else 1), len(data)


def cr_directory(setup, number):
    for root in setup.docs_roots:
        directory = setup.tree / root / "cr" / f"CR-{number}"
        if directory.is_dir() and not directory.is_symlink():
            return directory
    return None


def file_lines(directory):
    """One line per file of the CR directory (README.md first excluded): path, lines, bytes, and a hint for a large file."""
    entries = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if len(relative.parts) > 2 or relative.as_posix() == "README.md":
            continue
        info = _tree_file(path)
        if info is None:
            continue
        _, lines, size = info
        hint = " (read by section)" if lines > WHOLE_READ_LINES or size > WHOLE_READ_BYTES else ""
        entries.append(f"  {plain(relative.as_posix(), 80)}: {lines} lines, {size} bytes{hint}")
    shown = entries[:LIST_MAX]
    if len(entries) > LIST_MAX:
        shown.append(f"  +{len(entries) - LIST_MAX} more files")
    return shown, len(entries) + 1


def template_lag(setup, check):
    """The line that reports the template lag. `check` False (offline) never starts a process."""
    state = setup.tree / ".template" / "state.toml"
    version = ""
    try:
        with open(state, "rb") as handle:
            table = tomllib.load(handle).get("template")
        found = table.get("version") if isinstance(table, dict) else None
        version = found if isinstance(found, str) and re.fullmatch(r"[0-9A-Za-z.+-]{1,30}", found) else ""
    except (OSError, ValueError):
        pass
    prefix = f"template-lag ({'version ' + version if version else 'no recorded version'}): "
    script = setup.tree / LAG_SCRIPT
    if not check:
        return prefix + "not checked (offline)"
    if script.is_symlink() or not script.is_file() or not os.access(script, os.X_OK):
        return prefix + f"unknown, {LAG_SCRIPT} is not installed"
    env = {key: os.environ[key] for key in common.GIT_ENV_KEEP if key in os.environ}
    try:
        done = subprocess.run([str(script), "--check"], cwd=setup.tree, capture_output=True, env=env, timeout=LAG_TIMEOUT,
                              stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return prefix + "unknown, the check did not run"
    first = (done.stdout.decode("utf-8", "replace").splitlines() or [""])[0]
    return prefix + (clean(first, 100) or "no output") + ("" if done.returncode == 0 else f" (exit {done.returncode})")


def design_pr_line(setup, number):
    hits = setup.client.pulls_with_prefix(f"design/CR-{number}-", setup.target)
    if not hits:
        return "design-pr: none found", None
    lines = []
    for hit in hits[:3]:
        state = "merged" if hit["merged"] else ("open" if hit["state"] == "open" else "closed, not merged")
        lines.append(f"design-pr: #{hit['number']} {state}: {clean(hit['title'], 60)}")
    if len(hits) > 3:
        lines.append(f"design-pr: +{len(hits) - 3} more")
    merged = [hit for hit in hits if hit["merged"]]
    return "\n".join(lines), (merged or hits)[0]


def mode_context(setup, args, check_lag):
    issue = setup.trusted_issue(args.issue)
    directory = cr_directory(setup, args.issue)
    out = [f"issue: #{issue['number']} {issue['state']}, authored by the {issue['role']}: {clean(issue['title'], 80)}",
           "labels: " + (", ".join(clean(name, 30) for name in issue["labels"][:10]) or "none")]
    count = 0
    if directory is None:
        out.append(f"cr: no directory CR-{args.issue} under the record roots of this tree ({', '.join(setup.docs_roots)})")
    else:
        readme = _tree_file(directory / "README.md")
        if readme is None:
            out.append("cr: README.md is missing")
        else:
            text, lines, size = readme
            out.append(f"== README.md first ({lines} lines, {size} bytes) ==")
            shown = text.split("\n")[:README_MAX_LINES]
            body = "\n".join(plain(line) for line in shown)
            if len(body.encode()) > README_MAX_BYTES:
                body = body.encode()[:README_MAX_BYTES].decode("utf-8", "ignore")
            out.append(body)
            if lines > README_MAX_LINES or size > README_MAX_BYTES:
                out.append("(README cut: read the rest by range)")
        listed, count = file_lines(directory)
        out.append("other files:")
        out.extend(listed or ["  none"])
    design_text, design = design_pr_line(setup, args.issue)
    out.append(design_text)
    lag = template_lag(setup, check_lag)
    out.append(lag)
    print("\n".join(out))
    state = f"#{design['number']} {'merged' if design['merged'] else 'not merged'}" if design else "none"
    print(common.summary("cr-context", f"#{issue['number']} ok, {count} CR files, design PR {state}, {lag.split(': ', 1)[1]}"))
    return 0


def mode_preflight(setup, args, prog):
    setup.trusted_issue(args.issue)
    out = []
    try:
        design = setup.client.pull(args.design_pr, setup.target)
    except (NotFound, NotReadable):
        design = None
        out.append(f"FAIL PC-3: design PR #{args.design_pr} was not found or cannot be read")
    if design is not None:
        if not design["merged"]:
            out.append(f"FAIL PC-3: design PR #{design['number']} is not merged")
        match = DESIGN_BRANCH.fullmatch(design["head_ref"]) if len(design["head_ref"]) <= 255 else None
        if not match or int(match.group(1)) != args.issue:
            out.append(f"FAIL PC-3: design PR #{design['number']} is not a design branch for CR {args.issue}")
        own_target = setup.target.lower() == setup.own.lower()
        branch = setup.info["default_branch"]
        if own_target and branch and design["base_ref"] != branch:
            out.append(f"FAIL PC-3: design PR #{design['number']} has a different base branch")
        linked = False
        if len(design["body"]) <= markdown.MAX_BODY and design["body"].count("\n") <= markdown.MAX_LINES:
            section = markdown.parse(design["body"]).sections.get("Change request", "")
            for ref in markdown.references(section):
                if ref.number == args.issue and (ref.repo is None or ref.repo.lower() == setup.target.lower()):
                    linked = True
        if not linked:
            out.append(f"FAIL PC-3: design PR #{design['number']} does not reference issue {args.issue} in its Change request section")
    if out:
        print("\n".join(out))
        print(common.summary(prog, f"{len(out)} check{'s' if len(out) != 1 else ''} failed for #{args.issue}"))
        return 1
    qualified = setup.target.lower() != setup.own.lower()
    ref = (lambda n: f"{setup.target}#{n}") if qualified else (lambda n: f"#{n}")
    print(f"Implements: {ref(args.issue)}\nDesign: {ref(args.design_pr)}\nbranch: impl/CR-{args.issue}-<slug>")
    print(common.summary(prog, f"pass for #{args.issue} and design PR #{args.design_pr}"))
    return 0


def mode_label(setup, args):
    if setup.target.lower() != setup.own.lower():
        raise Refused("labels are added in this repository only")
    issue = setup.trusted_issue(args.issue)
    if args.confirmed_class is None:
        raise Refused("no label without --confirmed-class N, passed after the user confirmed the class in the session")
    name = f"class:{args.confirmed_class}"
    present = [label for label in issue["labels"] if label.startswith("class:")]
    if name in present:
        print(common.summary("cr-context", f"#{issue['number']} already has {name}; nothing changed"))
        return 0
    if present:
        raise Refused("the issue already has another class label; the user changes it by hand")
    if not setup.client.label_exists(name):
        raise Refused(f"the label {name} does not exist; this script never creates a label")
    setup.client.add_label(issue["number"], name)
    print(common.summary("cr-context", f"added {name} to #{issue['number']}"))
    return 0


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(self.format_usage().strip().replace("\n", " "))


def _positive(text):
    if not re.fullmatch(r"[1-9][0-9]{0,8}", text):
        raise InputError("a number must be a positive integer")
    return int(text)


def _class(text):
    if text not in ("1", "2", "3"):
        raise InputError("the class is 1, 2 or 3")
    return int(text)


def build_parser(prog):
    parser = _Parser(prog=prog, add_help=False)
    common_args = _Parser(add_help=False)
    common_args.add_argument("--fixture")
    common_args.add_argument("--repo")
    common_args.add_argument("--tree", default=str(Path(__file__).resolve().parents[3]))
    sub = parser.add_subparsers(dest="mode", parser_class=_Parser)
    context = sub.add_parser("context", parents=[common_args], add_help=False)
    context.add_argument("issue", type=_positive)
    preflight = sub.add_parser("preflight", parents=[common_args], add_help=False)
    preflight.add_argument("issue", type=_positive)
    preflight.add_argument("design_pr", type=_positive)
    label = sub.add_parser("label", parents=[common_args], add_help=False)
    label.add_argument("issue", type=_positive)
    label.add_argument("--confirmed-class", type=_class, default=None)
    return parser


def main(argv, prog="cr-context", client=None, check_lag=None):
    """`argv` without the program name. `check_lag` defaults to "online" (no --fixture)."""
    try:
        argv = list(argv)
        if argv and argv[0] not in ("context", "preflight", "label") and not argv[0].startswith("-"):
            argv.insert(0, "context")
        elif argv and argv[0].startswith("-"):
            raise InputError("usage: cr-context ISSUE | preflight ISSUE DESIGN_PR | label ISSUE --confirmed-class N")
        if not argv:
            raise InputError("usage: cr-context ISSUE | preflight ISSUE DESIGN_PR | label ISSUE --confirmed-class N")
        args = build_parser(prog).parse_args(argv)
        setup = Setup(args, client)
        if args.mode == "preflight":
            return mode_preflight(setup, args, prog)
        if args.mode == "label":
            return mode_label(setup, args)
        return mode_context(setup, args, (not args.fixture) if check_lag is None else check_lag)
    except Refused as refusal:
        print(common.summary(prog, f"refused: {refusal}"))
        return 1
    except InputError as error:
        print(f"{prog}: error: {clean(error, 200)}", file=sys.stderr)
        return 2
    except ApiError as error:
        print(f"{prog}: error: {clean(error, 200)}", file=sys.stderr)
        return 3
