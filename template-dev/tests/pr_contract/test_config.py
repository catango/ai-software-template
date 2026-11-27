"""The reader of the three policy keys of config/project.yaml: the form `initialize-project` writes, and nothing outside it."""
import json
import shutil
import subprocess
import unittest

import helpers
from pr_contract import config
from pr_contract.errors import ConfigError

import template_config  # noqa: E402  (scripts/lib is on the path)

GOOD = '''# comment
schema_version: 1
github:
  owner: "example-owner"
  approver_id: 1000001
workflow:
  merge_method: squash
  change_classes: [1, 2, 3]
  design_repos:
    - "example-owner/design-records"   # trailing comment
    - example-owner/other.repo
  operator_paths:
    - "ops/"
    - 'tools/run.yml'
    - ops2/lib*/
other:
  nested: {a: [1, 2]}
architectures:
  - name: "arch-a"
    runner: "runner-a"
    runner_private: ""
  - name: arch-b
    runner: runner-b
'''


def parse(text):
    return config.parse_yaml(text)


class Reader(unittest.TestCase):
    def test_the_documented_form_is_read(self):
        parsed = parse(GOOD)
        self.assertEqual(parsed["workflow"]["design_repos"], ["example-owner/design-records", "example-owner/other.repo"])
        self.assertEqual(parsed["workflow"]["operator_paths"], ["ops/", "tools/run.yml", "ops2/lib*/"])
        self.assertEqual(parsed["architectures"][0], {"name": "arch-a", "runner": "runner-a", "runner_private": ""})
        self.assertEqual(parsed["architectures"][1], {"name": "arch-b", "runner": "runner-b"})
        self.assertEqual(config.design_repos(parsed), ("example-owner/design-records", "example-owner/other.repo"))
        self.assertEqual(config.operator_paths(parsed), ("ops/", "tools/run.yml", "ops2/lib*/"))
        self.assertEqual([a.name for a in config.architectures(parsed)], ["arch-a", "arch-b"])

    def test_crlf_a_leading_document_marker_and_comments_between_items(self):
        text = "---\n" + GOOD.replace("    - \"ops/\"\n", "    # a note\n\n    - \"ops/\"\n")
        self.assertEqual(parse(text.replace("\n", "\r\n"))["workflow"]["operator_paths"][0], "ops/")

    def test_empty_lists_and_absent_keys(self):
        parsed = parse("workflow:\n  design_repos: []\n  operator_paths: [] # none\narchitectures: []\n")
        self.assertEqual(parsed, {"workflow": {"design_repos": [], "operator_paths": []}, "architectures": []})
        self.assertEqual(parse("schema_version: 1\n"), {})
        self.assertEqual(parse(""), {})
        with self.assertRaises(ConfigError):
            config.design_repos({})
        with self.assertRaises(ConfigError):
            config.architectures({})
        with self.assertRaises(ConfigError):
            config.operator_paths(None)

    def test_the_form_outside_the_subset_is_an_error_and_never_a_guess(self):
        bad = {
            "flow list": "workflow:\n  design_repos: [a/b]\n",
            "flow architectures": "architectures: [{name: a}]\n",
            "block scalar": "workflow:\n  operator_paths: |\n    a/\n",
            "repeated workflow": "workflow:\n  operator_paths: []\nworkflow:\n  operator_paths: []\n",
            "repeated key": "workflow:\n  operator_paths: []\n  operator_paths: []\n",
            "repeated architectures": "architectures: []\narchitectures: []\n",
            "quoted key": "\"workflow\":\n  operator_paths: []\n",
            "quoted child key": "workflow:\n  \"operator_paths\": []\n",
            "complex key": "? workflow\n: x\n",
            "merge key": "<<: *base\n",
            "workflow flow mapping": "workflow: {operator_paths: []}\n",
            "workflow list": "workflow:\n  - a\n",
            "key with text after the colon": "workflow:\n  operator_paths: a/\n",
            "empty block": "workflow:\n  operator_paths:\n",
            "item with a mapping": "workflow:\n  operator_paths:\n    - a: b\n",
            "item with an anchor": "workflow:\n  operator_paths:\n    - &x a/\n",
            "item with an alias": "workflow:\n  operator_paths:\n    - *x\n",
            "item with a tag": "workflow:\n  operator_paths:\n    - !!str a/\n",
            "bool item": "workflow:\n  design_repos:\n    - true\n",
            "number item": "workflow:\n  operator_paths:\n    - 5\n",
            "null item": "workflow:\n  operator_paths:\n    - null\n",
            "escape in quotes": 'workflow:\n  operator_paths:\n    - "a\\n"\n',
            "space in quotes": 'workflow:\n  operator_paths:\n    - "a b/"\n',
            "indentation changes": "workflow:\n  operator_paths:\n    - a/\n     - b/\n",
            "child indentation below": "workflow:\n    merge: x\n  operator_paths: []\n",
            "tab": "workflow:\n\toperator_paths: []\n",
            "tab in list": "workflow:\n  operator_paths:\n\t- a/\n",
            "second document": "workflow:\n  operator_paths: []\n---\nworkflow:\n  operator_paths: []\n",
            "document end": "a: 1\n...\n",
            "arch with an unknown shape": "architectures:\n  - name: a\n   runner: b\n",
            "arch value not a string": "architectures:\n  - name: a\n    runner: true\n",
            "arch key repeated": "architectures:\n  - name: a\n    name: b\n",
            "arch not a list": "architectures:\n  name: a\n",
            "nul": "workflow:\n  operator_paths: []\n\x00",
            "too large": "# " + "x" * 70000 + "\n",
        }
        for name, text in bad.items():
            with self.subTest(case=name), self.assertRaises(ConfigError):
                parse(text)

    def test_other_keys_may_hold_any_form(self):
        text = "x: {a: 1}\ny: [1, 2]\nz: |\n  text\nzz: &a\n  - 1\nworkflow:\n  operator_paths: []\n  other:\n    - {a: b}\n  design_repos: []\n"
        self.assertEqual(parse(text), {"workflow": {"operator_paths": [], "design_repos": []}})

    def test_the_other_key_named_like_a_read_key_is_not_confused(self):
        parsed = parse("workflow_x: 1\nworkflow:\n  design_repos_all: [1]\n  design_repos: []\n  operator_paths: []\n")
        self.assertEqual(parsed["workflow"], {"design_repos": [], "operator_paths": []})

    def test_design_repos_entries_are_validated(self):
        bad = ["a", "a/b/c", "/b", "a/", "a b/c", "-a/b", "a/..", "a/" + "b" * 101, "*/x", "a/b*", ""]
        for entry in bad:
            with self.subTest(entry=entry), self.assertRaises(ConfigError):
                config.design_repos({"workflow": {"design_repos": [entry]}})
        for value in ("a/b", None, 5, [5], ["a/b", "a/b"]):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                config.design_repos({"workflow": {"design_repos": value}})

    def test_a_missing_architecture_key_is_an_error_not_a_default(self):
        with self.assertRaises(ConfigError):
            config.architectures({"workflow": {}})

    def test_the_architecture_name_follows_the_schema_pattern(self):
        for name in ("a", "arch-a", "arm64", "a_b", "x" * 20):
            self.assertEqual(config.architectures({"architectures": [{"name": name}]})[0].name, name)
        for name in ("", "-a", "A", "a b", "x" * 21, "a.b"):
            with self.subTest(name=name), self.assertRaises(ConfigError):
                config.architectures({"architectures": [{"name": name}]})


