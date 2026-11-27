"""GitHub access for the Skill scripts, and its offline twin.

Everything goes through `gh api` with an argument array (never a shell). Each endpoint and each GraphQL document is written out in
this file, so the gate check `forbidden-api` can read it; a variable stands only for a whole path segment. Reads are GET. The only
writes are the ones the Skills need: the pull request body (REST PATCH), marking a pull request ready (the GraphQL mutation
`markPullRequestReadyForReview`), and adding an existing label to an issue. `gh pr edit` and `gh pr ready` are not used: both fail on
a host with the Projects classic error. The token is never read here; `gh` takes it from its own environment.

Both clients have the same interface. Writes act on the client's own repository only. The offline client reads a directory:

    repo.json                  {"private": false, "default_branch": "main"}
    issue-<n>.json             an issue as the API returns it (a `pull_request` key marks a pull request)
    pr-<n>.json                a pull request as the API returns it (with `node_id`)
    pulls.json                 the list of pull requests (one page)
    labels.json                the labels of the repository: [{"name": "..."}]
    check-runs-<sha>.json      {"check_runs": [...]}        annotations-<id>.json   [...]
    base/config/project.yaml   the configuration of the base commit, or of the default branch for an issue
    repos/<owner>/<name>/      the same files for another repository (without that directory it is not readable)

Writes change the files of the directory (`pr-<n>.json`, `labels-added.json`), so a test works on a copy.
"""
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from pr_contract import api as base_api
from pr_contract.errors import ApiError, ConfigError, InputError, NotFound

MAX_PAGES = 5
PAGE_SIZE = 100
MAX_ITEMS = 100
_NODE_ID = re.compile(r"[A-Za-z0-9_=-]{4,100}")
_LABEL = re.compile(r"[A-Za-z0-9 :._-]{1,50}")
_SHA = re.compile(r"[0-9a-f]{40}")
_ENV_KEEP = base_api._ENV_KEEP


def _object(data):
    if not isinstance(data, dict):
        raise ApiError("malformed response")
    return data


def _field(data, key, kind):
    value = data.get(key)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ApiError("malformed response")
    return value


def _login(data):
    user = data.get("user")
    if not isinstance(user, dict):
        raise ApiError("malformed response")
    return _field(user, "login", str)


def issue_data(raw):
    """(number, title, state, author, labels, is_pull_request) of an issue as the API returns it."""
    raw = _object(raw)
    labels = raw.get("labels", [])
    if not isinstance(labels, list):
        raise ApiError("malformed response")
    names = []
    for label in labels:
        name = label.get("name") if isinstance(label, dict) else label
        if not isinstance(name, str):
            raise ApiError("malformed response")
        names.append(name)
    return {
        "number": _field(raw, "number", int), "title": _field(raw, "title", str), "state": _field(raw, "state", str),
        "author": _login(raw), "labels": names, "is_pull_request": "pull_request" in raw,
    }


def pull_data(raw):
    raw = _object(raw)
    head, base = _field(raw, "head", dict), _field(raw, "base", dict)
    body = raw.get("body")
    if body is not None and not isinstance(body, str):
        raise ApiError("malformed response")
    node_id = raw.get("node_id", "")
    if not isinstance(node_id, str):
        raise ApiError("malformed response")
    return {
        "number": _field(raw, "number", int), "title": _field(raw, "title", str), "body": body or "",
        "state": _field(raw, "state", str), "draft": _field(raw, "draft", bool), "merged": raw.get("merged", False) is True,
        "author": _login(raw), "head_ref": _field(head, "ref", str), "head_sha": _field(head, "sha", str),
        "base_ref": _field(base, "ref", str), "base_sha": _field(base, "sha", str), "node_id": node_id,
    }


def check_run_data(raw):
    raw = _object(raw)
    runs = raw.get("check_runs")
    if not isinstance(runs, list):
        raise ApiError("malformed response")
    out = []
    for run in runs[:MAX_ITEMS]:
        run = _object(run)
        conclusion = run.get("conclusion")
        if conclusion is not None and not isinstance(conclusion, str):
            raise ApiError("malformed response")
        out.append({"id": _field(run, "id", int), "name": _field(run, "name", str), "status": _field(run, "status", str), "conclusion": conclusion})
    return out


def annotation_messages(raw):
    if not isinstance(raw, list):
        raise ApiError("malformed response")
    out = []
    for item in raw[:MAX_ITEMS]:
        message = _object(item).get("message")
        if not isinstance(message, str):
            raise ApiError("malformed response")
        out.append(message)
    return out


def _search_hit(item, prefix):
    """A pull request of a list, reduced to what the scripts need; None when its head branch does not start with `prefix`."""
    head = _field(item, "head", dict)
    ref = _field(head, "ref", str)
    if not ref.startswith(prefix):
        return None
    return {
        "number": _field(item, "number", int), "title": _field(item, "title", str), "state": _field(item, "state", str),
        "merged": item.get("merged_at") is not None or item.get("merged") is True, "head_ref": ref,
    }


