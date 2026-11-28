"""CR-43 S-20, S-21: the commands of the `pr-contract` workflow that run the policy checks.

The step is cut out of pr-contract.yml and run in a throw-away layout that looks like the job's: the base checkout is the working
directory (with the checkers) and the head checkout is `head/`, a separate repository that does not contain the base commit.
Needs `yq` (as workflow-policy does); the tests are skipped where it is not installed.
"""
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import helpers

ROOT = helpers.ROOT
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from template_sync import state as state_module  # noqa: E402  (renders the state.toml of the owned-drift tests)
WORKFLOW = ROOT / ".github" / "workflows" / "pr-contract.yml"
STEP = "Run the policy checks of the base against the head"
CHECKS = ("workflow-policy", "no-claude-in-workflows", "forbidden-api", "operator-contract", "cr-layout", "owned-drift")
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
STATE = ".template/state.toml"
OVERRIDES = ".template/overrides.toml"
OWNED = "scripts/owned-file.txt"
OWNED_SH = "scripts/checks/owned-drift.sh"
OWNED_PY = "scripts/checks/owned_drift.py"
SOURCE = "example-owner/software-template"
COMMIT = "0" * 40
# What a hostile branch name could do if it were ever parsed by a shell: run, redirect, substitute, or end the word.
HOSTILE_REF = "x;touch planted-1;$(touch planted-2)`touch planted-3`|touch planted-4\"'>planted-5 #"
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

    def seed_owned(self, version="1.0.0"):
        """The base commit gets a state.toml that records three owned files (one plain file and the two files of owned-drift) as they are, and
        the head repository gets the same files and the same state."""
        self.files = {OWNED: "one\n", OWNED_SH: (self.base / OWNED_SH).read_text(), OWNED_PY: (self.base / OWNED_PY).read_text()}
        write(self.base, OWNED, self.files[OWNED])
        write(self.base, STATE, self.state_text(version, self.files))
        self.base_sha = commit(self.base)
        for relative, text in {**self.files, STATE: (self.base / STATE).read_text()}.items():
            write(self.head, relative, text)
        commit(self.head)

    @staticmethod
    def state_text(version, files, drop=()):
        owned = {path: hashlib.sha256(text.encode()).hexdigest() for path, text in files.items() if path not in drop}
        return state_module.render(SOURCE, version, COMMIT, owned, {})

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

    def test_an_owned_file_changed_with_a_state_rewritten_to_match_fails_on_an_ordinary_branch(self):
        # the hashes come from the base commit, so a head that rewrites state.toml to match its own change gains nothing
        self.seed_owned()
        changed = {**self.files, OWNED: "two\n"}
        write(self.head, STATE, self.state_text("1.0.0", changed))
        self.head_edit(OWNED, "two\n")
        result = self.run_step(GATE_HEAD_REF="feature/change-it")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pr-contract: owned-drift: failed", result.stdout)
        self.assertIn(f"{OWNED}:1: owned file changed without an entry in {OVERRIDES}", result.stdout)
        self.assertIn("pr-contract: workflow-policy: ok", result.stdout)

    def test_a_head_that_leaves_the_owned_files_alone_passes(self):
        self.seed_owned()
        result = self.run_step(GATE_HEAD_REF="feature/other-work")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pr-contract: owned-drift: ok", result.stdout)

    def test_a_pr_that_replaces_owned_drift_and_adds_an_entry_for_its_own_change_still_fails(self):
        # the head replaces both files of the checker with ones that find nothing, declares those two changes in its own overrides.toml, and
        # changes another owned file without an entry; the checker of the base commit judges, so the last change is found
        self.seed_owned()
        write(self.head, OWNED_SH, "#!/usr/bin/env bash\nexit 0\n")
        write(self.head, OWNED_PY, "import sys\nsys.exit(0)\n")
        write(self.head, OVERRIDES, "".join(
            f'[[override]]\npath = "{path}"\nreason = "replaced"\npr = "example-owner/sample-app#7"\n\n' for path in (OWNED_SH, OWNED_PY)))
        self.head_edit(OWNED, "two\n")
        result = self.run_step(GATE_HEAD_REF="feature/weaken")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pr-contract: owned-drift: failed", result.stdout)
        self.assertIn(f"{OWNED}:1: owned file changed without an entry", result.stdout)
        self.assertNotIn(f"{OWNED_SH}:1", result.stdout)  # the entries are honoured: it is the unlisted change that fails

    def test_a_sync_branch_with_a_consistent_state_passes_and_the_same_name_with_a_dropped_hash_fails(self):
        # the fixes of the template-sync PR: the exemption needs the state and the files to agree, and a path may not leave [owned] while present
        self.seed_owned()
        changed = {**self.files, OWNED: "two\n"}
        write(self.head, STATE, self.state_text("1.1.0", changed))
        self.head_edit(OWNED, "two\n")
        result = self.run_step(GATE_HEAD_REF="template-sync/v1.1.0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pr-contract: owned-drift: ok", result.stdout)
        # the same branch name with a tampered file whose hash is dropped from [owned]
        write(self.head, STATE, self.state_text("1.1.0", changed, drop=(OWNED,)))
        self.head_edit(OWNED, "tampered\n")
        result = self.run_step(GATE_HEAD_REF="template-sync/v1.1.0")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pr-contract: owned-drift: failed", result.stdout)
        self.assertIn(f"{OWNED}:1: dropped from [owned] of {STATE} but still present", result.stdout)

    def test_a_sync_branch_name_without_the_matching_state_is_not_exempt(self):
        # the branch says 1.1.0 and the state says 1.0.0, or the branch is only a lookalike
        self.seed_owned()
        write(self.head, STATE, self.state_text("1.0.0", {**self.files, OWNED: "two\n"}))
        self.head_edit(OWNED, "two\n")
        for ref in ("template-sync/v1.1.0", "template-sync/v1.1.0-x", "xtemplate-sync/v1.1.0", "template-sync/v1.1.0\n"):
            with self.subTest(ref=ref):
                result = self.run_step(GATE_HEAD_REF=ref)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("pr-contract: owned-drift: failed", result.stdout)

    def test_the_branch_name_is_taken_from_gate_head_ref_before_github_head_ref(self):
        self.seed_owned()
        write(self.head, STATE, self.state_text("1.1.0", {**self.files, OWNED: "two\n"}))
        self.head_edit(OWNED, "two\n")
        result = self.run_step(GATE_HEAD_REF="feature/x", GITHUB_HEAD_REF="template-sync/v1.1.0")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        result = self.run_step(GITHUB_HEAD_REF="template-sync/v1.1.0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_missing_owned_drift_on_the_base_commit_fails_like_any_other_checker(self):
        self.seed_owned()
        (self.base / OWNED_SH).unlink()
        result = self.run_step()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pr-contract: the checker owned-drift is missing on the base commit", result.stdout)
        self.assertIn("pr-contract: workflow-policy: ok", result.stdout)

    def test_a_branch_name_with_shell_metacharacters_is_plain_text(self):
        self.seed_owned()
        refs = (HOSTILE_REF, "$(touch planted-2)", "--base=HEAD", "--head-ref=template-sync/v1.1.0", "-x")
        expected = [f"{OWNED}:1: owned file changed without an entry in {OVERRIDES}"]
        for changed in (False, True):
            if changed:
                write(self.head, STATE, self.state_text("1.0.0", {**self.files, OWNED: "two\n"}))
                self.head_edit(OWNED, "two\n")
            for ref in refs:
                with self.subTest(ref=ref, changed=changed):
                    result = self.run_step(GATE_HEAD_REF=ref)
                    shown = result.stdout + result.stderr
                    if changed:
                        self.assertEqual(result.returncode, 1, shown)
                        self.assertEqual(re.findall(r"(?m)^  (.+)$", result.stdout), expected)
                    else:
                        self.assertEqual(result.returncode, 0, shown)
                        self.assertIn("pr-contract: owned-drift: ok", shown)
                    for tree in (self.base, self.head, self.tmp, pathlib.Path.cwd()):
                        self.assertEqual(sorted(path.name for path in tree.glob("planted*")), [], tree)

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
