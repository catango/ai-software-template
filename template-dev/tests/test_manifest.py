"""`manifest` test: T-1, and the generation part of T-2.

T-1: every tracked file has exactly one class; no class and two classes fail;
`initialize-project` removes every template-only path.
T-2 (generation part): a generated tree has no `template-dev/`, no template-only path, no CR
or ADR instance from the template, and no unfilled placeholder (the placeholder scan is in
the `leak` test).
"""

import ast
import re
import tomllib
import unittest
from pathlib import Path

import helpers
import template_manifest as tm
from helpers import ROOT, TempDirTestCase

MINIMAL = """
schema_version = 1
version = "1.2.3"
[classes]
owned = [".template/manifest.toml", "a/owned.txt", "shared/**"]
seeded = ["seeded.txt", "shared/seeded/"]
template-only = ["dev/"]
"""


def write_tree(base, files):
    for rel, text in files.items():
        target = Path(base) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


class RealTreeTests(unittest.TestCase):
    def setUp(self):
        self.manifest = tm.load_manifest(ROOT / ".template" / "manifest.toml")
        self.paths = helpers.tracked_paths()

    def test_every_file_has_exactly_one_class(self):
        report = tm.check_paths(self.manifest, self.paths)
        self.assertEqual(report["unclassed"], [])
        self.assertEqual(report["multiple"], [])

    def test_template_carries_no_state_override_or_instances(self):
        for rel in self.paths:
            self.assertNotIn(rel, (".template/state.toml", ".template/overrides.toml"))
            self.assertFalse(rel.startswith(("docs/cr/CR-", "docs/adr/ADR-")), rel)

    def test_layout_reference_paths_have_the_expected_class(self):
        data = tomllib.loads((ROOT / "template-dev" / "layout.toml").read_text())
        self.assertGreater(len(data["path"]), 30)
        for entry in data["path"]:
            self.assertEqual(
                self.manifest.classify(entry["path"]), [entry["class"]], entry["path"]
            )

    def test_every_class_has_at_least_one_file_in_the_template(self):
        for cls in tm.CLASSES:
            self.assertTrue(
                any(self.manifest.classify(p) == [cls] for p in self.paths), cls
            )


class FailingFixtureTests(TempDirTestCase):
    """One failing fixture per case."""

    def check(self, manifest_text, files):
        root = self.make_dir()
        write_tree(root, {".template/manifest.toml": manifest_text, **files})
        manifest = tm.load_manifest(root / ".template" / "manifest.toml")
        return tm.check_paths(manifest, tm.walk_files(root))

    def test_passing_fixture(self):
        report = self.check(MINIMAL, {"a/owned.txt": "x", "seeded.txt": "x", "dev/x": "x"})
        self.assertEqual(report, {"unclassed": [], "multiple": []})

    def test_file_without_class_fails(self):
        report = self.check(MINIMAL, {"a/owned.txt": "x", "unknown/file.txt": "x"})
        self.assertIn("unknown/file.txt", report["unclassed"])

    def test_file_with_two_classes_fails(self):
        report = self.check(MINIMAL, {"shared/seeded/file.txt": "x"})
        self.assertEqual(
            report["multiple"], [("shared/seeded/file.txt", ["owned", "seeded"])]
        )

    def test_manifest_file_itself_must_be_classed(self):
        report = self.check(MINIMAL.replace('".template/manifest.toml", ', ""), {})
        self.assertEqual(report["unclassed"], [".template/manifest.toml"])


