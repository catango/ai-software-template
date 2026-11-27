"""T-8 and T-20 (rule part): `scripts/pr-contract` against fixture PRs, one directory per case under fixtures/cases/.

A case holds the pull request as the API shows it (`pr-<n>.json`, `pr-<n>-files.json`, `issue-<n>.json`, `repo.json`), the tree
of the base commit under `base/` and of the head commit under `head/`, other repositories under `repos/<owner>/<name>/`, and
`EXPECT`: the first line `exit <code>`, then every line the script prints. The script runs as a process, exactly as the
workflow runs it, so the same cases run against a generated tree: set PR_CONTRACT_TREE to its root (the dry run does).
"""
import os
import pathlib
import subprocess
import sys
import unittest

import helpers

CASES = helpers.FIXTURES / "cases"
TREE = pathlib.Path(os.environ.get("PR_CONTRACT_TREE", helpers.ROOT))
REPO = helpers.OWN_REPO


def run(case_dir, number=10):
    command = [sys.executable, "-I", "-B", str(TREE / "scripts" / "pr-contract"), "--repo", REPO, "--pr", str(number),
               "--fixture", str(case_dir), "--templates", str(TREE / ".github" / "PULL_REQUEST_TEMPLATE")]
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(command, capture_output=True, text=True, env=env, cwd=case_dir, check=False)


def expectation(case_dir):
    lines = (case_dir / "EXPECT").read_text().splitlines()
    return int(lines[0].split()[1]), lines[1:]


class Cases(unittest.TestCase):
    def test_every_case_gives_its_expected_output_and_exit_code(self):
        names = sorted(p.name for p in CASES.iterdir() if p.is_dir())
        self.assertGreater(len(names), 20)
        for name in names:
            with self.subTest(case=name):
                code, lines = expectation(CASES / name)
                done = run(CASES / name)
                self.assertEqual(done.stdout.splitlines(), lines, done.stderr)
                self.assertEqual(done.returncode, code, done.stderr)

    def test_the_cases_cover_every_rule(self):
        text = "\n".join((CASES / p.name / "EXPECT").read_text() for p in CASES.iterdir() if p.is_dir())
        for rule in ("PC-1", "PC-3", "PC-4", "PC-5", "PC-6", "PC-7", "PC-8", "PC-9", "TC-1", "TC-2"):
            self.assertIn(f"FAIL {rule}:", text, rule)
        for kind in ("design", "implementation", "change", "template-sync", "init", "dependabot"):
            self.assertIn(f"(type: {kind})", text, kind)
        # PC-2 is covered by the unit tests: a case needs a body whose marker disagrees, which the generator does not build

    def test_the_run_writes_nothing_into_the_case_or_the_tree(self):
        case = CASES / "design-pass"
        before = sorted(str(p) for p in case.rglob("*"))
        done = run(case)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(before, sorted(str(p) for p in case.rglob("*")))
        self.assertEqual(list(helpers.ROOT.joinpath("scripts").rglob("__pycache__")), [])

    def test_head_files_are_never_read_for_policy(self):
        # every case carries a head/config/project.yaml that widens the policy; removing it changes no verdict
        for name in ("change-operator-path-without-section", "change-head-edit-of-policy-changes-nothing"):
            self.assertTrue((CASES / name / "head" / "config" / "project.yaml").is_file())
            self.assertIn("FAIL PC-8", (CASES / name / "EXPECT").read_text())

    def test_usage_errors_exit_2_and_a_missing_pr_exits_3(self):
        done = subprocess.run([sys.executable, "-I", "-B", str(TREE / "scripts" / "pr-contract"), "--repo", REPO],
                              capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(run(CASES / "design-pass", number=99).returncode, 3)


if __name__ == "__main__":
    unittest.main()
