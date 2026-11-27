"""Rule PC-9 (CR-43 criteria S-9, S-20), the evidence line for an architecture with no runner.

The configuration of the base commit decides which architectures need a line. The head configuration is never read.
"""
import unittest

import helpers
from helpers import BASE_SHA, HEAD_SHA, MemClient, body, evidence_line, fc, make_pr
from pr_contract import rules
from pr_contract.errors import ApiError

SECTION = "Architecture evidence"
NO_RUNNER = {"architectures": [
    {"name": "amd64", "runner": "ubuntu-24.04", "runner_private": "ubuntu-24.04"},
    {"name": "arm64", "runner": "", "runner_private": ""},
]}
DEFAULT_LIKE = {"architectures": [
    {"name": "amd64", "runner": "ubuntu-24.04", "runner_private": "ubuntu-24.04"},
    {"name": "arm64", "runner": "ubuntu-24.04-arm", "runner_private": ""},
]}


def pr_with(text, kind="implementation", **over):
    top = helpers.IMPL_TOP if kind == "implementation" else ""
    return make_pr(kind, body=body(kind, top=top, sections={SECTION: text}), **over)


def evaluate(pr, client):
    client.files.setdefault(pr.number, [fc("scripts/x")])
    return rules.evaluate(pr, client, helpers.TEMPLATES)


def pc9(violations):
    return [v for v in violations if v.rule == "PC-9"]


def messages(violations):
    return " | ".join(v.message for v in pc9(violations))


class TemplatesCarryTheSection(unittest.TestCase):
    def test_change_and_implementation_templates_have_the_section(self):
        for kind in ("change", "implementation"):
            self.assertIn(SECTION, helpers.headings(kind))

    def test_other_templates_do_not(self):
        for kind in ("design", "template-sync", "init"):
            self.assertNotIn(SECTION, helpers.headings(kind))

    def test_template_comment_names_the_script_and_the_na_form(self):
        for kind in ("change", "implementation"):
            text = (helpers.TEMPLATES / f"{kind}.md").read_text()
            self.assertIn("scripts/gate-evidence", text)
            self.assertIn("n/a:", text)

    def test_placeholder_section_alone_is_not_content(self):
        pr = pr_with("<!-- only the template comment -->")
        self.assertIn("PC-1", {v.rule for v in evaluate(pr, MemClient(config=DEFAULT_LIKE))})


class Applicability(unittest.TestCase):
    def test_no_architecture_without_a_runner_needs_no_line(self):
        for config in (DEFAULT_LIKE, {"architectures": []}):
            for kind in ("implementation", "change"):
                with self.subTest(kind=kind):
                    client = MemClient(config=config)
                    self.assertEqual(pc9(evaluate(pr_with("n/a: every architecture has a runner", kind), client)), [])

    def test_the_private_flag_is_not_read_when_every_label_is_set_or_the_list_is_empty(self):
        every = {"architectures": [{"name": "amd64", "runner": "a", "runner_private": "a"}]}
        for config in (every, {"architectures": []}):
            client = MemClient(config=config)
            self.assertEqual(pc9(evaluate(pr_with("n/a"), client)), [])
            self.assertNotIn(("repo_private", None), client.calls)

    def test_a_missing_key_or_file_fails_closed_there_is_no_default(self):
        for config in (None, {}, {"approver": "x"}):
            with self.subTest(config=config):
                client = MemClient(config=config)
                found = pc9(evaluate(pr_with("n/a: every architecture has a runner"), client))
                self.assertEqual([v.message for v in found], ["architectures in the base configuration is missing or invalid"])

    def test_design_and_dependabot_prs_are_exempt(self):
        design = make_pr("design")
        self.assertEqual(pc9(evaluate(design, MemClient(config=NO_RUNNER))), [])
        client = MemClient(config=NO_RUNNER)  # a fresh client: PC-8 now reads the base configuration for a design PR, not for dependabot
        dependabot = make_pr("change", author="dependabot[bot]", head_ref="dependabot/pip/x", body="")
        client.files[dependabot.number] = [fc("requirements.txt")]
        self.assertEqual(rules.evaluate(dependabot, client, helpers.TEMPLATES), [])
        self.assertEqual(client.calls.count(("base_config", BASE_SHA)), 0)

    def test_template_sync_and_init_prs_are_not_covered(self):
        for kind in ("template-sync", "init"):
            with self.subTest(kind=kind):
                self.assertEqual(pc9(evaluate(make_pr(kind), MemClient(config=NO_RUNNER))), [])

    def test_a_body_too_large_to_check_is_left_to_pc1(self):
        pr = make_pr(body="x" * 70000)
        violations = evaluate(pr, MemClient(config=NO_RUNNER))
        self.assertEqual(pc9(violations), [])
        self.assertIn("PC-1", {v.rule for v in violations})


