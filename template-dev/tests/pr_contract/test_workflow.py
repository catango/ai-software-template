"""P-10: the workflow passes only --repo and --pr, from a base checkout, with least privilege.

CR-43 S-7 and S-21: no secret, read scopes only, `pull_request` only, no bootstrap skip, and the policy checks run from the base
checkout against a read-only head checkout with no persisted credentials (the commands themselves: test_policy_commands.py).
"""
import re
import unittest

import helpers

WORKFLOW = helpers.ROOT / ".github" / "workflows" / "pr-contract.yml"


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.lines = self.text.splitlines()

    def test_top_level_permissions_are_empty_and_job_is_read_only(self):
        self.assertIn("\npermissions: {}\n", self.text)
        self.assertRegex(self.text, r"permissions:\n      contents: read\n      pull-requests: read\n      issues: read\n")
        self.assertNotRegex(self.text, r"(write|admin)")

    def test_triggers(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("types: [opened, edited, synchronize, reopened, ready_for_review, converted_to_draft]", self.text)
        for banned in ("pull_request_target", "workflow_run", "schedule:", "secrets.", "paths:", "paths-ignore:"):
            self.assertNotIn(banned, self.text)

    def test_checkout_is_the_base_commit_without_credentials(self):
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertRegex(self.text, r"uses: actions/checkout@[0-9a-f]{40} # v\d+\.\d+\.\d+")

    def test_script_arguments_are_exactly_repo_and_pr_from_env(self):
        invocations = [line.strip() for line in self.lines if "scripts/pr-contract" in line and "[" not in line]
        self.assertEqual(invocations, ['python3 -I scripts/pr-contract --repo "$REPO" --pr "$PR_NUMBER"'])
        self.assertIn("REPO: ${{ github.repository }}", self.text)
        self.assertIn("PR_NUMBER: ${{ github.event.pull_request.number }}", self.text)

    def test_no_expression_inside_run_blocks(self):
        inside = False
        for line in self.lines:
            if re.match(r"\s+run: [|>]", line):
                inside = True
                continue
            if inside and line.strip() and not line.startswith("          "):
                inside = False
            if inside:
                self.assertNotIn("${{", line)

    def test_there_is_no_bootstrap_skip(self):
        for banned in ("bootstrap", "skipped", "[[ ! -f scripts/pr-contract"):
            self.assertNotIn(banned, self.text)
        self.assertEqual(self.text.count("exit 0"), 0)

    def test_trigger_is_pull_request_only_and_there_is_one_job(self):
        self.assertEqual(re.findall(r"(?m)^on:\n((?:  .*\n)+)", self.text)[0].count("pull_request"), 1)
        self.assertEqual(re.findall(r"(?m)^  ([a-z-]+):\n    runs-on", self.text), ["pr-contract"])
        self.assertNotRegex(self.text, r"(?m)^    (if|continue-on-error):")

    def test_two_checkouts_base_for_the_checkers_and_head_in_its_own_directory(self):
        checkouts = re.findall(r"(?ms)- uses: actions/checkout@\S+ # v\S+\n        with:\n(.*?)(?=\n\n|\n      - name:|\Z)", self.text)
        self.assertEqual(len(checkouts), 2, checkouts)
        base, head = checkouts
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", base)
        self.assertNotIn("path:", base)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", head)
        self.assertIn("path: head", head)
        for block in checkouts:
            self.assertIn("persist-credentials: false", block)

    def test_the_policy_inputs_come_from_the_base_commit(self):
        self.assertIn("GATE_BASE_REF: ${{ github.event.pull_request.base.sha }}", self.text)
        for check in ("workflow-policy", 'no-cl""aude-in-workflows', "forbidden-api", "operator-contract", "cr-layout"):
            self.assertIn(check, self.text)

    def test_the_base_run_list_is_exactly_the_checks_with_a_policy_input_from_the_base(self):
        # PR #6 (follow-up spec): operator-contract is on the list; claude-md and deny-list are not (nothing to read from the base);
        # cr-layout reads docs_roots from the base configuration like operator-contract reads operator_paths.
        found = re.search(r"for id in ([^;\n]+); do", self.text).group(1).split()
        self.assertEqual(found, ["workflow-policy", '"no-cl""aude-in-workflows"', "forbidden-api", "operator-contract", "cr-layout"])

    def test_timeout_and_concurrency(self):
        self.assertIn("timeout-minutes:", self.text)
        self.assertIn("cancel-in-progress: true", self.text)

    def test_runner_is_the_hosted_base_label_and_names_no_architecture(self):
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertNotRegex(self.text, r"(?i)\b(amd64|arm64|aarch64|x86[_-]64|ubuntu-\d)")

    def test_the_token_reaches_only_the_step_that_runs_the_script_and_only_as_gh_token(self):
        self.assertEqual(self.text.count("github.token"), 1)
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.text)
        self.assertNotIn("secrets", self.text)


if __name__ == "__main__":
    unittest.main()
