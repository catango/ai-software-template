"""T-1: `check` rejects each malformed manifest; plus the structural rules in design 3.2."""
import pathlib
import tempfile
import unittest

import helpers
from helpers import ARM64_URL, AMD64_URL, HEAD, manifest_text, without_arm64
from toolchain_engine import manifest

GOOD = "a" * 64
LOCK = (
    "# comment\nyamllint==1.0.0 \\\n    --hash=sha256:" + GOOD + " \\\n    --hash=sha256:" + "b" * 64 + "\n\n"
    "pyyaml==6.0 \\\n    --hash=sha256:" + "c" * 64 + "\n"
)
PIP = '''
[tools.pylint]
profiles = ["gate"]
source = "pip"
lockfile = "toolchain/python/ci.lock"
binaries = ["yamllint"]
smoke = ["yamllint", "--version"]
smoke_match = "yamllint"
'''


def check(text, lock=LOCK):
    """Validation errors of TEXT."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "toolchain" / "python").mkdir(parents=True)
        if lock is not None:
            (root / "toolchain" / "python" / "ci.lock").write_text(lock)
        (root / "toolchain" / "ci-manifest.toml").write_text(text)
        return manifest.check_file(root / "toolchain" / "ci-manifest.toml", root)


def good(**kw):
    kw.setdefault("amd64_sha", GOOD)
    kw.setdefault("arm64_sha", "b" * 64)
    return manifest_text(**kw)


class Accepts(unittest.TestCase):
    def test_a_good_manifest_has_no_errors(self):
        self.assertEqual(check(good()), [])

    def test_pip_and_apt_sources(self):
        self.assertEqual(check(good(extra_tools=PIP)), [])

    def test_binary_format(self):
        text = good().replace('format = "tar.gz"', 'format = "binary"').replace("strip = 1\n", "strip = 0\n") \
            .replace('binaries = ["bin/alpha"]', 'binaries = ["alpha"]')
        self.assertEqual(check(text), [])


class DeclaredArchitectures(unittest.TestCase):
    """The manifest declares its architectures; every download needs an artifact for each of them and for no other."""

    def test_a_single_declared_architecture_needs_only_its_artifacts(self):
        text = without_arm64(good()).replace(HEAD, 'schema_version = 1\n\n[architectures]\namd64 = ["x86_64"]\n')
        self.assertEqual(check(text), [])

    def test_an_artifact_for_an_undeclared_architecture_fails(self):
        text = good().replace(HEAD, 'schema_version = 1\n\n[architectures]\namd64 = ["x86_64"]\n')
        errors = check(text)
        self.assertTrue(any("tools.alpha.artifacts.arm64" in e and "does not declare" in e for e in errors), errors)

    def test_a_declared_architecture_without_an_artifact_fails(self):
        errors = check(without_arm64(good()))
        self.assertTrue(any("tools.alpha.artifacts.arm64" in e and "missing" in e for e in errors), errors)

    def test_the_architectures_table_is_required_and_checked(self):
        for bad in ("schema_version = 1\n", "schema_version = 1\n[architectures]\n",
                    'schema_version = 1\n[architectures]\nBad_Name = ["x"]\n',
                    'schema_version = 1\n[architectures]\namd64 = []\n',
                    'schema_version = 1\n[architectures]\namd64 = "x86_64"\n',
                    'schema_version = 1\n[architectures]\namd64 = ["x"]\narm64 = ["x"]\n'):
            with self.subTest(bad=bad):
                errors = check(good().replace(HEAD, bad))
                self.assertTrue(any("architectures" in e for e in errors), errors)


class Rejects(unittest.TestCase):
    def assert_error(self, text, fragment, lock=LOCK):
        errors = check(text, lock)
        self.assertTrue(any(fragment in e for e in errors), f"{fragment!r} not in {errors}")

    def test_missing_architecture(self):
        text = good()
        text = text[: text.index("[tools.alpha.artifacts.arm64]")] + text[text.index("[tools.gitlike]"):]
        self.assert_error(text, "arm64")
        text = good()
        start = text.index("[tools.alpha.artifacts.amd64]")
        end = text.index("[tools.alpha.artifacts.arm64]")
        self.assert_error(text[:start] + text[end:], "amd64")

    def test_undeclared_architecture(self):
        self.assert_error(good() + '\n[tools.alpha.artifacts.riscv]\nurl = "https://example.invalid/1.2.3"\nsha256 = "' + GOOD + '"\n', "riscv")

    def test_malformed_checksums(self):
        for bad in ("", "abc", "A" * 64, "g" * 64, "a" * 63, "a" * 65, " " + "a" * 63, "sha256:" + "a" * 64):
            with self.subTest(bad=bad):
                self.assert_error(good(amd64_sha=bad), "sha256")

    def test_non_https_urls(self):
        for url in (AMD64_URL.replace("https", "http"), "ftp://example.invalid/alpha-1.2.3", "file:///tmp/alpha-1.2.3", "//example.invalid/1.2.3",
                    "example.invalid/alpha-1.2.3"):
            with self.subTest(url=url):
                self.assert_error(good(amd64_url=url), "https")

    def test_latest_and_version_ranges(self):
        for version in ("latest", "LATEST", "^1.2.3", "~1.2", ">=1.2.3", "1.x", "*", "1.2.3,1.2.4", "", "v 1"):
            with self.subTest(version=version):
                self.assert_error(good(version=version, amd64_url=f"https://example.invalid/{version}", arm64_url=f"https://example.invalid/{version}"), "version")
        self.assert_error(good(amd64_url="https://example.invalid/latest/alpha-1.2.3.tar.gz"), "latest")

    def test_unknown_keys(self):
        self.assert_error(good(alpha_extra='surprise = "x"\n'), "surprise")
        self.assert_error(good(head='schema_version = 1\nsurprise = 1\n'), "surprise")
        self.assert_error(good() + '\n[tools.alpha.artifacts.amd64x]\nurl = "https://x/1.2.3"\nsha256 = "' + GOOD + '"\nextra = 1\n', "alpha")

    def test_version_must_appear_in_every_url(self):
        self.assert_error(good(amd64_url="https://example.invalid/alpha/linux-amd64.tar.gz"), "version")
        self.assert_error(good(arm64_url="https://example.invalid/alpha/9.9.9/linux-arm64.tar.gz"), "version")

    def test_unknown_or_missing_schema_version(self):
        self.assert_error(good(head="schema_version = 2\n"), "schema_version")
        self.assert_error(good(head=""), "schema_version")
        self.assert_error(good(head='schema_version = "1"\n'), "schema_version")

    def test_profiles(self):
        self.assert_error(good().replace('profiles = ["gate"]', "profiles = []"), "profiles")
        self.assert_error(good().replace('profiles = ["gate"]', 'profiles = ["gate", "gate"]'), "profiles")
        self.assert_error(good().replace('profiles = ["gate"]', 'profiles = ["Gate Two"]'), "profiles")
        self.assert_error(good().replace('profiles = ["gate"]', 'profiles = "gate"'), "profiles")

    def test_smoke_must_be_an_argument_array(self):
        self.assert_error(good().replace('smoke = ["alpha", "--version"]', 'smoke = "alpha --version"'), "smoke")
        self.assert_error(good().replace('smoke = ["alpha", "--version"]', "smoke = []"), "smoke")
        self.assert_error(good().replace('smoke = ["alpha", "--version"]', 'smoke = ["alpha", 3]'), "smoke")

    def test_smoke_match_required_for_download_and_pip(self):
        self.assert_error(good().replace('smoke_match = "alpha 1.2.3"\n', ""), "smoke_match")
        self.assert_error(good(extra_tools=PIP.replace('smoke_match = "yamllint"\n', "")), "smoke_match")

    def test_duplicate_binary_names_across_tools(self):
        other = PIP.replace('binaries = ["yamllint"]', 'binaries = ["alpha"]')
        self.assert_error(good(extra_tools=other), "alpha")

    def test_shell_metacharacters(self):
        for value in ("a;b", "a|b", "a$(id)", "a`id`", "a b", "a&b", "a>b", "a\\b", 'a"b', "a'b", "a\nb"):
            with self.subTest(value=value):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                self.assert_error(good().replace('binaries = ["bin/alpha"]', f'binaries = ["{escaped}"]'), "binaries")
        self.assert_error(good(amd64_url=AMD64_URL + "?a=b;rm"), "url")

    def test_download_fields(self):
        self.assert_error(good().replace('format = "tar.gz"', 'format = "rar"'), "format")
        self.assert_error(good().replace("strip = 1", "strip = -1"), "strip")
        self.assert_error(good().replace("strip = 1", 'strip = "1"'), "strip")
        self.assert_error(good().replace('binaries = ["bin/alpha"]', "binaries = []"), "binaries")
        self.assert_error(good().replace('binaries = ["bin/alpha"]', 'binaries = ["../alpha"]'), "binaries")
        self.assert_error(good().replace('binaries = ["bin/alpha"]', 'binaries = ["/bin/alpha"]'), "binaries")
        self.assert_error(good(alpha_extra="max_bytes = 0\n"), "max_bytes")
        self.assert_error(good(alpha_extra='max_bytes = "1"\n'), "max_bytes")

    def test_binary_format_rules(self):
        text = good().replace('format = "tar.gz"', 'format = "binary"')
        self.assert_error(text, "binary")

    def test_unknown_source_and_names(self):
        self.assert_error(good().replace('source = "download"', 'source = "curl"'), "source")
        self.assert_error(good() + '\n[tools."Bad Name"]\nversion = "1"\nprofiles = ["gate"]\nsource = "apt"\npackage = "x"\nversion_min = "1"\nsmoke = ["x"]\n', "Bad Name")

    def test_apt_fields(self):
        self.assert_error(good().replace('version_min = "1:2.0"\n', ""), "version_min")
        self.assert_error(good().replace('package = "git"\n', ""), "package")
        self.assert_error(good().replace('package = "git"', 'package = "git; id"'), "package")

    def test_pip_lockfile_rules(self):
        self.assert_error(good(extra_tools=PIP), "lockfile", lock=None)
        self.assert_error(good(extra_tools=PIP.replace("toolchain/python/ci.lock", "../outside.lock")), "lockfile")
        self.assert_error(good(extra_tools=PIP), "pinned", lock="yamllint>=1.0 \\\n    --hash=sha256:" + GOOD + "\n")
        self.assert_error(good(extra_tools=PIP), "hash", lock="yamllint==1.0\n")
        self.assert_error(good(extra_tools=PIP), "hash", lock="yamllint==1.0 \\\n    --hash=sha256:abc\n")
        self.assert_error(good(extra_tools=PIP), "lockfile", lock="-e git+https://example.invalid/x#egg=x\n")
        self.assert_error(good(extra_tools=PIP), "lockfile", lock="https://example.invalid/x.whl\n")

    def test_toml_syntax_error(self):
        self.assert_error("this is = = not toml", "TOML")

    def test_errors_do_not_echo_values(self):
        marker = "SECRET-MARKER-VALUE"
        errors = check(good(amd64_sha=marker))
        self.assertFalse(any(marker in e for e in errors))


if __name__ == "__main__":
    unittest.main()