class ValidAndInvalidLines(unittest.TestCase):
    def check(self, text, config=NO_RUNNER, **over):
        return pc9(evaluate(pr_with(text, **over), MemClient(config=config)))

    def test_a_valid_line_passes_for_each_kind(self):
        for kind in ("implementation", "change"):
            with self.subTest(kind=kind):
                self.assertEqual(self.check(evidence_line(), kind=kind), [])

    def test_a_valid_line_in_a_fence_or_with_text_around_it_passes(self):
        self.assertEqual(self.check("Run on the lab host:\n```text\n" + evidence_line() + "\n```\nAccepted."), [])

    def test_missing_line_fails(self):
        for text in ("n/a: every architecture has a runner", "no line yet"):
            with self.subTest(text=text):
                self.assertIn("no evidence line for architecture arm64", messages(pc9_of(text)))

    def test_wrong_architecture_fails(self):
        found = self.check(evidence_line(arch="amd64", machine="x86_64"))
        self.assertIn("no evidence line for architecture arm64", messages(found))

    def test_sha_that_is_not_the_head_fails(self):
        for sha in ("c3" * 20, BASE_SHA):
            with self.subTest(sha=sha):
                self.assertIn("SHA is not the head of the PR", messages(self.check(evidence_line(sha=sha))))

    def test_dirty_fails(self):
        self.assertIn("working tree is not clean", messages(self.check(evidence_line(state="dirty"))))

    def test_a_different_command_fails(self):
        for command in ("./scripts/quality-gate.sh", "./scripts/quality-gate.sh --strict --skip links",
                        "./scripts/quality-gate.sh --base main --strict", "true"):
            with self.subTest(command=command):
                self.assertIn("command is not the quality gate", messages(self.check(evidence_line(command=command))))

    def test_a_non_zero_exit_fails(self):
        for code in ("1", "3", "127", "255"):
            with self.subTest(code=code):
                self.assertIn("exit status is not 0", messages(self.check(evidence_line(code=code))))

    def test_malformed_lines_fail_without_echoing_the_text(self):
        good = evidence_line()
        cases = {
            "seven fields": good.replace(" | exit 0", ""),
            "nine fields": good + " | extra",
            "short sha": evidence_line(sha=HEAD_SHA[:39]),
            "long sha": evidence_line(sha=HEAD_SHA + "a"),
            "upper-case sha": evidence_line(sha=HEAD_SHA.upper()),
            "state word": evidence_line(state="tidy"),
            "exit word": good.replace("exit 0", "exit zero"),
            "exit sign": good.replace("exit 0", "exit -1"),
            "time without Z": evidence_line(time="2026-11-23T10:15:00"),
            "time with offset": evidence_line(time="2026-11-23T10:15:00+01:00"),
            "impossible date": evidence_line(time="2026-13-45T10:15:00Z"),
            "empty machine": evidence_line(machine=""),
            "machine with space": evidence_line(machine="x 86"),
            "empty os": evidence_line(os_name=""),
            "upper-case arch": evidence_line(arch="ARM64"),
            "no space after colon": good.replace("evidence: ", "evidence:"),
            "tab separated": good.replace(" | ", "\t|\t"),
            "empty command": evidence_line(command=""),
            "trailing text": good + " trailing",
            "prefix only": "evidence: arm64",
        }
        for name, line in cases.items():
            with self.subTest(case=name):
                found = self.check(line)
                self.assertTrue(found, name)
                self.assertIn("malformed evidence line", messages(found))
                self.assertIn("no evidence line for architecture arm64", messages(found))
                for violation in found:
                    self.assertNotIn("trailing", violation.message)
                    self.assertNotIn(HEAD_SHA, violation.message)

    def test_a_line_outside_the_section_does_not_count(self):
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP, sections={SECTION: "n/a"},
                               extra=evidence_line()))
        self.assertIn("no evidence line for architecture arm64", messages(evaluate(pr, MemClient(config=NO_RUNNER))))
        other = body("implementation", top=helpers.IMPL_TOP, sections={SECTION: "n/a", "Summary": evidence_line()})
        self.assertIn("no evidence line", messages(evaluate(make_pr(body=other), MemClient(config=NO_RUNNER))))

    def test_a_line_in_an_html_comment_does_not_count(self):
        self.assertIn("no evidence line", messages(self.check("<!-- " + evidence_line() + " -->\nn/a")))

    def test_two_lines_for_one_architecture_fail(self):
        text = evidence_line() + "\n" + evidence_line(sha="c3" * 20)
        self.assertIn("more than one evidence line for architecture arm64", messages(self.check(text)))

    def test_a_line_for_an_architecture_that_needs_none_is_harmless(self):
        text = evidence_line() + "\n" + evidence_line(arch="amd64", machine="x86_64")
        self.assertEqual(self.check(text), [])

    def test_one_line_for_each_architecture_without_a_runner(self):
        config = {"architectures": [
            {"name": "amd64", "runner": "", "runner_private": ""},
            {"name": "arm64", "runner": "", "runner_private": ""},
        ]}
        both = evidence_line(arch="amd64", machine="x86_64") + "\n" + evidence_line()
        self.assertEqual(self.check(both, config=config), [])
        self.assertEqual(messages(self.check(evidence_line(), config=config)), "no evidence line for architecture amd64")
        found = self.check("n/a", config=config)
        self.assertEqual([v.message for v in found],
                         ["no evidence line for architecture amd64", "no evidence line for architecture arm64"])

    def test_the_messages_are_fixed_text(self):
        found = self.check(evidence_line(sha="c3" * 20, state="dirty", command="true", code="2"))
        self.assertEqual([v.message for v in found], [
            "evidence for arm64: SHA is not the head of the PR",
            "evidence for arm64: working tree is not clean",
            "evidence for arm64: command is not the quality gate",
            "evidence for arm64: exit status is not 0",
        ])
        self.assertEqual({v.rule for v in found}, {"PC-9"})


