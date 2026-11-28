"""A release that ships seeded files its manifest already named (`.github/dependabot.yml`, `.github/CODEOWNERS`, `SECURITY.md`) reaches a
project generated before the release: written once, never over a copy of the project, with `CODEOWNERS` made for the project's own
approver, and the result passes TC-1 and `owned-drift`.

The template here has its own three versions: A has the manifest patterns but not the three files (the state before they were seeded), B
adds them, C changes an owned file. Projects are generated from A or B by `scripts/initialize-project` with the second fixture configuration.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import syncfx
from syncfx import base, git, read, write

from pr_contract import markdown, template_rules  # noqa: E402
from test_sync_apply import _GitClient, _Pr, changed_files  # noqa: E402

THREE = (".github/dependabot.yml", ".github/CODEOWNERS", "SECURITY.md")
TEMPLATE_LINE = b"* @template-approver\n"  # what B ships: the approver of the template, not of any project
SYNC_FILES = {".template/state.toml", ".template/manifest.toml"}  # every sync writes both: the manifest carries the version
PROJECT_LINE = b"* @example-lead\n"  # approver_login of the second fixture configuration
_cache = {}


def _template():
    """(directory that holds `<owner>/<name>`, {name: commit}) of a template with the versions A, B and C. Do not modify it."""
    if "template" not in _cache:
        scratch = Path(tempfile.mkdtemp(prefix="template-sync-seeded-"))
        _cache["scratch"] = scratch
        repo = scratch / "remotes" / syncfx.SOURCE
        base.export_template(repo)
        git(repo, "init", "-q", "-b", "main")
        write(repo, THREE[1], TEMPLATE_LINE)
        shipped = {rel: read(repo, rel) for rel in THREE}
        for rel in THREE:
            (repo / rel).unlink()
        syncfx.set_version(repo, "1.0.0")
        shas = {"A": syncfx.commit(repo, "A")}
        syncfx.set_tag(repo, "v1.0.0", shas["A"])
        for rel, data in shipped.items():
            write(repo, rel, data)
        syncfx.set_version(repo, "1.1.0")
        shas["B"] = syncfx.commit(repo, "B")
        syncfx.set_tag(repo, "v1.1.0", shas["B"])
        syncfx.edit(repo, ".claude/rules/bash.md", lambda t: t + "- A line that only version C has.\n")
        syncfx.set_version(repo, "1.2.0")
        shas["C"] = syncfx.commit(repo, "C")
        syncfx.set_tag(repo, "v1.2.0", shas["C"])
        git(repo, "repack", "-a", "-d", "-q")
        _cache["template"] = (scratch / "remotes", shas)
    return _cache["template"]


def tearDownModule():
    if "scratch" in _cache:
        shutil.rmtree(_cache["scratch"], True)


class Case(unittest.TestCase):
    """A private copy of the template history and a project generated from one of its tags."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="template-sync-seeded-test-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        root, self.shas = _template()
        self.remotes = self.tmp / "remotes"
        self.remote = self.remotes / syncfx.SOURCE
        shutil.copytree(root / syncfx.SOURCE / ".git", self.remote / ".git", symlinks=True)
        self.tree = self.tmp / "project"

    def generate(self, tag="v1.0.0", commit="A", old_project=True):
        """The project: generated from `tag`; `old_project` removes the CODEOWNERS that this checkout's initialize-project writes, as a
        project generated before it did that has none."""
        syncfx.archive(self.remote, tag, self.tree)
        args = ["--template-commit", self.shas[commit]] if commit else []
        done = base.run_init(self.tree, syncfx.CONFIG, *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        if old_project and (self.tree / THREE[1]).exists():
            (self.tree / THREE[1]).unlink()
        git(self.tree, "init", "-q", "-b", "main")
        self.before = syncfx.commit(self.tree, "initial")

    def run_sync(self, *args):
        command = [sys.executable, "-B", str(self.tree / "scripts" / "template-sync"), *args, "--fixture", str(self.remotes)]
        return subprocess.run(command, cwd=self.tree, capture_output=True, text=True, env=syncfx.env())

    def body(self):
        return json.loads((self.remotes / "created-pr.json").read_text())["body"]


class NewSeededFilesAreAdded(Case):
    def setUp(self):
        super().setUp()
        self.generate()
        self.assertFalse(any((self.tree / rel).exists() for rel in THREE))
        self.done = self.run_sync("v1.1.0")

    def test_the_sync_succeeds_and_lists_the_three_files(self):
        self.assertEqual(self.done.returncode, 0, self.done.stderr + self.done.stdout)
        for rel in THREE:
            self.assertIn(rel, self.done.stdout)

    def test_dependabot_and_security_are_the_files_of_the_tag(self):
        tag_tree = syncfx.archive(self.remote, "v1.1.0", self.tmp / "b-tree")
        for rel in (THREE[0], THREE[2]):
            self.assertEqual(read(self.tree, rel), read(tag_tree, rel), rel)

    def test_codeowners_names_the_approver_of_the_project_and_not_the_one_of_the_tag(self):
        self.assertEqual(read(self.tree, THREE[1]), PROJECT_LINE)
        self.assertNotIn(b"template-approver", read(self.tree, THREE[1]))

    def test_the_sync_commit_holds_the_three_files_and_the_new_state(self):
        names = set(git(self.tree, "diff", "--name-only", self.before, "HEAD").splitlines())
        self.assertEqual(names, {*THREE, *SYNC_FILES})

    def test_the_body_lists_them_under_changed_files_by_class_and_the_result_passes_tc_1(self):
        parsed = markdown.parse(self.body())
        for rel in THREE:
            self.assertIn(rel, parsed.sections["Changed files by class"])
        files = changed_files(self.tree, self.before, "HEAD")
        self.assertEqual({f.path for f in files} - SYNC_FILES, set(THREE))
        self.assertEqual({f.status for f in files}, {"added", "modified"})
        pr = _Pr(self.before, git(self.tree, "rev-parse", "HEAD").strip(), "template-sync/v1.1.0")
        found = template_rules.check_template_sync(pr, files, _GitClient(self.tree), parsed)
        self.assertEqual([v.message for v in found], [])

    def test_tc_1_rejects_the_same_list_when_the_body_leaves_one_out(self):
        files = changed_files(self.tree, self.before, "HEAD")
        pr = _Pr(self.before, git(self.tree, "rev-parse", "HEAD").strip(), "template-sync/v1.1.0")
        parsed = markdown.parse(self.body().replace("SECURITY.md", "other.md"))
        found = template_rules.check_template_sync(pr, files, _GitClient(self.tree), parsed)
        self.assertEqual([v.message for v in found], ["added seeded path is not listed under 'Changed files by class': SECURITY.md"])

    def test_the_result_passes_owned_drift(self):
        done = subprocess.run(["bash", str(self.tree / "scripts/checks/owned-drift.sh")], input="", cwd=self.tree, capture_output=True, text=True,
                              env=syncfx.env({"GATE_BASE_REF": self.before, "GITHUB_HEAD_REF": "template-sync/v1.1.0"}))
        self.assertEqual((done.returncode, done.stdout + done.stderr), (0, ""))

    def test_a_second_sync_to_the_same_tag_after_the_merge_has_nothing_to_do(self):
        git(self.tree, "checkout", "-q", "main")
        git(self.tree, "merge", "-q", "--ff-only", "template-sync/v1.1.0")
        done = self.run_sync("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("nothing to do", done.stdout)

    def test_the_gate_checks_of_the_seeded_files_pass_in_the_synced_project(self):
        for check in ("codeowners", "dependabot-config"):
            done = subprocess.run(["bash", str(self.tree / "scripts/quality-gate.sh"), "--only", check], cwd=self.tree, capture_output=True,
                                  text=True, env=syncfx.env())
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn(f"PASS {check}", done.stdout)


class ExistingFilesOfTheProjectStay(Case):
    CUSTOM = {THREE[0]: b"version: 2\nupdates: []\n", THREE[1]: b"* @example-lead\n", THREE[2]: b"# Our own policy\n"}

    def test_a_copy_of_the_project_is_never_overwritten_and_not_listed(self):
        self.generate()
        for rel, data in self.CUSTOM.items():
            write(self.tree, rel, data)
        before = syncfx.commit(self.tree, "the project has its own files")
        done = self.run_sync("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        for rel, data in self.CUSTOM.items():
            self.assertEqual(read(self.tree, rel), data, rel)
        self.assertEqual(set(git(self.tree, "diff", "--name-only", before, "HEAD").splitlines()), SYNC_FILES)
        for rel in THREE:
            self.assertNotIn(rel, done.stdout)

    def test_one_missing_file_is_added_and_the_other_two_stay(self):
        self.generate()
        for rel in (THREE[0], THREE[2]):
            write(self.tree, rel, self.CUSTOM[rel])
        before = syncfx.commit(self.tree, "two own files")
        done = self.run_sync("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(read(self.tree, THREE[1]), PROJECT_LINE)
        for rel in (THREE[0], THREE[2]):
            self.assertEqual(read(self.tree, rel), self.CUSTOM[rel], rel)
        self.assertEqual(set(git(self.tree, "diff", "--name-only", before, "HEAD").splitlines()), {THREE[1], *SYNC_FILES})


class FilesTheProjectRemovedAreNotAddedBack(Case):
    def test_a_file_that_the_recorded_commit_had_and_the_project_deleted_stays_deleted(self):
        self.generate("v1.1.0", "B", old_project=False)
        self.assertTrue(all((self.tree / rel).exists() for rel in THREE))
        (self.tree / "SECURITY.md").unlink()
        write(self.tree, THREE[1], b"* @example-lead\n")
        syncfx.commit(self.tree, "the project dropped its policy")
        done = self.run_sync("v1.2.0")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertFalse((self.tree / "SECURITY.md").exists())
        self.assertNotIn("SECURITY.md", done.stdout)


class WhenTheBaseIsUnknownOrTheApproverIsNot(Case):
    def test_without_a_recorded_commit_nothing_is_added_for_a_path_the_manifest_already_names(self):
        # The sync cannot tell a deleted file from one that was never shipped; it keeps the project's choice.
        self.generate(commit=None)
        done = self.run_sync("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertFalse(any((self.tree / rel).exists() for rel in THREE))

    def test_a_configuration_without_a_valid_approver_adds_the_other_two_and_says_why(self):
        self.generate()
        text = read(self.tree, "config/project.yaml").decode()
        write(self.tree, "config/project.yaml", text.replace('approver_login: "example-lead"', 'approver_login: "bad login"'))
        syncfx.commit(self.tree, "broken configuration")
        done = self.run_sync("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertFalse((self.tree / THREE[1]).exists())
        self.assertTrue((self.tree / THREE[0]).is_file() and (self.tree / THREE[2]).is_file())
        self.assertIn("Note: .github/CODEOWNERS is new in the tag but was not added", done.stdout)


if __name__ == "__main__":
    unittest.main()
