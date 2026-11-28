"""Unit tests of the pure parts of scripts/lib/template_sync/ (standard library only, no git, no network)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import syncfx  # noqa: F401  (sets up sys.path)

from template_sync import fsops, overrides, settings, state, uses_drift, versions  # noqa: E402

WORKFLOW = """name: ci
jobs:
  build:
    steps:
      - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v4.0.0
      - uses: actions/cache@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v3.0.0
      - run: echo hi
"""


class Versions(unittest.TestCase):
    def test_a_tag_is_v_major_minor_patch_without_leading_zeros(self):
        self.assertEqual(versions.parse_tag("v1.2.3"), (1, 2, 3))
        self.assertEqual(versions.parse_tag("v0.0.0"), (0, 0, 0))
        self.assertEqual(versions.parse_tag("v10.20.30"), (10, 20, 30))
        for bad in ("1.2.3", "v1.2", "v1.2.3" + ".4", "v01.2.3", "v1.2.3-rc1", "V1.2.3", "v1.2.3\n", " v1.2.3", "v1.2.3 ", "vx.y.z", "", "v1.2.3٠",
                    "v1234567890.0.0", "main", "refs/tags/v1.2.3"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                versions.parse_tag(bad)

    def test_a_version_is_major_minor_patch(self):
        self.assertEqual(versions.parse_version("1.2.3"), (1, 2, 3))
        for bad in ("v1.2.3", "1.2", "1.2.3" + ".4", "01.2.3", "", "1.2.3\n"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                versions.parse_version(bad)

    def test_versions_compare_as_numbers(self):
        self.assertLess(versions.parse_version("1.9.0"), versions.parse_version("1.10.0"))
        self.assertEqual(versions.format_tag((1, 2, 3)), "v1.2.3")
        self.assertEqual(versions.format_version((1, 2, 3)), "1.2.3")

    def test_the_lag_line(self):
        recorded = (1, 0, 0)
        self.assertEqual(versions.lag_line(recorded, [(1, 0, 0), (1, 1, 0), (1, 2, 0)]), "behind by 2 releases (recorded v1.0.0, newest v1.2.0)")
        self.assertEqual(versions.lag_line(recorded, [(1, 0, 0), (1, 1, 0)]), "behind by 1 release (recorded v1.0.0, newest v1.1.0)")
        self.assertEqual(versions.lag_line((1, 2, 0), [(1, 0, 0), (1, 2, 0)]), "up to date at v1.2.0")
        self.assertEqual(versions.lag_line((1, 3, 0), [(1, 0, 0), (1, 2, 0)]), "up to date at v1.3.0 (the newest tag of the template is v1.2.0)")
        self.assertEqual(versions.lag_line(recorded, []), "no release tag found in the template (recorded v1.0.0)")
        self.assertLessEqual(len(versions.lag_line((123456789, 123456789, 123456789), [(223456789, 223456789, 223456789)] * 3)), 100)


class UsesDrift(unittest.TestCase):
    """The reading to confirm (design section 8): a change limited to `uses:` lines of the same actions is allowed by owned-drift."""

    def bump(self, text, old="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", new="cccccccccccccccccccccccccccccccccccccccc"):
        return text.replace(old, new).replace("# v4.0.0", "# v4.1.0")

    def test_the_reading_is_one_named_constant_and_it_is_on(self):
        self.assertIs(uses_drift.OWNED_DRIFT_ALLOWS_USES_ONLY, True)
        self.assertIs(uses_drift.SAME_ACTION_ONLY, True)

    def test_a_pin_bump_is_uses_only(self):
        self.assertTrue(uses_drift.is_uses_only(WORKFLOW, self.bump(WORKFLOW)))

    def test_two_bumps_are_uses_only(self):
        changed = self.bump(WORKFLOW).replace("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "dddddddddddddddddddddddddddddddddddddddd")
        self.assertTrue(uses_drift.is_uses_only(WORKFLOW, changed))

    def test_equal_text_is_not_a_drift(self):
        self.assertFalse(uses_drift.is_uses_only(WORKFLOW, WORKFLOW))

    def test_another_line_is_not_uses_only(self):
        self.assertFalse(uses_drift.is_uses_only(WORKFLOW, self.bump(WORKFLOW).replace("echo hi", "echo bye")))

    def test_another_action_is_not_uses_only(self):
        self.assertFalse(uses_drift.is_uses_only(WORKFLOW, WORKFLOW.replace("actions/checkout@", "actions/other@")))

    def test_an_added_or_removed_line_is_not_uses_only(self):
        self.assertFalse(uses_drift.is_uses_only(WORKFLOW, WORKFLOW + "      - uses: actions/checkout@" + "e" * 40 + " # v4\n"))
        self.assertFalse(uses_drift.is_uses_only(WORKFLOW, WORKFLOW.replace("      - run: echo hi\n", "")))

    def test_a_local_action_or_a_container_has_no_pin_to_bump(self):
        base = WORKFLOW.replace("actions/cache@" + "b" * 40 + " # v3.0.0", "./.github/actions/local")
        self.assertFalse(uses_drift.is_uses_only(base, base.replace("local", "other")))

    def test_a_line_that_only_mentions_uses_is_not_a_uses_line(self):
        base = WORKFLOW.replace("echo hi", "echo uses: actions/checkout@" + "a" * 40)
        self.assertFalse(uses_drift.is_uses_only(base, base.replace("a" * 40 + "\n", "c" * 40 + "\n")))

    def test_a_comment_after_the_pin_may_change_and_so_may_the_indent_marker_style(self):
        base = "steps:\n  - uses: actions/checkout@" + "a" * 40 + "\n"
        self.assertTrue(uses_drift.is_uses_only(base, "steps:\n  - uses: actions/checkout@" + "c" * 40 + " # v9\n"))

    def test_line_endings_are_part_of_the_text(self):
        self.assertFalse(uses_drift.is_uses_only(WORKFLOW, self.bump(WORKFLOW).replace("\n", "\r\n")))


class Overrides(unittest.TestCase):
    GOOD = '[[override]]\npath = ".claude/rules/bash.md"\nreason = "why"\npr = "example-owner/sample-app#7"\n'

    def test_entries_are_path_reason_and_pr(self):
        found = overrides.parse(self.GOOD + '\n[[override]]\npath = "scripts/x"\nreason = "r"\npr = "#8"\n')
        self.assertEqual(list(found), [".claude/rules/bash.md", "scripts/x"])
        self.assertEqual(found[".claude/rules/bash.md"].reason, "why")
        self.assertEqual(found["scripts/x"].pr, "#8")

    def test_no_entries_is_fine_and_other_tables_are_left_to_their_readers(self):
        self.assertEqual(overrides.parse(""), {})
        self.assertEqual(overrides.parse('[[budget]]\nclass = "rule"\nkey = "max_bytes"\nvalue = 1\nreason = "r"\npr = "#1"\n'), {})
        self.assertEqual(overrides.parse("# a comment\n"), {})

    def test_malformed_files_raise_with_a_message_that_names_the_entry(self):
        cases = {
            "= =\n": "not valid TOML",
            '[override]\npath = "x"\n': "override must be an array of tables",
            self.GOOD + 'x = 1\n': "entry 1: needs exactly path, reason, and pr",
            self.GOOD + self.GOOD: "entry 2: path is listed twice",
            self.GOOD.replace('reason = "why"', 'reason = ""'): "entry 1: reason must be a non-empty string",
            self.GOOD.replace('pr = "example-owner/sample-app#7"', 'pr = "7"'): "entry 1: pr must be",
            self.GOOD.replace('pr = "example-owner/sample-app#7"', 'pr = "#7 and more"'): "entry 1: pr must be",
            self.GOOD.replace('".claude/rules/bash.md"', '"../x"'): "entry 1: path must be",
            self.GOOD.replace('".claude/rules/bash.md"', '"/x"'): "entry 1: path must be",
            self.GOOD.replace('".claude/rules/bash.md"', '"a//b"'): "entry 1: path must be",
            self.GOOD.replace('".claude/rules/bash.md"', '"a\\\\b"'): "entry 1: path must be",
            self.GOOD.replace('".claude/rules/bash.md"', '".template/budgets.toml"'): "cannot be overridden as a whole file",
            self.GOOD.replace('reason = "why"', 'reason = 5'): "entry 1: reason must be a non-empty string",
        }
        for text, message in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(overrides.OverridesError) as caught:
                    overrides.parse(text)
                self.assertIn(message, str(caught.exception))

    def test_a_reason_with_a_control_character_is_refused(self):
        with self.assertRaises(overrides.OverridesError):
            overrides.parse(self.GOOD.replace('reason = "why"', 'reason = "a\\u0007b"'))


class State(unittest.TestCase):
    OWNED = {"CLAUDE.md": "a" * 64, "scripts/x": "b" * 64}

    def test_render_then_parse_round_trips(self):
        text = state.render("example-owner/template", "1.2.3", "c" * 40, self.OWNED, {"scripts/x": "override"})
        parsed = state.parse(text)
        self.assertEqual((parsed.source, parsed.version, parsed.commit), ("example-owner/template", "1.2.3", "c" * 40))
        self.assertEqual(parsed.owned, self.OWNED)
        self.assertEqual(parsed.overrides, {"scripts/x": "override"})

    def test_the_commit_is_optional_and_then_absent(self):
        text = state.render("example-owner/template", "1.2.3", None, self.OWNED, {})
        self.assertNotIn("commit", text)
        self.assertIsNone(state.parse(text).commit)

    def test_paths_are_written_sorted(self):
        text = state.render("example-owner/template", "1.2.3", None, {"b": "0" * 64, "a": "1" * 64}, {})
        self.assertLess(text.index('"a"'), text.index('"b"'))

    def test_parse_is_strict(self):
        good = state.render("example-owner/template", "1.2.3", "c" * 40, self.OWNED, {})
        for name, text in {
            "toml": "= =", "schema": good.replace("schema_version = 1", "schema_version = 2"), "extra": good + "x = 1\n",
            "extra key in template": good.replace("[owned]", 'x = "y"\n\n[owned]'),
            "version": good.replace('"1.2.3"', '"1.2"'), "hash": good.replace("a" * 64, "A" * 64),
            "source": good.replace("example-owner/template", "https://x/y"), "commit": good.replace("c" * 40, "c" * 39),
            "path": good.replace('"scripts/x"', '"../x"'), "missing owned": good.replace("[owned]", "[owned2]"),
            "override value": good.replace("[overrides]\n", '[overrides]\n"CLAUDE.md" = "no"\n'),
        }.items():
            with self.subTest(name=name):
                with self.assertRaises(state.StateError):
                    state.parse(text)


class Settings(unittest.TestCase):
    TAG = json.dumps({"permissions": {"disableBypassPermissionsMode": "disable", "deny": ["Bash(a *)", "Bash(b *)"]}, "k": True}, indent=2) + "\n"

    def test_without_entries_to_add_the_tag_bytes_are_returned_unchanged(self):
        self.assertEqual(settings.regenerate(self.TAG.encode(), "# nothing\n\n"), self.TAG.encode())
        self.assertEqual(settings.regenerate(self.TAG.encode(), "Bash(a *)\n"), self.TAG.encode())

    def test_entries_of_the_seeded_list_that_the_tag_lacks_are_appended_in_file_order(self):
        out = json.loads(settings.regenerate(self.TAG.encode(), "# platform\nBash(z *)\n  Bash(a *)  \nBash(y *)\nBash(z *)\n"))
        self.assertEqual(out["permissions"]["deny"], ["Bash(a *)", "Bash(b *)", "Bash(z *)", "Bash(y *)"])
        self.assertEqual(out["k"], True)

    def test_the_format_is_the_one_of_initialize_project(self):
        out = settings.regenerate(self.TAG.encode(), "Bash(z *)\n")
        self.assertEqual(out, (json.dumps(json.loads(out), indent=2) + "\n").encode())

    def test_an_entry_that_is_not_a_deny_rule_or_a_file_that_cannot_take_it_is_refused(self):
        for bad in ("not a rule!\n", "Bash(\n", "1abc\n"):
            with self.assertRaises(ValueError, msg=bad):
                settings.regenerate(self.TAG.encode(), bad)
        for tag in (b"not json", b"[]", b'{"permissions": {}}', b'{"permissions": {"deny": "x"}}', b'{"permissions": {"deny": [1]}}'):
            with self.assertRaises(ValueError, msg=tag):
                settings.regenerate(tag, "Bash(z *)\n")

    def test_the_lines_are_split_as_initialize_project_splits_them(self):
        out = json.loads(settings.regenerate(self.TAG.encode(), "Bash(p *) Bash(q *)\n"))
        self.assertEqual(out["permissions"]["deny"][-2:], ["Bash(p *)", "Bash(q *)"])


class Paths(unittest.TestCase):
    def test_a_path_is_relative_posix_and_plain(self):
        for good in ("a", "a/b.c", ".claude/rules/x.md", "scripts/lib/a_b-c.py"):
            fsops.check_path(good)
        for bad in ("", "/a", "a/", "a//b", "./a", "a/./b", "../a", "a/../b", "a\\b", "a\x00b", "a\nb", ".git/config", "a/.git/x", ".GIT/x", "a‮b",
                    "x" * 300):
            with self.assertRaises(ValueError, msg=repr(bad)):
                fsops.check_path(bad)

    def test_safe_write_stays_inside_the_root_and_replaces_files_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            fsops.safe_write(root, "a/b.txt", b"one", 0o644)
            self.assertEqual((root / "a/b.txt").read_bytes(), b"one")
            fsops.safe_write(root, "a/b.txt", b"two", 0o755)
            self.assertEqual((root / "a/b.txt").read_bytes(), b"two")
            self.assertTrue((root / "a/b.txt").stat().st_mode & 0o111)
            os.symlink(outside, root / "link")
            with self.assertRaises(OSError):
                fsops.safe_write(root, "link/x.txt", b"x", 0o644)
            self.assertEqual(list(outside.iterdir()), [])
            os.symlink(outside / "target", root / "a/c.txt")
            fsops.safe_write(root, "a/c.txt", b"three", 0o644)
            self.assertFalse((outside / "target").exists())
            self.assertFalse((root / "a/c.txt").is_symlink())

    def test_safe_remove_removes_a_file_and_the_directories_it_leaves_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fsops.safe_write(root, "a/b/c.txt", b"x", 0o644)
            fsops.safe_write(root, "a/keep.txt", b"x", 0o644)
            fsops.safe_remove(root, "a/b/c.txt")
            self.assertFalse((root / "a/b").exists())
            self.assertTrue((root / "a/keep.txt").exists())


if __name__ == "__main__":
    unittest.main()
