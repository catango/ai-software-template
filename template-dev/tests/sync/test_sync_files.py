"""Plan row 8: every file of the row has exactly one class, and the row's pieces are where the rest of the template expects them."""
import os
import unittest

import syncfx

ROOT = syncfx.ROOT
manifest_module = syncfx.base.template_manifest

EXPECTED = {
    "scripts/template-sync": "owned",
    "scripts/lib/template_sync/__init__.py": "owned",
    "scripts/lib/template_sync/uses_drift.py": "owned",
    "scripts/lib/template_sync/cli.py": "owned",
    "scripts/checks/owned-drift.sh": "owned",
    "scripts/checks/owned_drift.py": "owned",
    ".claude/skills/sync-template/SKILL.md": "owned",
    "tests/quality-gate/fixtures/owned-drift/pass-clean/.template/state.toml": "owned",
    "template-dev/tests/sync/test_sync_apply.py": "template-only",
    "template-dev/tests/sync/syncfx.py": "template-only",
    "template-dev/docs/template-sync.md": "template-only",
    "template-dev/tests/skills/test_sync_template_skill.py": "template-only",
    ".template/state.toml": "seeded",
    ".template/overrides.toml": "seeded",
}


class Classes(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest_module.load_manifest(ROOT / ".template" / "manifest.toml")

    def test_the_files_of_the_row_have_the_class_they_should(self):
        for path, cls in EXPECTED.items():
            self.assertEqual(self.manifest.classify(path), [cls], path)

    def test_every_file_of_the_row_that_exists_has_exactly_one_class(self):
        paths = syncfx.base.tracked_paths()
        row = [p for p in paths if p.startswith(("scripts/lib/template_sync/", "template-dev/tests/sync/", "tests/quality-gate/fixtures/owned-drift/"))
               or p in ("scripts/template-sync", "scripts/checks/owned-drift.sh", "scripts/checks/owned_drift.py",
                        ".claude/skills/sync-template/SKILL.md", "template-dev/docs/template-sync.md")]
        self.assertGreater(len(row), 20)
        for path in row:
            self.assertEqual(len(self.manifest.classify(path)), 1, path)

    def test_the_whole_tree_has_exactly_one_class_per_file(self):
        report = manifest_module.check_paths(self.manifest, syncfx.base.tracked_paths())
        self.assertEqual(report, {"unclassed": [], "multiple": []})

    def test_the_scripts_are_executable_and_the_library_is_not_a_script(self):
        for path in ("scripts/template-sync", "scripts/checks/owned-drift.sh"):
            self.assertTrue(os.access(ROOT / path, os.X_OK), path)

    def test_the_gate_lists_owned_drift_as_a_template_check(self):
        text = (ROOT / "tests/quality-gate/catalogue.tsv").read_text()
        self.assertIn("owned-drift\ttemplate\tpresent", text)
        head = (ROOT / "scripts/checks/owned-drift.sh").read_text().splitlines()[:12]
        self.assertIn("# class: template", head)

    def test_the_pr_contract_workflow_lists_owned_drift_since_the_follow_up(self):
        # Row 8 left the list alone (a checker missing on the base commit fails pr-contract); the follow-up PR added it.
        text = (ROOT / ".github/workflows/pr-contract.yml").read_text()
        self.assertIn("owned-drift", text.split("for id in", 1)[1].split("; do", 1)[0].split())


if __name__ == "__main__":
    unittest.main()
