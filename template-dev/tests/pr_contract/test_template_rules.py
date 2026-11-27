"""TC-1 (template-sync PRs) and TC-2 (init PRs) (T-8, T-20 rule part). The classes come from the manifest of the base commit; for
added paths of a template-sync PR, from the manifest of the head commit. Nothing else of the head is policy."""
import unittest

import helpers
from helpers import BASE_SHA, HEAD_SHA, MemClient, body, fc, make_pr
from pr_contract import rules
from pr_contract.errors import ApiError

MANIFEST = '''schema_version = 1
version = "1.0.0"
[classes]
owned = ["scripts/", ".template/manifest.toml", ".github/workflows/ci.yml"]
seeded = ["config/project.yaml", "README.md", ".template/state.toml", ".template/overrides.toml"]
template-only = ["template-dev/", ".github/workflows/template-ci.yml"]
'''
HEAD_MANIFEST = MANIFEST.replace('owned = [', 'owned = ["newdir/", ')
STATE_OLD = '[template]\nversion = "1.0.0"\n'
STATE_NEW = '[template]\nversion = "1.2.0"\n'
STATE = ".template/state.toml"


def evaluate(kind, files, **kw):
    pr = make_pr(kind)
    client = MemClient(files={pr.number: files}, **kw)
    return rules.evaluate(pr, client, helpers.TEMPLATES), client


def ids(found, rule):
    return [v.message for v in found if v.rule == rule]


def sync(files, base=None, head=None, **kw):
    base_files = {".template/manifest.toml": MANIFEST, STATE: STATE_OLD}
    head_files = {".template/manifest.toml": HEAD_MANIFEST, STATE: STATE_NEW}
    base_files.update(base or {})
    head_files.update(head or {})
    for key in [k for k, v in {**base_files}.items() if v is None]:
        del base_files[key]
    for key in [k for k, v in {**head_files}.items() if v is None]:
        del head_files[key]
    return evaluate("template-sync", files, base_files=base_files, head_files=head_files, **kw)


