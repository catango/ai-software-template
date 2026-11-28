"""T-9: `scripts/template-sync` from version A to version B, run against a local git repository as the template (`--fixture`)."""
import json
import re
import unittest
from pathlib import Path

import syncfx
from syncfx import base, git, read, sha256, write

from pr_contract import markdown, template_rules  # noqa: E402

OVERRIDES = ".template/overrides.toml"
OVERRIDDEN = ".claude/rules/workflows.md"
LOCAL_TEXT = "\n- A local line of the project.\n"
SECTIONS = ("Summary", "Versions", "Changed files by class", "Overrides honoured", "Drift limited to uses lines", "Release notes", "Checkpoint")


def bump_first_uses(text):
    """What a Dependabot pin bump does to a workflow: the commit of one `uses:` line and its version comment."""
    return re.sub(r"(uses: [^\s@]+@)[0-9a-f]{40}( # )v[0-9.]+", lambda m: m.group(1) + "0" * 40 + m.group(2) + "v9.9.9", text, count=1)


class _Pr:
    def __init__(self, base_sha, head_sha, head_ref):
        self.base_sha, self.head_sha, self.head_ref = base_sha, head_sha, head_ref


class _File:
    def __init__(self, path, status):
        self.path, self.status, self.previous = path, status, None


class _GitClient:
    def __init__(self, tree):
        self.tree = tree

    def file_text(self, sha, path):
        import subprocess
        done = subprocess.run(["git", "-C", str(self.tree), "show", f"{sha}:{path}"], capture_output=True, env=syncfx.env())
        return done.stdout.decode() if done.returncode == 0 else None


def changed_files(tree, base_sha, head_sha):
    names = {"A": "added", "M": "modified", "D": "removed"}
    out = git(tree, "diff", "--name-status", "--no-renames", base_sha, head_sha)
    return [_File(line.split("\t")[1], names[line.split("\t")[0]]) for line in out.splitlines()]


