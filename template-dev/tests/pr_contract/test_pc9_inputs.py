"""CR-43 S-9, S-20: where PC-9 gets its inputs (the base configuration, the private flag, the head SHA), end to end.

Part 1 runs the command line against fixture directories (`--fixtures`), as a PR check would. Part 2 checks the GitHub client's
two new read-only calls. Part 3 checks the subset reader of the base configuration (standard library, no process) and compares it with `yq` where `yq`
is installed (the gate always has it).
"""
import base64
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import helpers
from helpers import BASE_SHA, HEAD_SHA, evidence_line
from pr_contract import cli, config
from pr_contract.api import FixtureClient, GitHubClient
from pr_contract.errors import ApiError, NotFound
from pr_contract.model import PullRequest
from test_api import Opener, http_error

YAML = helpers.FIXTURES / "pc9"
RECORDED = helpers.FIXTURES / "recorded"
SECTION = "Architecture evidence"
HAVE_YQ = shutil.which("yq") is not None


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class Offline:
    """A fixture directory for PR 10 (an implementation PR) built next to the recorded design PR 3."""

    def __init__(self, test, section, base=None, head=None, private=False, files=("scripts/x",), head_sha=HEAD_SHA):
        self.dir = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.dir, True)
        for name in ("pr-3.json", "pr-3-files.json", "issue-2.json"):
            shutil.copy(RECORDED / name, self.dir)
        pr = {
            "number": 10, "title": "t", "state": "open", "draft": True, "merged": False, "changed_files": len(files),
            "user": {"login": "example-author"}, "head": {"ref": "impl/CR-2-evidence", "sha": head_sha},
            "base": {"ref": "main", "sha": BASE_SHA},
            "body": helpers.body("implementation", top=helpers.IMPL_TOP, sections={SECTION: section}),
        }
        self.write("pr-10.json", json.dumps(pr))
        self.write("pr-10-files.json", json.dumps([{"filename": f, "status": "modified"} for f in files]))
        self.write("repo.json", json.dumps({"private": private}))
        if base is not None:
            shutil.copy(YAML / base, os.path.join(self.dir, "base-config.yaml"))
        if head is not None:
            shutil.copy(YAML / head, os.path.join(self.dir, "head-config.yaml"))  # never read: a PR's own value

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def run(self):
        return run(["--repo", "o/r", "--pr", "10", "--fixtures", self.dir, "--templates", str(helpers.TEMPLATES)])