def pc9_of(text):
    return pc9(evaluate(pr_with(text), MemClient(config=NO_RUNNER)))


class PrivateFlag(unittest.TestCase):
    CONFIG = {"architectures": [
        {"name": "amd64", "runner": "ubuntu-24.04", "runner_private": ""},
        {"name": "arm64", "runner": "ubuntu-24.04-arm", "runner_private": ""},
    ]}

    def test_a_public_repository_uses_runner(self):
        client = MemClient(config=self.CONFIG, private=False)
        self.assertEqual(pc9(evaluate(pr_with("n/a: every architecture has a runner"), client)), [])
        self.assertIn(("repo_private", None), client.calls)

    def test_a_private_repository_uses_runner_private(self):
        client = MemClient(config=self.CONFIG, private=True)
        found = pc9(evaluate(pr_with("n/a"), client))
        self.assertEqual([v.message for v in found],
                         ["no evidence line for architecture amd64", "no evidence line for architecture arm64"])

    def test_a_private_repository_passes_with_a_line_for_each(self):
        text = evidence_line(arch="amd64", machine="x86_64") + "\n" + evidence_line()
        self.assertEqual(pc9(evaluate(pr_with(text), MemClient(config=self.CONFIG, private=True))), [])


class BaseConfigIsTheOnlyPolicySource(unittest.TestCase):
    """S-20 for `architectures`: the head of the PR cannot change which architectures need a line."""

    def test_the_config_is_requested_for_the_base_commit_only(self):
        client = MemClient(config=NO_RUNNER)
        evaluate(pr_with("n/a"), client)
        requests = [call for call in client.calls if call[0] == "base_config"]
        self.assertEqual(requests, [("base_config", BASE_SHA)])
        self.assertNotIn(HEAD_SHA, [call[1] for call in client.calls])

    def test_a_head_that_edits_the_configuration_changes_nothing(self):
        # The base requires a line for arm64. The head's file list shows config/project.yaml changed (it would give arm64 a
        # runner); the client returns the base value, and the verdict does not move.
        client = MemClient(config=NO_RUNNER, files={10: [fc("scripts/x"), fc("config/project.yaml")]})
        found = pc9(rules.evaluate(pr_with("n/a"), client, helpers.TEMPLATES))
        self.assertEqual([v.message for v in found], ["no evidence line for architecture arm64"])

    def test_an_architecture_added_by_the_head_needs_no_line_until_the_base_has_it(self):
        client = MemClient(config=DEFAULT_LIKE, files={10: [fc("scripts/x"), fc("config/project.yaml")]})
        self.assertEqual(pc9(rules.evaluate(pr_with("n/a: every architecture has a runner"), client, helpers.TEMPLATES)), [])

    def test_the_rule_never_looks_at_the_pr_body_for_policy(self):
        text = "architectures: none need evidence\n" + "n/a"
        self.assertIn("no evidence line", messages(pc9_of(text)))


