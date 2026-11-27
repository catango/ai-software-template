"""Read-only GitHub access through `gh api` (argument arrays, never a shell) and an offline fixture client.

Every call is a GET with an endpoint written out in this file (a variable stands only for a whole path segment), so the gate
check `forbidden-api` can read it. The token is never read here: `gh` takes it from its own environment. Error messages are
fixed text. Both clients have the same interface:

    pull(number, repo=None)      issue_kind(number, repo=None)      repo_readable(repo)
    pull_files(number)           repo_private()
    base_config(sha)             file_text(sha, path)
"""
import base64
import binascii
import json
import os
import re
import subprocess
from pathlib import Path

from . import config
from .errors import ApiError, InputError, LimitExceeded, NotFound, NotReadable
from .model import FileChange, PullRequest

MAX_FILES = 3000
PAGE_SIZE = 100
MAX_PAGES = MAX_FILES // PAGE_SIZE
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
TIMEOUT_SECONDS = 30
MAX_TEXT_BYTES = config.MAX_TEXT * 4

_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,6}")
_STATUS = re.compile(r"\(HTTP ([0-9]{3})\)")
_ENV_KEEP = ("PATH", "HOME", "GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_CONFIG_DIR", "XDG_CONFIG_HOME", "LANG", "LC_ALL")


def check_repo(repo):
    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or any(part in (".", "..") for part in repo.split("/")):
        raise InputError("invalid --repo (expected owner/name)")
    return repo


def check_number(number):
    if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= 999_999_999:
        raise InputError("invalid pull request or issue number")
    return number


def check_sha(sha):
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ApiError("malformed response")
    return sha


def check_path(path):
    if not isinstance(path, str) or not _PATH.fullmatch(path) or ".." in path.split("/"):
        raise InputError("invalid file path")
    return path


def parse_files(data):
    if not isinstance(data, list):
        raise ApiError("malformed response")
    return [FileChange.from_api(item) for item in data]


def parse_private(data):
    private = data.get("private") if isinstance(data, dict) else None
    if not isinstance(private, bool):
        raise ApiError("malformed response")
    return private


def decode_contents(data):
    """The text of a file from the contents API; anything but a small base64 file is malformed."""
    if not isinstance(data, dict) or data.get("type") != "file" or data.get("encoding") != "base64":
        raise ApiError("malformed response")
    content, size = data.get("content"), data.get("size", 0)
    if not isinstance(content, str) or not isinstance(size, int) or isinstance(size, bool) or size > MAX_TEXT_BYTES:
        raise ApiError("malformed response")
    if len(content) > MAX_TEXT_BYTES * 2:
        raise ApiError("response too large")
    try:
        raw = base64.b64decode(content.replace("\n", ""), validate=True)
        if len(raw) > MAX_TEXT_BYTES:
            raise ValueError
        return raw.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise ApiError("malformed response") from None