class OfflineCommandLine(unittest.TestCase):
    def test_valid_line_passes(self):
        code, out, _ = Offline(self, evidence_line(), base="base-no-arm-runner.yaml").run()
        self.assertEqual(code, 0, out)
        self.assertTrue(out.rstrip().endswith("pr-contract: pass"))

    def test_each_failure_exits_1_with_the_rule_id(self):
        cases = {
            "missing": "n/a: every architecture has a runner",
            "wrong architecture": evidence_line(arch="amd64", machine="x86_64"),
            "sha not the head": evidence_line(sha="c3" * 20),
            "dirty": evidence_line(state="dirty"),
            "different command": evidence_line(command="./scripts/quality-gate.sh"),
            "non-zero exit": evidence_line(code="1"),
            "malformed": "evidence: arm64 | aarch64",
        }
        for name, section in cases.items():
            with self.subTest(case=name):
                code, out, _ = Offline(self, section, base="base-no-arm-runner.yaml").run()
                self.assertEqual(code, 1, out)
                self.assertIn("FAIL PC-9: ", out)
                self.assertNotIn("FAIL PC-1", out)

    def test_s20_a_head_that_gives_arm64_a_runner_is_still_judged_by_the_base(self):
        offline = Offline(self, "n/a: every architecture has a runner", base="base-no-arm-runner.yaml",
                          head="head-gives-arm-a-runner.yaml", files=("scripts/x", "config/project.yaml"))
        code, out, _ = offline.run()
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL PC-9: no evidence line for architecture arm64", out)

    def test_s20_a_head_that_empties_the_list_is_still_judged_by_the_base(self):
        offline = Offline(self, "n/a", base="base-no-arm-runner.yaml", head="head-empties-architectures.yaml",
                          files=("scripts/x", "config/project.yaml"))
        code, out, _ = offline.run()
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL PC-9: no evidence line for architecture arm64", out)

    def test_s20_a_head_that_adds_an_architecture_is_still_judged_by_the_base(self):
        offline = Offline(self, "n/a: every architecture has a runner", base="base-with-runners.yaml",
                          head="head-adds-an-architecture.yaml", files=("scripts/x", "config/project.yaml"))
        code, out, _ = offline.run()
        self.assertEqual(code, 0, out)

    def test_s20_the_head_configuration_file_is_never_read(self):
        offline = Offline(self, "n/a", base="base-no-arm-runner.yaml")
        offline.write("head-config.yaml", "architectures: [\n")  # not even valid YAML
        self.assertEqual(offline.run()[0], 1)  # judged by the base, and nothing crashed on the head

    def test_the_private_flag_selects_the_runner_value(self):
        public = Offline(self, "n/a: every architecture has a runner", base="base-with-runners.yaml", private=False)
        self.assertEqual(public.run()[0], 0)
        private = Offline(self, "n/a", base="base-with-runners.yaml", private=True)
        code, out, _ = private.run()
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL PC-9: no evidence line for architecture arm64", out)
        ok = Offline(self, evidence_line(), base="base-with-runners.yaml", private=True)
        self.assertEqual(ok.run()[0], 0)

    def test_no_configuration_or_no_key_on_the_base_means_the_default(self):
        # For PC-9 only: PC-8 fails closed without `operator_paths` (CR-57 PR 0), so the file without the key states it empty.
        code, out, _ = Offline(self, "n/a: every architecture has a runner", base="base-without-key.yaml").run()
        self.assertEqual(code, 0, out)
        code, out, _ = Offline(self, "n/a: every architecture has a runner", base=None).run()
        self.assertNotIn("PC-9", out)
        self.assertIn("FAIL PC-8: operator_paths missing or invalid in the base configuration", out)

    def test_an_unreadable_base_configuration_fails_closed(self):
        for base in ("base-invalid.yaml", "base-not-yaml.yaml", "base-two-documents.yaml"):
            with self.subTest(base=base):
                code, out, _ = Offline(self, evidence_line(), base=base).run()
                self.assertEqual(code, 1, out)
                self.assertIn("FAIL PC-9: architectures in the base configuration is invalid", out)

    def test_a_missing_private_flag_is_an_error_not_a_pass(self):
        offline = Offline(self, evidence_line(), base="base-with-runners.yaml", private=True)
        os.remove(os.path.join(offline.dir, "repo.json"))
        code, _, err = offline.run()
        self.assertEqual(code, 3)
        self.assertIn("pr-contract: error:", err)

    def test_the_recorded_prs_still_pass_without_any_new_fixture_file(self):
        code, out, _ = run(["--repo", "o/r", "--pr", "5", "--fixtures", str(RECORDED), "--templates", str(helpers.TEMPLATES)])
        self.assertEqual(code, 1, out)  # the recorded body predates the new template section, so PC-1 names exactly that
        self.assertIn("FAIL PC-1: section missing: Architecture evidence", out)
        self.assertNotIn("PC-9", out)


