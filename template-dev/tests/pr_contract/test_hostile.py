"""P-8: hostile PR text produces no side effect and no unsanitized output."""
import contextlib
import io
import re
import time
import unittest
from unittest import mock

import helpers
from helpers import MemClient, body, fc, make_pr
from pr_contract import cli

HOSTILE_STRINGS = [
    "$(touch hostile-marker)", "`touch hostile-marker`", "${{ secrets.TOKEN }}", "${{ github.event.pull_request.title }}",
    "::error::injected", "::add-mask::abc", "::set-output name=x::y", "\n::error::after newline",
    "‮ rtl override", "zero​width⁠joiner", "\x1b[31mred\x1b[0m", "\x00nul", "a" * 5000,
]


def run_cli(pr, client=None, files=None):
    client = client or MemClient()
    client.prs[pr.number] = pr
    if files is not None:
        client.files[pr.number] = files
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["--repo", "o/r", "--pr", str(pr.number), "--templates", str(helpers.TEMPLATES)], client=client)
    return code, out.getvalue()


def assert_clean(test, output):
    for line in output.split("\n"):
        test.assertFalse(line.startswith("::"), line)
        test.assertNotIn("::", line)
        test.assertLess(len(line), 400)
    for ch in output:
        if ch != "\n":
            test.assertNotEqual(ch, "\x1b")
            test.assertNotRegex(ch, r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁠-⁤⁦-⁩﻿]")


class Hostile(unittest.TestCase):
    def test_hostile_text_everywhere_has_no_side_effect(self):
        for text in HOSTILE_STRINGS:
            with self.subTest(text=text[:30]):
                sections = {h: text + " filled" for h in helpers.headings("implementation")}
                pr = make_pr(title=text, body=body("implementation", top=helpers.IMPL_TOP + text.replace("\n", " ") + "\n",
                                                   sections=sections, omit=("Scope",)))
                with mock.patch("subprocess.Popen", side_effect=AssertionError("subprocess")), \
                        mock.patch("os.system", side_effect=AssertionError("os.system")):
                    code, output = run_cli(pr)
                self.assertEqual(code, 1)
                assert_clean(self, output)
                self.assertNotIn("hostile-marker", output)
                self.assertNotIn(pr.title, output)

    def test_hostile_branch_names_and_paths_are_sanitized(self):
        for text in HOSTILE_STRINGS:
            with self.subTest(text=text[:30]):
                pr = make_pr(head_ref="feature/" + text)
                code, output = run_cli(pr, files=[fc("docs/" + text, "renamed", previous="::error::" + text)])
                self.assertEqual(code, 1)
                assert_clean(self, output)
        pr = make_pr("design")
        code, output = run_cli(pr, files=[fc("scripts/\n::error::x‮")])
        self.assertEqual(code, 1)
        assert_clean(self, output)
        self.assertIn("PC-4", output)

    def test_one_megabyte_body_fails_quickly(self):
        pr = make_pr(body="x" * 1_000_000)
        start = time.monotonic()
        code, output = run_cli(pr)
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(code, 1)
        self.assertIn("too large", output)

    def test_pathological_reference_lines_are_fast(self):
        top = "Implements: #" + "1" * 60000 + "\nDesign: #" + "9" * 3000 + "\n" + "Implements: " * 2000
        pr = make_pr(body=body("implementation", top=top))
        start = time.monotonic()
        run_cli(pr)
        self.assertLess(time.monotonic() - start, 2.0)

    def test_fake_headings_cannot_satisfy_sections(self):
        extra = "```\n## Summary\nfake\n```\n<!--\n## Summary\nfake\n-->"
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP, omit=("Summary",), extra=extra))
        code, output = run_cli(pr)
        self.assertEqual(code, 1)
        self.assertIn("PC-1: section missing: Summary", output)

    def test_duplicate_headings_first_wins(self):
        extra = "## Summary\nsecond heading has content"
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP, empty=("Summary",), extra=extra))
        self.assertEqual(run_cli(pr)[0], 1)

    def test_crlf_body_passes(self):
        pr = make_pr(body=body("implementation", top=helpers.IMPL_TOP, newline="\r\n"))
        code, output = run_cli(pr)
        self.assertEqual(code, 0, output)

    def test_hidden_characters_in_headings_do_not_match(self):
        raw = body("implementation", top=helpers.IMPL_TOP).replace("## Summary", "## Summ​ary")
        code, output = run_cli(make_pr(body=raw))
        self.assertEqual(code, 1)
        self.assertIn("section missing: Summary", output)

    def test_title_with_newlines_is_harmless(self):
        pr = make_pr(title="line one\nline two\r\n::error::x")
        code, output = run_cli(pr)
        self.assertEqual(code, 0, output)
        assert_clean(self, output)

    def test_source_has_no_shell_or_dynamic_code_and_only_api_py_starts_a_process(self):
        root = helpers.ROOT / "scripts"
        sources = [root / "pr-contract", *sorted((root / "pr_contract").glob("*.py"))]
        banned = re.compile(r"\b(os\.system|os\.popen|os\.exec\w*|os\.spawn\w*|eval|exec|__import__|pickle|shell\s*=\s*True|Popen)\b")
        process = re.compile(r"\bsubprocess\b")
        for path in sources:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                code = line.split("#", 1)[0]
                self.assertIsNone(banned.search(code), f"{path.name}:{number}: {line.strip()}")
                if path.name != "api.py":
                    self.assertIsNone(process.search(code), f"{path.name}:{number}: {line.strip()}")

    def test_the_only_process_is_gh_api_as_an_argument_array(self):
        import ast
        tree = ast.parse((helpers.ROOT / "scripts" / "pr_contract" / "api.py").read_text())
        argv = []
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and len(node.elts) >= 2 and all(isinstance(e, ast.Constant) for e in node.elts[:2]):
                argv.append(" ".join(e.value for e in node.elts[:2]))
        self.assertTrue(argv)
        self.assertTrue(all(pair == "gh api" for pair in argv), argv)


if __name__ == "__main__":
    unittest.main()