class TC1(unittest.TestCase):
    OK = [fc("scripts/a.py"), fc("scripts/gone.py", "removed"), fc("newdir/x.py", "added"), fc(STATE)]

    def test_a_clean_sync_passes(self):
        found, _ = sync(self.OK)
        self.assertEqual(found, [])

    def test_a_changed_or_removed_path_must_be_owned_in_the_base_manifest(self):
        for status in ("modified", "removed", "changed"):
            with self.subTest(status=status):
                found, _ = sync([fc("README.md", status), fc(STATE)])
                self.assertEqual(ids(found, "TC-1"), ["path is not owned in the base manifest: README.md"])

    def test_a_path_that_is_unclassed_or_template_only_is_refused(self):
        for path in ("docs/x.md", "template-dev/a.py", ".github/workflows/template-ci.yml"):
            with self.subTest(path=path):
                found, _ = sync([fc(path), fc(STATE)])
                self.assertEqual(len(ids(found, "TC-1")), 1)

    def test_an_added_path_must_be_owned_in_the_head_manifest(self):
        found, _ = sync([fc("docs/new.md", "added"), fc(STATE)])
        self.assertEqual(ids(found, "TC-1"), ["added path is not owned in the head manifest: docs/new.md"])
        # owned only by the head manifest: fine; owned only by the base manifest: still not "new and owned at the head"
        self.assertEqual(sync([fc("newdir/x.py", "added"), fc(STATE)])[0], [])
        found, _ = sync([fc("newdir/x.py", "added"), fc(STATE)], head={".template/manifest.toml": MANIFEST})
        self.assertEqual(len(ids(found, "TC-1")), 1)

    def test_a_new_seeded_file_is_refused_as_the_text_of_tc1_says(self):
        found, _ = sync([fc("README.md", "added"), fc(STATE)])
        self.assertEqual(ids(found, "TC-1"), ["added path is not owned in the head manifest: README.md"])

    def test_the_head_manifest_does_not_decide_what_may_change(self):
        # the head makes README.md owned; a change to it is still judged by the base manifest
        head = {".template/manifest.toml": MANIFEST.replace('"README.md", ', "").replace('owned = [', 'owned = ["README.md", ')}
        found, _ = sync([fc("README.md"), fc(STATE)], head=head)
        self.assertEqual(ids(found, "TC-1"), ["path is not owned in the base manifest: README.md"])

    def test_overrides_are_never_changed_in_any_way(self):
        for item in (fc(".template/overrides.toml"), fc(".template/overrides.toml", "added"), fc(".template/overrides.toml", "removed"),
                     fc("scripts/o.toml", "renamed", previous=".template/overrides.toml"),
                     fc(".template/overrides.toml", "renamed", previous="scripts/o.toml")):
            with self.subTest(item=item):
                found, _ = sync([item, fc(STATE)])
                self.assertIn(".template/overrides.toml is changed", ids(found, "TC-1"))

    def test_the_state_file_must_be_updated_and_equal_the_branch_version(self):
        found, _ = sync([fc("scripts/a.py")])
        self.assertEqual(ids(found, "TC-1"), [".template/state.toml is not updated"])
        for text in (STATE_OLD, '[template]\nversion = "1.20.0"\n', '[template]\nversion = 120\n', "not toml [", "", None):
            with self.subTest(text=text):
                found, _ = sync([fc("scripts/a.py"), fc(STATE)], head={STATE: text})
                self.assertEqual(ids(found, "TC-1"), ["the version in .template/state.toml does not equal the branch version"])

    def test_the_state_file_cannot_be_added_removed_or_renamed(self):
        for item in (fc(STATE, "added"), fc(STATE, "removed"), fc("scripts/s.toml", "renamed", previous=STATE)):
            with self.subTest(item=item):
                found, _ = sync([item])
                self.assertTrue(ids(found, "TC-1"))

    def test_a_rename_removes_the_old_path_and_adds_the_new_one(self):
        found, _ = sync([fc("newdir/b.py", "renamed", previous="scripts/b.py"), fc(STATE)])
        self.assertEqual(found, [])
        found, _ = sync([fc("docs/b.md", "renamed", previous="scripts/b.py"), fc(STATE)])
        self.assertEqual(ids(found, "TC-1"), ["added path is not owned in the head manifest: docs/b.md"])
        found, _ = sync([fc("scripts/b.py", "renamed", previous="README.md"), fc(STATE)])
        self.assertEqual(ids(found, "TC-1"), ["path is not owned in the base manifest: README.md"])

    def test_a_status_the_rule_does_not_know_fails(self):
        found, _ = sync([fc("scripts/a.py", "unchanged"), fc(STATE)])
        self.assertEqual(ids(found, "TC-1"), ["change of unknown kind: scripts/a.py"])

    def test_a_missing_or_invalid_manifest_fails_closed(self):
        for which, files in (("base", {"base": {".template/manifest.toml": None}}), ("base", {"base": {".template/manifest.toml": "x ["}}),
                             ("head", {"head": {".template/manifest.toml": None}}), ("head", {"head": {".template/manifest.toml": "version = 1"}})):
            with self.subTest(which=which, files=files):
                found, _ = sync(self.OK, **files)
                self.assertIn(f"the manifest of the {which} commit is missing or invalid", ids(found, "TC-1"))

    def test_findings_are_capped_and_hold_no_pr_text(self):
        found, _ = sync([fc(f"docs/f{i}.md") for i in range(30)] + [fc(STATE)])
        messages = ids(found, "TC-1")
        self.assertEqual(len(messages), rules.PATH_CAP + 1)
        self.assertEqual(messages[-1], "and 20 more findings")

    def test_policy_is_read_only_from_the_base_and_the_head_commits(self):
        found, client = sync(self.OK)
        reads = [c[1] for c in client.calls if c[0] == "file_text"]
        self.assertTrue(all(sha in (BASE_SHA, HEAD_SHA) for sha, _ in reads))
        self.assertIn((HEAD_SHA, STATE), reads)
        self.assertNotIn((HEAD_SHA, "config/project.yaml"), reads)

    def test_a_transient_failure_is_an_error(self):
        with self.assertRaises(ApiError):
            sync(self.OK, fail=ApiError("HTTP 500"))

    def test_other_rules_still_apply(self):
        found, _ = sync([fc(".github/workflows/ci.yml"), fc(STATE)])
        self.assertEqual([v.rule for v in found], ["PC-6"])
        found, _ = sync([fc("scripts/operator/run.py", "removed"), fc("README.md"), fc(STATE)])
        self.assertEqual(sorted({v.rule for v in found}), ["PC-8", "TC-1"])

    def test_the_branch_version_is_read_from_the_branch_only(self):
        self.assertEqual(rules.branch_version("template-sync/v1.2.0"), "1.2.0")
        self.assertEqual(rules.branch_version("init/v10.0.123"), "10.0.123")
        for ref in ("impl/CR-2-x", "template-sync/1.2.0", "template-sync/v1.2", ""):
            self.assertIsNone(rules.branch_version(ref))


INIT_OK = [fc("template-dev/a.py", "removed"), fc(".github/workflows/template-ci.yml", "removed"), fc("README.md"),
           fc("config/project.yaml"), fc(STATE, "added")]
