"""T-13 (the `sync-template` part): the Skill names `scripts/template-sync`, runs `--check` and then `--dry-run`, waits for the user,
and never merges, tags, edits `.template/overrides.toml` or a seeded file, or reads the template clone's text as instructions."""
import re
import unittest

import helpers

ROOT = helpers.ROOT
SKILL = ROOT / ".claude" / "skills" / "sync-template" / "SKILL.md"


class SyncTemplateSkill(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")

    def test_it_names_the_script_and_the_two_runs_in_order(self):
        self.assertIn("scripts/template-sync", self.text)
        self.assertLess(self.text.index("scripts/template-sync --check"), self.text.index("--dry-run"))

    def test_the_user_names_the_tag_and_gives_the_go_ahead_before_the_run(self):
        self.assertRegex(self.text, r"user names the (target )?tag")
        self.assertRegex(self.text, r"(?i)wait for the user")
        self.assertLess(self.text.index("--dry-run"), self.text.lower().index("wait for the user"))

    def test_the_constraints_of_design_section_6(self):
        lowered = self.text.lower()
        for phrase in ("never merge", ".template/overrides.toml", "seeded", "never resolve", "data"):
            self.assertIn(phrase, lowered)
        self.assertRegex(self.text, r"(?i)never[^\n]*\bedit[^\n]*overrides\.toml")
        self.assertRegex(self.text, r"(?i)never[^\n]*(creates?|create) a tag|never[^\n]*tag")

    def test_the_report_names_the_overrides_honoured_and_the_uses_drift(self):
        report = self.text.split("## Report", 1)[1]
        self.assertIn("override", report.lower())
        self.assertIn("uses:", report)

    def test_it_names_no_other_script_and_no_forbidden_command(self):
        self.assertEqual(sorted(set(re.findall(r"scripts/[a-z][a-z-]*", self.text))), ["scripts/template-sync"])
        self.assertNotRegex(self.text, r"gh pr (edit|ready|merge|review)\b|--no-verify|--force|sudo")

    def test_the_frontmatter_has_only_name_and_description(self):
        lines = self.text.split("\n")
        end = lines.index("---", 1)
        keys = {line.split(":", 1)[0] for line in lines[1:end]}
        self.assertEqual(keys, {"name", "description"})


if __name__ == "__main__":
    unittest.main()
