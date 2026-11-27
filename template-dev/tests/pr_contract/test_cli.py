"""Exit codes, output format, offline mode (--fixture), and the job summary."""
import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import helpers
from pr_contract import cli

CASES = helpers.FIXTURES / "cases"


def run(argv, env=None, client=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.dict(os.environ, env or {}):
        code = cli.main(argv, client=client)
    return code, out.getvalue(), err.getvalue()


def offline(case, pr=10, extra=()):
    return run(["--repo", helpers.OWN_REPO, "--pr", str(pr), "--fixture", str(CASES / case), "--templates", str(helpers.TEMPLATES), *extra])


class Offline(unittest.TestCase):
    def test_a_passing_case(self):
        code, out, _ = offline("design-pass")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines(), ["pr-contract: PR #10 (type: design)", "pr-contract: pass"])

    def test_a_violation_exits_1_with_the_fixed_format(self):
        code, out, _ = offline("design-touches-code")
        self.assertEqual(code, 1)
        lines = out.strip().split("\n")
        self.assertEqual(lines[0], "pr-contract: PR #10 (type: design)")
        self.assertEqual(lines[1], "FAIL PC-4: path outside docs/: scripts/x.py")
        self.assertEqual(lines[-1], "pr-contract: 1 violation")


class Usage(unittest.TestCase):
    def test_usage_errors_exit_2(self):
        base = ["--fixture", str(CASES / "design-pass"), "--templates", str(helpers.TEMPLATES)]
        cases = [
            [], ["--repo", "o/r"], ["--pr", "5"], ["--repo", "o/r", "--pr", "abc", *base],
            ["--repo", "o/r", "--pr", "0", *base], ["--repo", "o/r", "--pr", "-3", *base],
            ["--repo", "o/r", "--pr", "1000000000", *base], ["--repo", "o/r", "--pr", "5; id", *base],
            ["--repo", "o/r", "--pr", "$(id)", *base], ["--repo", "bad repo", "--pr", "5", *base],
            ["--repo", "../x", "--pr", "5", *base], ["--repo", "o/r", "--pr", "5", "--unknown", *base],
            ["--repo", "o/r", "--pr", "5", "--fixture", "/nonexistent-dir", "--templates", str(helpers.TEMPLATES)],
            ["--repo", "o/r", "--pr", "5", "--fixture", str(CASES / "design-pass"), "--templates", "/nonexistent-dir"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                code, out, _ = run(argv)
                self.assertEqual(code, 2)
                self.assertNotIn("pr-contract: pass", out)

    def test_missing_pr_in_fixtures_exits_3(self):
        code, out, _ = offline("design-pass", pr=99)
        self.assertEqual(code, 3)
        self.assertNotIn("pass", out)

    def test_token_is_never_printed(self):
        with mock.patch.dict(os.environ, {"GH_TOKEN": "tok-SECRET-0123456789"}):
            code, out, err = offline("design-pass")
        self.assertNotIn("tok-SECRET", out + err)


class Summary(unittest.TestCase):
    def test_summary_is_appended_with_code_spans(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".md") as handle:
            handle.write("existing\n")
            handle.flush()
            pr = helpers.make_pr(head_ref="feature/`<b>x</b>`|y")
            code, out, _ = run(["--repo", "o/r", "--pr", str(pr.number), "--templates", str(helpers.TEMPLATES)],
                               env={"GITHUB_STEP_SUMMARY": handle.name}, client=helpers.MemClient(prs={pr.number: pr}))
            text = open(handle.name).read()
        self.assertEqual(code, 1)
        self.assertTrue(text.startswith("existing\n"))
        self.assertIn("### pr-contract", text)
        self.assertIn("| PC-2 |", text)
        for line in text.splitlines()[1:]:
            self.assertEqual(line.count("`") % 2, 0, line)
            self.assertNotIn("<b>", line)

    def test_pass_summary(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".md") as handle:
            code, _, _ = offline("design-pass", extra=())
            code, _, _ = run(["--repo", helpers.OWN_REPO, "--pr", "10", "--fixture", str(CASES / "design-pass"),
                              "--templates", str(helpers.TEMPLATES)], env={"GITHUB_STEP_SUMMARY": handle.name})
            text = open(handle.name).read()
        self.assertEqual(code, 0)
        self.assertIn("pass", text)


if __name__ == "__main__":
    unittest.main()