class InvalidInputs(unittest.TestCase):
    BAD = {
        "not a list": {"architectures": "arm64"},
        "list of strings": {"architectures": ["arm64"]},
        "no name": {"architectures": [{"runner": ""}]},
        "bad name": {"architectures": [{"name": "ARM 64", "runner": "", "runner_private": ""}]},
        "unknown key": {"architectures": [{"name": "arm64", "runnr": ""}]},
        "runner not a string": {"architectures": [{"name": "arm64", "runner": 5, "runner_private": ""}]},
        "runner with a space": {"architectures": [{"name": "arm64", "runner": "a b", "runner_private": ""}]},
        "duplicate name": {"architectures": [{"name": "arm64"}, {"name": "arm64"}]},
        "null architectures": {"architectures": None},
        "a mapping": {"architectures": {"arm64": {}}},
    }

    def test_an_invalid_base_configuration_fails_closed_with_one_violation(self):
        for name, config in self.BAD.items():
            with self.subTest(case=name):
                found = pc9(evaluate(pr_with(evidence_line()), MemClient(config=config)))
                self.assertEqual([v.message for v in found], ["architectures in the base configuration is missing or invalid"])

    def test_a_configuration_that_is_not_a_mapping_fails_closed(self):
        for config in ([], "text", 5):
            with self.subTest(config=config):
                found = pc9(evaluate(pr_with(evidence_line()), MemClient(config=config)))
                self.assertEqual([v.message for v in found], ["architectures in the base configuration is missing or invalid"])

    def test_a_missing_runner_value_means_no_runner(self):
        config = {"architectures": [{"name": "arm64"}]}
        self.assertIn("no evidence line", messages(pc9(evaluate(pr_with("n/a"), MemClient(config=config)))))

    def test_api_errors_are_not_a_pass(self):
        with self.assertRaises(ApiError):
            evaluate(pr_with(evidence_line()), MemClient(config=NO_RUNNER, fail=ApiError("HTTP 500")))

    def test_a_head_sha_that_is_missing_cannot_be_judged(self):
        with self.assertRaises(ApiError):
            evaluate(pr_with(evidence_line(), head_sha=""), MemClient(config=NO_RUNNER))

    def test_a_head_sha_is_not_needed_when_no_line_is_required(self):
        self.assertEqual(pc9(evaluate(pr_with("n/a", head_sha=""), MemClient(config=DEFAULT_LIKE))), [])


if __name__ == "__main__":
    unittest.main()
