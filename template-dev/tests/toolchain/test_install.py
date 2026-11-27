"""T-2, T-4, T-5 and the install semantics of design 3.3."""
import io
import os
import pathlib
import subprocess
import tempfile
import unittest

import helpers
from helpers import AMD64_URL, ARM64_URL, FakeFetcher, good_archive, manifest_text, sha256, tar_bytes, tree
from toolchain_engine import cli
from toolchain_engine.errors import FetchError


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.prefix = self.root / "prefix"
        # The engine is generic: install and verify take either architecture, so the arm64 path stays covered here.
        self.good = good_archive()
        self.arm = good_archive()
        self.fetcher = FakeFetcher({AMD64_URL: self.good, ARM64_URL: self.arm})
        self.write_manifest(manifest_text(amd64_sha=sha256(self.good), arm64_sha=sha256(self.arm)))

    def write_manifest(self, text, lock=None):
        (self.root / "toolchain" / "python").mkdir(parents=True, exist_ok=True)
        (self.root / "toolchain" / "ci-manifest.toml").write_text(text)
        if lock is not None:
            (self.root / "toolchain" / "python" / "ci.lock").write_text(lock)

    def run_cli(self, *args, runner=None):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--manifest", str(self.root / "toolchain" / "ci-manifest.toml"), "--root", str(self.root), *args]
        code = cli.main(argv, fetcher=self.fetcher, runner=runner, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def install(self, *extra, arch="amd64"):
        return self.run_cli("install", "--profile", "gate", "--prefix", str(self.prefix), "--arch", arch, *extra)


class Install(Base):
    def test_installs_verifies_and_links(self):
        code, out, err = self.install()
        self.assertEqual(code, 0, out + err)
        binary = self.prefix / "tools" / "alpha" / "1.2.3" / "bin" / "alpha"
        self.assertTrue(binary.is_file() and os.access(binary, os.X_OK))
        link = self.prefix / "bin" / "alpha"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), binary.resolve())
        self.assertFalse(os.path.isabs(os.readlink(link)), "links are relative so a prefix can be relocated")
        self.assertIn("changed=1", out)
        self.assertIn("alpha 1.2.3: installed", out)
        self.assertEqual([p.name for p in self.prefix.iterdir() if p.name.startswith(".")], [], "staging left behind")

    def test_selects_the_artifact_for_the_architecture(self):
        arm = tar_bytes([("alpha-1.2.3/bin/alpha", "file", b"#!/bin/sh\necho alpha 1.2.3 arm\n", 0o755)])
        self.fetcher.routes[ARM64_URL] = arm
        self.write_manifest(manifest_text(amd64_sha=sha256(self.good), arm64_sha=sha256(arm)))
        code, out, _ = self.install(arch="arm64")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.fetcher.calls, [ARM64_URL])

    def test_only_selects_one_tool(self):
        code, out, _ = self.install("--only", "gitlike")
        self.assertEqual(code, 0)
        self.assertEqual(self.fetcher.calls, [])
        code, out, err = self.install("--only", "nosuchtool")
        self.assertEqual(code, 2)

    def test_source_filter(self):
        code, out, _ = self.install("--source", "apt")
        self.assertEqual(code, 0)
        self.assertEqual(self.fetcher.calls, [])
        self.assertIn("gitlike", out)
        self.assertNotIn("alpha", out)
        code, out, _ = self.install("--source", "download")
        self.assertEqual(code, 0)
        self.assertEqual(self.fetcher.calls, [AMD64_URL])
        self.assertNotIn("gitlike", out)

    def test_apt_tools_are_not_installed_by_the_engine(self):
        code, out, _ = self.install()
        self.assertIn("gitlike", out)
        self.assertIn("apt", out)


