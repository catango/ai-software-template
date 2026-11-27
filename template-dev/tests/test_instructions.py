"""T-4, T-5, T-6, T-22 on the template and on generated trees (plan row 4, `instructions`).

The fixtures of the three gate checks and the tests of `operator_guard` are in `tests/quality-gate/` (they travel with the checks).
These tests cover what only the template can: how it carries the seeded instruction files, that `initialize-project` writes what
the checks expect, and that a generated tree passes the checks with both configurations.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import unittest

import helpers

sys.path.insert(0, str(helpers.ROOT / "scripts"))
from pr_contract import template_rules  # noqa: E402

ROOT = helpers.ROOT
CHECKS = ("claude-md", "deny-list", "operator-contract")


def init_constant(name):
    """A constant of scripts/initialize-project, evaluated without running the script."""
    text = (ROOT / "scripts" / "initialize-project").read_text()
    body = re.search(rf"^{name} = \(\n(.*?)\n\)\n", text, re.S | re.M).group(1)
    return eval("(" + body + ")")  # a parenthesised run of string literals of our own file


def run_gate(tree, *args):
    return subprocess.run(["bash", str(tree / "scripts" / "quality-gate.sh"), *args], cwd=tree, capture_output=True, text=True)


def git_tree(tree):
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "PATH": os.environ["PATH"], "HOME": str(tree)}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True, env=env)


class SeededInstructionFiles(unittest.TestCase):
    """How the template carries the seeds (see template-dev/docs/instructions.md)."""

    def test_the_template_copy_of_the_platform_file_is_the_neutral_text_of_init(self):
        self.assertEqual((ROOT / "docs" / "PLATFORM.md").read_text(), init_constant("NEUTRAL_PLATFORM_DOC"))

    def test_the_template_copy_of_the_platform_deny_list_is_what_init_writes_without_a_preset(self):
        self.assertEqual((ROOT / ".claude" / "deny.platform.txt").read_bytes(), b"")

    def test_the_project_file_is_a_seed_without_an_import(self):
        text = (ROOT / "docs" / "PROJECT.md").read_text()
        self.assertGreater(len(text.strip()), 100)
        self.assertNotRegex(text, r"(?m)^@")

    def test_the_seeds_and_the_owned_files_have_their_classes(self):
        manifest = helpers.template_manifest.load_manifest(ROOT / ".template" / "manifest.toml")
        expected = {"CLAUDE.md": "owned", ".claude/settings.json": "owned", ".claude/required-deny.txt": "owned",
                    ".claude/deny.platform.txt": "seeded", "docs/PLATFORM.md": "seeded", "docs/PROJECT.md": "seeded",
                    "scripts/lib/operator_guard.py": "owned", "scripts/checks/claude-md.sh": "owned",
                    "scripts/checks/operator_contract.py": "owned", ".claude/rules/operator.md": "owned"}
        for path, cls in expected.items():
            self.assertEqual(manifest.classify(path), [cls], path)

    def test_the_template_itself_passes_the_three_checks(self):
        done = run_gate(ROOT, "--only", ",".join(CHECKS), "--strict")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for check in CHECKS:
            self.assertIn(f"PASS {check}", done.stdout)


class _Change:
    def __init__(self, path, status):
        self.path, self.status, self.previous = path, status, None


class _Pr:
    base_sha = "base"


class _Client:
    """The one thing check_init reads: files of the base commit (the template's manifest, and no state file)."""

    def __init__(self, manifest_text):
        self.manifest_text = manifest_text

    def file_text(self, sha, path):
        return self.manifest_text if path == ".template/manifest.toml" else None


class InitPullRequestTC2(helpers.TempDirTestCase):
    """TC-2 on the file list that a generated tree produces: what an init PR would change against the template it was made from."""

    def init_pr_violations(self, config_name):
        exported = helpers.export_template(self.make_dir() / "base")
        tree, result = self.generate(config_name)
        self.assertEqual(result.returncode, 0, result.stderr)
        before, after = helpers.snapshot(exported), helpers.snapshot(tree)
        files = [_Change(p, "removed") for p in sorted(set(before) - set(after))]
        files += [_Change(p, "added") for p in sorted(set(after) - set(before))]
        files += [_Change(p, "modified") for p in sorted(set(before) & set(after)) if before[p] != after[p]]
        manifest = (exported / ".template" / "manifest.toml").read_text()
        return [v.message for v in template_rules.check_init(_Pr(), files, _Client(manifest))], files

    def test_an_init_pr_without_preset_entries_passes_tc2(self):
        found, files = self.init_pr_violations("neutral")
        self.assertEqual(found, [])
        self.assertNotIn(".claude/settings.json", [f.path for f in files])

    @unittest.expectedFailure
    def test_an_init_pr_with_preset_deny_entries_passes_tc2(self):
        # OPEN DESIGN QUESTION (see template-dev/docs/instructions.md, point 2): init adds the preset's deny entries to the owned
        # .claude/settings.json, and TC-2 lets an init PR write only seeded paths. Remove the decorator when the approver decides.
        found, _ = self.init_pr_violations("second")
        self.assertEqual(found, [])

    def test_the_failure_of_the_second_configuration_is_exactly_the_settings_file(self):
        found, _ = self.init_pr_violations("second")
        self.assertEqual(found, ["written path is not seeded: .claude/settings.json"])


class GeneratedTree(helpers.TempDirTestCase):
    def test_both_configurations_generate_a_tree_that_passes_the_checks(self):
        for name in ("neutral", "second"):
            with self.subTest(config=name):
                tree, result = self.generate(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                git_tree(tree)
                done = run_gate(tree, "--only", ",".join(CHECKS), "--strict")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_recorded_hash_is_the_hash_of_claude_md(self):
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = tomllib.loads((tree / ".template" / "state.toml").read_text())
        self.assertEqual(state["owned"]["CLAUDE.md"], hashlib.sha256((tree / "CLAUDE.md").read_bytes()).hexdigest())

    def test_an_edit_of_claude_md_in_a_generated_tree_fails_the_gate(self):
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        (tree / "CLAUDE.md").write_text((tree / "CLAUDE.md").read_text().replace("## Never", "## Never\n\n- Be kind."))
        git_tree(tree)
        done = run_gate(tree, "--only", "claude-md")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("differs from its hash", done.stdout)

    def test_a_deleted_core_entry_in_a_generated_tree_fails_the_gate(self):
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = (tree / ".claude" / "settings.json").read_text()
        self.assertIn('"Bash(gh pr merge *)",\n', settings)
        (tree / ".claude" / "settings.json").write_text(settings.replace('      "Bash(gh pr merge *)",\n', ""))
        git_tree(tree)
        done = run_gate(tree, "--only", "deny-list")
        self.assertEqual(done.returncode, 1, done.stdout)

    def test_the_second_configuration_activates_the_operator_contract(self):
        tree, result = self.generate("second")
        self.assertEqual(result.returncode, 0, result.stderr)
        (tree / "ops" / "scripts").mkdir(parents=True)
        (tree / "ops" / "scripts" / "tool.py").write_text("import os\nos.system('x')\n")
        git_tree(tree)
        done = run_gate(tree, "--only", "operator-contract")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("ops/scripts/tool.py:2: os.system is not allowed", done.stdout)

    def test_the_neutral_configuration_leaves_the_operator_contract_inactive(self):
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        (tree / "ops" / "scripts").mkdir(parents=True)
        (tree / "ops" / "scripts" / "tool.py").write_text("import os\nos.system('x')\n")
        git_tree(tree)
        done = run_gate(tree, "--only", "operator-contract")
        self.assertEqual(done.returncode, 0, done.stdout)

    def test_init_adds_the_platform_entries_to_settings_once_and_leaves_it_alone_without_a_preset(self):
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((tree / ".claude" / "settings.json").read_bytes(), (ROOT / ".claude" / "settings.json").read_bytes())
        tree, result = self.generate("second")
        self.assertEqual(result.returncode, 0, result.stderr)
        deny = json.loads((tree / ".claude" / "settings.json").read_text())["permissions"]["deny"]
        core = [l for l in (ROOT / ".claude" / "required-deny.txt").read_text().splitlines() if l and not l.startswith("#")]
        entries = [l for l in (tree / ".claude" / "deny.platform.txt").read_text().splitlines() if l and not l.startswith("#")]
        self.assertTrue(entries)
        self.assertEqual(deny[:len(core)], core)
        self.assertEqual(deny[len(core):], entries)
        state = tomllib.loads((tree / ".template" / "state.toml").read_text())
        self.assertEqual(state["owned"][".claude/settings.json"],
                         hashlib.sha256((tree / ".claude" / "settings.json").read_bytes()).hexdigest())

    def test_a_platform_entry_that_is_not_a_deny_rule_is_refused_before_anything_is_written(self):
        preset = self.make_dir() / "preset"
        shutil.copytree(helpers.SECOND.parent, preset)
        (preset / "deny.platform.txt").write_text("not a rule!\n")
        tree = helpers.export_template(self.make_dir() / "tree")
        before = helpers.snapshot(tree)
        result = helpers.run_init(tree, preset / "config.toml")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("not a deny rule", result.stderr)
        self.assertEqual(helpers.snapshot(tree), before)

    def test_the_generated_configuration_carries_the_operator_commands_key(self):
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("  operator_commands: []\n", (tree / "config" / "project.yaml").read_text())


if __name__ == "__main__":
    unittest.main()