class ManifestFormatTests(unittest.TestCase):
    def parse(self, text):
        return tm.parse_manifest(text)

    def test_valid(self):
        manifest = self.parse(MINIMAL)
        self.assertEqual(manifest.version, "1.2.3")
        self.assertEqual(manifest.classify("dev/anything/deep.txt"), ["template-only"])

    def test_rejects_malformed_manifests(self):
        bad = {
            "not toml": "classes = [",
            "wrong schema version": MINIMAL.replace("schema_version = 1", "schema_version = 2"),
            "missing version": MINIMAL.replace('version = "1.2.3"', ""),
            "missing class": MINIMAL.replace('template-only = ["dev/"]', ""),
            "unknown class": MINIMAL.replace("[classes]", '[classes]\nzoned = ["x"]'),
            "class not a list": MINIMAL.replace('seeded = ["seeded.txt", "shared/seeded/"]', 'seeded = "x"'),
            "empty pattern": MINIMAL.replace('"seeded.txt"', '""'),
            "absolute pattern": MINIMAL.replace('"seeded.txt"', '"/seeded.txt"'),
            "parent segment": MINIMAL.replace('"seeded.txt"', '"../seeded.txt"'),
            "dot segment": MINIMAL.replace('"seeded.txt"', '"./seeded.txt"'),
            "backslash": MINIMAL.replace('"seeded.txt"', '"a\\\\b"'),
            "partial double star": MINIMAL.replace('"seeded.txt"', '"a**/b"'),
            "double slash": MINIMAL.replace('"seeded.txt"', '"a//b"'),
        }
        for name, text in bad.items():
            with self.subTest(name), self.assertRaises(tm.ManifestError):
                self.parse(text)

    def test_pattern_semantics(self):
        cases = [
            ("a/b.txt", "a/b.txt", True),
            ("a/b.txt", "a/b.txt.bak", False),
            ("a/b.txt", "x/a/b.txt", False),
            ("dir/", "dir/x", True),
            ("dir/", "dir/x/y/z", True),
            ("dir/", "dir", False),
            ("dir/", "dirx/y", False),
            ("a/*.md", "a/x.md", True),
            ("a/*.md", "a/b/x.md", False),
            ("a/*/SKILL.md", "a/n/SKILL.md", True),
            ("a/*/SKILL.md", "a/n/m/SKILL.md", False),
            ("a/?.md", "a/x.md", True),
            ("a/?.md", "a/xy.md", False),
            ("a/**", "a/b/c", True),
            ("a/**", "a", False),
            ("a/**/b", "a/b", True),
            ("a/**/b", "a/x/y/b", True),
            ("a/**/b", "a/x/y/c", False),
            ("**/x.txt", "x.txt", True),
            ("**/x.txt", "p/q/x.txt", True),
            ("a.b", "aXb", False),
            ("a[1]", "a[1]", True),
        ]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                regex = tm.compile_pattern(pattern)
                self.assertEqual(bool(regex.fullmatch(path)), expected)



class WalkFilesTests(TempDirTestCase):
    def test_skips_git_and_bytecode_directories_and_reports_directory_links(self):
        root = self.make_dir()
        write_tree(root, {
            ".git/config": "x", "keep.txt": "x", "a/b.txt": "x",
            "__pycache__/top.pyc": "x", "a/__pycache__/inner.pyc": "x",
        })
        (root / "link").symlink_to(root / "a", target_is_directory=True)
        (root / "filelink").symlink_to(root / "keep.txt")
        self.assertEqual(
            tm.walk_files(root), ["a/b.txt", "filelink", "keep.txt", "link"]
        )