class AgainstTheWriter(unittest.TestCase):
    """The reader must read what `scripts/initialize-project` writes (template_config.project_yaml), for every configuration
    of the template's own fixtures and for hostile values."""

    def configs(self):
        out = []
        for path in helpers.ROOT.joinpath("template-dev", "fixtures", "config").rglob("*.toml"):
            fields = template_config.load_schema(helpers.ROOT / ".template" / "config.schema.toml")
            loaded, _ = template_config.load_config(path, fields)
            out.append(loaded)
        return out

    def test_every_fixture_configuration_round_trips(self):
        self.assertTrue(self.configs())
        for loaded in self.configs():
            with self.subTest(project=loaded["project"]["name"]):
                parsed = parse(template_config.project_yaml(loaded))
                self.assertEqual(parsed["workflow"]["design_repos"], loaded["workflow"]["design_repos"])
                self.assertEqual(parsed["workflow"]["operator_paths"], loaded["workflow"]["operator_paths"])
                self.assertEqual([(a["name"], a["runner"], a["runner_private"]) for a in parsed["architectures"]],
                                 [(a["name"], a["runner"], a["runner_private"]) for a in loaded["architectures"]])

    def test_a_filled_configuration_round_trips(self):
        loaded = self.configs()[0]
        loaded["workflow"] = {"design_repos": ["example-owner/design-records", "other-owner/x.y"],
                              "operator_paths": ["ops/", "tools/run.yml", "roles/op_*/"]}
        parsed = parse(template_config.project_yaml(loaded))
        self.assertEqual(parsed["workflow"], loaded["workflow"])

    def test_this_repositorys_own_file_is_read(self):
        parsed = parse((helpers.ROOT / "config" / "project.yaml").read_text())
        self.assertTrue(config.design_repos(parsed))
        self.assertTrue(config.architectures(parsed))
        self.assertEqual(config.operator_paths(parsed), ())

    @unittest.skipUnless(shutil.which("yq"), "yq is not installed")
    def test_the_reader_agrees_with_yq_on_the_files_it_accepts(self):
        texts = [GOOD, (helpers.ROOT / "config" / "project.yaml").read_text(), *[template_config.project_yaml(c) for c in self.configs()]]
        for text in texts:
            done = subprocess.run(["yq", "-o=json", "."], input=text, capture_output=True, text=True, check=True)
            reference = json.loads(done.stdout)
            parsed = parse(text)
            self.assertEqual(parsed["workflow"]["design_repos"], reference["workflow"]["design_repos"])
            self.assertEqual(parsed["workflow"]["operator_paths"], reference["workflow"]["operator_paths"])
            for mine, theirs in zip(parsed["architectures"], reference["architectures"]):
                self.assertEqual({k: v for k, v in mine.items() if v is not None}, {k: v for k, v in theirs.items() if v is not None})


if __name__ == "__main__":
    unittest.main()