class ClientCalls(unittest.TestCase):
    """The two new GitHub reads: the repository's private flag and the configuration file at the base commit."""

    URL = "https://api.github.com/repos/o/r"

    def client(self, routes):
        opener = Opener(routes)
        return GitHubClient("o/r", token="tok-SECRET", opener=opener), opener

    def contents(self, text, **extra):
        data = {"type": "file", "encoding": "base64", "content": base64.encodebytes(text.encode()).decode()}
        data.update(extra)
        return data

    def test_base_config_asks_for_the_file_at_the_base_sha_and_parses_it(self):
        url = f"{self.URL}/contents/config/project.yaml?ref={BASE_SHA}"
        c, opener = self.client({url: self.contents("architectures:\n  - name: arm64\n")})
        self.assertEqual(c.base_config(BASE_SHA), {"architectures": [{"name": "arm64"}]})
        self.assertEqual([r.full_url for r in opener.requests], [url])
        self.assertEqual(opener.requests[0].get_method(), "GET")

    def test_base_config_missing_file_is_none(self):
        c, _ = self.client({"*": http_error(404)})
        self.assertIsNone(c.base_config(BASE_SHA))

    def test_base_config_refuses_a_bad_sha_before_any_request(self):
        c, opener = self.client({})
        for sha in ("", "main", BASE_SHA[:39], BASE_SHA + "0", BASE_SHA.upper(), "../x", BASE_SHA + "?x=1"):
            with self.subTest(sha=sha):
                with self.assertRaises(ApiError):
                    c.base_config(sha)
        self.assertEqual(opener.requests, [])

    def test_base_config_rejects_a_response_that_is_not_a_file(self):
        bad = [
            [],
            {"type": "dir"},
            {"type": "file", "encoding": "none", "content": ""},
            {"type": "file", "encoding": "base64", "content": 5},
            {"type": "file", "encoding": "base64", "content": "!!!not base64!!!"},
            {"type": "symlink", "target": "x"},
            self.contents("x", size=10_000_000),
            {"type": "file", "encoding": "base64", "content": base64.b64encode(b"\xff\xfe").decode()},
            {"type": "file", "encoding": "base64", "content": base64.b64encode(b"a: 1\n" * 40000).decode()},
        ]
        for data in bad:
            with self.subTest(data=str(data)[:40]):
                c, _ = self.client({"*": data})
                with self.assertRaises(ApiError):
                    c.base_config(BASE_SHA)

    def test_base_config_other_errors_are_not_a_pass(self):
        for error in (http_error(500), http_error(403, {"x-ratelimit-remaining": "0"})):
            c, _ = self.client({"*": error})
            with self.assertRaises(ApiError):
                c.base_config(BASE_SHA)

    def test_repo_private_reads_the_flag(self):
        for flag in (True, False):
            c, opener = self.client({self.URL: {"private": flag, "name": "r"}})
            self.assertIs(c.repo_private(), flag)
            self.assertEqual([r.full_url for r in opener.requests], [self.URL])

    def test_repo_private_malformed_is_an_error(self):
        for data in ({}, {"private": "false"}, {"private": None}, [], {"private": 0}):
            c, _ = self.client({self.URL: data})
            with self.assertRaises(ApiError):
                c.repo_private()

    def test_the_pull_request_carries_both_shas(self):
        api = {"number": 5, "title": "t", "body": None, "state": "open", "draft": True, "merged": False, "changed_files": 1,
               "user": {"login": "u"}, "head": {"ref": "impl/CR-2-x", "sha": HEAD_SHA}, "base": {"ref": "main", "sha": BASE_SHA}}
        pr = PullRequest.from_api(api)
        self.assertEqual((pr.head_sha, pr.base_sha), (HEAD_SHA, BASE_SHA))
        api["head"].pop("sha")
        api["base"].pop("sha")
        pr = PullRequest.from_api(api)
        self.assertEqual((pr.head_sha, pr.base_sha), ("", ""))
        api["head"]["sha"] = 5
        with self.assertRaises(ApiError):
            PullRequest.from_api(api)

    def test_the_token_is_only_sent_as_the_authorization_header(self):
        url = f"{self.URL}/contents/config/project.yaml?ref={BASE_SHA}"
        c, opener = self.client({url: self.contents("a: 1\n")})
        c.base_config(BASE_SHA)
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer tok-SECRET")
        self.assertNotIn("tok-SECRET", opener.requests[0].full_url)


