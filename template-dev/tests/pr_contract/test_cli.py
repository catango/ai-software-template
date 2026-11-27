"""Exit codes, output format, offline mode (--fixtures), and the job summary."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import helpers
from pr_contract import cli

RECORDED = helpers.FIXTURES / "recorded"
RECORDED_TEMPLATES = helpers.FIXTURES / "recorded-templates"  # the templates of the time the PRs were recorded


def run(argv, env=None, client=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.dict(os.environ, env or {}):
        code = cli.main(argv, client=client)
    return code, out.getvalue(), err.getvalue()


def offline(pr, fixtures=RECORDED, extra=()):
    return run(["--repo", "o/r", "--pr", str(pr), "--fixtures", str(fixtures), "--templates", str(RECORDED_TEMPLATES), *extra])


class Recorded(unittest.TestCase):
    """Real API responses (trimmed) recorded from this repository's own merged PRs."""

    def test_recorded_design_pr_passes(self):
        code, out, _ = offline(3)
        self.assertEqual(code, 0, out)
        self.assertIn("pr-contract: PR #3 (type: design)", out)
        self.assertTrue(out.rstrip().endswith("pr-contract: pass"))

    def test_recorded_implementation_pr_passes(self):
        code, out, _ = offline(5)
        self.assertEqual(code, 0, out)
        self.assertIn("pr-contract: PR #5 (type: implementation)", out)

    def test_violation_exits_1_with_fixed_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(RECORDED, tmp, dirs_exist_ok=True)
            path = os.path.join(tmp, "pr-5.json")
            data = json.load(open(path))
            data["body"] = data["body"].replace("## Validation", "## Renamed")
            json.dump(data, open(path, "w"))
            code, out, _ = offline(5, fixtures=tmp)
        self.assertEqual(code, 1)
        lines = out.strip().split("\n")
        self.assertEqual(lines[0], "pr-contract: PR #5 (type: implementation)")
        self.assertEqual(lines[1], "FAIL PC-1: section missing: Validation")
        self.assertEqual(lines[-1], "pr-contract: 1 violation")


class Usage(unittest.TestCase):
    def test_usage_errors_exit_2(self):
        base = ["--fixtures", str(RECORDED), "--templates", str(helpers.TEMPLATES)]
        cases = [
            [], ["--repo", "o/r"], ["--pr", "5"], ["--repo", "o/r", "--pr", "abc", *base],
            ["--repo", "o/r", "--pr", "0", *base], ["--repo", "o/r", "--pr", "-3", *base],
            ["--repo", "o/r", "--pr", "1000000000", *base], ["--repo", "o/r", "--pr", "5; id", *base],
            ["--repo", "o/r", "--pr", "$(id)", *base], ["--repo", "bad repo", "--pr", "5", *base],
            ["--repo", "../x", "--pr", "5", *base], ["--repo", "o/r", "--pr", "5", "--unknown", *base],
            ["--repo", "o/r", "--pr", "5", "--fixtures", "/nonexistent-dir", "--templates", str(helpers.TEMPLATES)],
            ["--repo", "o/r", "--pr", "5", "--fixtures", str(RECORDED), "--templates", "/nonexistent-dir"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                code, out, _ = run(argv)
                self.assertEqual(code, 2)
                self.assertNotIn("pr-contract: pass", out)

    def test_missing_pr_in_fixtures_exits_3(self):
        code, out, err = offline(99)
        self.assertEqual(code, 3)
        self.assertNotIn("pass", out)

    def test_token_is_never_printed(self):
        code, out, err = offline(5, extra=())
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok-SECRET-0123456789"}):
            code, out, err = offline(5)
        self.assertNotIn("tok-SECRET", out + err)


class Summary(unittest.TestCase):
    def test_summary_is_appended_with_code_spans(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".md") as handle:
            handle.write("existing\n")
            handle.flush()
            pr = helpers.make_pr(head_ref="feature/`<b>x</b>`|y")
            client = helpers.MemClient()
            code, out, _ = run(["--repo", "o/r", "--pr", str(pr.number), "--templates", str(helpers.TEMPLATES)],
                               env={"GITHUB_STEP_SUMMARY": handle.name}, client=helpers.MemClient(prs={pr.number: pr}))
            text = open(handle.name).read()
        self.assertEqual(code, 1)
        self.assertTrue(text.startswith("existing\n"))
        self.assertIn("### pr-contract", text)
        self.assertIn("| PC-2 |", text)
        for char in "<>`":
            for line in text.splitlines()[1:]:
                self.assertEqual(line.count("`") % 2, 0, line)
                self.assertNotIn("<b>", line)

    def test_pass_summary(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".md") as handle:
            code, _, _ = offline(5, extra=())
            code, _, _ = run(["--repo", "o/r", "--pr", "5", "--fixtures", str(RECORDED), "--templates", str(RECORDED_TEMPLATES)],
                             env={"GITHUB_STEP_SUMMARY": handle.name})
            text = open(handle.name).read()
        self.assertEqual(code, 0)
        self.assertIn("pass", text)


if __name__ == "__main__":
    unittest.main()
