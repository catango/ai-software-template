"""Builders for the Skill-script tests (standard library only): offline fixture directories and a small git repository.

Logins and IDs are made-up values. Nothing here is identifier-shaped, so no fragments are needed.
"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(1, str(ROOT / "scripts" / "lib"))

from cr_tools import checkpoint, context, criteria  # noqa: E402
from cr_tools.client import FixtureClient, GhClient  # noqa: E402
from pr_contract.errors import ApiError  # noqa: E402,F401

REPO = "example-owner/sample-app"
DESIGN_REPO = "example-owner/design-records"
APPROVER, MACHINE, STRANGER = "example-approver", "example-bot", "example-stranger"
HEAD_SHA, BASE_SHA = "a1" * 20, "b2" * 20
NODE_ID = "PR_example_node_1"

CONFIG = """schema_version: 1
github:
  owner: "example-owner"
  approver_login: "example-approver"
  approver_id: 1001
  machine_login: "example-bot"
workflow:
  design_repos:
    - "example-owner/design-records"
  docs_roots:
    - "docs/"
  operator_paths: []
  operator_commands: []
"""


def implementation_body(*, ticked=0, newline="\n", extra_after=""):
    """The real implementation template as a body: references filled in, `ticked` checklist items ticked."""
    text = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE" / "implementation.md").read_text(encoding="utf-8")
    text = text.replace("Implements: #<issue>", "Implements: #40").replace("Design: #<merged design PR>", "Design: #41")
    lines = text.split("\n")
    count = 0
    for index, line in enumerate(lines):
        if line.startswith("- [ ] ") and count < ticked:
            lines[index] = "- [x] " + line[6:]
            count += 1
    return newline.join(lines) + extra_after


def pr_json(number, *, author=MACHINE, head_ref="impl/CR-40-thing", base_ref="main", body=None, draft=True, merged=False,
            state=None, head_sha=HEAD_SHA, base_sha=BASE_SHA, node_id=None, title="A pull request", merged_at=None):
    return {
        "number": number, "title": title, "body": body if body is not None else implementation_body(), "state": state or ("closed" if merged else "open"),
        "draft": draft, "merged": merged, "merged_at": merged_at, "user": {"login": author}, "head": {"ref": head_ref, "sha": head_sha},
        "base": {"ref": base_ref, "sha": base_sha}, "node_id": node_id or f"PR_example_node_{number}",
    }


def issue_json(number, *, author=APPROVER, title="CR: an example", state="open", labels=(), is_pr=False):
    data = {"number": number, "title": title, "state": state, "user": {"login": author}, "labels": [{"name": name} for name in labels]}
    if is_pr:
        data["pull_request"] = {"url": "x"}
    return data


def design_body(issue=40):
    return f"## Summary\nA design.\n\n## Change request\nCR: #{issue}\n\n## Class\n3\n"


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def make_fixture(directory, *, config=CONFIG, labels=("class:1", "class:2", "class:3"), private=False):
    """The own repository: configuration of the base commit, repository data, an approver issue 40, a merged design PR 41."""
    directory = pathlib.Path(directory)
    write(directory / "base" / "config" / "project.yaml", config)
    write(directory / "repo.json", {"private": private, "default_branch": "main"})
    write(directory / "labels.json", [{"name": name} for name in labels])
    write(directory / "issue-40.json", issue_json(40))
    design = pr_json(41, head_ref="design/CR-40-thing", body=design_body(), draft=False, merged=True, merged_at="2026-01-01T00:00:00Z", title="Design of the thing")
    write(directory / "pr-41.json", design)
    write(directory / "pulls.json", [design])
    return directory


def add_pr(directory, number, **kwargs):
    """Add a pull request to the fixture and to its list."""
    directory = pathlib.Path(directory)
    data = pr_json(number, **kwargs)
    write(directory / f"pr-{number}.json", data)
    listing = json.loads((directory / "pulls.json").read_text())
    write(directory / "pulls.json", listing + [data])
    return data


class Case(unittest.TestCase):
    """A temporary directory per test, with the fixture and a tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        self.fix = make_fixture(self.tmp / "fixture")
        self.tree = self.tmp / "tree"
        self.tree.mkdir()

    def client(self, repo=REPO, directory=None):
        return FixtureClient(directory or self.fix, repo)

    def stored(self, name):
        return json.loads((self.fix / name).read_text())


def capture(function, *args, **kwargs):
    """(exit code, stdout, stderr) of a call that prints."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = function(*args, **kwargs)
    return code, out.getvalue(), err.getvalue()


def run_checkpoint(case, *argv, client=None, tree=None):
    base = ["--tree", str(tree or case.tree)]
    if client is None:
        base += ["--fixture", str(case.fix), "--repo", REPO]
    return capture(checkpoint.main, [*argv, *base], client=client)


def run_context(case, *argv, client=None, check_lag=None, prog="cr-context"):
    base = ["--tree", str(case.tree)]
    if client is None:
        base += ["--fixture", str(case.fix)]
    return capture(context.main, [*argv, *base], prog=prog, client=client, check_lag=check_lag)


def run_criteria(*argv):
    return capture(criteria.main, list(argv))


def git(tree, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="noreply",
               GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="noreply")
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()


def make_repo(tree, subjects=("first", "second", "third")):
    """A git repository: one base commit on main (also origin/main), then one commit per subject on a branch. Returns (base sha, head sha)."""
    tree = pathlib.Path(tree)
    git(tree, "init", "-q", "-b", "main")
    write(tree / "f.txt", "0\n")
    git(tree, "add", "f.txt")
    git(tree, "commit", "-q", "-m", "base")
    base = git(tree, "rev-parse", "HEAD")
    git(tree, "update-ref", "refs/remotes/origin/main", base)
    git(tree, "switch", "-q", "-c", "impl/CR-40-thing")
    for number, subject in enumerate(subjects, 1):
        write(tree / "f.txt", f"{number}\n")
        git(tree, "commit", "-q", "-a", "-m", subject)
    return base, git(tree, "rev-parse", "HEAD")


class FakeRunner:
    """Replaces `gh`: records every argument array and its stdin, and answers from a table of (substring of the endpoint) -> JSON."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __call__(self, argv, data=None):
        self.calls.append((list(argv), data))
        text = " ".join(argv)
        for key, value in self.answers.items():
            if key in text:
                if isinstance(value, tuple):
                    return value
                return 0, json.dumps(value).encode(), b""
        return 1, b"", b"gh: Not Found (HTTP 404)"

    def writes(self):
        return [call for call in self.calls if "-X" in call[0] or "graphql" in call[0] or "--input" in call[0]]


def copy_tree_file(source, tree, relative):
    target = pathlib.Path(tree) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