def via_yq(text):
    done = subprocess.run(["yq", "-o=json", "."], input=text, capture_output=True, text=True, timeout=10, check=True)
    return json.loads(done.stdout)


GOOD = {
    "block, plain values": "architectures:\n  - name: arm64\n    runner: ubuntu-24.04-arm\n    runner_private: ubuntu-24.04\n",
    "quoted and empty": "architectures:\n  - name: 'arm64'\n    runner: \"\"\n    runner_private: ''\n",
    "omitted values": "architectures:\n  - name: arm64\n    runner:\n    runner_private: # none\n",
    "comments and blanks": "# head\narchitectures:   # the list\n\n  # first\n  - name: amd64  # x\n    runner: a\n\n  - name: arm64\n    runner: b\n",
    "list at column 0": "architectures:\n- name: amd64\n  runner: a\n- name: arm64\n",
    "wide gap after dash": "architectures:\n  -   name: amd64\n      runner: a\n",
    "empty list": "architectures: []  # none\n",
    "other keys around it": "a: 1\nb:\n  c: [1, 2]\narchitectures:\n  - name: x\nlast: \"y\"\n",
    "document start": "---\narchitectures: []\n",
    "crlf": "architectures:\r\n  - name: amd64\r\n    runner: a\r\n",
    "keys in another order": "architectures:\n  - runner_private: p\n    name: arm64\n    runner: r\n",
    "no key": "schema_version: 1\n",
    "empty file": "",
    "only a comment": "# nothing\n",
}
BAD = {
    "flow list of mappings": "architectures: [{name: arm64}]\n",
    "unclosed flow": "architectures: [\n",
    "scalar": "architectures: arm64\n",
    "null": "architectures:\n",
    "null then next key": "architectures:\nother: 1\n",
    "flow item": "architectures:\n  - {name: arm64}\n",
    "string item": "architectures:\n  - arm64\n",
    "block scalar": "architectures: |\n  - name: x\n",
    "anchor": "architectures:\n  - &a\n    name: x\n",
    "alias value": "architectures:\n  - name: *a\n",
    "tag": "architectures:\n  - name: !!str x\n",
    "merge key": "<<: *base\narchitectures: []\n",
    "tab indent": "architectures:\n\t- name: x\n",
    "tab in item": "architectures:\n  - name: x\n\truner: y\n",
    "repeated key": "architectures: []\narchitectures: []\n",
    "quoted key": "\"architectures\": []\n",
    "complex key": "? architectures\n: []\n",
    "space before colon": "architectures : []\n",
    "no space after colon": "architectures:[]\n",
    "second document": "architectures: []\n---\narchitectures: []\n",
    "document end": "architectures: []\n...\n",
    "directive then doc": "x: 1\n---\narchitectures: []\n",
    "indentation changes": "architectures:\n  - name: a\n - name: b\n",
    "key at the wrong column": "architectures:\n  - name: a\n   runner: b\n",
    "orphan key": "architectures:\n    runner: b\n",
    "repeated entry key": "architectures:\n  - name: a\n    name: b\n",
    "number name": "architectures:\n  - name: 123\n",
    "boolean runner": "architectures:\n  - name: a\n    runner: true\n",
    "null word": "architectures:\n  - name: a\n    runner: null\n",
    "spaces in a value": "architectures:\n  - name: a\n    runner: two words\n",
    "escape in a quoted value": "architectures:\n  - name: a\n    runner: \"a\\\\n\"\n",
    "multi-line value": "architectures:\n  - name: a\n    runner: \"a\n      b\"\n",
    "nested mapping": "architectures:\n  - name: a\n    runner:\n      x: y\n",
    "nul": "architectures: []\n\x00",
    "upper-case key": "architectures:\n  - Name: a\n",
}


