"""scripts/cr-criteria: the criteria table as JSON."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

import helpers

SCRIPT = helpers.ROOT / "scripts" / "cr-criteria"
TABLE = """# Acceptance criteria

| ID | Criterion | Evidence |
|---|---|---|
| T-1 | Every file has a class | Job `manifest` |
| T-2 | Pipes \\| inside a cell stay | A test |
| AC3-7 | Third | Fixture |

Not part of the table.

| ID | Criterion | Evidence |
|---|---|---|
| T-9 | A second table | ignored |
"""


class Criteria(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = pathlib.Path(self._tmp.name)
        self.tree = self.tmp_dir / "tree"
        helpers.write(self.tree / "config" / "project.yaml", helpers.CONFIG.replace('"docs/"', '"docs/"\n    - "records/"'))

    def put(self, text, root="docs", number=7):
        helpers.write(self.tree / root / "cr" / f"CR-{number}" / "acceptance-criteria.md", text)

    def run_it(self, *argv):
        code, out, err = helpers.run_criteria(*argv, "--tree", str(self.tree))
        return code, out, err

    def test_prints_one_json_object_with_the_ids_and_evidence(self):
        self.put(TABLE)
        for name in ("CR-7", "7"):
            code, out, err = self.run_it("--offline", name)
            self.assertEqual((code, err), (0, ""), out)
            data = json.loads(out.rsplit("\n", 2)[0])
            self.assertEqual(data["cr"], 7)
            self.assertEqual(data["file"], "docs/cr/CR-7/acceptance-criteria.md")
            self.assertEqual([c["id"] for c in data["criteria"]], ["T-1", "T-2", "AC3-7"])
            self.assertEqual(data["criteria"][0], {"id": "T-1", "criterion": "Every file has a class", "evidence": "Job `manifest`"})
            self.assertEqual(data["criteria"][1]["criterion"], "Pipes | inside a cell stay")
            self.assertEqual(data["count"], 3)
            self.assertEqual(data["problems"], [])
            self.assertEqual(out.strip().splitlines()[-1], "cr-criteria: CR 7: 3 criteria")

    def test_the_second_root_is_searched(self):
        self.put(TABLE, root="records", number=8)
        code, out, _ = self.run_it("8")
        self.assertEqual(code, 0)
        self.assertIn('"file":"records/cr/CR-8/acceptance-criteria.md"', out)

    def test_long_fields_are_cut_and_marked(self):
        self.put("| ID | Criterion | Evidence |\n|---|---|---|\n| T-1 | " + "word " * 200 + " | ok |\n")
        data = json.loads(self.run_it("7")[1].rsplit("\n", 2)[0])
        item = data["criteria"][0]
        self.assertTrue(item["cut"])
        self.assertLessEqual(len(item["criterion"]), 400)

    def test_duplicates_and_rows_without_an_id_are_reported(self):
        self.put("| ID | Criterion | Evidence |\n|---|---|---|\n| T-1 | a | b |\n| T-1 | again | b |\n| note | no id | b |\n")
        data = json.loads(self.run_it("7")[1].rsplit("\n", 2)[0])
        self.assertEqual([c["id"] for c in data["criteria"]], ["T-1"])
        self.assertEqual(len(data["problems"]), 2)

    def test_control_characters_are_dropped(self):
        self.put("| ID | Criterion | Evidence |\n|---|---|---|\n| T-1 | a\x1b[31m‮b | c |\n")
        out = self.run_it("7")[1]
        self.assertNotIn("\x1b", out)
        self.assertNotIn("‮", out)
        json.loads(out.rsplit("\n", 2)[0])

    def test_refusals(self):
        self.put("No table here.\n")
        self.assertEqual(self.run_it("7")[0], 1)
        self.assertEqual(self.run_it("9")[0], 1)
        (self.tree / "config" / "project.yaml").write_text("schema_version: 1\nworkflow:\n  docs_roots: []\n")
        self.assertEqual(self.run_it("7")[0], 1)

    def test_a_symbolic_link_is_not_followed(self):
        outside = self.tree / "outside.md"
        outside.write_text(TABLE)
        path = self.tree / "docs" / "cr" / "CR-7" / "acceptance-criteria.md"
        path.parent.mkdir(parents=True)
        path.symlink_to(outside)
        self.assertEqual(self.run_it("7")[0], 1)

    def test_a_symbolic_link_on_the_path_is_not_followed(self):
        outside = self.tmp_dir / "outside"
        helpers.write(outside / "cr" / "CR-7" / "acceptance-criteria.md", TABLE)
        (self.tree / "docs").symlink_to(outside)  # the root itself
        self.assertEqual(self.run_it("7")[0], 1)
        (self.tree / "docs").unlink()
        (self.tree / "docs").mkdir()
        (self.tree / "docs" / "cr").symlink_to(outside / "cr")  # the `cr` directory
        self.assertEqual(self.run_it("7")[0], 1)
        (self.tree / "docs" / "cr").unlink()
        (self.tree / "docs" / "cr").mkdir()
        (self.tree / "docs" / "cr" / "CR-7").symlink_to(outside / "cr" / "CR-7")  # the CR directory
        self.assertEqual(self.run_it("7")[0], 1)

    def test_a_root_that_leaves_the_tree_is_refused(self):
        helpers.write(self.tmp_dir / "elsewhere" / "cr" / "CR-7" / "acceptance-criteria.md", TABLE)
        (self.tree / "config" / "project.yaml").write_text(helpers.CONFIG.replace('"docs/"', '"../elsewhere/"'))
        self.assertEqual(self.run_it("7")[0], 1)

    def test_usage_errors(self):
        for argv in ((), ("x",), ("0",), ("CR-",), ("7", "8")):
            self.assertEqual(helpers.run_criteria(*argv)[0], 2, argv)

    def test_runs_as_a_program(self):
        self.put(TABLE)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="")
        done = subprocess.run([sys.executable, str(SCRIPT), "--offline", "--tree", str(self.tree), "CR-7"], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(list(helpers.ROOT.rglob("__pycache__")), [])


class ReadsTheTemplateCriteriaTable(unittest.TestCase):
    """The table of the CR skeleton parses (the criteria of a new CR have the shape this script reads)."""

    def test_the_skeleton_table_has_the_three_columns(self):
        from cr_tools import criteria
        text = (helpers.ROOT / "docs" / "cr" / "_template" / "acceptance-criteria.md").read_text()
        items, problems = criteria.parse_table(text)
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