class GhClient:
    """The repository `repo` through `gh api`. `runner` replaces the process call in tests."""

    def __init__(self, repo, runner=None, timeout=TIMEOUT_SECONDS):
        self.repo = check_repo(repo)
        self._runner = runner or self._spawn
        self._timeout = timeout

    def _spawn(self, argv):
        env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
        env.update({"GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
        try:
            done = subprocess.run(argv, capture_output=True, env=env, timeout=self._timeout, check=False)
        except FileNotFoundError:
            raise ApiError("the gh command is not available") from None
        except subprocess.TimeoutExpired:
            raise ApiError("timeout") from None
        except OSError as error:
            raise ApiError(f"cannot run gh ({type(error).__name__})") from None
        return done.returncode, done.stdout, done.stderr

    def _get(self, argv):
        code, out, err = self._runner(argv)
        if code != 0:
            self._raise_for_status(err)
        if len(out) > MAX_RESPONSE_BYTES:
            raise ApiError("response too large")
        try:
            return json.loads(out.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ApiError("malformed response") from None

    @staticmethod
    def _raise_for_status(err):
        text = err.decode("utf-8", "replace") if isinstance(err, bytes) else str(err)
        found = _STATUS.search(text)
        code = int(found.group(1)) if found else 0
        lowered = text.lower()
        if code == 404:
            raise NotFound("not found")
        if code in (401, 403) and "rate limit" not in lowered and "abuse" not in lowered:
            raise NotReadable("not readable")
        if code == 429 or "rate limit" in lowered:
            raise ApiError(f"rate limited (HTTP {code or 429})")
        raise ApiError(f"HTTP {code}" if code else "gh failed")

    def pull(self, number, repo=None):
        check_number(number)
        target = check_repo(repo or self.repo)
        data = self._get(["gh", "api", f"repos/{target}/pulls/{number}"])
        pr = PullRequest.from_api(data)
        if pr.number != number:
            raise ApiError("malformed response")
        return pr

    def pull_files(self, number):
        check_number(number)
        files = []
        for page in range(1, MAX_PAGES + 1):
            batch = parse_files(self._get(["gh", "api", f"repos/{self.repo}/pulls/{number}/files?per_page={PAGE_SIZE}&page={page}"]))
            files.extend(batch)
            if len(batch) < PAGE_SIZE:
                return files
        raise LimitExceeded("file limit reached")

    def issue_kind(self, number, repo=None):
        check_number(number)
        target = check_repo(repo or self.repo)
        data = self._get(["gh", "api", f"repos/{target}/issues/{number}"])
        if not isinstance(data, dict):
            raise ApiError("malformed response")
        return "pull_request" if "pull_request" in data else "issue"

    def repo_readable(self, repo):
        """Returns when the repository can be read; raises NotFound when it is missing or not readable."""
        target = check_repo(repo)
        parse_private(self._get(["gh", "api", f"repos/{target}"]))

    def repo_private(self):
        return parse_private(self._get(["gh", "api", f"repos/{self.repo}"]))

    def file_text(self, sha, path):
        """The text of a file of this repository at a commit; None only when the API answers 404 (the file does not exist there).
        A refused read (401, 403) raises NotReadable."""
        check_sha(sha)
        check_path(path)
        try:
            data = self._get(["gh", "api", f"repos/{self.repo}/contents/{path}?ref={sha}"])
        except NotReadable:
            raise  # a refused read is not an absent file
        except NotFound:
            return None
        return decode_contents(data)

    def base_config(self, sha):
        """The configuration file at the base commit, parsed; None when the file does not exist there."""
        text = self.file_text(sha, "config/project.yaml")
        return None if text is None else config.parse_yaml(text)


class FixtureClient:
    """Offline mode, one directory per case. Files of the repository under judgement: `pr-<n>.json`, `pr-<n>-files.json`,
    `issue-<n>.json`, `repo.json` (the private flag). The tree of the base commit is under `base/` and that of the head under
    `head/`; a file is found there by its path (`base/config/project.yaml`). Only files under `base/` are policy: nothing of
    the configuration is ever read from `head/`. Another repository is the directory `repos/<owner>/<name>/` holding its
    `repo.json`, `pr-<n>.json`, and `issue-<n>.json`; a repository without that directory is not readable."""

    def __init__(self, directory, repo="example-owner/sample-app"):
        self._dir = Path(directory)
        self.repo = check_repo(repo)
        if not self._dir.is_dir():
            raise InputError("fixtures directory not found")
        self._shas = {}

    def _where(self, repo):
        if repo is None or repo == self.repo:
            return self._dir
        check_repo(repo)
        owner, name = repo.split("/")
        return self._dir / "repos" / owner / name

    def _load(self, directory, name):
        try:
            text = (directory / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise NotFound("not found") from None
        except (OSError, UnicodeDecodeError):
            raise ApiError("fixture unreadable") from None
        try:
            return json.loads(text)
        except (ValueError, RecursionError):
            raise ApiError("malformed response") from None

    def pull(self, number, repo=None):
        check_number(number)
        directory = self._where(repo)
        pr = PullRequest.from_api(self._load(directory, f"pr-{number}.json"))
        if directory == self._dir:
            self._shas[pr.base_sha] = "base"
            self._shas[pr.head_sha] = "head"
        return pr

    def pull_files(self, number):
        check_number(number)
        files = parse_files(self._load(self._dir, f"pr-{number}-files.json"))
        if len(files) >= MAX_FILES:
            raise LimitExceeded("file limit reached")
        return files

    def issue_kind(self, number, repo=None):
        check_number(number)
        data = self._load(self._where(repo), f"issue-{number}.json")
        if not isinstance(data, dict):
            raise ApiError("malformed response")
        return "pull_request" if "pull_request" in data else "issue"

    def repo_readable(self, repo):
        if not self._where(repo).is_dir():
            raise NotFound("not found")

    def repo_private(self):
        return parse_private(self._load(self._dir, "repo.json"))

    def file_text(self, sha, path):
        check_path(path)
        side = self._shas.get(sha)
        if side is None:
            raise ApiError("malformed response")
        try:
            return (self._dir / side / path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError):
            raise ApiError("fixture unreadable") from None

    def base_config(self, sha):
        text = self.file_text(sha, "config/project.yaml")
        return None if text is None else config.parse_yaml(text)
