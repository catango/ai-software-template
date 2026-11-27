"""The git and GitHub steps around a sync, for the project's own repository: the preconditions, the branch, the commit, the push of that
branch, and the draft pull request. Every call is an argument array (no shell). The one write to GitHub is `POST repos/<repo>/pulls` with
`draft: true`; the script never merges, never pushes the default branch, and never touches a tag."""
import json
import os
import re
import subprocess
from pathlib import Path

from pr_contract import api as base_api

from .errors import Refused, SourceError

LOCAL_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_AUTHOR_NAME",
                  "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE", "SSH_AUTH_SOCK",
                  "XDG_CONFIG_HOME", "GIT_SSH_COMMAND")
TIMEOUT = 120
_BRANCH = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]{0,99}")


def _spawn(argv, cwd, env, data=None):
    try:
        done = subprocess.run(argv, cwd=cwd, env=env, input=data, capture_output=True, timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise SourceError(f"{argv[0]} timed out") from None
    except OSError:
        raise SourceError(f"cannot run {argv[0]}") from None
    return done.returncode, done.stdout


class Repo:
    """The project's repository. `runner(argv, cwd, env, data)` replaces the process call in tests."""

    def __init__(self, tree, runner=None):
        self.tree = Path(tree)
        self._runner = runner or _spawn

    def _env(self):
        env = {key: os.environ[key] for key in LOCAL_ENV_KEEP if key in os.environ}
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def git(self, *args, ok=(0,), data=None):
        status, out = self._runner(["git", *args], str(self.tree), self._env(), data)
        if status not in ok:
            raise SourceError(f"git {args[0]} failed")
        return status, out.decode("utf-8", "replace")

    def text(self, *args, ok=(0,)):
        return self.git(*args, ok=ok)[1].strip()

    # ---- preconditions
    def check_work_tree(self):
        status, out = self.git("rev-parse", "--show-toplevel", ok=(0, 128))
        if status != 0 or Path(out.strip()).resolve() != self.tree.resolve():
            raise Refused("the tree is not the top of a git work tree")

    def check_clean(self):
        if self.git("status", "--porcelain", "--untracked-files=all", "-z")[1]:
            raise Refused("the working tree is not clean: commit, stash, or remove your changes first")

    def current_branch(self):
        status, out = self.git("symbolic-ref", "--short", "-q", "HEAD", ok=(0, 1))
        return out.strip() if status == 0 else None

    def default_branch(self):
        status, out = self.git("symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD", ok=(0, 1))
        if status == 0 and out.strip().startswith("origin/") and _BRANCH.fullmatch(out.strip()[7:]):
            return out.strip()[7:]
        for name in ("main", "master"):
            if self.git("rev-parse", "--verify", "-q", f"refs/heads/{name}", ok=(0, 1))[0] == 0:
                return name
        raise Refused("cannot tell the default branch; set it with `git remote set-head origin --auto`")

    def check_on_default_branch(self):
        default, current = self.default_branch(), self.current_branch()
        if current != default:
            raise Refused(f"start from the default branch ({default}): the current branch is {current or 'a detached HEAD'}")
        return default

    def check_branch_free(self, branch):
        if self.git("rev-parse", "--verify", "-q", f"refs/heads/{branch}", ok=(0, 1))[0] == 0:
            raise Refused(f"the branch {branch} already exists")

    def check_remote_state(self, default, branch):
        """Online only: the default branch equals origin's, and the sync branch is not on origin."""
        self.git("fetch", "-q", "origin", f"+refs/heads/{default}:refs/remotes/origin/{default}")
        if self.text("rev-parse", "HEAD") != self.text("rev-parse", f"refs/remotes/origin/{default}"):
            raise Refused(f"{default} differs from origin/{default}: bring it level first")
        if self.text("ls-remote", "--heads", "origin", f"refs/heads/{branch}"):
            raise Refused(f"the branch {branch} already exists on origin")

    # ---- the branch and the commit
    def start_branch(self, branch):
        self.git("switch", "-q", "-c", branch)

    def commit(self, paths, message):
        self.git("add", "-A", "--", *paths)
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", message)
        return self.text("rev-parse", "HEAD")

    def rollback(self, default, branch, added):
        """Back to a clean default branch after a failure: the tree was clean, so every change is the sync's own."""
        self.git("reset", "-q", "--hard", "HEAD", ok=(0, 128))
        for path in added:
            self.git("clean", "-f", "-d", "-q", "--", path, ok=(0, 128))
        self.git("switch", "-q", default, ok=(0, 128))
        self.git("branch", "-D", "-q", branch, ok=(0, 1, 128))

    # ---- GitHub
    def push(self, branch):
        self.git("push", "-q", "origin", f"refs/heads/{branch}:refs/heads/{branch}")

    def repository(self):
        url = self.text("config", "--get", "remote.origin.url", ok=(0, 1))
        match = re.fullmatch(r"(?:[A-Za-z][A-Za-z0-9+.-]*://[^/]+/|[^/@\s]+@[^:/\s]+:)(?P<repo>[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?", url)
        if not match:
            raise Refused("cannot read the repository from the origin remote")
        return base_api.check_repo(match.group("repo"))

    def open_pull_request(self, repo, branch, base, title, body, runner=None):
        """The one write to GitHub: a draft pull request. Returns its URL."""
        payload = json.dumps({"title": title, "head": branch, "base": base, "body": body, "draft": True}).encode()
        env = {key: os.environ[key] for key in base_api._ENV_KEEP if key in os.environ}
        env.update({"GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
        owner, name = repo.split("/")
        status, out = (runner or self._runner)(["gh", "api", "-X", "POST", f"repos/{owner}/{name}/pulls", "--input", "-"], str(self.tree), env, payload)
        if status != 0:
            raise SourceError("the draft pull request was not opened")
        try:
            url = json.loads(out.decode("utf-8")).get("html_url", "")
        except (ValueError, AttributeError):
            url = ""
        return url if isinstance(url, str) and re.fullmatch(r"https://[A-Za-z0-9./_-]{1,200}", url) else ""