class T2TamperedArtifact(Base):
    def test_checksum_mismatch_installs_nothing(self):
        self.fetcher.routes[AMD64_URL] = good_archive() + b"tampered"
        code, out, err = self.install()
        self.assertEqual(code, 1)
        self.assertIn("checksum mismatch", out + err)
        self.assertEqual(tree(self.prefix) or [], [], "something was left on disk")

    def test_swapped_content_with_valid_archive_is_still_rejected(self):
        evil = tar_bytes([("alpha-1.2.3/bin/alpha", "file", b"#!/bin/sh\necho owned\n", 0o755)])
        self.fetcher.routes[AMD64_URL] = evil
        code, out, err = self.install()
        self.assertEqual(code, 1)
        self.assertEqual(tree(self.prefix) or [], [])

    def test_failed_upgrade_keeps_the_previous_install(self):
        self.assertEqual(self.install()[0], 0)
        before = tree(self.prefix)
        new = tar_bytes([("alpha-1.2.4/bin/alpha", "file", b"#!/bin/sh\necho alpha 1.2.4\n", 0o755)])
        url = AMD64_URL.replace("1.2.3", "1.2.4")
        self.fetcher.routes[url] = new + b"tampered"
        self.write_manifest(manifest_text(amd64_sha=sha256(new), arm64_sha=sha256(self.arm), version="1.2.4",
                                          amd64_url=url, arm64_url=ARM64_URL.replace("1.2.3", "1.2.4")))
        code, _, _ = self.install()
        self.assertEqual(code, 1)
        self.assertEqual(tree(self.prefix), before)

    def test_download_errors_and_size_cap_install_nothing(self):
        self.fetcher.routes[AMD64_URL] = FetchError("HTTP 503")
        self.assertEqual(self.install()[0], 1)
        self.assertEqual(tree(self.prefix) or [], [])
        self.fetcher.routes[AMD64_URL] = self.good
        self.write_manifest(manifest_text(amd64_sha=sha256(self.good), arm64_sha=sha256(self.arm), alpha_extra="max_bytes = 10\n"))
        code, out, err = self.install()
        self.assertEqual(code, 1)
        self.assertIn("size", out + err)
        self.assertEqual(tree(self.prefix) or [], [])

    def test_malicious_archive_with_a_valid_checksum_installs_nothing(self):
        evil = tar_bytes([("alpha-1.2.3/bin/alpha", "file", b"x", 0o755), ("alpha-1.2.3/../../escape", "file", b"x", 0o644)])
        self.fetcher.routes[AMD64_URL] = evil
        self.write_manifest(manifest_text(amd64_sha=sha256(evil), arm64_sha=sha256(self.arm)))
        code, out, err = self.install()
        self.assertEqual(code, 1)
        self.assertEqual(tree(self.prefix) or [], [])
        self.assertFalse((self.root / "escape").exists())

    def test_missing_declared_binary_installs_nothing(self):
        data = tar_bytes([("alpha-1.2.3/README", "file", b"x", 0o644)])
        self.fetcher.routes[AMD64_URL] = data
        self.write_manifest(manifest_text(amd64_sha=sha256(data), arm64_sha=sha256(self.arm)))
        self.assertEqual(self.install()[0], 1)
        self.assertEqual(tree(self.prefix) or [], [])


class T4Idempotent(Base):
    def test_second_run_changes_nothing_and_downloads_nothing(self):
        self.assertEqual(self.install()[0], 0)
        calls = len(self.fetcher.calls)
        before = tree(self.prefix)
        code, out, _ = self.install()
        self.assertEqual(code, 0)
        self.assertIn("changed=0", out)
        self.assertIn("skipped", out)
        self.assertEqual(len(self.fetcher.calls), calls, "the second run downloaded something")
        self.assertEqual(tree(self.prefix), before)

    def test_a_changed_checksum_reinstalls(self):
        self.assertEqual(self.install()[0], 0)
        other = tar_bytes([("alpha-1.2.3/bin/alpha", "file", SCRIPT_V2, 0o755)])
        self.fetcher.routes[AMD64_URL] = other
        self.write_manifest(manifest_text(amd64_sha=sha256(other), arm64_sha=sha256(self.arm)))
        code, out, _ = self.install()
        self.assertEqual(code, 0)
        self.assertIn("changed=1", out)
        self.assertIn(b"v2", (self.prefix / "tools" / "alpha" / "1.2.3" / "bin" / "alpha").read_bytes())

    def test_a_broken_install_is_repaired(self):
        self.assertEqual(self.install()[0], 0)
        (self.prefix / "bin" / "alpha").unlink()
        code, out, _ = self.install()
        self.assertEqual(code, 0)
        self.assertIn("changed=1", out)
        self.assertTrue((self.prefix / "bin" / "alpha").is_symlink())

    def test_upgrade_switches_the_link_and_keeps_the_old_version(self):
        self.assertEqual(self.install()[0], 0)
        new = tar_bytes([("alpha-1.2.4/bin/alpha", "file", b"#!/bin/sh\necho alpha 1.2.4\n", 0o755)])
        url = AMD64_URL.replace("1.2.3", "1.2.4")
        self.fetcher.routes[url] = new
        self.write_manifest(manifest_text(amd64_sha=sha256(new), arm64_sha=sha256(self.arm), version="1.2.4",
                                          amd64_url=url, arm64_url=ARM64_URL.replace("1.2.3", "1.2.4")))
        code, out, _ = self.install()
        self.assertEqual(code, 0, out)
        self.assertIn("alpha 1.2.4: installed", out)
        self.assertTrue((self.prefix / "tools" / "alpha" / "1.2.3" / "bin" / "alpha").exists())
        self.assertTrue(os.readlink(self.prefix / "bin" / "alpha").endswith("1.2.4/bin/alpha"))


