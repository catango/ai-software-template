"""CLI behavior: exit codes, arguments, check, list, and the real manifest of this repository."""
import io
import pathlib
import subprocess
import sys
import unittest

import helpers
from helpers import ROOT, manifest_text, sha256, without_arm64
from test_install import Base
from toolchain_engine import cli, manifest


class Usage(Base):
    def test_a_manifest_without_an_artifact_for_a_declared_architecture_is_refused(self):
        """Every declared architecture needs an artifact: install and verify refuse before any download, and check says so."""
        self.write_manifest(without_arm64(manifest_text(amd64_sha=sha256(self.good))))
        self.assertEqual(self.install(arch="amd64")[0], 2)
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("tools.alpha.artifacts.arm64: missing", out)

    def test_an_architecture_the_manifest_does_not_declare_is_a_usage_error(self):
        code, _, err = self.install(arch="riscv")
        self.assertEqual(code, 2)
        self.assertIn("not declared", err)

    def test_usage_errors_exit_2(self):
        cases = [[], ["bogus"], ["install"], ["install", "--profile", "gate"], ["install", "--profile", "Bad Name", "--prefix", "/x"],
                 ["install", "--profile", "gate", "--prefix", "/x", "--arch", "riscv"], ["list"], ["verify", "--profile", "gate"],
                 ["install", "--profile", "gate", "--prefix", "/x", "--unknown"]]
        for argv in cases:
            with self.subTest(argv=argv):
                out, err = io.StringIO(), io.StringIO()
                self.assertEqual(cli.main(argv, out=out, err=err), 2)

    def test_invalid_manifest_is_exit_2_for_install_and_1_for_check(self):
        self.write_manifest("schema_version = 9\n")
        self.assertEqual(self.install()[0], 2)
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("schema_version", out)

    def test_missing_manifest_is_exit_2(self):
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(cli.main(["--manifest", "/nonexistent/manifest.toml", "check"], out=out, err=err), 2)

    def test_check_ok(self):
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 0)
        self.assertIn("ok", out)

    def test_list(self):
        code, out, _ = self.run_cli("list", "--profile", "gate", "--arch", "amd64")
        self.assertEqual(code, 0)
        self.assertIn("alpha 1.2.3 download", out)
        self.assertIn("gitlike", out)
        code, out, _ = self.run_cli("list", "--profile", "other")
        self.assertIn("alpha", out)
        self.assertNotIn("gitlike", out)


    def test_list_json_and_source_filter(self):
        import json
        code, out, _ = self.run_cli("list", "--profile", "gate", "--format", "json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        names = {item["name"]: item for item in data}
        self.assertEqual(names["gitlike"]["package"], "git")
        self.assertEqual(names["gitlike"]["source"], "apt")
        self.assertEqual(names["alpha"]["version"], "1.2.3")
        code, out, _ = self.run_cli("list", "--profile", "gate", "--format", "json", "--source", "apt")
        self.assertEqual([item["name"] for item in json.loads(out)], ["gitlike"])
        code, out, _ = self.run_cli("list", "--profile", "gate", "--source", "download")
        self.assertNotIn("gitlike", out)

    def test_bad_format_or_source_is_a_usage_error(self):
        for argv in (["list", "--profile", "gate", "--format", "xml"], ["list", "--profile", "gate", "--source", "snap"],
                     ["install", "--profile", "gate", "--prefix", "/x", "--source", "snap"]):
            with self.subTest(argv=argv):
                self.assertEqual(cli.main(argv, out=io.StringIO(), err=io.StringIO()), 2)


class Arch(unittest.TestCase):
    MACHINES = {"amd64": ("x86_64", "amd64"), "arm64": ("aarch64", "arm64")}

    def test_detect(self):
        self.assertEqual(cli.detect_arch(self.MACHINES, "x86_64"), "amd64")
        self.assertEqual(cli.detect_arch(self.MACHINES, "aarch64"), "arm64")
        for bad in ("armv7l", "i686", "ppc64le", "riscv64", ""):
            with self.subTest(machine=bad), self.assertRaises(Exception):
                cli.detect_arch(self.MACHINES, bad)

    def test_the_engine_holds_no_architecture_table_of_its_own(self):
        self.assertEqual(cli.detect_arch({"only": ("odd-machine",)}, "odd-machine"), "only")
        with self.assertRaises(Exception):
            cli.detect_arch({}, "x86_64")


class RealManifest(unittest.TestCase):
    """The CI manifest shipped in this repository (T-14: both architectures, checksums)."""

    def setUp(self):
        self.path = ROOT / "toolchain" / "ci-manifest.toml"
        self.raw = manifest.load(self.path)

    def test_check_passes(self):
        self.assertEqual(manifest.check_file(self.path, ROOT), [])

    def test_gate_tools_resolve_under_their_expected_names(self):
        names = set()
        for tool in self.raw["tools"].values():
            names.update(pathlib.PurePosixPath(b).name for b in tool.get("binaries", []))
        for expected in ("yq", "shellcheck", "actionlint", "yamllint", "pymarkdown"):
            self.assertIn(expected, names)

    def test_every_download_has_an_artifact_and_a_checksum_for_every_declared_architecture(self):
        declared = set(self.raw["architectures"])
        self.assertEqual(declared, {"amd64", "arm64"})
        for name, tool in self.raw["tools"].items():
            if tool["source"] == "download":
                with self.subTest(tool=name):
                    self.assertEqual(set(tool["artifacts"]), declared)
                    for arch, artifact in tool["artifacts"].items():
                        self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$", arch)
                        self.assertTrue(artifact["url"].startswith("https://"))

    def test_the_two_architectures_have_different_artifacts(self):
        for name, tool in self.raw["tools"].items():
            if tool["source"] == "download":
                with self.subTest(tool=name):
                    shas = [a["sha256"] for a in tool["artifacts"].values()]
                    self.assertEqual(len(shas), len(set(shas)))

    def test_no_tool_is_unpinned(self):
        for name, tool in self.raw["tools"].items():
            with self.subTest(tool=name):
                self.assertIn(tool["source"], ("download", "pip"))
                if tool["source"] == "download":
                    self.assertRegex(tool["version"], r"^\d+(\.\d+)*$")

    def test_lock_file_has_hashes_for_both_architectures_of_native_packages(self):
        text = (ROOT / "toolchain" / "python" / "ci.lock").read_text()
        self.assertIn("yamllint==", text)
        self.assertIn("pymarkdownlnt==", text)
        self.assertNotIn("--index-url", text)

    def test_script_runs_isolated(self):
        done = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "toolchain" / "install"), "check"], capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
