"""API failure, rate limit, and malformed responses all fail closed; the only process is `gh api` with an argument array."""
import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import helpers
from pr_contract import api, cli
from pr_contract.api import MAX_FILES, FixtureClient, GhClient
from pr_contract.errors import ApiError, InputError, LimitExceeded, NotFound

TOKEN = "tok-SECRET-0123456789"
PR_JSON = {"number": 5, "title": "t", "body": None, "state": "open", "draft": True, "merged": False,
           "changed_files": 2, "user": {"login": "u"}, "head": {"ref": "impl/CR-2-x", "sha": "a" * 40},
           "base": {"ref": "main", "sha": "b" * 40}}


class Runner:
    """Stands in for the process call: maps the endpoint (second word after `api`) to (code, stdout, stderr) or a callable."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, argv):
        self.calls.append(list(argv))
        endpoint = [a for a in argv[2:] if not a.startswith("-")][-1]
        route = self.routes.get(endpoint, self.routes.get("*"))
        if callable(route):
            route = route(argv)
        if isinstance(route, BaseException):
            raise route
        if isinstance(route, tuple):
            return route
        data = route if isinstance(route, bytes) else json.dumps(route).encode()
        return 0, data, b""


def client(routes):
    runner = Runner(routes)
    return GhClient("o/r", runner=runner), runner


def fail(status, text=""):
    return 1, b"", f"gh: {text or 'error'} (HTTP {status})".encode()


class Requests(unittest.TestCase):
    def test_every_call_is_gh_api_with_an_argument_array_and_a_literal_endpoint(self):
        c, runner = client({"*": PR_JSON})
        c.pull(5)
        self.assertEqual(runner.calls, [["gh", "api", "repos/o/r/pulls/5"]])

    def test_pull_of_another_repository(self):
        c, runner = client({"*": PR_JSON})
        c.pull(5, "p/q")
        self.assertEqual(runner.calls[0][2], "repos/p/q/pulls/5")

    def test_pull_request_parse(self):
        c, _ = client({"repos/o/r/pulls/5": PR_JSON})
        pr = c.pull(5)
        self.assertEqual((pr.number, pr.body, pr.head_ref, pr.base_ref, pr.author), (5, "", "impl/CR-2-x", "main", "u"))
        self.assertEqual((pr.head_sha, pr.base_sha), ("a" * 40, "b" * 40))

    def test_issue_kind(self):
        c, _ = client({"repos/o/r/issues/2": {"number": 2}, "repos/o/r/issues/3": {"number": 3, "pull_request": {}},
                       "repos/p/q/issues/9": {"number": 9}})
        self.assertEqual(c.issue_kind(2), "issue")
        self.assertEqual(c.issue_kind(3), "pull_request")
        self.assertEqual(c.issue_kind(9, "p/q"), "issue")

    def test_files_are_paginated(self):
        page = [{"filename": f"f{i}", "status": "modified"} for i in range(100)]
        routes = {"repos/o/r/pulls/5/files?per_page=100&page=1": page,
                  "repos/o/r/pulls/5/files?per_page=100&page=2": [{"filename": "last", "status": "renamed", "previous_filename": "old", "patch": "p"}]}
        c, _ = client(routes)
        files = c.pull_files(5)
        self.assertEqual(len(files), 101)
        self.assertEqual((files[-1].path, files[-1].previous, files[-1].patch, files[-1].status), ("last", "old", "p", "renamed"))

    def test_file_cap_is_an_error(self):
        page = [{"filename": f"f{i}", "status": "modified"} for i in range(100)]
        c, runner = client({"*": page})
        with self.assertRaises(LimitExceeded):
            c.pull_files(5)
        self.assertEqual(len(runner.calls), MAX_FILES // 100)

    def test_file_text_reads_the_contents_api_at_the_commit(self):
        text = "key: value\n"
        data = {"type": "file", "encoding": "base64", "size": len(text), "content": base64.b64encode(text.encode()).decode()}
        sha = "c" * 40
        c, runner = client({f"repos/o/r/contents/config/project.yaml?ref={sha}": data})
        self.assertEqual(c.file_text(sha, "config/project.yaml"), text)
        self.assertEqual(" ".join(runner.calls[0][:2]), "gh api")

    def test_a_missing_file_is_none_and_other_failures_are_errors(self):
        sha = "c" * 40
        c, _ = client({"*": fail(404)})
        self.assertIsNone(c.file_text(sha, "x/y.toml"))
        self.assertIsNone(c.base_config(sha))
        c, _ = client({"*": fail(500)})
        with self.assertRaises(ApiError):
            c.file_text(sha, "x/y.toml")

    def test_only_a_404_means_the_file_is_absent_a_refusal_is_an_error(self):
        sha = "c" * 40
        for status in (401, 403):
            c, _ = client({"*": fail(status)})
            with self.subTest(status=status):
                with self.assertRaises(NotFound):
                    c.file_text(sha, "x/y.toml")
                with self.assertRaises(NotFound):
                    c.base_config(sha)
        c, _ = client({"*": fail(404)})
        self.assertIsNone(c.file_text(sha, "x/y.toml"))

    def test_a_refused_read_of_the_state_file_is_not_a_pass_for_an_init_pr(self):
        """TC-2 passes only when the base has no `.template/state.toml`: a forbidden read must not count as absent."""
        from pr_contract import rules
        sha_base, sha_head = "b" * 40, "a" * 40
        pr_json = {**PR_JSON, "number": 5, "head": {"ref": "init/v1.0.0", "sha": sha_head}, "base": {"ref": "main", "sha": sha_base},
                   "body": helpers.body("init"), "changed_files": 1}

        def route(argv):
            endpoint = [a for a in argv[2:] if not a.startswith("-")][-1]
            if endpoint.endswith("/pulls/5"):
                return 0, json.dumps(pr_json).encode(), b""
            if "/pulls/5/files" in endpoint:
                return 0, json.dumps([{"filename": "README.md", "status": "modified"}]).encode(), b""
            return fail(403)

        c, _ = client({"*": route})
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--repo", "o/r", "--pr", "5", "--templates", str(helpers.TEMPLATES)], client=c)
        self.assertEqual(code, 3)
        self.assertNotIn("pass", out.getvalue())

    def test_file_text_rejects_bad_input_and_odd_content(self):
        c, _ = client({"*": {"type": "file", "encoding": "base64", "size": 3, "content": "!!!"}})
        with self.assertRaises(ApiError):
            c.file_text("c" * 40, "a.toml")
        for sha, path in (("short", "a.toml"), ("c" * 40, "../a"), ("c" * 40, "a b"), ("c" * 40, "/a"), ("c" * 40, "a?x=1")):
            with self.subTest(sha=sha, path=path), self.assertRaises((ApiError, InputError)):
                c.file_text(sha, path)
        for odd in ({"type": "dir"}, {"type": "file", "encoding": "none", "content": ""}, [1],
                    {"type": "file", "encoding": "base64", "size": 10**9, "content": ""}):
            with self.subTest(odd=odd):
                c, _ = client({"*": odd})
                with self.assertRaises(ApiError):
                    c.file_text("c" * 40, "a.toml")

    def test_repo_readable_and_private(self):
        c, _ = client({"repos/p/q": {"private": False}, "repos/o/r": {"private": True}})
        c.repo_readable("p/q")
        self.assertTrue(c.repo_private())
        c, _ = client({"*": fail(404)})
        with self.assertRaises(NotFound):
            c.repo_readable("p/q")
        c, _ = client({"*": {"private": "yes"}})
        with self.assertRaises(ApiError):
            c.repo_private()

    def test_bad_repo_and_numbers_are_input_errors(self):
        for repo in ("", "a", "a/b/c", "../x", "a b/c", "a/b?x", "a/" + "b" * 101):
            with self.subTest(repo=repo), self.assertRaises(InputError):
                GhClient(repo)
        c, _ = client({})
        for number in ("5", -1, 0, True, 10**9):
            with self.subTest(number=number), self.assertRaises(InputError):
                c.pull(number)
        with self.assertRaises(InputError):
            c.pull(5, "../x")


class Failures(unittest.TestCase):
    def call(self, route):
        c, _ = client({"*": route})
        return c.pull(5)

    def assert_api_error(self, route, fragment=None):
        with self.assertRaises(ApiError) as caught:
            self.call(route)
        self.assertNotIn(TOKEN, str(caught.exception))
        if fragment:
            self.assertIn(fragment, str(caught.exception))

    def test_not_found_and_not_readable_are_both_not_found_but_the_second_is_its_own_class(self):
        from pr_contract.errors import NotReadable
        with self.assertRaises(NotReadable):
            self.call(fail(403))
        with self.assertRaises(NotReadable):
            self.call(fail(401))
        with self.assertRaises(NotFound) as caught:
            self.call(fail(404))
        self.assertNotIsInstance(caught.exception, NotReadable)

    def test_not_found_and_not_readable_are_not_found(self):
        for status in (404, 401, 403):
            with self.subTest(status=status), self.assertRaises(NotFound):
                self.call(fail(status))

    def test_rate_limits(self):
        self.assert_api_error(fail(403, "API rate limit exceeded"), "rate limit")
        self.assert_api_error(fail(429), "rate limit")

    def test_other_http_errors(self):
        for code in (500, 502, 503):
            with self.subTest(code=code):
                self.assert_api_error(fail(code), str(code))
        self.assert_api_error((1, b"", b"something else"), "gh failed")

    def test_the_runner_failing_is_an_error(self):
        self.assert_api_error(ApiError("timeout"))

    def test_malformed_responses(self):
        for route in (b"", b"not json", b"[1, 2]", b"null", b"{", b'{"number": "5"}', b"\xff\xfe",
                      json.dumps({**PR_JSON, "head": None}).encode(),
                      json.dumps({**PR_JSON, "title": 5}).encode(),
                      json.dumps({**PR_JSON, "draft": "yes"}).encode(),
                      json.dumps({k: v for k, v in PR_JSON.items() if k != "user"}).encode()):
            with self.subTest(route=route[:30]):
                self.assert_api_error(route)

    def test_oversized_response(self):
        self.assert_api_error(b"{" + b" " * (11 * 1024 * 1024) + b"}", "too large")

    def test_files_response_must_be_a_list_of_objects(self):
        for route in ({"a": 1}, [1], [{"status": "added"}], [{"filename": "x"}], [{"filename": 3, "status": "added"}]):
            with self.subTest(route=route):
                c, _ = client({"*": route})
                with self.assertRaises(ApiError):
                    c.pull_files(5)

    def test_issue_response_must_be_an_object(self):
        c, _ = client({"*": [1]})
        with self.assertRaises(ApiError):
            c.issue_kind(2)


class RealProcessCall(unittest.TestCase):
    """The default runner, with the process call replaced: argument array, no shell, a narrow environment."""

    def test_spawn_passes_an_array_without_a_shell_and_a_filtered_environment(self):
        done = mock.Mock(returncode=0, stdout=json.dumps(PR_JSON).encode(), stderr=b"")
        env = {"PATH": "/bin", "GH_TOKEN": TOKEN, "UNRELATED_SECRET": "x", "HOME": "/h"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("subprocess.run", return_value=done) as run:
            GhClient("o/r").pull(5)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["gh", "api", "repos/o/r/pulls/5"])
        self.assertFalse(kwargs.get("shell", False))
        self.assertIn("GH_TOKEN", kwargs["env"])
        self.assertNotIn("UNRELATED_SECRET", kwargs["env"])
        self.assertGreater(kwargs["timeout"], 0)

    def test_a_missing_gh_command_and_a_timeout_are_api_errors(self):
        import subprocess
        for error in (FileNotFoundError("gh"), subprocess.TimeoutExpired("gh", 1), PermissionError("x")):
            with self.subTest(error=type(error).__name__), mock.patch("subprocess.run", side_effect=error):
                with self.assertRaises(ApiError) as caught:
                    GhClient("o/r").pull(5)
                self.assertNotIn("gh api", str(caught.exception))


class FixtureTests(unittest.TestCase):
    def test_missing_and_malformed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = FixtureClient(tmp)
            with self.assertRaises(NotFound):
                c.pull(1)
            with open(f"{tmp}/pr-1.json", "w") as handle:
                handle.write("{broken")
            with self.assertRaises(ApiError):
                c.pull(1)

    def test_files_are_not_found_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(NotFound):
                FixtureClient(tmp).pull_files(1)

    def test_head_files_are_not_policy_and_an_unknown_commit_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            for side, text in (("base", "a: 1\n"), ("head", "b: 2\n")):
                os.makedirs(f"{tmp}/{side}/config")
                with open(f"{tmp}/{side}/config/project.yaml", "w") as handle:
                    handle.write(text)
            with open(f"{tmp}/pr-1.json", "w") as handle:
                json.dump(PR_JSON, handle)
            c = FixtureClient(tmp)
            pr = c.pull(1)
            self.assertEqual(c.file_text(pr.base_sha, "config/project.yaml"), "a: 1\n")
            self.assertEqual(c.file_text(pr.head_sha, "config/project.yaml"), "b: 2\n")
            self.assertIsNone(c.file_text(pr.base_sha, "missing.toml"))
            with self.assertRaises(ApiError):
                c.file_text("d" * 40, "config/project.yaml")
            self.assertEqual(c.base_config(pr.base_sha), {})

    def test_another_repository_is_a_directory_and_absent_means_not_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(f"{tmp}/repos/p/q")
            with open(f"{tmp}/repos/p/q/pr-4.json", "w") as handle:
                json.dump({**PR_JSON, "number": 4}, handle)
            c = FixtureClient(tmp)
            c.repo_readable("p/q")
            self.assertEqual(c.pull(4, "p/q").number, 4)
            with self.assertRaises(NotFound):
                c.repo_readable("p/other")


class ExitCodes(unittest.TestCase):
    def run_main(self, routes):
        c, _ = client(routes)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--repo", "o/r", "--pr", "5", "--templates", str(helpers.TEMPLATES)], client=c)
        return code, out.getvalue()

    def test_every_api_failure_exits_3_and_never_leaks_the_token(self):
        for name, route in (("rate limit", fail(429)), ("500", fail(500)), ("malformed", b"not json"),
                            ("404 on the PR itself", fail(404))):
            with self.subTest(name=name):
                code, output = self.run_main({"*": route})
                self.assertEqual(code, 3)
                self.assertNotIn("pass", output)
                self.assertNotIn(TOKEN, output)


if __name__ == "__main__":
    unittest.main()
