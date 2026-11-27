"""CR-43 S-20, S-21: the commands of the `pr-contract` workflow that run the policy checks.

The step is cut out of pr-contract.yml and run in a throw-away layout that looks like the job's: the base checkout is the working
directory (with the checkers) and the head checkout is `head/`, a separate repository that does not contain the base commit.
Needs `yq` (as workflow-policy does); the tests are skipped where it is not installed.
"""
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

import helpers

ROOT = helpers.ROOT
WORKFLOW = ROOT / ".github" / "workflows" / "pr-contract.yml"
STEP = "Run the policy checks of the base against the head"
CHECKS = ("workflow-policy", "no-claude-in-workflows", "forbidden-api", "operator-contract", "cr-layout")
BASE_CONFIG = """schema_version: 1
workflow:
  design_repos: []
  docs_roots:
    - "docs/"
  operator_paths:
    - "ops/"
  operator_commands:
    - "git"
"""
OPERATOR_VIOLATION = "import subprocess\nsubprocess.run('x', shell=True)\n"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "noreply",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "noreply",
}
PASSING_CI = (ROOT / "tests/quality-gate/fixtures/workflow-policy/pass-aggregate/.github/workflows/ci.yml").read_text()
PASSING_PR_CONTRACT = (ROOT / "tests/quality-gate/fixtures/workflow-policy/pass-required-names/.github/workflows/pr-contract.yml").read_text()


def step_script():
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {STEP}")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    body = []
    for line in lines[run + 1:]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line[10:])
    return "\n".join(body) + "\n"


def git(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV}, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def write(root, relative, text):
    path = pathlib.Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def commit(root):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "c")
    return git(root, "rev-parse", "HEAD")


