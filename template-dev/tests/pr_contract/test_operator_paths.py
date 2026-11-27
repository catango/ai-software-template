"""CR-57 PR 0 (PA-A6): the keys `operator_paths` and `operator_endpoints` of the base configuration.

Part 1 checks the strict reader (standard library, no process) on the new keys: valid forms, the same strictness as for
`architectures`, both keys with `architectures` in any order, and agreement with `yq` where it is installed.
Part 2 runs the command line against fixture directories (`--fixtures`), as a PR check would: PC-8 configured, empty
(inactive), missing and malformed (fail closed), with deleted and renamed paths counted.
"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile
import unittest

import helpers
from helpers import BASE_SHA, HEAD_SHA
from pr_contract import cli, config
from test_pc9_inputs import HAVE_YQ, RECORDED, via_yq

YAML = helpers.FIXTURES / "operator_paths"
PATHS_TODAY = ("scripts/operator/", "ansible/operator.yml", "ansible/roles/operator_*/", "toolchain/python/operator.lock",
               "ansible/lab.yml", "ansible/roles/ssh_hardening/")
# This repository's own file: the paths above plus the other roles of the lab profile (CR-57 plan PR 2).
OWN_PATHS = PATHS_TODAY + ("ansible/roles/release_check/", "ansible/roles/claude_managed_settings/", "ansible/roles/lab_tools/",
                                "ansible/roles/git_identity/", "ansible/roles/unattended_upgrades/",
                           "ansible/roles/claude_user_deny/", "ansible/roles/lab_project/")

GOOD = {
    "block paths": "operator_paths:\n  - scripts/operator/\n  - ansible/operator.yml\n  - ansible/roles/operator_*/\n",
    "empty paths": "operator_paths: []\n",
    "empty paths, comment": "operator_paths: []   # none\n",
    "paths at column 0": "operator_paths:\n- scripts/operator/\n- ansible/operator.yml\n",
    "wide gap": "operator_paths:\n  -   scripts/operator/\n",
    "comments and blanks": "# head\noperator_paths:  # the set\n\n  # first\n  - scripts/operator/  # x\n\n  - a.yml\n",
    "comment at column 0 inside": "operator_paths:\n  - a/\n# note\n  - b/\n",
    "block endpoints": "operator_endpoints:\n  - api.github.com\n  - example.org\n",
    "empty endpoints": "operator_endpoints: []\n",
    "both, paths first": "operator_paths:\n  - a/\noperator_endpoints:\n  - api.github.com\n",
    "both, endpoints first": "operator_endpoints:\n  - api.github.com\noperator_paths:\n  - a/\n",
    "both and architectures": "architectures: []\noperator_paths:\n  - a/\noperator_endpoints: []\n",
    "architectures between": "operator_paths:\n  - a/\narchitectures:\n  - name: amd64\noperator_endpoints:\n  - h.example\n",
    "other keys around": "a: 1\nb:\n  c: [1, 2]\noperator_paths:\n  - a/\nlast: \"y\"\n",
    "document start": "---\noperator_paths: []\n",
    "crlf": "operator_paths:\r\n  - a/\r\n",
    "dots and dashes": "operator_paths:\n  - .github/workflows/x-y_z.yml\n",
    "no key": "schema_version: 1\n",
}
BAD = {
    "flow list": "operator_paths: [a/, b/]\n",
    "unclosed flow": "operator_paths: [\n",
    "scalar": "operator_paths: scripts/operator/\n",
    "null": "operator_paths:\n",
    "null then next key": "operator_paths:\nother: 1\n",
    "flow item": "operator_paths:\n  - [a/]\n",
    "mapping item": "operator_paths:\n  - name: a/\n",
    "block scalar": "operator_paths: |\n  - a/\n",
    "anchor": "operator_paths:\n  - &a a/\n",
    "alias item": "operator_paths:\n  - *a\n",
    "tag": "operator_paths:\n  - !!str a/\n",
    "double quoted item": "operator_paths:\n  - \"a/\"\n",
    "single quoted item": "operator_paths:\n  - 'a/'\n",
    "empty item": "operator_paths:\n  -\n",
    "tab indent": "operator_paths:\n\t- a/\n",
    "tab inside": "operator_paths:\n  - a/\n\t- b/\n",
    "tab after dash": "operator_paths:\n  -\ta/\n",
    "repeated key": "operator_paths: []\noperator_paths: []\n",
    "repeated key, block then empty": "operator_paths:\n  - a/\noperator_paths: []\n",
    "quoted key": "\"operator_paths\": []\n",
    "complex key": "? operator_paths\n: []\n",
    "space before colon": "operator_paths : []\n",
    "no space after colon": "operator_paths:[]\n",
    "second document": "operator_paths: []\n---\noperator_paths: []\n",
    "document end": "operator_paths: []\n...\n",
    "second document, other keys": "x: 1\n---\noperator_paths: []\n",
    "indentation changes": "operator_paths:\n  - a/\n - b/\n",
    "item at a deeper column": "operator_paths:\n  - a/\n    - b/\n",
    "number": "operator_paths:\n  - 123\n",
    "boolean": "operator_paths:\n  - true\n",
    "null word": "operator_paths:\n  - null\n",
    "spaces in a value": "operator_paths:\n  - two words\n",
    "multi-line plain": "operator_paths:\n  - a/\n    b/\n",
    "nested mapping": "operator_paths:\n  x: y\n",
    "nul": "operator_paths: []\n\x00",
    "endpoints flow": "operator_endpoints: [api.github.com]\n",
    "endpoints scalar": "operator_endpoints: api.github.com\n",
    "endpoints null": "operator_endpoints:\n",
    "endpoints repeated": "operator_endpoints: []\noperator_endpoints: []\n",
    "endpoints tab": "operator_endpoints:\n\t- api.github.com\n",
    "endpoints quoted item": "operator_endpoints:\n  - \"api.github.com\"\n",
    "endpoints mapping item": "operator_endpoints:\n  - host: api.github.com\n",
    "endpoints second document": "operator_endpoints: []\n---\n",
    "one bad key among good ones": "operator_paths:\n  - a/\noperator_endpoints: [x]\n",
    "bad architectures beside good paths": "operator_paths:\n  - a/\narchitectures: [x]\n",
}
# Well formed by the reader's grammar, refused when the value is read as a path list or an endpoint list.
BAD_PATH_VALUES = {
    "star only": "*/", "slash only": "/", "absolute": "/etc/x", "dot dot": "a/../b", "star in the middle": "a/*/b",
    "star at the end": "scripts/*", "double star": "scripts/**/x", "duplicate": None, "too long": "a" * 300,
}
BAD_ENDPOINTS = {"with a path": "api.github.com/x", "with a star": "*.github.com", "with a colon": "api.github.com:443",
                 "slash": "/", "too long": "a" * 300}


class ReadingTheNewKeys(unittest.TestCase):
    def test_supported_forms_read(self):
        for name, text in GOOD.items():
            with self.subTest(name=name):
                parsed = config.parse_yaml(text)
                for key in ("operator_paths", "operator_endpoints"):
                    if parsed.get(key):
                        self.assertTrue(all(isinstance(item, str) for item in parsed[key]), parsed)

    def test_unsupported_forms_raise(self):
        for name, text in BAD.items():
            with self.subTest(name=name):
                with self.assertRaises(config.ConfigError):
                    config.parse_yaml(text)

    def test_values_are_the_plain_strings(self):
        parsed = config.parse_yaml("operator_paths:\n  - scripts/operator/  # x\n  - ansible/roles/operator_*/\n"
                                   "operator_endpoints:\n  - api.github.com\n")
        self.assertEqual(parsed, {"operator_paths": ["scripts/operator/", "ansible/roles/operator_*/"],
                                  "operator_endpoints": ["api.github.com"]})
        self.assertEqual(config.parse_yaml("operator_paths: []\noperator_endpoints: []\n"),
                         {"operator_paths": [], "operator_endpoints": []})

    def test_both_keys_and_architectures_in_any_order(self):
        a = "architectures:\n  - name: amd64\n    runner: r\n"
        p = "operator_paths:\n  - a/\n  - b.yml\n"
        e = "operator_endpoints:\n  - api.github.com\n"
        expect = {"architectures": [{"name": "amd64", "runner": "r"}], "operator_paths": ["a/", "b.yml"],
                  "operator_endpoints": ["api.github.com"]}
        for order in ((a, p, e), (e, p, a), (p, a, e), (p, e, a), (e, a, p), (a, e, p)):
            with self.subTest(order=[x[:5] for x in order]):
                self.assertEqual(config.parse_yaml("".join(order)), expect)

    def test_the_key_after_a_block_is_not_swallowed_by_it(self):
        parsed = config.parse_yaml("operator_paths:\n  - a/\noperator_endpoints:\n  - h.example\n")
        self.assertEqual(parsed, {"operator_paths": ["a/"], "operator_endpoints": ["h.example"]})

    def test_a_missing_key_is_absent_from_the_parse(self):
        self.assertEqual(config.parse_yaml("schema_version: 1\n"), {})
        self.assertEqual(config.parse_yaml("operator_paths: []\n"), {"operator_paths": []})

    def test_a_key_that_only_starts_like_ours_is_another_key(self):
        parsed = config.parse_yaml("operator_paths_extra: 1\noperator_pathsx:\n  - a\noperator_endpoints_list: [x]\n")
        self.assertEqual(parsed, {})

    def test_the_head_file_forms_of_architectures_are_unchanged(self):
        parsed = config.parse_yaml("architectures:\n  - name: arm64\n    runner: ubuntu-24.04-arm\n")
        self.assertEqual(parsed, {"architectures": [{"name": "arm64", "runner": "ubuntu-24.04-arm"}]})


class ReadingTheValues(unittest.TestCase):
    def test_operator_paths_returns_the_tuple(self):
        self.assertEqual(config.operator_paths({"operator_paths": ["a/", "b.yml", "c_*/"]}), ("a/", "b.yml", "c_*/"))
        self.assertEqual(config.operator_paths({"operator_paths": []}), ())

    def test_operator_paths_missing_or_malformed_raises(self):
        for value in (None, {}, {"architectures": []}, [], "x", 5):
            with self.subTest(value=value):
                with self.assertRaises(config.ConfigError):
                    config.operator_paths(value)
        for value in (None, "a/", 5, {"a": "b"}, [5], [None], [["a/"]], [""], [" "], ["a\nb"], ["a b"], ["a\\b"]):
            with self.subTest(value=value):
                with self.assertRaises(config.ConfigError):
                    config.operator_paths({"operator_paths": value})
        for name, entry in BAD_PATH_VALUES.items():
            with self.subTest(name=name):
                with self.assertRaises(config.ConfigError):
                    config.operator_paths({"operator_paths": ["a/", "a/"] if entry is None else [entry]})

    def test_operator_endpoints_returns_the_tuple_or_none_when_absent(self):
        self.assertIsNone(config.operator_endpoints(None))
        self.assertIsNone(config.operator_endpoints({}))
        self.assertEqual(config.operator_endpoints({"operator_endpoints": ["api.github.com", "h.example"]}),
                         ("api.github.com", "h.example"))
        self.assertEqual(config.operator_endpoints({"operator_endpoints": []}), ())

    def test_operator_endpoints_malformed_raises(self):
        for value in (None, "api.github.com", 5, [5], [None], [""], ["a", "a"], [["a"]]):
            with self.subTest(value=value):
                with self.assertRaises(config.ConfigError):
                    config.operator_endpoints({"operator_endpoints": value})
        for name, entry in BAD_ENDPOINTS.items():
            with self.subTest(name=name):
                with self.assertRaises(config.ConfigError):
                    config.operator_endpoints({"operator_endpoints": [entry]})

    def test_is_operator_path_exact_or_prefix(self):
        entries = ("ansible/lab.yml", "scripts/operator/", "ansible/roles/operator_*/")
        for path in ("ansible/lab.yml", "scripts/operator/x", "ansible/roles/operator_a/b", "ansible/roles/operator_x"):
            with self.subTest(hit=path):
                self.assertTrue(config.is_operator_path(path, entries))
        for path in ("ansible/lab.yml.bak", "scripts/operator", "scripts/Operator/x", "ansible/roles/operator/x", "", "x/scripts/operator/y"):
            with self.subTest(miss=path):
                self.assertFalse(config.is_operator_path(path, entries))
        self.assertFalse(config.is_operator_path("scripts/operator/x", ()))

    def test_the_fixture_files_read_as_intended(self):
        configured = config.parse_yaml((YAML / "base-configured.yaml").read_text())
        self.assertEqual(config.operator_paths(configured), PATHS_TODAY)
        self.assertEqual(config.operator_endpoints(configured), ("api.github.com",))
        empty = config.parse_yaml((YAML / "base-empty.yaml").read_text())
        self.assertEqual(config.operator_paths(empty), ())
        self.assertEqual(config.operator_endpoints(empty), ())
        with self.assertRaises(config.ConfigError):
            config.operator_paths(config.parse_yaml((YAML / "base-missing.yaml").read_text()))
        with self.assertRaises(config.ConfigError):
            config.parse_yaml((YAML / "base-malformed.yaml").read_text())
        both = config.parse_yaml((YAML / "base-two-keys-reversed.yaml").read_text())
        self.assertEqual(config.operator_paths(both), ("scripts/operator/", "ansible/roles/operator_*/"))
        self.assertEqual(config.operator_endpoints(both), ("api.github.com", "example.org"))
        self.assertEqual(config.architectures(both), (config.Arch("amd64", "ubuntu-24.04", "ubuntu-24.04"),))

    def test_this_repositorys_own_file_reads_back(self):
        parsed = config.parse_yaml((helpers.ROOT / "config" / "project.yaml").read_text())
        self.assertEqual(config.operator_paths(parsed), OWN_PATHS)
        self.assertEqual(config.operator_endpoints(parsed), ("api.github.com",))
        # CR-57 PR 5 and 6: the repository's own file and the code default both list amd64 only.
        self.assertEqual(config.architectures(parsed), (config.Arch("amd64", "ubuntu-24.04", "ubuntu-24.04"),))

    @unittest.skipUnless(HAVE_YQ, "yq is not installed")
    def test_the_reader_agrees_with_yq_on_every_supported_form(self):
        texts = dict(GOOD)
        texts["repository file"] = (helpers.ROOT / "config" / "project.yaml").read_text()
        for path in ("base-configured.yaml", "base-empty.yaml", "base-missing.yaml", "base-two-keys-reversed.yaml"):
            texts[path] = (YAML / path).read_text()
        for name, text in texts.items():
            with self.subTest(name=name):
                oracle = via_yq(text)
                ours = config.parse_yaml(text)
                for key in ("operator_paths", "operator_endpoints"):
                    if isinstance(oracle, dict) and oracle.get(key) is not None:
                        self.assertEqual(ours[key], oracle[key])
                    else:
                        self.assertNotIn(key, ours)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class Offline:
    """A fixture directory for PR 10 (an implementation PR, section "Operator script review" optional) next to recorded PR 3."""

    def __init__(self, test, base, files, review=None):
        self.dir = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.dir, True)
        for name in ("pr-3.json", "pr-3-files.json", "issue-2.json"):
            shutil.copy(RECORDED / name, self.dir)
        extra = f"## Operator script review\n{review}\n" if review else ""
        pr = {
            "number": 10, "title": "t", "state": "open", "draft": True, "merged": False, "changed_files": len(files),
            "user": {"login": "example-author"}, "head": {"ref": "impl/CR-2-operator-paths", "sha": HEAD_SHA},
            "base": {"ref": "main", "sha": BASE_SHA},
            "body": helpers.body("implementation", top=helpers.IMPL_TOP, sections={"Architecture evidence": "n/a: every architecture has a runner"},
                                 extra=extra),
        }
        self.write("pr-10.json", json.dumps(pr))
        self.write("pr-10-files.json", json.dumps(files))
        self.write("repo.json", json.dumps({"private": False}))
        if base is not None:
            shutil.copy(YAML / base, os.path.join(self.dir, "base-config.yaml"))

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def run(self):
        return run(["--repo", "o/r", "--pr", "10", "--fixtures", self.dir, "--templates", str(helpers.TEMPLATES)])


def changed(path, status="modified", previous=None):
    item = {"filename": path, "status": status}
    if previous:
        item["previous_filename"] = previous
    return item


class PC8OnTheCommandLine(unittest.TestCase):
    def test_configured_paths_need_the_section(self):
        for path in ("scripts/operator/setup", "ansible/operator.yml", "ansible/roles/operator_ssh/tasks/main.yml",
                     "toolchain/python/operator.lock", "ansible/lab.yml", "ansible/roles/ssh_hardening/tasks/main.yml"):
            with self.subTest(path=path):
                code, out, _ = Offline(self, "base-configured.yaml", [changed(path)]).run()
                self.assertEqual(code, 1, out)
                self.assertIn("FAIL PC-8: operator change without a non-empty 'Operator script review' section", out)
                code, out, _ = Offline(self, "base-configured.yaml", [changed(path)], review="Credentials: none.").run()
                self.assertEqual(code, 0, out)

    def test_other_paths_need_nothing(self):
        code, out, _ = Offline(self, "base-configured.yaml", [changed("scripts/checks/x.sh"), changed("ansible/site.yml")]).run()
        self.assertEqual(code, 0, out)

    def test_an_explicit_empty_value_is_inactive(self):
        code, out, _ = Offline(self, "base-empty.yaml", [changed("scripts/operator/setup")]).run()
        self.assertEqual(code, 0, out)
        self.assertNotIn("PC-8", out)

    def test_a_missing_key_fails_closed(self):
        for base in ("base-missing.yaml", None):
            with self.subTest(base=base):
                code, out, _ = Offline(self, base, [changed("README.md")]).run()
                self.assertEqual(code, 1, out)
                self.assertIn("FAIL PC-8: operator_paths missing or invalid in the base configuration", out)

    def test_a_malformed_key_fails_closed(self):
        code, out, _ = Offline(self, "base-malformed.yaml", [changed("README.md")]).run()
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL PC-8: operator_paths missing or invalid in the base configuration", out)

    def test_a_deleted_path_is_counted(self):
        code, out, _ = Offline(self, "base-configured.yaml", [changed("scripts/operator/old.py", "removed")]).run()
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL PC-8", out)

    def test_a_renamed_path_is_counted(self):
        for new, old in (("docs/x.md", "scripts/operator/setup"), ("scripts/operator/setup", "docs/x.md")):
            with self.subTest(new=new):
                code, out, _ = Offline(self, "base-configured.yaml", [changed(new, "renamed", previous=old)]).run()
                self.assertEqual(code, 1, out)
                self.assertIn("FAIL PC-8", out)

    def test_the_head_configuration_file_is_never_read(self):
        offline = Offline(self, "base-configured.yaml", [changed("scripts/operator/setup"), changed("config/project.yaml")])
        offline.write("head-config.yaml", "operator_paths: []\n")
        code, out, _ = offline.run()
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL PC-8", out)


if __name__ == "__main__":
    unittest.main()
