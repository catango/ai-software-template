"""GitHub access for the Skill scripts, and its offline twin.

Everything goes through `gh api` with an argument array (never a shell). Each endpoint and each GraphQL document is written out in
this file, so the gate check `forbidden-api` can read it; a variable stands only for a whole path segment. Reads are GET, or a GraphQL
document that starts with `query`. The only writes are the ones the Skills need: the pull request body (REST PATCH), marking a pull
request ready (the GraphQL mutation `markPullRequestReadyForReview`), adding an existing label to an issue, and one reply on a review
thread (the GraphQL mutation `addPullRequestReviewThreadReply`). `gh pr edit` and `gh pr ready` are not used: both fail on a host
with the Projects classic error. The token is never read here; `gh` takes it from its own environment.

Both clients have the same interface. Writes act on the client's own repository only. The offline client reads a directory:

    repo.json                  {"private": false, "default_branch": "main"}
    issue-<n>.json             an issue as the API returns it (a `pull_request` key marks a pull request)
    pr-<n>.json                a pull request as the API returns it (with `node_id`)
    pulls.json                 the list of pull requests (one page); an empty file `pulls-limited` makes the search report that it was limited
    labels.json                the labels of the repository: [{"name": "..."}]
    review-threads-<n>.json    the GraphQL answer for the review threads of pull request <n> (`{"data": {"repository": ...}}`, one page)
    reviews-<n>.json           the GraphQL answer for its reviews
    compare-<sha>.json         {"status": "ahead"} for a commit that is on the head of a pull request (a missing file: it is not)
    check-runs-<sha>.json      {"check_runs": [...]}        annotations-<id>.json   [...]
    base/config/project.yaml   the configuration of the base commit, or of the default branch for an issue
    repos/<owner>/<name>/      the same files for another repository (without that directory it is not readable)

Writes change the files of the directory (`pr-<n>.json`, `labels-added.json`, `replies.json`), so a test works on a copy.
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
THREAD_PAGE = 50
MAX_REVIEW_PAGES = 10
_NODE_ID = re.compile(r"[A-Za-z0-9_=-]{4,100}")
_LABEL = re.compile(r"[A-Za-z0-9 :._-]{1,50}")
_SHA = re.compile(r"[0-9a-f]{40}")
_THREAD_ID = re.compile(r"PRRT_[A-Za-z0-9_-]{1,90}")
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


def _repo_name(side):
    """`full_name` of the repository of a `head` or `base` object, or None (a deleted fork has none)."""
    repo = side.get("repo")
    if repo is None:
        return None
    name = _object(repo).get("full_name")
    if not isinstance(name, str):
        raise ApiError("malformed response")
    return name


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
        "head_repo": _repo_name(head), "base_repo": _repo_name(base),
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


def _actor(raw):
    """{login, type, id} of a GraphQL actor (the `__typename` and the `databaseId` of a user or a bot), or None."""
    if raw is None:
        return None
    raw = _object(raw)
    uid = raw.get("databaseId")
    if uid is not None and (not isinstance(uid, int) or isinstance(uid, bool)):
        raise ApiError("malformed response")
    return {"login": _field(raw, "login", str), "type": _field(raw, "__typename", str), "id": uid}


def _history(raw):
    """The `userContentEdits` of a comment: {total, more, editors}; None when GitHub gave none (the history is then unknown)."""
    if raw is None:
        return None
    raw = _object(raw)
    nodes, info = raw.get("nodes"), raw.get("pageInfo")
    if not isinstance(nodes, list) or not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool):
        raise ApiError("malformed response")
    return {"total": _field(raw, "totalCount", int), "more": info["hasNextPage"], "editors": [_actor(_object(node).get("editor")) for node in nodes]}


def _timestamp(raw, key):
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        raise ApiError("malformed response")
    return value


def comment_data(raw):
    """A review comment or a review as GraphQL returns it, reduced to what the judgement needs. Nothing is judged here."""
    raw = _object(raw)
    node_id = _field(raw, "id", str)
    if not _NODE_ID.fullmatch(node_id):
        raise ApiError("malformed response")
    return {
        "id": node_id, "author": _actor(raw.get("author")), "body": _field(raw, "body", str), "last_edited_at": _timestamp(raw, "lastEditedAt"),
        "created_edit": _field(raw, "includesCreatedEdit", bool), "editor": _actor(raw.get("editor")), "history": _history(raw.get("userContentEdits")),
    }


def _connection(page, key):
    """(nodes, total or None, has_next, end_cursor) of the connection `key` of the pull request in a GraphQL answer."""
    data = _object(page).get("data")
    repository = _object(data).get("repository") if data is not None else None
    request = _object(repository).get("pullRequest") if repository is not None else None
    if request is None:
        raise NotFound("not found")
    connection = _object(_object(request).get(key))
    nodes, info = connection.get("nodes"), connection.get("pageInfo")
    if not isinstance(nodes, list) or not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool):
        raise ApiError("malformed response")
    cursor = info.get("endCursor")
    if cursor is not None and (not isinstance(cursor, str) or not _NODE_ID.fullmatch(cursor)):
        raise ApiError("malformed response")
    total = connection.get("totalCount")
    if total is not None and (not isinstance(total, int) or isinstance(total, bool)):
        raise ApiError("malformed response")
    return nodes, total, info["hasNextPage"], cursor


def thread_page(page):
    """([thread], has_next, cursor) of one answer of the review-threads query."""
    nodes, _, more, cursor = _connection(page, "reviewThreads")
    threads = []
    for raw in nodes:
        raw = _object(raw)
        node_id = _field(raw, "id", str)
        if not _THREAD_ID.fullmatch(node_id):
            raise ApiError("malformed response")
        comments = _object(raw.get("comments"))
        items = comments.get("nodes")
        if not isinstance(items, list):
            raise ApiError("malformed response")
        line = raw.get("line") if raw.get("line") is not None else raw.get("originalLine")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool)):
            raise ApiError("malformed response")
        threads.append({
            "id": node_id, "path": _field(raw, "path", str), "line": line, "outdated": _field(raw, "isOutdated", bool),
            "resolved": _field(raw, "isResolved", bool), "comments": [comment_data(item) for item in items],
            "comments_total": _field(comments, "totalCount", int),
        })
    return threads, more, cursor


def review_page(page):
    """([review], total, has_next, cursor) of one answer of the reviews query."""
    nodes, total, more, cursor = _connection(page, "reviews")
    if total is None:
        raise ApiError("malformed response")
    reviews = []
    for raw in nodes:
        item = comment_data(raw)
        item["state"] = _field(_object(raw), "state", str)
        reviews.append(item)
    return reviews, total, more, cursor


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
        """(hits, limited): the pull requests (any state, newest first) of a repository whose head branch starts with `prefix`, and whether the
        search stopped at MAX_PAGES full pages, so that older pull requests were not looked at."""
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
                return hits, False
        return hits, True

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

    def review_threads(self, number):
        """Every review thread of a pull request of this repository, paged by cursor: [thread] (see `thread_page`)."""
        base_api.check_number(number)
        owner, name = self.repo.split("/")
        threads, cursor = [], None
        for _ in range(MAX_REVIEW_PAGES):
            argv = [
                "gh", "api", "graphql",
                "-f", "query=query($owner: String!, $name: String!, $number: Int!, $after: String) { repository(owner: $owner, name: $name) { pullRequest(number: $number) { reviewThreads(first: 50, after: $after) { pageInfo { hasNextPage endCursor } nodes { id isResolved isOutdated path line originalLine comments(first: 50) { totalCount nodes { id body lastEditedAt includesCreatedEdit author { login __typename ... on User { databaseId } ... on Bot { databaseId } } editor { login __typename ... on User { databaseId } ... on Bot { databaseId } } userContentEdits(first: 20) { totalCount pageInfo { hasNextPage } nodes { editor { login __typename ... on User { databaseId } ... on Bot { databaseId } } } } } } } } } } }",
                "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={number}",
            ]
            if cursor is not None:
                argv += ["-f", f"after={cursor}"]
            page, more, cursor = thread_page(self._call(argv))
            threads += page
            if not more:
                return threads
        raise ApiError("too many review threads")

    def reviews(self, number):
        """(reviews, total): the reviews of a pull request, paged by cursor, and how many exist (see `review_page`)."""
        base_api.check_number(number)
        owner, name = self.repo.split("/")
        reviews, cursor = [], None
        for _ in range(MAX_REVIEW_PAGES):
            argv = [
                "gh", "api", "graphql",
                "-f", "query=query($owner: String!, $name: String!, $number: Int!, $after: String) { repository(owner: $owner, name: $name) { pullRequest(number: $number) { reviews(first: 50, after: $after) { totalCount pageInfo { hasNextPage endCursor } nodes { id state body lastEditedAt includesCreatedEdit author { login __typename ... on User { databaseId } ... on Bot { databaseId } } editor { login __typename ... on User { databaseId } ... on Bot { databaseId } } userContentEdits(first: 20) { totalCount pageInfo { hasNextPage } nodes { editor { login __typename ... on User { databaseId } ... on Bot { databaseId } } } } } } } } }",
                "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={number}",
            ]
            if cursor is not None:
                argv += ["-f", f"after={cursor}"]
            page, total, more, cursor = review_page(self._call(argv))
            reviews += page
            if not more:
                return reviews, total
        raise ApiError("too many reviews")

    def thread_pull(self, thread_id):
        """(repository, number) of the pull request a review thread belongs to; None when the ID is not a review thread."""
        if not _THREAD_ID.fullmatch(thread_id):
            raise InputError("invalid thread id")
        data = _object(self._call([
            "gh", "api", "graphql",
            "-f", "query=query($id: ID!) { node(id: $id) { __typename ... on PullRequestReviewThread { pullRequest { number repository { nameWithOwner } } } } }",
            "-f", f"id={thread_id}",
        ]))
        node = (_object(data.get("data")).get("node")) if data.get("data") is not None else None
        if node is None or _object(node).get("__typename") != "PullRequestReviewThread":
            return None
        request = _object(_object(node).get("pullRequest"))
        repository = _object(request.get("repository"))
        return _field(repository, "nameWithOwner", str), _field(request, "number", int)

    def is_ancestor(self, commit, head_sha):
        """True when `commit` is the head commit or one of its ancestors (the compare status of `commit...head` is `identical` or `ahead`)."""
        if not _SHA.fullmatch(commit) or not _SHA.fullmatch(head_sha):
            raise InputError("invalid commit")
        try:
            basehead = f"{commit}...{head_sha}"
            data = _object(self._call(["gh", "api", f"repos/{self.repo}/compare/{basehead}"]))
        except NotFound:
            return False
        return data.get("status") in ("identical", "ahead")

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

    def reply_to_thread(self, thread_id, body):
        """Post ONE reply on a review thread of this repository (the GraphQL mutation; no review is created, nothing is resolved)."""
        if not _THREAD_ID.fullmatch(thread_id):
            raise InputError("invalid thread id")
        data = _object(self._call([
            "gh", "api", "graphql",
            "-f", "query=mutation($thread: ID!, $body: String!) { addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body}) { comment { id } } }",
            "-f", f"thread={thread_id}", "-f", f"body={body}",
        ]))
        reply = (_object(data.get("data")).get("addPullRequestReviewThreadReply")) if data.get("data") is not None else None
        if not isinstance(reply, dict) or not isinstance(reply.get("comment"), dict):
            raise ApiError("the reply was not posted")

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
        return [hit for hit in hits if hit is not None], (self._dir / "pulls-limited").exists()

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

    def _page(self, name):
        try:
            return self._load(self._dir, name)
        except NotFound:
            raise NotFound("not found") from None

    def review_threads(self, number):
        base_api.check_number(number)
        threads, more, _ = thread_page(self._page(f"review-threads-{number}.json"))
        if more:
            raise ApiError("too many review threads")
        return threads

    def reviews(self, number):
        base_api.check_number(number)
        reviews, total, more, _ = review_page(self._page(f"reviews-{number}.json"))
        if more:
            raise ApiError("too many reviews")
        return reviews, total

    def thread_pull(self, thread_id):
        if not _THREAD_ID.fullmatch(thread_id):
            raise InputError("invalid thread id")
        for path in sorted(self._dir.glob("review-threads-*.json")):
            number = path.stem.rsplit("-", 1)[1]
            if number.isdigit() and any(thread["id"] == thread_id for thread in thread_page(self._load(self._dir, path.name))[0]):
                return self.repo, int(number)
        return None

    def is_ancestor(self, commit, head_sha):
        if not _SHA.fullmatch(commit) or not _SHA.fullmatch(head_sha):
            raise InputError("invalid commit")
        try:
            data = _object(self._load(self._dir, f"compare-{commit}.json"))
        except NotFound:
            return False
        return data.get("status") in ("identical", "ahead")

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

    def reply_to_thread(self, thread_id, body):
        if not _THREAD_ID.fullmatch(thread_id):
            raise InputError("invalid thread id")
        try:
            replies = self._load(self._dir, "replies.json")
        except NotFound:
            replies = []
        self._store("replies.json", replies + [{"thread": thread_id, "body": body}])

    def mark_ready(self, node_id):
        for path in sorted(self._dir.glob("pr-*.json")):
            raw = self._load(self._dir, path.name)
            if isinstance(raw, dict) and raw.get("node_id") == node_id:
                raw["draft"] = False
                self._store(path.name, raw)
                return
        raise NotFound("not found")
