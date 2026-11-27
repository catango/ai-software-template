"""verify: smoke commands (T-7), checksum re-verification with --download (T-6), and the argument array rule."""
import os
import pathlib
import subprocess
import unittest

import helpers
from helpers import AMD64_URL, ARM64_URL, manifest_text, sha256, tar_bytes, tree
from test_install import Base


def hybrid(calls):
    """Runs real commands except dpkg, which is faked so the apt version check is deterministic."""
    def run(argv, env=None, timeout=None):
        calls.append((list(argv), env, timeout))
        if argv[0] == "dpkg-query":
            return 0, "1:2.39.5-0"
        if argv[0] == "dpkg":
            return (0, "") if argv[1:2] == ["--compare-versions"] else (1, "")
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout or 30, check=False)
        return done.returncode, done.stdout + done.stderr
    return run


class Verify(Base):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.assertEqual(self.install()[0], 0)

    def verify(self, *extra):
        return self.run_cli("verify", "--profile", "gate", "--prefix", str(self.prefix), *extra, runner=hybrid(self.calls))

    def test_smoke_passes_and_prints_versions(self):
        code, out, err = self.verify()
        self.assertEqual(code, 0, out + err)
        self.assertIn("alpha 1.2.3: ok", out)
        self.assertIn("gitlike", out)

    def test_smoke_runs_an_argument_array_with_the_prefix_first_on_path(self):
        self.verify()
        argv, env, timeout = [c for c in self.calls if c[0][0] == "alpha"][0]
        self.assertEqual(argv, ["alpha", "--version"])
        self.assertTrue(env["PATH"].startswith(str(self.prefix / "bin") + os.pathsep))
        self.assertIsNotNone(timeout)

    def test_version_mismatch_fails(self):
        binary = self.prefix / "tools" / "alpha" / "1.2.3" / "bin" / "alpha"
        binary.write_text("#!/bin/sh\necho alpha 9.9.9\n")
        code, out, _ = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("alpha 1.2.3: FAILED", out)
        self.assertIn("expected", out)

    def test_missing_binary_fails(self):
        (self.prefix / "bin" / "alpha").unlink()
        code, out, _ = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("alpha 1.2.3: FAILED", out)

    def test_failing_smoke_command_fails(self):
        binary = self.prefix / "tools" / "alpha" / "1.2.3" / "bin" / "alpha"
        binary.write_text("#!/bin/sh\necho alpha 1.2.3\nexit 3\n")
        self.assertEqual(self.verify()[0], 1)

    def test_apt_minimum_version_is_checked_when_dpkg_is_available(self):
        code, out, _ = self.verify()
        self.assertIn("2.39.5", out)
        self.assertTrue(any(c[0][:2] == ["dpkg", "--compare-versions"] for c in self.calls))

    def test_skip_apt_leaves_apt_tools_out(self):
        code, out, _ = self.verify("--skip-apt")
        self.assertEqual(code, 0, out)
        self.assertNotIn("gitlike", out)
        self.assertFalse(any(c[0][0] == "dpkg-query" for c in self.calls))

    def test_verify_without_install_fails(self):
        import shutil
        shutil.rmtree(self.prefix)
        self.assertEqual(self.verify()[0], 1)


class VerifyDownload(Base):
    def verify(self, *extra, arch="amd64"):
        return self.run_cli("verify", "--profile", "gate", "--prefix", str(self.prefix), "--download", "--arch", arch, *extra, runner=hybrid([]))

    def test_checksums_match_and_nothing_is_written(self):
        before = tree(self.prefix)
        code, out, err = self.verify("--skip-smoke")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.fetcher.calls, [AMD64_URL])
        self.assertIn("alpha 1.2.3 amd64: checksum ok", out)
        self.assertEqual(tree(self.prefix), before)

    def test_mismatch_is_reported(self):
        self.fetcher.routes[AMD64_URL] = self.good + b"x"
        code, out, _ = self.verify("--skip-smoke")
        self.assertEqual(code, 1)
        self.assertIn("checksum mismatch", out)

    def test_fetch_failure_is_reported(self):
        from toolchain_engine.errors import FetchError
        self.fetcher.routes[AMD64_URL] = FetchError("HTTP 404")
        code, out, _ = self.verify("--skip-smoke")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 404", out)

    def test_other_architecture(self):
        code, out, _ = self.verify("--skip-smoke", arch="arm64")
        self.assertEqual(code, 0)
        self.assertEqual(self.fetcher.calls, [ARM64_URL])


class VerifySource(Base):
    """`verify --source download` runs the smoke commands of the downloaded tools only, so a project manifest's apt
    entries (installed through the package list) and the engine copy that the AI user runs need nothing else."""

    def setUp(self):
        super().setUp()
        self.calls = []
        self.assertEqual(self.install()[0], 0)

    def test_the_source_filter_leaves_apt_tools_out(self):
        code, out, err = self.run_cli("verify", "--profile", "gate", "--prefix", str(self.prefix), "--source", "download", runner=hybrid(self.calls))
        self.assertEqual(code, 0, out + err)
        self.assertIn("alpha 1.2.3: ok", out)
        self.assertNotIn("gitlike", out)
        self.assertFalse([c for c in self.calls if c[0][0] in ("dpkg", "dpkg-query")])

    def test_an_unknown_source_is_a_usage_error(self):
        code, _, err = self.run_cli("verify", "--profile", "gate", "--prefix", str(self.prefix), "--source", "bogus", runner=hybrid(self.calls))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
