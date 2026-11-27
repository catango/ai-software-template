"""Shared builders for the pr-contract tests (standard library only)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(1, str(ROOT / "scripts" / "lib"))

from pr_contract.errors import ApiError, NotFound  # noqa: E402
from pr_contract.model import FileChange, PullRequest  # noqa: E402

TEMPLATES = ROOT / ".github" / "PULL_REQUEST_TEMPLATE"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
TYPES = ("design", "implementation", "change", "template-sync", "init")
OWN_REPO = "example-owner/sample-app"
DESIGN_REPO = "example-owner/design-records"


def template_lines(kind):
    return (TEMPLATES / f"{kind}.md").read_text().splitlines()


def headings(kind):
    return [line[3:].strip() for line in template_lines(kind) if line.startswith("## ")]


def body(kind, *, omit=(), empty=(), comment_only=(), sections=None, top="", marker=True,
         extra="", newline="\n"):
    """A PR body built from the real template: marker comment, then every section filled in."""
    sections = sections or {}
    out = []
    if marker:
        out.append(template_lines(kind)[0])
    if top:
        out.extend(top.rstrip("\n").split("\n"))
        out.append("")
    for heading in headings(kind):
        if heading in omit:
            continue
        out.append(f"## {heading}")
        if heading in comment_only:
            out.append("<!-- Restates the template comment only. -->")
        elif heading in empty:
            out.append("")
        else:
            default = {"Change request": "CR: #2", "Checkpoint": "- [ ] item"}.get(heading, "Filled in for the test.")
            out.append(sections.get(heading, default))
        out.append("")
    if extra:
        out.append(extra)
    return newline.join(out)


IMPL_TOP = "Implements: #2\nDesign: #3\n"
HEAD_SHA = "a1" * 20
BASE_SHA = "b2" * 20
GATE_COMMAND = "./scripts/quality-gate.sh --strict"
HEADS = {
    "design": "design/CR-2-thing", "implementation": "impl/CR-2-thing", "change": "change/1-thing",
    "template-sync": "template-sync/v1.2.0", "init": "init/v1.0.0",
}


def evidence_line(arch="arm64", machine="aarch64", os_name="ubuntu 24.04", sha=HEAD_SHA, state="clean",
                  command=GATE_COMMAND, code="0", time="2026-11-23T10:15:00Z"):
    """One evidence line, every field replaceable."""
    return f"evidence: {arch} | {machine} | {os_name} | {sha} | {state} | {command} | exit {code} | {time}"


def make_pr(kind="implementation", **over):
    base = dict(
        number=10, title="A title", body="", state="open", draft=True, merged=False,
        author="example-author", head_ref=HEADS["implementation"], base_ref="main", changed_files=1,
        head_sha=HEAD_SHA, base_sha=BASE_SHA,
    )
    base.update(head_ref=HEADS[kind], body=body(kind, top=IMPL_TOP if kind == "implementation" else ""))
    base.update(over)
    return PullRequest(**base)


def fc(path, status="modified", previous=None, patch=None):
    return FileChange(path=path, status=status, previous=previous, patch=patch)


OPERATOR_PATHS_SAMPLE = ["scripts/operator/", "ansible/operator.yml", "ansible/roles/operator_*/", "toolchain/python/operator.lock"]
ARCHITECTURES_SAMPLE = [{"name": "arch-a", "runner": "runner-a", "runner_private": "runner-a"}]
DEFAULT_CONFIG = object()  # MemClient(config=DEFAULT_CONFIG): a base configuration that lists everything the rules read


def base_config(paths=None, repos=None, architectures=None, **more):
    """A parsed base configuration, in the shape of config.parse_yaml. The defaults make PC-3, PC-8 and PC-9 active but satisfied."""
    out = {
        "workflow": {
            "operator_paths": list(OPERATOR_PATHS_SAMPLE) if paths is None else paths,
            "design_repos": [DESIGN_REPO] if repos is None else repos,
        },
        "architectures": list(ARCHITECTURES_SAMPLE) if architectures is None else architectures,
    }
    out.update(more)
    return out


operator_config = base_config  # the name the ported PC-8 tests use


class MemClient:
    """In-memory stand-in for the GitHub client.

    `config` is the parsed base configuration: a dict, None (the file does not exist at the base), or an exception instance
    that base_config raises (an unreadable file). The default lists the operator paths of the sample, so PC-8 is active.
    `base_files` and `head_files` map a path to the text of a file at the base and at the head commit.
    `foreign` maps owner/name to {"prs": {...}, "issues": {...}} of another repository; a repository not in it is not readable."""

    repo = OWN_REPO

    def __init__(self, prs=None, files=None, issues=None, fail=None, config=DEFAULT_CONFIG, private=False,
                 base_files=None, head_files=None, foreign=None):
        design = make_pr("design", number=3, state="closed", draft=False, merged=True)
        self.prs = {3: design}
        self.prs.update(prs or {})
        self.files = files or {}
        self.issues = {2: "issue"}
        self.issues.update(issues or {})
        self.fail = fail
        self.config = base_config() if config is DEFAULT_CONFIG else config
        self.private = private
        self.base_files = base_files or {}
        self.head_files = head_files or {}
        self.foreign = foreign or {}
        self.calls = []

    def _check(self, name, number):
        self.calls.append((name, number))
        if self.fail is not None:
            raise self.fail

    def pull(self, number, repo=None):
        self._check("pull", number if repo is None else (repo, number))
        table = self.prs if repo is None else self._foreign(repo)["prs"]
        if number not in table:
            raise NotFound(f"pull {number}")
        return table[number]

    def pull_files(self, number):
        self._check("pull_files", number)
        return list(self.files.get(number, []))

    def issue_kind(self, number, repo=None):
        self._check("issue_kind", number if repo is None else (repo, number))
        table = self.issues if repo is None else self._foreign(repo).get("issues", {})
        if number not in table:
            raise NotFound(f"issue {number}")
        return table[number]

    def _foreign(self, repo):
        if repo not in self.foreign:
            raise NotFound(f"repository {repo}")
        return self.foreign[repo]

    def repo_readable(self, repo):
        self._check("repo_readable", repo)
        self._foreign(repo)

    def repo_private(self):
        self._check("repo_private", None)
        return self.private

    def base_config(self, sha):
        """The parsed configuration of the base commit, or None when the file does not exist there."""
        self._check("base_config", sha)
        if isinstance(self.config, Exception):
            raise self.config
        return self.config

    def file_text(self, sha, path):
        self._check("file_text", (sha, path))
        files = {BASE_SHA: self.base_files, HEAD_SHA: self.head_files}.get(sha, {})
        return files.get(path)


__all__ = ["ApiError", "NotFound", "ROOT", "TEMPLATES", "FIXTURES", "TYPES", "headings", "body", "make_pr", "fc",
           "MemClient", "IMPL_TOP", "template_lines", "HEAD_SHA", "BASE_SHA", "GATE_COMMAND", "evidence_line",
           "OPERATOR_PATHS_SAMPLE", "DEFAULT_CONFIG", "operator_config", "base_config", "OWN_REPO", "DESIGN_REPO"]