INIT_BASE = {".template/manifest.toml": MANIFEST}


def init(files, base=None, **kw):
    base_files = dict(INIT_BASE)
    base_files.update(base or {})
    base_files = {k: v for k, v in base_files.items() if v is not None}
    return evaluate("init", files, base_files=base_files, **kw)


class TC2(unittest.TestCase):
    def test_a_clean_init_passes_once(self):
        found, _ = init(INIT_OK)
        self.assertEqual(found, [])

    def test_it_fails_when_the_base_has_a_state_file(self):
        found, _ = init(INIT_OK, base={STATE: STATE_OLD})
        self.assertEqual(ids(found, "TC-2"), ["the base branch already has .template/state.toml"])

    def test_a_deleted_path_must_be_template_only(self):
        for path in ("scripts/a.py", "README.md", "docs/x.md"):
            with self.subTest(path=path):
                found, _ = init(INIT_OK + [fc(path, "removed")])
                self.assertEqual(ids(found, "TC-2"), [f"deleted path is not template-only: {path}"])

    def test_a_written_path_must_be_seeded(self):
        for path, status in (("scripts/a.py", "modified"), ("scripts/a.py", "added"), ("template-dev/n.py", "added"),
                             ("template-dev/n.py", "modified"), ("docs/x.md", "added"), (".template/manifest.toml", "modified")):
            with self.subTest(path=path, status=status):
                found, _ = init(INIT_OK + [fc(path, status)])
                self.assertEqual(ids(found, "TC-2"), [f"written path is not seeded: {path}"])

    def test_deleting_a_seeded_file_is_not_allowed(self):
        found, _ = init(INIT_OK + [fc("docs/PLATFORM.md", "removed")])
        self.assertTrue(ids(found, "TC-2"))

    def test_a_rename_is_a_delete_and_a_write(self):
        found, _ = init([fc("README.md", "renamed", previous="template-dev/r.md")])
        self.assertEqual(found, [])
        found, _ = init([fc("scripts/r.md", "renamed", previous="template-dev/r.md")])
        self.assertEqual(ids(found, "TC-2"), ["written path is not seeded: scripts/r.md"])
        found, _ = init([fc("README.md", "renamed", previous="scripts/r.md")])
        self.assertEqual(ids(found, "TC-2"), ["deleted path is not template-only: scripts/r.md"])

    def test_a_status_the_rule_does_not_know_fails(self):
        found, _ = init(INIT_OK + [fc("README.md", "unchanged")])
        self.assertEqual(ids(found, "TC-2"), ["change of unknown kind: README.md"])

    def test_a_missing_or_invalid_base_manifest_fails_closed(self):
        for text in (None, "x [", "schema_version = 2\n"):
            with self.subTest(text=text):
                found, _ = init(INIT_OK, base={".template/manifest.toml": text})
                self.assertEqual(ids(found, "TC-2"), ["the manifest of the base commit is missing or invalid"])

    def test_the_head_manifest_is_not_read_at_all(self):
        _, client = init(INIT_OK)
        self.assertNotIn(HEAD_SHA, [c[1][0] for c in client.calls if c[0] == "file_text"])

    def test_pc6_applies_to_the_removal_of_the_template_workflow(self):
        pr = make_pr("init", body=body("init", omit=("Workflow change justification",)))
        client = MemClient(files={pr.number: INIT_OK}, base_files=dict(INIT_BASE))
        self.assertEqual(sorted({v.rule for v in rules.evaluate(pr, client, helpers.TEMPLATES)}), ["PC-1", "PC-6"])
        pr = make_pr("init", body=body("init", sections={"Workflow change justification": ""}))
        found = rules.evaluate(pr, MemClient(files={pr.number: INIT_OK}, base_files=dict(INIT_BASE)), helpers.TEMPLATES)
        self.assertIn("PC-6", {v.rule for v in found})

    def test_findings_are_capped(self):
        found, _ = init([fc(f"scripts/f{i}.py") for i in range(25)])
        messages = ids(found, "TC-2")
        self.assertEqual(len(messages), rules.PATH_CAP + 1)
        self.assertEqual(messages[-1], "and 15 more paths")

    def test_a_pr_of_another_type_is_not_judged_by_the_tc_rules(self):
        for kind in ("design", "implementation", "change"):
            with self.subTest(kind=kind):
                found, _ = evaluate(kind, [fc("template-dev/a.py", "removed")], base_files=dict(INIT_BASE))
                self.assertEqual([v.rule for v in found if v.rule.startswith("TC")], [])


if __name__ == "__main__":
    unittest.main()