class SyncFromAToB(unittest.TestCase):
    """A project generated from A, with local edits, synced to B (see syncfx.py for what B changes)."""

    def setUp(self):
        self.fx = syncfx.Fixture(self)
        t = self.fx.tree
        # local edits: an owned file (B leaves it alone), an owned file with an override entry (B changes it too), a pin bump in a
        # workflow that B leaves alone, a pin bump in a workflow that B also changes, and a seeded file that B also changes
        write(t, ".claude/rules/docs.md", read(t, ".claude/rules/docs.md") + LOCAL_TEXT.encode())
        write(t, OVERRIDDEN, read(t, OVERRIDDEN) + LOCAL_TEXT.encode())
        write(t, OVERRIDES, '[[override]]\npath = ".claude/rules/workflows.md"\nreason = "the project words this rule its own way"\npr = "example-org/other-service#7"\n')
        write(t, ".github/workflows/ci.yml", bump_first_uses(read(t, ".github/workflows/ci.yml").decode()))
        write(t, ".github/workflows/pr-contract.yml", bump_first_uses(read(t, ".github/workflows/pr-contract.yml").decode()))
        write(t, "docs/PROJECT.md", read(t, "docs/PROJECT.md") + LOCAL_TEXT.encode())
        self.before_local = self.fx.commit_local("local edits")
        self.b_tree = syncfx.archive(self.fx.remote, "v1.1.0", self.fx.tmp / "b-tree")
        self.a_tree = syncfx.archive(self.fx.remote, "v1.0.0", self.fx.tmp / "a-tree")
        self.snapshot_before = self.fx.snapshot()
        self.done = self.fx.run("v1.1.0")

    # ---------------------------------------------------------------------------------------------- the run

    def test_it_succeeds_on_a_new_branch_with_one_commit_and_says_so_in_one_summary_line(self):
        self.assertEqual(self.done.returncode, 0, self.done.stderr + self.done.stdout)
        self.assertEqual(self.fx.branch(), "template-sync/v1.1.0")
        self.assertEqual(self.fx.git("rev-parse", "HEAD~1").strip(), self.before_local)
        self.assertTrue(self.fx.clean())
        lines = self.done.stdout.splitlines()
        self.assertTrue(lines[-1].startswith("template-sync: "), lines[-1])
        self.assertLessEqual(len(lines[-1]), 120)
        self.assertEqual(sum(1 for line in lines if line.startswith("template-sync: ")), 1)

    def test_it_pushes_nothing_and_opens_no_pull_request_in_fixture_mode_but_records_the_one_it_would_open(self):
        created = json.loads((self.fx.remotes / "created-pr.json").read_text())
        self.assertEqual((created["head"], created["base"], created["draft"]), ("template-sync/v1.1.0", "main", True))
        self.assertIn("v1.1.0", created["title"])

    # ---------------------------------------------------------------------------------------------- owned files

    def test_owned_files_take_the_tag_version_also_a_locally_changed_one_without_an_entry(self):
        for rel in (".claude/rules/bash.md", ".claude/rules/docs.md"):
            self.assertEqual(read(self.fx.tree, rel), read(self.b_tree, rel), rel)
        self.assertEqual(read(self.fx.tree, ".claude/rules/docs.md"), read(self.a_tree, ".claude/rules/docs.md"))

    def test_owned_files_are_added_and_removed(self):
        self.assertEqual(read(self.fx.tree, "scripts/lib/template_sync_fixture_added.py"), b"# added in B\n")
        self.assertTrue((self.fx.tree / "scripts/lib/template_sync_fixture_added.py").stat().st_mode & 0o111, "the executable bit is kept")
        self.assertFalse((self.fx.tree / "scripts/lib/template_sync_fixture_removed.py").exists())

    def test_a_changed_workflow_is_overwritten_with_the_tag_version(self):
        self.assertEqual(read(self.fx.tree, ".github/workflows/pr-contract.yml"), read(self.b_tree, ".github/workflows/pr-contract.yml"))

    def test_drift_limited_to_uses_lines_is_overwritten(self):
        self.assertEqual(read(self.fx.tree, ".github/workflows/ci.yml"), read(self.b_tree, ".github/workflows/ci.yml"))

    # ---------------------------------------------------------------------------------------------- overrides

    def test_an_overridden_file_is_kept_as_it_was(self):
        self.assertEqual(read(self.fx.tree, OVERRIDDEN), read(syncfx.project(), OVERRIDDEN) + LOCAL_TEXT.encode())
        self.assertNotEqual(read(self.fx.tree, OVERRIDDEN), read(self.b_tree, OVERRIDDEN))

    def test_overrides_toml_is_never_changed(self):
        self.assertEqual(self.fx.git("diff", "--name-only", self.before_local, "HEAD").splitlines().count(OVERRIDES), 0)

    def test_the_override_is_recorded_as_override_with_the_hash_of_the_tag_file(self):
        state = self.fx.state()
        self.assertEqual(state["overrides"], {OVERRIDDEN: "override"})
        self.assertEqual(state["owned"][OVERRIDDEN], sha256(read(self.b_tree, OVERRIDDEN)))

    # ---------------------------------------------------------------------------------------------- seeded files

    def test_a_seeded_file_is_never_changed_or_removed(self):
        self.assertEqual(read(self.fx.tree, "docs/PROJECT.md"), read(syncfx.project(), "docs/PROJECT.md") + LOCAL_TEXT.encode())
        manifest = base.template_manifest.parse_manifest(read(self.b_tree, ".template/manifest.toml").decode())
        after = self.fx.snapshot()
        seeded = [p for p in self.snapshot_before if manifest.classify(p) == ["seeded"] and p != ".template/state.toml"]
        self.assertGreater(len(seeded), 5)
        for path in seeded:
            self.assertEqual(after.get(path), self.snapshot_before[path], path)

    def test_a_seeded_file_that_is_new_in_the_tag_is_added_once(self):
        self.assertEqual(read(self.fx.tree, "docs/NEW-SEEDED.md"), b"# A seeded file that is new in B\n")

    def test_a_new_file_inside_a_seeded_pattern_that_the_project_already_has_is_not_added(self):
        self.assertFalse((self.fx.tree / "scripts/checks.d/zz-new.sh").exists())

    def test_a_template_only_file_never_arrives(self):
        self.assertFalse((self.fx.tree / "template-dev/docs/only-in-b.md").exists())

    # ---------------------------------------------------------------------------------------------- state.toml

    def test_state_toml_records_the_version_the_commit_and_every_owned_file(self):
        state = self.fx.state()
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["template"], {"source": syncfx.SOURCE, "version": "1.1.0", "commit": self.fx.shas["B"]})
        expected = {p for p in syncfx.owned_paths(self.b_tree)}
        self.assertEqual(set(state["owned"]), expected)
        for rel, digest in state["owned"].items():
            if rel != OVERRIDDEN:
                self.assertEqual(digest, sha256(read(self.fx.tree, rel)), rel)

    def test_state_toml_is_the_format_of_initialize_project(self):
        text = read(self.fx.tree, ".template/state.toml").decode()
        self.assertTrue(text.startswith("# Written by scripts/initialize-project and by template-sync only."))
        self.assertIn("\n[overrides]\n", text)

    # ---------------------------------------------------------------------------------------------- settings.json

    def test_settings_json_is_the_tag_file_plus_the_entries_of_the_seeded_list_that_the_tag_lacks(self):
        deny = json.loads(read(self.fx.tree, ".claude/settings.json"))["permissions"]["deny"]
        tag_deny = json.loads(read(self.b_tree, ".claude/settings.json"))["permissions"]["deny"]
        self.assertEqual(deny, tag_deny + ["Bash(example-cli delete *)"])
        self.assertIn(syncfx.CORE_ADDED_IN_B, deny)
        self.assertNotIn(syncfx.CORE_REMOVED_IN_B, deny)
        self.assertEqual(deny.count("Bash(example-cli apply *)"), 1)
        self.assertEqual(read(self.fx.tree, ".claude/deny.platform.txt"), read(syncfx.project(), ".claude/deny.platform.txt"))

    def test_the_regenerated_file_passes_the_deny_list_check(self):
        done = base.subprocess.run(["bash", str(self.fx.tree / "scripts/checks/deny-list.sh")], input=".claude/settings.json\n", cwd=self.fx.tree,
                                   capture_output=True, text=True, env=syncfx.env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    # ---------------------------------------------------------------------------------------------- the pull request

    def body(self):
        return json.loads((self.fx.remotes / "created-pr.json").read_text())["body"]

    def test_the_body_comes_from_the_template_and_every_section_is_filled(self):
        body = self.body()
        parsed = markdown.parse(body)
        for name in SECTIONS:
            self.assertTrue(markdown.has_content(parsed.sections.get(name, "")), name)
        self.assertIn("<!-- checkpoint:start -->", body)
        self.assertIn("- [x] Sync run and state file updated", body)
        self.assertIn("- [x] Changed files listed by class", body)
        self.assertIn("- [ ] Quality gate passes locally", body)
        self.assertIn("- [ ] Marked ready for review", body)

    def test_the_body_names_the_versions_and_the_tag_commit(self):
        section = markdown.parse(self.body()).sections["Versions"]
        for text in ("v1.0.0", "v1.1.0", self.fx.shas["B"]):
            self.assertIn(text, section)

    def test_the_body_lists_the_changes_by_class(self):
        section = markdown.parse(self.body()).sections["Changed files by class"]
        for path in ("docs/NEW-SEEDED.md", ".claude/rules/bash.md", ".claude/rules/docs.md", "scripts/lib/template_sync_fixture_added.py",
                     "scripts/lib/template_sync_fixture_removed.py", ".github/workflows/pr-contract.yml", ".claude/settings.json"):
            self.assertIn(path, section)
        self.assertNotIn("docs/PROJECT.md", section)
        self.assertNotIn("zz-new.sh", section)

    def test_the_body_lists_the_override_and_the_uses_drift_and_justifies_the_workflow_change(self):
        sections = markdown.parse(self.body()).sections
        for text in (OVERRIDDEN, "the project words this rule its own way", "example-org/other-service#7"):
            self.assertIn(text, sections["Overrides honoured"])
        self.assertIn(".github/workflows/ci.yml", sections["Drift limited to uses lines"])
        self.assertNotIn("pr-contract.yml", sections["Drift limited to uses lines"], "a pin bump next to a template change is not uses-only")
        self.assertIn(".github/workflows/pr-contract.yml", sections["Workflow change justification"])

    def test_the_body_links_the_release_notes_of_the_tag(self):
        self.assertIn(f"{syncfx.SOURCE}/releases/tag/v1.1.0", markdown.parse(self.body()).sections["Release notes"])

    # ---------------------------------------------------------------------------------------------- TC-1

    def test_the_resulting_file_list_passes_tc_1(self):
        files = changed_files(self.fx.tree, self.before_local, "HEAD")
        pr = _Pr(self.before_local, self.fx.head(), "template-sync/v1.1.0")
        found = template_rules.check_template_sync(pr, files, _GitClient(self.fx.tree), markdown.parse(self.body()))
        self.assertEqual([v.message for v in found], [])
        self.assertIn(".template/state.toml", [f.path for f in files])
        self.assertNotIn(OVERRIDES, [f.path for f in files])

    def test_tc_1_rejects_a_changed_or_a_removed_seeded_file_in_such_a_list(self):
        files = changed_files(self.fx.tree, self.before_local, "HEAD")
        pr = _Pr(self.before_local, self.fx.head(), "template-sync/v1.1.0")
        parsed = markdown.parse(self.body())
        for status in ("modified", "removed"):
            found = template_rules.check_template_sync(pr, files + [_File("docs/PROJECT.md", status)], _GitClient(self.fx.tree), parsed)
            self.assertTrue(any("not owned in the base manifest" in v.message for v in found), status)

    def test_tc_1_rejects_the_new_seeded_file_when_the_body_does_not_list_it(self):
        files = changed_files(self.fx.tree, self.before_local, "HEAD")
        pr = _Pr(self.before_local, self.fx.head(), "template-sync/v1.1.0")
        parsed = markdown.parse(self.body().replace("docs/NEW-SEEDED.md", "docs/other.md"))
        found = template_rules.check_template_sync(pr, files, _GitClient(self.fx.tree), parsed)
        self.assertTrue(any("is not listed" in v.message for v in found))

    # ---------------------------------------------------------------------------------------------- the same files outside the diff

    def test_nothing_else_changed(self):
        after = self.fx.snapshot()
        changed = {p for p in set(after) | set(self.snapshot_before) if after.get(p) != self.snapshot_before.get(p)}
        self.assertEqual(changed - set(syncfx.owned_paths(self.b_tree)) - set(syncfx.owned_paths(self.a_tree)),
                         {"docs/NEW-SEEDED.md", ".template/state.toml"})


class SecondRunAndDryRun(unittest.TestCase):
    def setUp(self):
        self.fx = syncfx.Fixture(self)

    def test_a_dry_run_prints_the_plan_and_writes_nothing(self):
        before = self.fx.snapshot()
        branches = self.fx.git("branch", "--list")
        done = self.fx.run("v1.1.0", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.fx.snapshot(), before)
        self.assertEqual(self.fx.git("branch", "--list"), branches)
        self.assertTrue(self.fx.clean())
        self.assertFalse((self.fx.remotes / "created-pr.json").exists())
        for text in ("v1.0.0", "v1.1.0", "docs/NEW-SEEDED.md", ".claude/rules/bash.md", "scripts/lib/template_sync_fixture_removed.py"):
            self.assertIn(text, done.stdout)
        self.assertRegex(done.stdout.splitlines()[-1], r"^template-sync: dry run")

    def test_a_second_run_after_the_merge_changes_nothing(self):
        self.assertEqual(self.fx.run("v1.1.0").returncode, 0)
        self.fx.merge_branch("template-sync/v1.1.0")
        before, head = self.fx.snapshot(), self.fx.head()
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("nothing to do", done.stdout)
        self.assertEqual((self.fx.snapshot(), self.fx.head(), self.fx.branch()), (before, head, "main"))
        self.assertTrue(self.fx.clean())

    def test_a_sync_from_a_to_c_skips_b(self):
        done = self.fx.run("v1.2.0")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.fx.state()["template"]["version"], "1.2.0")
        self.assertEqual(self.fx.state()["template"]["commit"], self.fx.shas["C"])
        self.assertIn("A line that only version C has", read(self.fx.tree, ".claude/rules/bash.md").decode())

    def test_without_a_state_change_the_state_file_keeps_its_bytes(self):
        """A run that finds the same version and the same files rewrites nothing: state.toml is byte-identical."""
        self.assertEqual(self.fx.run("v1.1.0").returncode, 0)
        self.fx.merge_branch("template-sync/v1.1.0")
        text = read(self.fx.tree, ".template/state.toml")
        self.fx.run("v1.1.0")
        self.assertEqual(read(self.fx.tree, ".template/state.toml"), text)

    def test_the_state_file_is_what_initialize_project_would_write_for_the_same_inputs(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("initialize_project_for_sync_test", str(syncfx.ROOT / "scripts" / "initialize-project"))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        import template_sync.state as state
        owned = {"CLAUDE.md": "a" * 64, "scripts/x": "b" * 64}
        expected = module.toml_state({"template": {"source": syncfx.SOURCE}}, "1.2.3", "c" * 40, owned)
        self.assertEqual(state.render(syncfx.SOURCE, "1.2.3", "c" * 40, owned, {}), expected)


class ADirectoryInTheWay(unittest.TestCase):
    def test_a_directory_where_the_tag_has_a_file_is_refused_before_anything_changes(self):
        fx = syncfx.Fixture(self)
        write(fx.tree, "scripts/lib/template_sync_fixture_added.py/keep", "x")
        fx.commit_local("a directory in the way")
        before = fx.head()
        done = fx.run("v1.1.0")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual((fx.branch(), fx.head()), ("main", before))
        self.assertTrue(fx.clean())
        self.assertNotIn("template-sync/v1.1.0", fx.git("branch", "--list"))


if __name__ == "__main__":
    unittest.main()