class GenerationTests(TempDirTestCase):
    """T-1 (removal) and T-2 (generation part) on both fixture configurations."""

    EXTRA = {
        ".github/workflows/template-ci.yml": "name: sample\n",
        "template-dev/provenance.toml": "history = true\n",
        "template-dev/docs/cr/CR-1/design.md": "# sample\n",
    }

    def manifest_of(self, tree):
        return tm.load_manifest(tree / ".template" / "manifest.toml")

    def test_init_removes_every_template_only_path(self):
        for name in helpers.CONFIGS:
            with self.subTest(config=name):
                tree, result = self.generate(name, self.EXTRA)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((tree / "template-dev").exists())
                self.assertFalse((tree / ".github" / "workflows" / "template-ci.yml").exists())
                manifest = self.manifest_of(tree)
                left = [p for p in tm.walk_files(tree) if "template-only" in manifest.classify(p)]
                self.assertEqual(left, [])

    def test_generated_tree_is_classified_without_template_only_paths(self):
        for name in helpers.CONFIGS:
            with self.subTest(config=name):
                tree, result = self.generate(name, self.EXTRA)
                self.assertEqual(result.returncode, 0, result.stderr)
                manifest = self.manifest_of(tree)
                report = tm.check_paths(manifest, tm.walk_files(tree))
                self.assertEqual(report["unclassed"], [])
                self.assertEqual(report["multiple"], [])

    def test_generated_tree_has_no_cr_or_adr_instance_from_the_template(self):
        tree, result = self.generate("neutral", {
            "docs/cr/CR-9/README.md": "x", "docs/adr/ADR-001.md": "x",
        })
        # An instance in the template is unclassed, so init refuses before writing.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("docs/cr/CR-9/README.md", result.stderr)
        self.assertFalse((tree / ".template" / "state.toml").exists())
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [p for p in tm.walk_files(tree) if p.startswith(("docs/cr/CR-", "docs/adr/ADR-"))], []
        )

    def test_init_refuses_a_file_without_a_class_and_writes_nothing(self):
        tree = helpers.export_template(self.make_dir() / "tree")
        (tree / "stray.txt").write_text("x")
        before = helpers.snapshot(tree)
        result = helpers.run_init(tree, helpers.NEUTRAL)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stray.txt", result.stderr)
        self.assertEqual(helpers.snapshot(tree), before)

    def test_seeded_files_are_written_from_the_configuration(self):
        tree, result = self.generate("neutral", {"README.md": "TEMPLATE-README-SENTINEL\n"})
        self.assertEqual(result.returncode, 0, result.stderr)
        readme = (tree / "README.md").read_text()
        self.assertIn("sample-app", readme)
        self.assertNotIn("SENTINEL", readme)
        for rel in ("docs/README.md", "config/project.yaml", "docs/PLATFORM.md",
                    ".claude/deny.platform.txt", ".template/state.toml"):
            self.assertTrue((tree / rel).is_file(), rel)

    def test_project_yaml_comes_only_from_the_configuration(self):
        tree, result = self.generate("second", {"config/project.yaml": "template: leftover\n"})
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (tree / "config" / "project.yaml").read_text()
        self.assertNotIn("leftover", text)
        for expected in ("approver_id: 2000002", 'approver_login: "example-lead"',
                         'machine_login: "example-agent"', 'name: "other-service"',
                         '"example-org/design-records"', '"ops/scripts/"',
                         'name: "arch-b"', 'runner_private: ""',
                         'source: "example-org/starter-template"'):
            self.assertIn(expected, text)
        for absent in ("visibility", "post_init_actions", "platform_doc", "deny_platform"):
            self.assertNotIn(absent, text)

    def test_preset_files_are_copied_and_defaults_are_neutral(self):
        tree, result = self.generate("second")
        self.assertEqual(result.returncode, 0, result.stderr)
        preset = helpers.SECOND.parent
        self.assertEqual((tree / "docs/PLATFORM.md").read_bytes(), (preset / "PLATFORM.md").read_bytes())
        self.assertEqual(
            (tree / ".claude/deny.platform.txt").read_bytes(), (preset / "deny.platform.txt").read_bytes()
        )
        tree, result = self.generate("neutral", {"docs/PLATFORM.md": "inherited\n"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("inherited", (tree / "docs/PLATFORM.md").read_text())
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(len((tree / "docs/PLATFORM.md").read_text().strip()), 20)
        self.assertNotIn("import", (tree / "docs/PLATFORM.md").read_text().lower().split())
        self.assertEqual((tree / ".claude/deny.platform.txt").read_text(), "")

    def test_state_toml_records_version_source_and_owned_hashes(self):
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = tomllib.loads((tree / ".template" / "state.toml").read_text())
        manifest = self.manifest_of(tree)
        self.assertEqual(state["template"]["version"], manifest.version)
        self.assertEqual(state["template"]["source"], "example-owner/software-template")
        self.assertNotIn("commit", state["template"])
        owned = [p for p in tm.walk_files(tree) if manifest.classify(p) == ["owned"]]
        self.assertEqual(sorted(state["owned"]), sorted(owned))
        snap = helpers.snapshot(tree)
        for rel, digest in state["owned"].items():
            self.assertEqual(digest, snap[rel], rel)
        self.assertEqual(state["overrides"], {})

    def test_run_without_the_bytecode_flag_leaves_no_bytecode_and_none_in_state(self):
        # A user runs the script directly; the tests otherwise always pass -B.
        tree, result = self.generate("neutral", bytecode=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(tree.rglob("__pycache__")), [])
        self.assertEqual(list(tree.rglob("*.pyc")), [])
        state = tomllib.loads((tree / ".template" / "state.toml").read_text())
        self.assertFalse([p for p in state["owned"] if "pycache" in p or p.endswith(".pyc")])

    def test_a_template_only_symbolic_link_to_a_directory_is_removed(self):
        tree = helpers.export_template(self.make_dir() / "tree")
        (tree / "template-dev" / "linkdir").symlink_to(tree / ".template", target_is_directory=True)
        result = helpers.run_init(tree, helpers.NEUTRAL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((tree / "template-dev").exists())
        self.assertTrue((tree / ".template" / "manifest.toml").is_file())

    def test_a_kept_symbolic_link_to_a_directory_is_refused_and_nothing_changes(self):
        tree = helpers.export_template(self.make_dir() / "tree")
        (tree / "scripts" / "lib" / "linkdir").symlink_to(tree / ".template", target_is_directory=True)
        before = helpers.snapshot(tree)
        result = helpers.run_init(tree, helpers.NEUTRAL)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scripts/lib/linkdir", result.stderr)
        self.assertEqual(helpers.snapshot(tree), before)

    def test_template_commit_is_recorded_only_when_given_and_valid(self):
        tree = helpers.export_template(self.make_dir() / "tree")
        result = helpers.run_init(tree, helpers.NEUTRAL, "--template-commit", "not-a-sha")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((tree / ".template" / "state.toml").exists())
        sha = "0123456789abcdef" * 2 + "01234567"
        result = helpers.run_init(tree, helpers.NEUTRAL, "--template-commit", sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = tomllib.loads((tree / ".template" / "state.toml").read_text())
        self.assertEqual(state["template"]["commit"], sha)

    def test_init_refuses_a_second_run_and_changes_nothing(self):
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        before = helpers.snapshot(tree)
        again = helpers.run_init(tree, helpers.SECOND)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("state.toml", again.stderr)
        self.assertEqual(helpers.snapshot(tree), before)

    def test_dry_run_writes_nothing_and_lists_the_plan(self):
        tree = helpers.export_template(self.make_dir() / "tree")
        (tree / "template-dev" / "extra").mkdir(parents=True, exist_ok=True)
        (tree / "template-dev" / "extra" / "f.txt").write_text("x")
        before = helpers.snapshot(tree)
        result = helpers.run_init(tree, helpers.SECOND, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(helpers.snapshot(tree), before)
        self.assertTrue((tree / "template-dev").is_dir())
        self.assertIn("template-dev/extra/f.txt", result.stdout)
        self.assertIn("config/project.yaml", result.stdout)
        self.assertIn("dry run", result.stdout.lower())

    def test_invalid_configuration_is_refused_before_anything_is_written(self):
        good = helpers.NEUTRAL.read_text()
        bad = {
            "unknown key": good + "\nsurprise = 1\n",
            "unknown section key": good.replace("[template]", "[template]\nextra = 1"),
            "missing required key": good.replace('owner = "example-owner"\n', "", 1),
            "wrong type": good.replace("approver_id = 1000001", 'approver_id = "1000001"'),
            "non-positive id": good.replace("approver_id = 1000001", "approver_id = 0"),
            "bad enum": good.replace('visibility = "public"', 'visibility = "internal"'),
            "bad repository form": good.replace('"example-owner/software-template"', '"software-template"'),
            "bad schema version": good.replace("schema_version = 1", "schema_version = 9"),
            "no architecture": good.split("[[architectures]]")[0],
            "duplicate architecture": good + '\n[[architectures]]\nname = "arch-a"\nrunner = ""\nrunner_private = ""\n',
            "newline in description": good.replace("Sample application", "Sample\\napplication"),
            "absolute operator path": good.replace("operator_paths = []", 'operator_paths = ["/etc/x"]'),
            "parent operator path": good.replace("operator_paths = []", 'operator_paths = ["../x"]'),
            "same approver and machine": good.replace('machine_login = "example-machine"', 'machine_login = "example-approver"'),
            "absolute preset path": good + '\n[preset]\nplatform_doc = "/etc/hostname"\n',
            "preset path leaves directory": good + '\n[preset]\nplatform_doc = "../neutral.toml"\n',
            "missing preset file": good + '\n[preset]\nplatform_doc = "nothing-here.md"\n',
        }
        tree = helpers.export_template(self.make_dir() / "tree")
        before = helpers.snapshot(tree)
        cfg_dir = self.make_dir()
        for name, text in bad.items():
            with self.subTest(name):
                cfg = cfg_dir / "bad.toml"
                cfg.write_text(text)
                result = helpers.run_init(tree, cfg)
                self.assertEqual(result.returncode, 2, (name, result.stdout, result.stderr))
                self.assertEqual(helpers.snapshot(tree), before)

    def test_missing_config_or_root_is_refused(self):
        tree = helpers.export_template(self.make_dir() / "tree")
        result = helpers.run_init(tree, tree / "no-such-config.toml")
        self.assertEqual(result.returncode, 2)
        empty = self.make_dir()
        result = helpers.run_init(tree, helpers.NEUTRAL, "--root", str(empty))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest", result.stderr)

    def test_prints_post_init_actions_and_rulesets_for_a_public_repository(self):
        tree = helpers.export_template(self.make_dir() / "tree")
        text = (helpers.SECOND.read_text().replace('visibility = "private"', 'visibility = "public"'))
        cfg_dir = self.make_dir()
        shutil_copy_preset(cfg_dir)
        (cfg_dir / "config.toml").write_text(text)
        result = helpers.run_init(tree, cfg_dir / "config.toml")
        self.assertEqual(result.returncode, 0, result.stderr)
        for action in ("Add the machine account as a collaborator.",
                       "Apply the printed rulesets before the init pull request merges."):
            self.assertIn(action, result.stdout)
        for expected in ("main-protection", "release-tags", "pr-contract", "quality-gate"):
            self.assertIn(expected, result.stdout)

    def test_prints_no_rulesets_for_a_private_repository(self):
        tree, result = self.generate("second")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Add the machine account as a collaborator.", result.stdout)
        self.assertIn("not available", result.stdout)
        self.assertNotIn('"required_status_checks"', result.stdout)

    def test_initialize_project_makes_no_github_or_network_call(self):
        """Static check (T-20, T-13): no label, ruleset, repository, collaborator, setting.

        Reads the syntax tree: the script and its two libraries import only local-file
        modules, start no process, and name no command of the GitHub client.
        """
        allowed = {"argparse", "hashlib", "json", "os", "re", "sys", "tomllib", "pathlib",
                   "dataclasses", "template_config", "template_manifest"}
        for rel in ("scripts/initialize-project", "scripts/lib/template_config.py",
                    "scripts/lib/template_manifest.py"):
            tree = ast.parse((ROOT / rel).read_text(), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or "").split(".")[0]]
                else:
                    names = []
                for name in names:
                    self.assertIn(name, allowed, f"{rel} imports {name}")
                if isinstance(node, ast.Call):
                    func = node.func
                    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    self.assertFalse(
                        called in ("system", "popen", "Popen", "run", "call", "exec", "eval")
                        or called.startswith(("spawn", "exec")), f"{rel} calls {called}")
                    self.assertNotIn("shell", [k.arg for k in node.keywords])
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertIsNone(
                        re.search(r"\bgh (api|repo|label|pr|ruleset|secret|variable)\b", node.value),
                        f"{rel} names a GitHub client command")

    def test_initialize_project_only_writes_inside_the_root(self):
        tree, result = self.generate("second")
        self.assertEqual(result.returncode, 0, result.stderr)
        # The configuration directory is read only and stays untouched.
        self.assertEqual(
            sorted(p.name for p in helpers.SECOND.parent.iterdir()),
            ["PLATFORM.md", "config.toml", "deny.platform.txt"],
        )


def shutil_copy_preset(dest):
    import shutil
    for name in ("PLATFORM.md", "deny.platform.txt"):
        shutil.copy2(helpers.SECOND.parent / name, Path(dest) / name)


if __name__ == "__main__":
    unittest.main()
