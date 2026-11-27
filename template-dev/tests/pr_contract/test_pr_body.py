"""P-12: scripts/pr-body prints the unchanged template for the five types and nothing else."""
import subprocess
import tempfile
import unittest

import helpers

SCRIPT = helpers.ROOT / "scripts" / "pr-body"


def run(*args, cwd=None):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, cwd=cwd, check=False)


class PrBody(unittest.TestCase):
    def test_prints_each_template_unchanged_with_its_marker(self):
        for kind in helpers.TYPES:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                result = run(kind, cwd=tmp)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, (helpers.TEMPLATES / f"{kind}.md").read_text())
                self.assertTrue(result.stdout.startswith(f"<!-- PR type: {kind}"))
                self.assertEqual(result.stderr, "")
                self.assertEqual(list(__import__("os").scandir(tmp)), [], "wrote to disk")

    def test_rejects_everything_else(self):
        bad = ["", "Design", "../design", "design/../change", "design.md", "design extra", "-h", "--help", "all", "dependabot",
               "design\n", "$(id)", "`id`", "docs", "*", "./design"]
        for arg in bad:
            with self.subTest(arg=arg):
                result = run(arg)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_rejects_wrong_argument_counts(self):
        for args in ((), ("design", "change")):
            with self.subTest(args=args):
                result = run(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_script_has_no_file_writes(self):
        text = SCRIPT.read_text()
        self.assertNotRegex(text, r"(?<![0-9&])>(?!&)")
        self.assertNotIn("tee ", text)

    def test_output_passes_pc2_marker_parse(self):
        from pr_contract import markdown
        for kind in helpers.TYPES:
            with self.subTest(kind=kind):
                self.assertEqual(markdown.parse(run(kind).stdout).marker, kind)


if __name__ == "__main__":
    unittest.main()