@unittest.skipUnless(shutil.which("yq") and shutil.which("git"), "yq or git is not installed")
class PolicyCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(lambda: subprocess.run(["chmod", "-R", "u+w", str(self.tmp)], check=False, capture_output=True))
        self.base = self.tmp / "base"
        self.head = self.base / "head"
        shutil.copytree(ROOT / "scripts" / "checks", self.base / "scripts" / "checks", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "scripts" / "lib", self.base / "scripts" / "lib")
        # operator-contract and cr-layout read their policy with this package, from the checker's own tree
        shutil.copytree(ROOT / "scripts" / "pr_contract", self.base / "scripts" / "pr_contract", ignore=shutil.ignore_patterns("__pycache__"))
        write(self.base, "config/project.yaml", BASE_CONFIG)
        write(self.base, ".github/workflows/ci.yml", PASSING_CI)
        write(self.base, ".github/workflows/pr-contract.yml", PASSING_PR_CONTRACT)
        git(self.base, "init", "-q")
        self.base_sha = commit(self.base)
        (self.base / ".gitignore").write_text("head/\n")
        # The head: its own repository, with no history in common with the base (the job's shallow checkout of the head commit).
        for relative in (".github/workflows/ci.yml", ".github/workflows/pr-contract.yml", "config/project.yaml"):
            write(self.head, relative, (self.base / relative).read_text())
        git(self.head, "init", "-q")
        commit(self.head)

    def head_edit(self, relative, text):
        write(self.head, relative, text)
        commit(self.head)

    def head_remove(self, relative):
        (self.head / relative).unlink()
        commit(self.head)

    def run_step(self, **extra):
        env = {"PATH": os.environ["PATH"], "GATE_BASE_REF": self.base_sha, "HOME": str(self.tmp), "TMPDIR": str(self.tmp),
               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        env.update(extra)
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step_script()], cwd=self.base, env=env,
                              capture_output=True, text=True, check=False)

    def test_a_head_without_findings_passes(self):
        result = self.run_step()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_loop_runs_every_check_of_the_list(self):
        result = self.run_step()
        for check in CHECKS:
            self.assertIn(f"pr-contract: {check}: ok", result.stdout + result.stderr)
        self.assertEqual(len(re.findall(r"pr-contract: \S+: ok", result.stdout + result.stderr)), len(CHECKS))

    def test_a_violation_in_the_head_fails(self):
        self.head_edit(".github/workflows/ci.yml", PASSING_CI.replace("pull_request:", "pull_request_target:", 1))
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("workflow-policy", result.stdout + result.stderr)
        self.assertIn("WP-5", result.stdout + result.stderr)

    def test_a_claude_reference_in_the_head_fails(self):
        self.head_edit(".github/workflows/pr-contract.yml", PASSING_PR_CONTRACT + "      - run: echo claude\n")
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no-claude-in-workflows", result.stdout + result.stderr)

    def test_a_pr_that_weakens_workflow_policy_and_adds_a_violation_still_fails(self):
        # S-21: the head replaces the checker with one that finds nothing and adds a violation; the base checker judges.
        write(self.head, "scripts/checks/workflow-policy.sh", "#!/usr/bin/env bash\nexit 0\n")
        write(self.head, "scripts/checks/workflow_policy.py", "import sys\nsys.exit(0)\n")
        self.head_edit(".github/workflows/ci.yml", PASSING_CI.replace("pull_request:", "pull_request_target:", 1))
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("WP-5", result.stdout + result.stderr)

    def test_a_forbidden_call_in_the_head_fails(self):
        self.head_edit("scripts/x.sh", "#!/usr/bin/env bash\nset -euo pipefail\ngh pr merge 5\n")
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("forbidden-api: failed", result.stdout + result.stderr)

    def test_a_pr_that_weakens_forbidden_api_and_adds_a_call_still_fails(self):
        # S-21: the head replaces the checker and its data with ones that find nothing; the base checker judges.
        write(self.head, "scripts/checks/forbidden-api.sh", "#!/usr/bin/env bash\nexit 0\n")
        write(self.head, "scripts/checks/forbidden-api.toml", "schema_version = 1\n")
        self.head_edit("scripts/x.sh", "#!/usr/bin/env bash\nset -euo pipefail\ngh pr merge 5\n")
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("forbidden-api: failed", result.stdout + result.stderr)

    def test_a_head_that_widens_the_policy_inputs_is_judged_by_the_base_values(self):
        # S-20: the head allows an action and a runner label and uses both; the base values apply.
        workflow = PASSING_CI.replace("      - run: echo ok\n", "      - uses: actions/setup-python@0123456789abcdef0123456789abcdef01234567 # v5.0.0\n", 1)
        self.head_edit("config/project.yaml", "schema_version: 1\nallowed_actions: [actions/checkout, actions/setup-python]\n")
        self.head_edit(".github/workflows/ci.yml", workflow)
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("WP-4", result.stdout + result.stderr)
        self.head_edit(".github/workflows/ci.yml", PASSING_CI.replace("ubuntu-latest", "runner-x", 1))
        self.head_edit("config/project.yaml", "schema_version: 1\nrunner_labels: [runner-x]\n")
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("WP-13", result.stdout + result.stderr)

    def test_a_violation_under_the_base_operator_paths_fails(self):
        self.head_edit("ops/tool.py", OPERATOR_VIOLATION)
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("operator-contract: failed", result.stdout + result.stderr)
        self.assertIn("pr-contract: forbidden-api: ok", result.stdout + result.stderr)

    def test_a_pr_that_weakens_operator_contract_and_adds_a_violation_still_fails(self):
        # the head replaces the checker, empties operator_paths in its own configuration, and adds a violation; the base judges
        write(self.head, "scripts/checks/operator-contract.sh", "#!/usr/bin/env bash\nexit 0\n")
        write(self.head, "scripts/checks/operator_contract.py", "import sys\nsys.exit(0)\n")
        write(self.head, "config/project.yaml", "schema_version: 1\nworkflow:\n  operator_paths: []\n")
        self.head_edit("ops/tool.py", OPERATOR_VIOLATION)
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("operator-contract: failed", result.stdout + result.stderr)

    def test_operator_contract_fails_closed_when_the_base_configuration_lacks_operator_paths(self):
        write(self.base, "config/project.yaml", "schema_version: 1\n")
        git(self.base, "add", "-A")
        git(self.base, "commit", "-q", "-m", "c")
        result = self.run_step(GATE_BASE_REF=git(self.base, "rev-parse", "HEAD"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("operator-contract: failed", result.stdout + result.stderr)

    def test_a_malformed_change_request_directory_fails(self):
        self.head_edit("docs/cr/CR-1/design.md", "# Design\n")
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cr-layout: failed", result.stdout + result.stderr)

    def test_a_pr_that_weakens_cr_layout_and_adds_a_malformed_directory_still_fails(self):
        # the head replaces the checker and drops the root from its own docs_roots; the base roots apply
        write(self.head, "scripts/checks/cr-layout.sh", "#!/usr/bin/env bash\nexit 0\n")
        write(self.head, "scripts/checks/cr_layout.py", "import sys\nsys.exit(0)\n")
        write(self.head, "config/project.yaml", "schema_version: 1\nworkflow:\n  docs_roots:\n    - \"elsewhere/\"\n")
        self.head_edit("docs/cr/CR-1/design.md", "# Design\n")
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cr-layout: failed", result.stdout + result.stderr)

    def test_the_checks_without_a_policy_input_to_read_from_the_base_are_not_on_the_list(self):
        # claude-md and deny-list compare files of the head with each other (CLAUDE.md with state.toml, required-deny.txt with
        # settings.json); the base's copy would add nothing. template-dev/docs/instructions.md, points 6 and 7.
        result = self.run_step()
        for check in ("claude-md", "deny-list"):
            self.assertNotIn(check, result.stdout + result.stderr)

    def test_a_missing_checker_on_the_base_commit_fails(self):
        for check in CHECKS:
            with self.subTest(check=check):
                saved = (self.base / "scripts" / "checks" / f"{check}.sh").read_bytes()
                (self.base / "scripts" / "checks" / f"{check}.sh").unlink()
                result = self.run_step()
                (self.base / "scripts" / "checks" / f"{check}.sh").write_bytes(saved)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(check, result.stdout + result.stderr)
                self.assertIn("missing on the base commit", result.stdout + result.stderr)

    def test_a_missing_tool_fails_instead_of_skipping(self):
        # yq is the tool of workflow-policy: a checker that cannot run is a failure, not a pass.
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for tool in ("bash", "git", "python3", "sed", "grep", "awk", "mktemp", "rm", "chmod", "cat", "sort", "head", "tr", "dirname", "basename"):
            found = shutil.which(tool)
            if found:
                (bin_dir / tool).symlink_to(found)
        result = self.run_step(PATH=str(bin_dir))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_the_head_checkout_is_read_only_while_the_checks_run(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file permissions")
        (self.base / "scripts" / "checks" / "no-claude-in-workflows.sh").write_text("#!/usr/bin/env bash\ntouch ./planted\n")
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.head / "planted").exists())
        # and the directory is writable again afterwards, so the post step of the checkout can clean up
        self.assertTrue(os.access(self.head, os.W_OK))

    def test_findings_cannot_forge_a_workflow_command(self):
        self.head_edit(".github/workflows/pr-contract.yml", PASSING_PR_CONTRACT + "      - run: echo claude ::error::x\n")
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("::error", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
