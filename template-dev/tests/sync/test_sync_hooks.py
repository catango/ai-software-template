"""Nothing the project's own hooks hold runs during a sync: after the sync has overwritten owned files, a hook such as `.githooks/pre-push` may be
one of them, so every git call of the script runs with `core.hooksPath=/dev/null`."""
import os
import subprocess
import unittest
from unittest import mock

import syncfx
from syncfx import write

from template_sync import publish  # noqa: E402

HOOKS = ("pre-commit", "commit-msg", "post-commit", "post-checkout", "pre-push", "reference-transaction", "post-merge", "pre-merge-commit",
         "prepare-commit-msg", "post-rewrite")


class Hooks(unittest.TestCase):
    def setUp(self):
        self.fx = syncfx.Fixture(self)
        self.markers = self.fx.tmp / "markers"
        self.markers.mkdir()
        for name in HOOKS:
            write(self.fx.tree, f".githooks/{name}", f'#!/bin/sh\ntouch "{self.markers}/{name}"\nexit 0\n', 0o755)
        self.fx.git("config", "core.hooksPath", ".githooks")
        self.fx.git("-c", "core.hooksPath=/dev/null", "add", "-A")
        self.fx.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "hooks")
        self.origin = self.fx.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True, env=syncfx.env())
        self.fx.git("remote", "add", "origin", str(self.origin))
        self.fx.git("-c", "core.hooksPath=/dev/null", "push", "-q", "origin", "main")

    def fired(self):
        return sorted(os.listdir(self.markers))

    def test_the_hooks_of_the_fixture_work_when_nothing_disables_them(self):
        self.fx.git("commit", "-q", "--allow-empty", "-m", "control")
        self.assertIn("pre-commit", self.fired())

    def test_a_sync_in_fixture_mode_runs_no_hook(self):
        done = self.fx.run("v1.1.0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.fired(), [])

    def test_branch_commit_and_push_run_no_hook(self):
        repo = publish.Repo(self.fx.tree)
        with mock.patch.dict(os.environ, syncfx.env()):
            repo.start_branch("template-sync/v1.1.0")
            write(self.fx.tree, "scripts/lib/template_sync_fixture_added.py", "# new\n")
            repo.commit(["scripts/lib/template_sync_fixture_added.py"], "Sync")
            repo.push("template-sync/v1.1.0")
        self.assertEqual(self.fired(), [])
        heads = subprocess.run(["git", "-C", str(self.origin), "branch", "--list"], capture_output=True, text=True, env=syncfx.env()).stdout
        self.assertIn("template-sync/v1.1.0", heads)

    def test_rollback_runs_no_hook_either(self):
        repo = publish.Repo(self.fx.tree)
        with mock.patch.dict(os.environ, syncfx.env()):
            repo.start_branch("template-sync/v1.1.0")
            repo.rollback("main", "template-sync/v1.1.0", [])
        self.assertEqual(self.fired(), [])
        self.assertEqual(self.fx.branch(), "main")


class EveryCallDisablesHooksAndFsmonitor(unittest.TestCase):
    def test_every_git_call_of_repo(self):
        calls = []

        def runner(argv, cwd, env, data=None):
            calls.append(argv)
            return 0, b"main\n"

        repo = publish.Repo("/x", runner=runner)
        for call in (lambda: repo.check_work_tree(), lambda: repo.check_clean(), lambda: repo.current_branch(), lambda: repo.default_branch(),
                     lambda: repo.check_branch_free("b"), lambda: repo.start_branch("b"), lambda: repo.commit(["a"], "m"), lambda: repo.push("b"),
                     lambda: repo.rollback("main", "b", ["a"]), lambda: repo.check_remote_state("main", "b")):
            try:
                call()
            except Exception:  # the answers are canned; only the arguments matter here
                pass
        self.assertGreater(len(calls), 10)
        for argv in calls:
            self.assertEqual(argv[:5], ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"], argv)


if __name__ == "__main__":
    unittest.main()
