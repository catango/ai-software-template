import unittest

import helpers  # noqa: F401  (sets up sys.path)
from pr_contract import markdown


class ParseTests(unittest.TestCase):
    def test_sections_and_marker(self):
        parsed = markdown.parse(helpers.body("implementation", top=helpers.IMPL_TOP))
        self.assertEqual(parsed.marker, "implementation")
        self.assertIn("Summary", parsed.sections)
        self.assertEqual(parsed.sections["Summary"].strip(), "Filled in for the test.")

    def test_comment_only_section_is_empty(self):
        parsed = markdown.parse("## A\n<!-- only a comment -->\n\n## B\ntext\n")
        self.assertFalse(markdown.has_content(parsed.sections["A"]))
        self.assertTrue(markdown.has_content(parsed.sections["B"]))

    def test_multiline_comment_is_removed(self):
        parsed = markdown.parse("## A\n<!-- start\n## Fake\nend -->\ntext\n")
        self.assertEqual(list(parsed.sections), ["A"])
        self.assertTrue(markdown.has_content(parsed.sections["A"]))

    def test_heading_in_fence_is_not_a_heading(self):
        parsed = markdown.parse("## A\n```\n## Fake\n```\ntext\n")
        self.assertEqual(list(parsed.sections), ["A"])
        parsed = markdown.parse("## A\n~~~~\n## Fake\n~~~\n## Still fake\n~~~~\ntext\n")
        self.assertEqual(list(parsed.sections), ["A"])

    def test_comment_marker_inside_fence_is_literal(self):
        parsed = markdown.parse("```\n<!-- PR type: design\n```\n## A\ntext\n")
        self.assertIsNone(parsed.marker)
        self.assertEqual(list(parsed.sections), ["A"])

    def test_fence_opener_inside_comment_is_ignored(self):
        parsed = markdown.parse("<!--\n```\n-->\n## A\ntext\n")
        self.assertEqual(list(parsed.sections), ["A"])

    def test_first_heading_wins(self):
        parsed = markdown.parse("## A\n\n## A\ntext\n")
        self.assertFalse(markdown.has_content(parsed.sections["A"]))

    def test_crlf_and_cr(self):
        for newline in ("\r\n", "\r"):
            parsed = markdown.parse(f"## A{newline}text{newline}{newline}## B{newline}more{newline}")
            self.assertEqual(list(parsed.sections), ["A", "B"])

    def test_zero_width_only_content_is_empty(self):
        parsed = markdown.parse("## A\n​‍⁠\n‮\n")
        self.assertFalse(markdown.has_content(parsed.sections["A"]))

    def test_heading_with_hidden_character_is_a_different_heading(self):
        parsed = markdown.parse("## Sum​mary\ntext\n")
        self.assertNotIn("Summary", parsed.sections)

    def test_marker_first_wins_and_must_be_a_comment(self):
        parsed = markdown.parse("PR type: design\n<!-- PR type: change. -->\n<!-- PR type: infra -->\n")
        self.assertEqual(parsed.marker, "change")

    def test_reference_lines(self):
        parsed = markdown.parse("Implements: #12\nDesign: #3  \n```\nImplements: #99\n```\n<!-- Design: #7 -->\n")
        self.assertEqual(parsed.implements, markdown.Ref(None, 12))
        self.assertEqual(parsed.design, markdown.Ref(None, 3))

    def test_reference_placeholders_do_not_match(self):
        parsed = markdown.parse("Implements: #<issue>\nDesign: #<merged design PR>\n")
        self.assertIsNone(parsed.implements)
        self.assertIsNone(parsed.design)

    def test_reference_digit_limit(self):
        parsed = markdown.parse("Implements: #1234567890\n")
        self.assertIsNone(parsed.implements)


class QualifiedReferenceTests(unittest.TestCase):
    def test_a_qualified_reference_names_its_repository(self):
        parsed = markdown.parse("Implements: example-owner/design-records#39\nDesign: example-owner/design-records#40\n")
        self.assertEqual(parsed.implements, markdown.Ref("example-owner/design-records", 39))
        self.assertEqual(parsed.design, markdown.Ref("example-owner/design-records", 40))

    def test_references_in_a_block_of_text(self):
        refs = markdown.references("CR: #2, also a/b#3 and x-y/z.w#4, not ab#5 or a/b/c#6 or &#7;")
        self.assertEqual(refs, [markdown.Ref(None, 2), markdown.Ref("a/b", 3), markdown.Ref("x-y/z.w", 4)])

    def test_malformed_qualified_references_do_not_match(self):
        for line in ("Implements: owner#5", "Implements: a/b/c#5", "Implements: /b#5", "Implements: a/#5",
                     "Implements: a/b#", "Implements: a/b#1234567890", "Implements: a b/c#5", "implements: a/b#5"):
            with self.subTest(line=line):
                self.assertIsNone(markdown.parse(line).implements)

    def test_a_marker_may_carry_a_hyphenated_type(self):
        for kind in ("template-sync", "init"):
            self.assertEqual(markdown.parse(f"<!-- PR type: {kind}. -->").marker, kind)


class HasContentTests(unittest.TestCase):
    def test_cases(self):
        self.assertFalse(markdown.has_content(""))
        self.assertFalse(markdown.has_content(" \n\t\n"))
        self.assertTrue(markdown.has_content("- [ ] item"))
        self.assertTrue(markdown.has_content("x"))


if __name__ == "__main__":
    unittest.main()