SCRIPT_V2 = b"#!/bin/sh\necho alpha 1.2.3 v2\n"


class T5DryRun(Base):
    def test_dry_run_writes_and_downloads_nothing(self):
        for existing in (False, True):
            with self.subTest(prefix_exists=existing):
                if existing:
                    self.prefix.mkdir(exist_ok=True)
                before = tree(self.prefix)
                code, out, _ = self.install("--dry-run")
                self.assertEqual(code, 0, out)
                self.assertEqual(self.fetcher.calls, [])
                self.assertEqual(tree(self.prefix), before)
                self.assertIn("would install", out)
                self.assertIn(AMD64_URL, out)
                self.assertFalse(self.prefix.exists() and not existing, "prefix was created by a dry run")

    def test_dry_run_reports_skip_after_an_install(self):
        self.assertEqual(self.install()[0], 0)
        before = tree(self.prefix)
        code, out, _ = self.install("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would skip", out)
        self.assertEqual(tree(self.prefix), before)


class Pip(Base):
    LOCK = "yamllint==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"
    PIP = '\n[tools.pylint]\nprofiles = ["gate"]\nsource = "pip"\nlockfile = "toolchain/python/ci.lock"\nbinaries = ["yamllint"]\nsmoke = ["yamllint", "--version"]\nsmoke_match = "yamllint"\n'

    def setUp(self):
        super().setUp()
        self.write_manifest(manifest_text(amd64_sha=sha256(self.good), arm64_sha=sha256(self.arm), extra_tools=self.PIP), lock=self.LOCK)
        self.commands = []

    def runner(self, argv, env=None, timeout=None):
        self.commands.append(list(argv))
        if argv[1:3] == ["-m", "venv"]:
            (pathlib.Path(argv[3]) / "bin").mkdir(parents=True)
        if "pip" in " ".join(argv) and "install" in argv:
            (self.prefix / "venv" / "bin" / "yamllint").write_text("#!/bin/sh\necho yamllint 1.0\n")
            os.chmod(self.prefix / "venv" / "bin" / "yamllint", 0o755)
        return 0, ""

    def test_pip_install_uses_hashes_wheels_only_and_no_deps(self):
        code, out, err = self.install("--only", "pylint")
        self.assertEqual(code, 0, out + err)
        pip = [c for c in self.commands if "install" in c][0]
        for flag in ("--require-hashes", "--only-binary", ":all:", "--no-deps", "-r", str(self.root / "toolchain" / "python" / "ci.lock")):
            self.assertIn(flag, pip)
        self.assertTrue(all(isinstance(part, str) for c in self.commands for part in c))
        self.assertEqual(os.readlink(self.prefix / "bin" / "yamllint"), "../venv/bin/yamllint")

    def run_cli(self, *args, runner=None):
        return super().run_cli(*args, runner=runner or self.runner)

    def test_pip_install_is_idempotent_and_changes_with_the_lock(self):
        self.assertEqual(self.install("--only", "pylint")[0], 0)
        count = len(self.commands)
        code, out, _ = self.install("--only", "pylint")
        self.assertIn("changed=0", out)
        self.assertEqual(len(self.commands), count)
        (self.root / "toolchain" / "python" / "ci.lock").write_text(self.LOCK.replace("1.0", "1.1"))
        code, out, _ = self.install("--only", "pylint")
        self.assertIn("changed=1", out)

    def test_pip_failure_is_reported(self):
        def failing(argv, env=None, timeout=None):
            return (1, "boom") if "install" in argv else (0, "")
        code, out, err = super(Pip, self).run_cli("install", "--profile", "gate", "--prefix", str(self.prefix), "--arch", "amd64", "--only", "pylint", runner=failing)
        self.assertEqual(code, 1)
        self.assertNotIn("changed=", out.replace("changed=0", ""))


if __name__ == "__main__":
    unittest.main()
