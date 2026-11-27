"""T-10: `scripts/template-sync` refuses what it must, clones with credential helpers disabled, runs nothing of the clone, uses no network."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import syncfx
from syncfx import read, write

SHIM = '''#!/usr/bin/env python3
"""Stands in for git on PATH: logs every call, sends the URL of the template source to a local repository, runs the real git."""
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
config = json.load(open(os.path.join(here, "shim.json")))
args = sys.argv[1:]
keys = ("GH_TOKEN", "GITHUB_TOKEN", "GIT_TERMINAL_PROMPT", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "HOME", "GIT_ASKPASS", "SSH_ASKPASS")
with open(config["log"], "a") as log:
    log.write(json.dumps({"argv": args, "env": {k: os.environ.get(k) for k in keys}}) + "\\n")
rewritten = []
for arg in args:
    if arg.startswith("https://github.com/"):
        arg = os.path.join(config["remotes"], arg[len("https://github.com/"):].removesuffix(".git"))
    rewritten.append(arg)
os.execv(config["real"], [config["real"], "-c", "protocol.file.allow=always", *rewritten])
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.fx = syncfx.Fixture(self)
        self.head = self.fx.head()
        self.snapshot = self.fx.snapshot()

    def sync_and_merge(self, tag="v1.1.0"):
        self.assertEqual(self.fx.run(tag).returncode, 0)
        self.fx.merge_branch(f"template-sync/{tag}")
        self.head, self.snapshot = self.fx.head(), self.fx.snapshot()

    def untouched(self):
        self.assertEqual((self.fx.branch(), self.fx.head()), ("main", self.head))
        self.assertEqual(self.fx.snapshot(), self.snapshot)
        self.assertTrue(self.fx.clean())
        self.assertEqual(self.fx.git("branch", "--list").split(), ["*", "main"])
        self.assertFalse((self.fx.remotes / "created-pr.json").exists())

    def refused(self, *args, code=1, text="", **kwargs):
        done = self.fx.run(*args, **kwargs)
        self.assertEqual(done.returncode, code, done.stdout + done.stderr)
        self.assertIn(text, done.stdout + done.stderr)
        self.untouched()
        return done


class MalformedTargets(Base):
    def test_only_a_tag_name_of_the_form_v_major_minor_patch_is_accepted(self):
        for target in ("main", "0123456789abcdef0123456789abcdef01234567", "v1.2", "1.2.0", "v1.2.0-rc1", "refs/tags/v1.2.0", "v01.2.0",
                       "V1.2.0", "v1.2.0.0", "v1.2.x", "v1.2.0 ", "v1.2.0\n", "HEAD", "origin/main", "", "v1.2.0/../x", "v1.2.0;touch x",
                       "template-sync/v1.2.0", "v1.2.0^{}", "v-1.2.0", "v1.2.0 "):
            with self.subTest(target=target):
                self.refused(target, code=2, text="v<MAJOR>.<MINOR>.<PATCH>")

    def test_an_option_is_not_a_target(self):
        self.refused("-v1.2.0", code=2)

    def test_the_target_is_given_once(self):
        self.refused("v1.1.0", "v1.2.0", code=2)

    def test_no_target_and_no_check_is_a_usage_error(self):
        self.refused(code=2)


class NotATag(Base):
    def test_a_branch_with_the_name_of_a_tag_is_refused(self):
        syncfx.set_branch(self.fx.remote, "v1.6.0", self.fx.shas["C"])
        self.refused("v1.6.0", text="no tag")

    def test_a_tag_that_does_not_exist_is_refused(self):
        self.refused("v9.9.9", text="no tag")

    def test_a_commit_that_is_not_tagged_is_refused_even_by_its_version_name(self):
        self.refused(self.fx.shas["C"], code=2)


class ContainedInTheDefaultBranch(Base):
    def test_a_tag_whose_commit_is_not_in_the_default_branch_is_refused(self):
        self.refused("v1.3.0", text="not contained in the default branch")

    def test_a_tag_on_the_default_branch_is_accepted(self):
        self.assertEqual(self.fx.run("v1.2.0", "--dry-run").returncode, 0)

    def test_a_default_branch_other_than_main_is_followed(self):
        git = self.fx.worktree()
        syncfx.git(git, "branch", "-m", "main", "trunk")
        syncfx.git(git, "checkout", "-q", "-b", "other", self.fx.shas["SIDE"])
        syncfx.git(git, "symbolic-ref", "HEAD", "refs/heads/trunk")
        self.assertEqual(self.fx.run("v1.2.0", "--dry-run").returncode, 0)
        self.refused("v1.3.0", text="not contained in the default branch")


class NoDowngrade(Base):
    def test_an_older_version_is_refused(self):
        self.sync_and_merge("v1.1.0")
        self.refused("v1.0.0", text="downgrade")

    def test_a_downgrade_by_a_larger_number_of_digits_is_judged_numerically(self):
        self.sync_and_merge("v1.2.0")
        self.fx.new_remote_commit("1.10.0", {"docs/x.md": "x"}, tag="v1.10.0")
        self.assertEqual(self.fx.run("v1.10.0", "--dry-run").returncode, 0)
        self.refused("v1.1.0", text="downgrade")

    def test_the_same_version_with_the_same_commit_is_not_a_downgrade(self):
        self.sync_and_merge("v1.1.0")
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("nothing to do", done.stdout)


class MovedTag(Base):
    def test_a_tag_that_points_to_another_commit_than_the_recorded_one_is_refused(self):
        self.sync_and_merge("v1.1.0")
        moved = self.fx.new_remote_commit("1.1.0", {"docs/x.md": "x"}, tag="v1.1.0")
        self.assertNotEqual(moved, self.fx.shas["B"])
        self.refused("v1.1.0", text="moved")

    def test_a_state_without_a_recorded_commit_cannot_vouch_for_the_same_version(self):
        text = read(self.fx.tree, ".template/state.toml").decode()
        write(self.fx.tree, ".template/state.toml", re.sub(r'commit = "[0-9a-f]{40}"\n', "", text))
        self.fx.commit_local("no commit recorded")
        self.head, self.snapshot = self.fx.head(), self.fx.snapshot()
        self.refused("v1.0.0", text="no commit is recorded")


class VersionOfTheManifest(Base):
    def test_a_tag_whose_manifest_names_another_version_is_refused(self):
        self.fx.new_remote_commit(None, {"docs/x.md": "x"}, tag="v1.4.0")  # the manifest still says 1.2.0
        self.refused("v1.4.0", text="manifest")


class StateFile(Base):
    def replace_state(self, text):
        write(self.fx.tree, ".template/state.toml", text)
        self.fx.commit_local("state")
        self.head, self.snapshot = self.fx.head(), self.fx.snapshot()

    def test_a_missing_state_file_is_refused(self):
        (self.fx.tree / ".template/state.toml").unlink()
        self.fx.commit_local("no state")
        self.head, self.snapshot = self.fx.head(), self.fx.snapshot()
        self.refused("v1.1.0", text="state.toml")

    def test_a_corrupt_state_file_is_refused(self):
        good = read(self.fx.tree, ".template/state.toml").decode()
        bad = {
            "not toml": "this is = = not toml\n",
            "empty": "",
            "no template table": re.sub(r"\[template\][^\[]*", "", good),
            "version not a version": good.replace('version = "1.0.0"', 'version = "one"'),
            "version of the wrong type": good.replace('version = "1.0.0"', "version = 1"),
            "commit not hex": re.sub(r'commit = "[0-9a-f]{40}"', 'commit = "xyz"', good),
            "source of another form": re.sub(r'source = "[^"]*"', 'source = "https://example.org/x/y"', good),
            "source with a dot segment": re.sub(r'source = "[^"]*"', 'source = "example-org/.."', good),
            "hash not hex": re.sub(r'("CLAUDE.md" = )"[0-9a-f]{64}"', r'\1"nothex"', good),
            "hash of the wrong type": re.sub(r'("CLAUDE.md" = )"[0-9a-f]{64}"', r"\g<1>5", good),
            "owned path with a dot dot": good.replace("[owned]\n", '[owned]\n"../outside" = "' + "0" * 64 + '"\n'),
            "owned path absolute": good.replace("[owned]\n", '[owned]\n"/etc/passwd" = "' + "0" * 64 + '"\n'),
            "unknown top-level key": good + "\nextra = 1\n",
            "wrong schema version": good.replace("schema_version = 1", "schema_version = 2"),
            "override value other than override": good.replace("[overrides]\n", '[overrides]\n"CLAUDE.md" = "yes"\n'),
            "override of a path that is not owned": good.replace("[overrides]\n", '[overrides]\n"README.md" = "override"\n'),
        }
        for name, text in bad.items():
            with self.subTest(name=name):
                self.assertNotEqual(text, good, name)
                write(self.fx.tree, ".template/state.toml", text)
                self.fx.git("add", "-A")
                self.fx.git("commit", "-q", "-m", name)
                self.head, self.snapshot = self.fx.head(), self.fx.snapshot()
                self.refused("v1.1.0", text="state.toml")
                self.fx.git("reset", "-q", "--hard", "HEAD~1")


class DirtyTree(Base):
    def test_a_modified_file_is_refused(self):
        write(self.fx.tree, "README.md", read(self.fx.tree, "README.md") + b"x")
        self.assertEqual(self.fx.run("v1.1.0").returncode, 1)
        self.assertEqual(self.fx.branch(), "main")

    def test_a_staged_change_is_refused(self):
        write(self.fx.tree, "README.md", read(self.fx.tree, "README.md") + b"x")
        self.fx.git("add", "README.md")
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 1)
        self.assertIn("not clean", done.stdout)

    def test_an_untracked_file_is_refused(self):
        write(self.fx.tree, "notes.txt", "x")
        done = self.fx.run("v1.1.0", "--dry-run")
        self.assertEqual(done.returncode, 1)
        self.assertIn("not clean", done.stdout)
        self.assertEqual(self.fx.branch(), "main")

    def test_an_ignored_file_does_not_count(self):
        write(self.fx.tree, ".gitignore", "ignored.txt\n")
        self.fx.commit_local("ignore")
        write(self.fx.tree, "ignored.txt", "x")
        self.assertEqual(self.fx.run("v1.1.0", "--dry-run").returncode, 0)


class Branches(Base):
    def test_a_branch_other_than_the_default_branch_is_refused(self):
        self.fx.git("checkout", "-q", "-b", "feature/x")
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 1)
        self.assertIn("default branch", done.stdout)
        self.assertEqual(self.fx.branch(), "feature/x")

    def test_a_branch_of_that_name_that_exists_is_refused(self):
        self.fx.git("branch", "template-sync/v1.1.0")
        self.head, self.snapshot = self.fx.head(), self.fx.snapshot()
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 1)
        self.assertIn("already exists", done.stdout)
        self.assertEqual(self.fx.branch(), "main")

    def test_a_detached_head_is_refused(self):
        self.fx.git("checkout", "-q", "--detach")
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 1)


class Source(Base):
    def test_the_source_is_never_an_argument(self):
        for args in (("--source", "other-owner/other"), ("--source=other-owner/other",), ("--remote", "other-owner/other"),
                     ("--repo", "other-owner/other"), ("--url", "https://example.org/x/y.git")):
            with self.subTest(args=args):
                self.refused("v1.1.0", *args, code=2)

    def test_a_source_that_cannot_be_read_is_an_error_and_changes_nothing(self):
        text = read(self.fx.tree, ".template/state.toml").decode().replace(syncfx.SOURCE, "example-org/not-there")
        write(self.fx.tree, ".template/state.toml", text)
        self.fx.commit_local("another source")
        self.head, self.snapshot = self.fx.head(), self.fx.snapshot()
        self.refused("v1.1.0", code=3, text="cannot read the template source")

    def test_only_the_recorded_source_is_read(self):
        other = self.fx.remotes / "other-owner" / "other"
        shutil.copytree(self.fx.remote, other, symlinks=True)
        syncfx.set_tag(other, "v1.1.0", self.fx.shas["C"])
        self.assertEqual(self.fx.run("v1.1.0", "--dry-run").returncode, 0)
        self.assertTrue(self.fx.clean())


class TheCloneIsLockedDown(Base):
    """The git calls of a run without --fixture, seen through a stand-in for git on PATH that sends the one URL to a local repository."""

    def setUp(self):
        super().setUp()
        self.bin = self.fx.tmp / "bin"
        self.bin.mkdir()
        self.log = self.fx.tmp / "git-calls.jsonl"
        shim = self.bin / "git"
        shim.write_text(SHIM)
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        (self.bin / "shim.json").write_text(json.dumps({"log": str(self.log), "remotes": str(self.fx.remotes), "real": shutil.which("git")}))
        self.canary = {"GH_TOKEN": "canary-not-a-token", "GITHUB_TOKEN": "canary-not-a-token-either", "GIT_ASKPASS": "canary-askpass"}

    def run_shimmed(self, *args):
        return self.fx.run(*args, fixture=False, path_prefix=str(self.bin), extra_env=self.canary)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def remote_calls(self):
        return [c for c in self.calls() if any(a.startswith("https://github.com/") for a in c["argv"])]

    def options(self, call):
        argv, found = call["argv"], []
        for index, arg in enumerate(argv[:-1]):
            if arg == "-c":
                found.append(argv[index + 1])
        return found

    def test_every_call_that_reaches_the_remote_disables_credential_helpers_hooks_and_prompts(self):
        done = self.run_shimmed("v1.1.0", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.remote_calls()
        self.assertGreaterEqual(len(calls), 2)
        for call in calls:
            options = self.options(call)
            for wanted in ("credential.helper=", "core.hooksPath=/dev/null", "protocol.allow=never", "protocol.https.allow=always"):
                self.assertIn(wanted, options, call["argv"])
            self.assertIn("-c", call["argv"])
            env = call["env"]
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
            self.assertEqual(env["GIT_CONFIG_SYSTEM"], "/dev/null")
            for key in ("GH_TOKEN", "GITHUB_TOKEN", "SSH_ASKPASS"):
                self.assertIsNone(env[key], key)
            self.assertNotEqual(env["GIT_ASKPASS"], "canary-askpass")
            self.assertNotEqual(env["HOME"], "/nonexistent")
        self.assertTrue(self.fx.clean())

    def test_the_url_is_built_from_the_recorded_source_and_nothing_else(self):
        self.assertEqual(self.run_shimmed("v1.1.0", "--dry-run").returncode, 0)
        urls = {a for c in self.calls() for a in c["argv"] if "://" in a}
        self.assertEqual(urls, {f"https://github.com/{syncfx.SOURCE}.git"})

    def test_the_clone_is_bounded_checks_nothing_out_and_has_no_submodules(self):
        self.assertEqual(self.run_shimmed("v1.1.0", "--dry-run").returncode, 0)
        words = [a for c in self.remote_calls() for a in c["argv"]]
        self.assertTrue(any(a.startswith("--depth") for a in words))
        for forbidden in ("checkout", "clone", "submodule", "--recurse-submodules", "pull", "push", "worktree"):
            self.assertNotIn(forbidden, words)
        subcommands = {c["argv"][next(i for i, a in enumerate(c["argv"]) if not a.startswith("-") and (i == 0 or c["argv"][i - 1] != "-c"))] for c in self.remote_calls()}
        self.assertLessEqual(subcommands, {"fetch", "ls-remote", "init"})

    def test_a_run_with_a_dry_run_uses_only_the_local_repository_and_the_temporary_clone(self):
        before = set(os.listdir(self.fx.tree))
        self.assertEqual(self.run_shimmed("v1.1.0", "--dry-run").returncode, 0)
        self.assertEqual(set(os.listdir(self.fx.tree)), before)

    def test_check_reads_the_tags_and_nothing_more(self):
        done = self.run_shimmed("--check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        subcommands = {next(a for a in c["argv"] if a in ("ls-remote", "fetch", "init", "clone")) for c in self.remote_calls()}
        self.assertEqual(subcommands, {"ls-remote"})
        for call in self.remote_calls():
            self.assertIn("credential.helper=", self.options(call))


class NothingOfTheCloneRuns(Base):
    def test_the_script_of_the_tag_is_written_but_never_run(self):
        sentinel = self.fx.tmp / "ran"
        self.fx.worktree()
        for rel in ("scripts/template-sync", "scripts/lib/template_sync/cli.py", "scripts/checks/owned-drift.sh", "scripts/quality-gate.sh",
                    ".github/workflows/ci.yml"):
            if (self.fx.remote / rel).exists():
                write(self.fx.remote, rel, f"#!/bin/sh\ntouch {sentinel}\n")
        self.fx.new_remote_commit("1.5.0", {"scripts/lib/zz_hook.py": f"open({str(sentinel)!r}, 'w')\n"}, tag="v1.5.0")
        done = self.fx.run("v1.5.0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(sentinel.exists())
        self.assertEqual(read(self.fx.tree, "scripts/template-sync"), f"#!/bin/sh\ntouch {sentinel}\n".encode())

    def test_a_hook_of_the_template_repository_does_not_run(self):
        sentinel = self.fx.tmp / "hook-ran"
        for name in ("post-checkout", "post-merge", "reference-transaction", "pre-auto-gc", "post-rewrite", "post-update", "pre-upload-pack"):
            hook = self.fx.remote / ".git" / "hooks" / name
            hook.write_text(f"#!/bin/sh\ntouch {sentinel}\n")
            hook.chmod(0o755)
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(sentinel.exists())

    def test_a_symbolic_link_in_the_tag_is_refused(self):
        link = self.fx.worktree() / "scripts/lib/zz_link.py"
        os.symlink("/etc/passwd", link)
        syncfx.set_version(self.fx.remote, "1.5.0")
        sha = syncfx.commit(self.fx.remote, "v1.5.0")
        syncfx.set_tag(self.fx.remote, "v1.5.0", sha)
        self.refused("v1.5.0", text="symbolic link")

    def test_a_submodule_in_the_tag_is_refused(self):
        self.fx.worktree()
        syncfx.git(self.fx.remote, "update-index", "--add", "--cacheinfo", f"160000,{self.fx.shas['A']},scripts/lib/zz_sub")
        syncfx.set_version(self.fx.remote, "1.5.0")
        syncfx.git(self.fx.remote, "add", ".template/manifest.toml")
        syncfx.git(self.fx.remote, "commit", "-q", "-m", "a gitlink")
        syncfx.set_tag(self.fx.remote, "v1.5.0", syncfx.git(self.fx.remote, "rev-parse", "HEAD").strip())
        self.refused("v1.5.0", text="submodule")


class TheTagIsJudgedByItsOwnManifest(Base):
    def test_a_file_without_a_class_is_refused(self):
        self.fx.new_remote_commit("1.5.0", {"weird-file.txt": "x"}, tag="v1.5.0")
        self.refused("v1.5.0", text="no class")

    def test_a_file_with_two_classes_is_refused(self):
        text = read(self.fx.worktree(), ".template/manifest.toml").decode().replace("template-only = [\n", 'template-only = [\n  "docs/PROJECT.md",\n', 1)
        self.fx.new_remote_commit("1.6.0", {".template/manifest.toml": text}, tag="v1.6.0")
        self.refused("v1.6.0", text="more than one class")

    def test_a_new_owned_path_that_the_project_already_has_as_a_seeded_file_is_refused(self):
        text = read(self.fx.worktree(), ".template/manifest.toml").decode()
        text = text.replace('  "docs/PLATFORM.md",\n', "", 1).replace('owned = [\n', 'owned = [\n  "docs/PLATFORM.md",\n', 1)
        self.fx.new_remote_commit("1.5.0", {".template/manifest.toml": text}, tag="v1.5.0")
        self.refused("v1.5.0", text="docs/PLATFORM.md")


class Overrides(Base):
    def entries(self, text):
        write(self.fx.tree, ".template/overrides.toml", text)
        self.fx.commit_local("overrides")
        self.head, self.snapshot = self.fx.head(), self.fx.snapshot()

    def test_a_malformed_overrides_file_is_refused(self):
        good = '[[override]]\npath = ".claude/rules/bash.md"\nreason = "r"\npr = "#3"\n'
        for name, text in {
            "not toml": "= =\n",
            "missing reason": '[[override]]\npath = ".claude/rules/bash.md"\npr = "#3"\n',
            "empty reason": '[[override]]\npath = ".claude/rules/bash.md"\nreason = " "\npr = "#3"\n',
            "missing pr": '[[override]]\npath = ".claude/rules/bash.md"\nreason = "r"\n',
            "a pr that is not a reference": '[[override]]\npath = ".claude/rules/bash.md"\nreason = "r"\npr = "soon"\n',
            "unknown key": good + 'extra = "x"\n',
            "path twice": good + good,
            "path with a dot dot": '[[override]]\npath = "../x"\nreason = "r"\npr = "#3"\n',
            "absolute path": '[[override]]\npath = "/x"\nreason = "r"\npr = "#3"\n',
            "the budget file as a whole": '[[override]]\npath = ".template/budgets.toml"\nreason = "r"\npr = "#3"\n',
            "override as a table instead of an array": '[override]\npath = ".claude/rules/bash.md"\nreason = "r"\npr = "#3"\n',
        }.items():
            with self.subTest(name=name):
                self.entries(text)
                self.refused("v1.1.0", text="overrides.toml")
                self.fx.git("reset", "-q", "--hard", "HEAD~1")
                self.head, self.snapshot = self.fx.head(), self.fx.snapshot()

    def test_budget_entries_of_size_budget_are_left_alone(self):
        self.entries('[[budget]]\nclass = "rule"\nkey = "max_bytes"\nvalue = 9999\nreason = "r"\npr = "#3"\n')
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(read(self.fx.tree, ".template/overrides.toml"), b'[[budget]]\nclass = "rule"\nkey = "max_bytes"\nvalue = 9999\nreason = "r"\npr = "#3"\n')

    def test_an_entry_for_a_path_that_is_not_owned_is_reported_and_not_recorded(self):
        self.entries('[[override]]\npath = "README.md"\nreason = "r"\npr = "#3"\n')
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("README.md", done.stdout)
        self.assertIn("not an owned file", done.stdout)
        self.assertEqual(self.fx.state()["overrides"], {})

    def test_an_entry_for_a_file_that_the_tag_removes_keeps_the_file(self):
        self.entries('[[override]]\npath = "scripts/lib/template_sync_fixture_removed.py"\nreason = "still used"\npr = "#4"\n')
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue((self.fx.tree / "scripts/lib/template_sync_fixture_removed.py").exists())
        self.assertEqual(self.fx.state()["overrides"], {"scripts/lib/template_sync_fixture_removed.py": "override"})
        self.assertNotIn("scripts/lib/template_sync_fixture_removed.py", self.fx.state()["owned"])

    def test_an_override_of_settings_json_keeps_the_project_file_and_regenerates_nothing(self):
        text = read(self.fx.tree, ".claude/settings.json").decode()
        write(self.fx.tree, ".claude/settings.json", text.replace('"enableAllProjectMcpServers": false', '"enableAllProjectMcpServers": false, "x": 1'))
        self.entries('[[override]]\npath = ".claude/settings.json"\nreason = "r"\npr = "#5"\n')
        local = read(self.fx.tree, ".claude/settings.json")
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(read(self.fx.tree, ".claude/settings.json"), local)


class TheLibraryHasNoNetworkCode(unittest.TestCase):
    def test_no_network_library_and_no_shell(self):
        sources = [syncfx.SCRIPT, *sorted((syncfx.ROOT / "scripts" / "lib" / "template_sync").glob("*.py"))]
        self.assertGreaterEqual(len(sources), 4)
        for path in sources:
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"\b(urllib|http\.client|socket|requests|ssl|ftplib|smtplib|xmlrpc)\b", path.name)
            self.assertNotRegex(text, r"shell\s*=\s*True|os\.system|os\.popen|\beval\(|\bexec\(", path.name)
            self.assertNotRegex(text, r"\bgit\b[^\n]{0,30}\btag\b[^\n]{0,10}(-a|-d|-f|v)|\"tag\"|'tag'", path.name)

    def test_the_script_names_no_endpoint_that_the_gate_forbids(self):
        done = subprocess.run(["bash", str(syncfx.ROOT / "scripts" / "checks" / "forbidden-api.sh")],
                              input="\n".join(["scripts/template-sync", *(f"scripts/lib/template_sync/{p.name}" for p in sorted((syncfx.ROOT / "scripts/lib/template_sync").glob("*.py")))]) + "\n",
                              cwd=syncfx.ROOT, capture_output=True, text=True, env=syncfx.env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class TemplateLag(Base):
    """`--check`: the first line is the lag in plain words (scripts/lib/cr_tools/context.py prints it)."""

    def setUp(self):
        super().setUp()
        (self.fx.remote / ".git/refs/tags/v1.3.0").unlink()

    def test_the_first_line_is_the_lag_and_it_changes_nothing(self):
        done = self.fx.run("--check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], "behind by 2 releases (recorded v1.0.0, newest v1.2.0)")
        self.assertTrue(lines[-1].startswith("template-sync: "))
        self.untouched()

    def test_up_to_date(self):
        self.sync_and_merge("v1.2.0")
        done = self.fx.run("--check")
        self.assertEqual(done.stdout.splitlines()[0], "up to date at v1.2.0")
        self.assertEqual(done.returncode, 0)

    def test_one_release_behind_is_singular(self):
        (self.fx.remote / ".git/refs/tags/v1.2.0").unlink()
        self.assertEqual(self.fx.run("--check").stdout.splitlines()[0], "behind by 1 release (recorded v1.0.0, newest v1.1.0)")

    def test_a_source_that_cannot_be_read_gives_an_unknown_line_and_exit_3(self):
        shutil.rmtree(self.fx.remote)
        done = self.fx.run("--check")
        self.assertEqual(done.returncode, 3)
        self.assertEqual(done.stdout.splitlines()[0], "unknown: the template source could not be read")

    def test_without_a_state_file_the_line_says_so_and_the_exit_is_1(self):
        (self.fx.tree / ".template/state.toml").unlink()
        self.fx.commit_local("no state")
        done = self.fx.run("--check")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout.splitlines()[0], "unknown: .template/state.toml is missing or not valid")

    def test_check_does_not_need_a_clean_tree_or_the_default_branch(self):
        write(self.fx.tree, "notes.txt", "x")
        self.fx.git("checkout", "-q", "-b", "feature/x")
        self.assertEqual(self.fx.run("--check").returncode, 0)

    def test_check_takes_no_target(self):
        self.assertEqual(self.fx.run("--check", "v1.1.0").returncode, 2)

    def test_cr_context_prints_the_first_line_of_check(self):
        from cr_tools import context  # noqa: PLC0415
        bin_dir = self.fx.tmp / "bin"
        bin_dir.mkdir()
        shim = bin_dir / "git"
        shim.write_text(SHIM)
        shim.chmod(0o755)
        (bin_dir / "shim.json").write_text(json.dumps({"log": str(self.fx.tmp / "calls"), "remotes": str(self.fx.remotes), "real": shutil.which("git")}))
        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", **syncfx.GIT_ENV}):
            line = context.template_lag(types.SimpleNamespace(tree=self.fx.tree), True)
        self.assertEqual(line, "template-lag (version 1.0.0): behind by 2 releases (recorded v1.0.0, newest v1.2.0)")
        self.assertNotIn("not installed", line)
        self.assertTrue(self.fx.clean())


if __name__ == "__main__":
    unittest.main()
