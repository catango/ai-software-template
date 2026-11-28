"""The git and GitHub steps of template-sync that --fixture replaces, through a fake runner (no process, no network), and the rollback."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

import syncfx

from template_sync import cli, publish  # noqa: E402
from template_sync.errors import SourceError  # noqa: E402


class Runner:
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, argv, cwd, env, data=None):
        self.calls.append({"argv": argv, "env": env, "data": data})
        for prefix, answer in self.answers.items():
            if argv[: len(prefix)] == list(prefix):
                return answer
        return 0, b""


class Push(unittest.TestCase):
    def test_it_pushes_the_one_branch_and_nothing_else(self):
        runner = Runner()
        publish.Repo("/x", runner=runner).push("template-sync/v1.1.0")
        self.assertEqual(runner.calls[0]["argv"], ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "push", "-q", "origin",
                                                   "refs/heads/template-sync/v1.1.0:refs/heads/template-sync/v1.1.0"])
        self.assertEqual(runner.calls[0]["env"]["GIT_TERMINAL_PROMPT"], "0")
        words = " ".join(runner.calls[0]["argv"])
        for forbidden in ("--force", "--tags", "--follow-tags", "--mirror", "--delete", "refs/tags", "main"):
            self.assertNotIn(forbidden, words)

    def test_a_failed_push_is_an_error(self):
        with self.assertRaises(SourceError):
            publish.Repo("/x", runner=Runner({("git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "push"): (1, b"")})).push("template-sync/v1.1.0")


class PullRequest(unittest.TestCase):
    def test_the_one_write_to_github_is_a_draft_pull_request(self):
        runner = Runner({("gh",): (0, json.dumps({"html_url": "https://example.org/o/r/pull/3"}).encode())})
        with mock.patch.dict(os.environ, {"GH_TOKEN": "canary-not-a-token", "AWS_SECRET_ACCESS_KEY": "canary"}):
            url = publish.Repo("/x", runner=runner).open_pull_request("example-owner/sample-app", "template-sync/v1.1.0", "main", "Title", "Body")
        call = runner.calls[0]
        self.assertEqual(call["argv"], ["gh", "api", "-X", "POST", "repos/example-owner/sample-app/pulls", "--input", "-"])
        self.assertEqual(json.loads(call["data"]), {"title": "Title", "head": "template-sync/v1.1.0", "base": "main", "body": "Body", "draft": True})
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", call["env"])
        self.assertEqual(url, "https://example.org/o/r/pull/3")

    def test_a_failure_or_an_odd_answer(self):
        with self.assertRaises(SourceError):
            publish.Repo("/x", runner=Runner({("gh",): (1, b"")})).open_pull_request("example-owner/sample-app", "b", "main", "t", "b")
        self.assertEqual(publish.Repo("/x", runner=Runner({("gh",): (0, b"not json")})).open_pull_request("example-owner/sample-app", "b", "main", "t", "b"), "")

    def test_the_repository_comes_from_the_origin_remote(self):
        for url in ("https://github.com/example-owner/sample-app.git", "git" + "@" + "github.com:example-owner/sample-app.git", "https://github.com/example-owner/sample-app"):
            runner = Runner({("git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "config"): (0, url.encode())})
            self.assertEqual(publish.Repo("/x", runner=runner).repository(), "example-owner/sample-app", url)


class Rollback(unittest.TestCase):
    def test_a_write_that_fails_halfway_leaves_a_clean_default_branch_and_no_branch(self):
        fx = syncfx.Fixture(self)
        before, snapshot = fx.head(), fx.snapshot()
        real, calls = cli.fsops.safe_write, []

        def failing(root, rel, data, mode):
            calls.append(rel)
            if len(calls) == 3:
                raise OSError("disk full")
            return real(root, rel, data, mode)

        out = io.StringIO()
        with mock.patch.dict(os.environ, syncfx.env()), mock.patch.object(cli.fsops, "safe_write", failing), redirect_stdout(out):
            code = cli.main(["v1.1.0", "--fixture", str(fx.remotes), "--tree", str(fx.tree)])
        self.assertEqual(code, 3)
        self.assertGreaterEqual(len(calls), 3)
        self.assertIn("back on the default branch", out.getvalue())
        self.assertEqual((fx.branch(), fx.head()), ("main", before))
        self.assertTrue(fx.clean())
        self.assertEqual(fx.snapshot(), snapshot)
        self.assertEqual(fx.git("branch", "--list").split(), ["*", "main"])


if __name__ == "__main__":
    unittest.main()