class GhClient:
    """The repository `repo` through `gh api`. `runner(argv, data)` replaces the process call in tests."""

    def __init__(self, repo, runner=None, timeout=base_api.TIMEOUT_SECONDS):
        self.repo = base_api.check_repo(repo)
        self._runner = runner or self._spawn
        self._timeout = timeout

    # -- process
    def _spawn(self, argv, data=None):
        env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
        env.update({"GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
        try:
            done = subprocess.run(argv, input=data, capture_output=True, env=env, timeout=self._timeout, check=False)
        except FileNotFoundError:
            raise ApiError("the gh command is not available") from None
        except subprocess.TimeoutExpired:
            raise ApiError("timeout") from None
        except OSError as error:
            raise ApiError(f"cannot run gh ({type(error).__name__})") from None
        return done.returncode, done.stdout, done.stderr

    def _call(self, argv, data=None):
        code, out, err = self._runner(argv, data)
        if code != 0:
            base_api.GhClient._raise_for_status(err)
        if len(out) > base_api.MAX_RESPONSE_BYTES:
            raise ApiError("response too large")
        try:
            return json.loads(out.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ApiError("malformed response") from None

    def _where(self, repo):
        return base_api.check_repo(repo or self.repo)

    # -- reads
    def repo_info(self, repo=None):
        target = self._where(repo)
        raw = _object(self._call(["gh", "api", f"repos/{target}"]))
        branch = raw.get("default_branch", "")
        return {"private": base_api.parse_private(raw), "default_branch": branch if isinstance(branch, str) else ""}

    def issue(self, number, repo=None):
        base_api.check_number(number)
        target = self._where(repo)
        return issue_data(self._call(["gh", "api", f"repos/{target}/issues/{number}"]))

    def pull(self, number, repo=None):
        base_api.check_number(number)
        target = self._where(repo)
        data = pull_data(self._call(["gh", "api", f"repos/{target}/pulls/{number}"]))
        if data["number"] != number:
            raise ApiError("malformed response")
        return data

    def pulls_with_prefix(self, prefix, repo=None):
        """Pull requests (any state) of a repository whose head branch starts with `prefix`; at most MAX_PAGES pages."""
        target = self._where(repo)
        hits = []
        for page in range(1, MAX_PAGES + 1):
            items = self._call(["gh", "api", f"repos/{target}/pulls?state=all&per_page={PAGE_SIZE}&page={page}"])
            if not isinstance(items, list):
                raise ApiError("malformed response")
            for item in items:
                hit = _search_hit(_object(item), prefix)
                if hit is not None:
                    hits.append(hit)
            if len(items) < PAGE_SIZE:
                break
        return hits

    def open_pulls_for_branch(self, branch):
        """Numbers of the open pull requests of this repository whose head is `branch` of this repository."""
        head = quote(f"{self.repo.split('/')[0]}:{branch}", safe="")
        items = self._call(["gh", "api", f"repos/{self.repo}/pulls?state=open&head={head}&per_page=10"])
        if not isinstance(items, list):
            raise ApiError("malformed response")
        hits = [_search_hit(_object(item), branch) for item in items]
        return [hit["number"] for hit in hits if hit is not None and hit["head_ref"] == branch]

    def config_text(self, ref=None):
        """config/project.yaml of this repository at a commit; with no `ref`, on its default branch. A missing file raises ConfigError."""
        if ref is None:
            path = ["gh", "api", f"repos/{self.repo}/contents/config/project.yaml"]
        else:
            if not isinstance(ref, str) or not _SHA.fullmatch(ref):
                raise InputError("invalid commit")
            path = ["gh", "api", f"repos/{self.repo}/contents/config/project.yaml?ref={ref}"]
        try:
            return base_api.decode_contents(self._call(path))
        except NotFound:
            raise ConfigError("config/project.yaml does not exist on the commit that judges the request") from None

    def label_exists(self, name, repo=None):
        target = self._where(repo)
        if not _LABEL.fullmatch(name):
            raise InputError("invalid label name")
        encoded = name.replace(":", "%3A").replace(" ", "%20")
        try:
            data = _object(self._call(["gh", "api", f"repos/{target}/labels/{encoded}"]))
        except NotFound:
            return False
        return data.get("name") == name

    def check_runs(self, sha):
        if not _SHA.fullmatch(sha):
            raise ApiError("malformed response")
        return check_run_data(self._call(["gh", "api", f"repos/{self.repo}/commits/{sha}/check-runs?per_page=100"]))

    def annotations(self, run_id):
        base_api.check_number(run_id)
        return annotation_messages(self._call(["gh", "api", f"repos/{self.repo}/check-runs/{run_id}/annotations?per_page=50"]))

    # -- writes (own repository only)
    def add_label(self, number, name):
        """Add an EXISTING label to an issue. The caller has checked that the label exists: GitHub would create a missing one."""
        base_api.check_number(number)
        if not _LABEL.fullmatch(name):
            raise InputError("invalid label name")
        self._call(["gh", "api", "-X", "POST", f"repos/{self.repo}/issues/{number}/labels", "-f", f"labels[]={name}"])

    def patch_body(self, number, body):
        """Replace the body of a pull request with the REST PATCH call."""
        base_api.check_number(number)
        payload = json.dumps({"body": body}).encode("utf-8")
        data = _object(self._call(["gh", "api", "-X", "PATCH", f"repos/{self.repo}/pulls/{number}", "--input", "-"], payload))
        if data.get("number") != number:
            raise ApiError("malformed response")

    def mark_ready(self, node_id):
        """Mark a draft pull request ready with the GraphQL mutation."""
        if not isinstance(node_id, str) or not _NODE_ID.fullmatch(node_id):
            raise ApiError("malformed response")
        data = _object(self._call([
            "gh", "api", "graphql",
            "-f", "query=mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) { pullRequest { isDraft } } }",
            "-f", f"id={node_id}",
        ]))
        result = (((data.get("data") or {}).get("markPullRequestReadyForReview") or {}).get("pullRequest") or {})
        if result.get("isDraft") is not False:
            raise ApiError("the pull request is still a draft")


class FixtureClient:
    """Offline mode: the same interface over a directory (see the module text)."""

    def __init__(self, directory, repo):
        self._dir = Path(directory)
        self.repo = base_api.check_repo(repo)
        if not self._dir.is_dir():
            raise InputError("fixtures directory not found")

    def _at(self, repo):
        target = self._where(repo)
        if target == self.repo:
            return self._dir
        owner, name = target.split("/")
        return self._dir / "repos" / owner / name

    def _where(self, repo):
        return base_api.check_repo(repo or self.repo)

    @staticmethod
    def _load(directory, name):
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

    def _store(self, name, value):
        try:
            (self._dir / name).write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")
        except OSError:
            raise ApiError("fixture not writable") from None

    def repo_info(self, repo=None):
        directory = self._at(repo)
        if not directory.is_dir():
            raise NotFound("not found")
        raw = _object(self._load(directory, "repo.json"))
        branch = raw.get("default_branch", "")
        return {"private": base_api.parse_private(raw), "default_branch": branch if isinstance(branch, str) else ""}

    def issue(self, number, repo=None):
        base_api.check_number(number)
        return issue_data(self._load(self._at(repo), f"issue-{number}.json"))

    def pull(self, number, repo=None):
        base_api.check_number(number)
        return pull_data(self._load(self._at(repo), f"pr-{number}.json"))

    def pulls_with_prefix(self, prefix, repo=None):
        items = self._load(self._at(repo), "pulls.json")
        if not isinstance(items, list):
            raise ApiError("malformed response")
        hits = [_search_hit(_object(item), prefix) for item in items]
        return [hit for hit in hits if hit is not None]

    def open_pulls_for_branch(self, branch):
        items = self._load(self._dir, "pulls.json")
        if not isinstance(items, list):
            raise ApiError("malformed response")
        hits = [_search_hit(_object(item), branch) for item in items]
        return [hit["number"] for hit in hits if hit is not None and hit["head_ref"] == branch and hit["state"] == "open"]

    def config_text(self, ref=None):
        try:
            return (self._dir / "base" / "config" / "project.yaml").read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConfigError("config/project.yaml does not exist on the commit that judges the request") from None
        except (OSError, UnicodeDecodeError):
            raise ApiError("fixture unreadable") from None

    def label_exists(self, name, repo=None):
        labels = self._load(self._at(repo), "labels.json")
        if not isinstance(labels, list):
            raise ApiError("malformed response")
        return any(isinstance(item, dict) and item.get("name") == name for item in labels)

    def check_runs(self, sha):
        if not _SHA.fullmatch(sha):
            raise ApiError("malformed response")
        try:
            return check_run_data(self._load(self._dir, f"check-runs-{sha}.json"))
        except NotFound:
            return []

    def annotations(self, run_id):
        base_api.check_number(run_id)
        try:
            return annotation_messages(self._load(self._dir, f"annotations-{run_id}.json"))
        except NotFound:
            return []

    def add_label(self, number, name):
        base_api.check_number(number)
        try:
            added = self._load(self._dir, "labels-added.json")
        except NotFound:
            added = []
        self._store("labels-added.json", added + [{"issue": number, "label": name}])

    def patch_body(self, number, body):
        base_api.check_number(number)
        raw = _object(self._load(self._dir, f"pr-{number}.json"))
        raw["body"] = body
        self._store(f"pr-{number}.json", raw)

    def mark_ready(self, node_id):
        for path in sorted(self._dir.glob("pr-*.json")):
            raw = self._load(self._dir, path.name)
            if isinstance(raw, dict) and raw.get("node_id") == node_id:
                raw["draft"] = False
                self._store(path.name, raw)
                return
        raise NotFound("not found")