class ReadingTheConfiguration(unittest.TestCase):
    def test_the_default_applies_for_a_missing_file_or_key(self):
        for parsed in (None, {}, {"schema_version": 1}):
            self.assertEqual(config.architectures(parsed), config.DEFAULT_ARCHITECTURES)

    def test_the_default_lists_amd64_only(self):
        # CR-57 PR 6 (O-24): this repository supports amd64 only, so the default needs no evidence line and names no arm64 runner.
        self.assertEqual(config.DEFAULT_ARCHITECTURES, (config.Arch("amd64", "ubuntu-24.04", "ubuntu-24.04"),))
        self.assertEqual({a.name for a in config.DEFAULT_ARCHITECTURES}, {"amd64"})

    def test_the_default_needs_no_line_for_a_public_repository(self):
        by_name = {a.name: a for a in config.DEFAULT_ARCHITECTURES}
        self.assertTrue(by_name["amd64"].runner and by_name["amd64"].runner_private)

    def test_this_repositorys_own_configuration_lists_amd64_only(self):
        # CR-57 PR 5: the arm64 entry is gone from config/project.yaml; PR 6 made the code default amd64 only too.
        parsed = config.parse_yaml((helpers.ROOT / "config" / "project.yaml").read_text())
        self.assertIn("architectures", parsed)
        self.assertEqual(config.architectures(parsed), (config.Arch("amd64", "ubuntu-24.04", "ubuntu-24.04"),))

    def test_the_fixture_files_read_as_intended(self):
        expect = {
            "base-no-arm-runner.yaml": (config.Arch("amd64", "ubuntu-24.04", "ubuntu-24.04"), config.Arch("arm64", "", "")),
            "base-with-runners.yaml": (config.Arch("amd64", "ubuntu-24.04", "ubuntu-24.04"), config.Arch("arm64", "ubuntu-24.04-arm", "")),
            "head-empties-architectures.yaml": (),
        }
        for name, arches in expect.items():
            with self.subTest(name=name):
                self.assertEqual(config.architectures(config.parse_yaml((YAML / name).read_text())), arches)

    def test_supported_forms_read_and_validate(self):
        for name, text in GOOD.items():
            with self.subTest(name=name):
                parsed = config.parse_yaml(text)
                if "architectures" in parsed and parsed["architectures"]:
                    config.architectures(parsed)

    def test_unsupported_forms_raise(self):
        for name, text in BAD.items():
            with self.subTest(name=name):
                with self.assertRaises(config.ConfigError):
                    config.architectures(config.parse_yaml(text))

    @unittest.skipUnless(HAVE_YQ, "yq is not installed")
    def test_the_reader_agrees_with_yq_on_every_supported_form(self):
        texts = dict(GOOD)
        texts["repository file"] = (helpers.ROOT / "config" / "project.yaml").read_text()
        for path in sorted(YAML.glob("*.yaml")):
            if path.name not in ("base-invalid.yaml", "base-not-yaml.yaml", "base-two-documents.yaml"):
                texts[path.name] = path.read_text()
        for name, text in texts.items():
            with self.subTest(name=name):
                oracle = via_yq(text)
                oracle = {} if not isinstance(oracle, dict) or "architectures" not in oracle else {"architectures": oracle["architectures"]}
                ours = config.parse_yaml(text)
                if oracle.get("architectures") is not None:
                    # yq turns an omitted value into null; the reader does too, and validation reads both as an empty label
                    self.assertEqual(ours["architectures"], oracle["architectures"])

    def test_a_non_string_or_oversize_input_is_an_error(self):
        for value in (None, 5, b"x", "x" * 70000):
            with self.assertRaises(config.ConfigError):
                config.parse_yaml(value)

    def test_the_reader_runs_no_process(self):
        source = pathlib.Path(config.__file__).read_text()
        self.assertNotIn("subprocess", source)


class FixtureClientCalls(unittest.TestCase):
    def test_missing_fixture_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = FixtureClient(tmp)
            self.assertIsNone(c.base_config(BASE_SHA))
            with self.assertRaises(NotFound):
                c.repo_private()


if __name__ == "__main__":
    unittest.main()
