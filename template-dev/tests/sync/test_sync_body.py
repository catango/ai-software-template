"""The body of a sync PR lists every new seeded path, as TC-1 requires, and is refused rather than cut when it would not fit."""
import unittest

import syncfx
from pr_contract import markdown, template_rules  # noqa: E402
from template_sync import body, plan  # noqa: E402
from template_sync.errors import Refused  # noqa: E402

TEMPLATE = (syncfx.ROOT / ".github" / "PULL_REQUEST_TEMPLATE" / "template-sync.md").read_text()
BASE_MANIFEST = 'schema_version = 1\nversion = "1.0.0"\n\n[classes]\nowned = ["scripts/"]\nseeded = [".template/state.toml"]\ntemplate-only = []\n'
HEAD_MANIFEST = 'schema_version = 1\nversion = "1.1.0"\n\n[classes]\nowned = ["scripts/"]\nseeded = [".template/state.toml", "docs/seeded/"]\ntemplate-only = []\n'
HEAD_STATE = 'schema_version = 1\n\n[template]\nsource = "example-owner/template"\nversion = "1.1.0"\n\n[owned]\n\n[overrides]\n'


class _Pr:
    base_sha, head_sha, head_ref = "base", "head", "template-sync/v1.1.0"


class _File:
    def __init__(self, path, status):
        self.path, self.status, self.previous = path, status, None


class _Client:
    def file_text(self, sha, path):
        return {("base", ".template/manifest.toml"): BASE_MANIFEST, ("head", ".template/manifest.toml"): HEAD_MANIFEST,
                ("head", ".template/state.toml"): HEAD_STATE}.get((sha, path))


def seeded_plan(paths):
    return plan.Plan([plan.Change(p, "seeded") for p in paths], {}, [], [], [], [], "")


def build(paths):
    return body.build(TEMPLATE, seeded_plan(paths), "example-owner/template", "1.0.0", "a" * 40, "v1.1.0", "b" * 40, {})


class SeededPaths(unittest.TestCase):
    def test_201_new_seeded_paths_are_all_listed_and_the_body_passes_tc_1(self):
        paths = [f"docs/seeded/file-{number:04d}.md" for number in range(201)]
        text = build(paths)
        section = markdown.parse(text).sections["Changed files by class"]
        for path in paths:
            self.assertIn(path, section)
        files = [_File(".template/state.toml", "modified")] + [_File(path, "added") for path in paths]
        found = template_rules.check_template_sync(_Pr(), files, _Client(), markdown.parse(text))
        self.assertEqual([v.message for v in found], [])

    def test_the_other_lists_are_still_cut_at_200_with_a_count(self):
        many = plan.Plan([plan.Change(f"scripts/lib/f{number:04d}.py", "added") for number in range(250)], {}, [], [], [], [], "")
        text = body.build(TEMPLATE, many, "example-owner/template", "1.0.0", "a" * 40, "v1.1.0", "b" * 40, {})
        self.assertIn("and 50 more", text)

    def test_a_body_that_would_not_fit_is_refused_and_not_cut(self):
        paths = [f"docs/seeded/{'x' * 80}-{number:05d}.md" for number in range(1000)]
        with self.assertRaises(Refused) as caught:
            build(paths)
        self.assertIn("too large", str(caught.exception))
        self.assertIn("seeded", str(caught.exception))

    def test_a_body_just_inside_the_limit_is_accepted(self):
        paths = [f"docs/seeded/file-{number:04d}.md" for number in range(900)]
        self.assertLessEqual(len(build(paths)), markdown.MAX_BODY)


if __name__ == "__main__":
    unittest.main()
