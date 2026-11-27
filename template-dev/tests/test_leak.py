"""`leak` test: T-15 (identifier patterns) and the scan part of T-21 (platform references).

The payload is every file not classed `template-only`; the generated tree is what
`initialize-project` leaves. Fixtures that need an identifier-shaped value are built from
fragments here, so no such value is committed anywhere in the repository.
"""

import unittest
from pathlib import Path

import helpers
import leak
import template_manifest as tm
from helpers import ROOT, TempDirTestCase

PATTERNS = ROOT / "template-dev" / "leak" / "patterns.toml"

# Files that carry configuration data by design and may therefore hold architecture names
# and runner labels: the configuration, and the CI manifest, which declares the architectures
# it has artifacts for (the install engine itself names none).
ARCH_DATA_FILES = frozenset({"config/project.yaml", "toolchain/ci-manifest.toml"})

# The template's own seeded configuration lists the repository that holds its design records under `design_repos` (rule PC-3
# needs it to accept the template's own implementation PRs). That one pattern is skipped for that one file, in the payload scan
# only: a generated tree is written from the configuration it was given and gets no exemption.
PAYLOAD_NAME_EXEMPT = {"config/project.yaml": frozenset({"platform-repository-name"})}


def dotted(*parts):
    return ".".join(parts)


# One failing sample per pattern, built from fragments.
SAMPLES = {
    "ocid": "ocid1" + dotted("", "tenancy", "oc1", "", "a" * 20),
    "ipv4": "host " + dotted("198", "51", "100", "7"),
    "ipv6": "addr " + ":".join(["2001", "db8", "0", "0", "0", "0", "0", "1"]),
    "email": "mail " + "someone" + "@" + "example" + ".org",
    "private-key": "-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----",
    "ssh-public-key": "ssh-" + "ed25519 " + "AAAA" + "C3Nza" * 6,
    "fingerprint": ":".join(["ab"] * 16),
    "github-token": "gh" + "p_" + "A1" * 20,
    "cloud-or-chat-token": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "credential-assignment": "to" + "ken: " + "Abc1" * 6,
    "platform-repository-name": "see ai-lab-" + "infrastructure for details",
    "platform-record-number": "defined in " + "ADR" + "-008",
    "oci": "runs on " + "O" + "CI",
    "platform-host": "the lab" + " VM",
    "fixed-architecture": "built for " + "arm" + "64",
    "fixed-runner-label": "runs-on: " + "ubuntu" + "-24.04",
    "token-placeholder": "name: " + "@@" + "PROJECT_NAME" + "@@",
    "brace-placeholder": "name: " + "{{" + " project.name " + "}}",
}


def payload_files(manifest, paths):
    return [p for p in paths if "template-only" not in manifest.classify(p)]


class PatternTests(TempDirTestCase):
    def setUp(self):
        self.patterns = leak.load_patterns(PATTERNS)

    def scan_text(self, text, scope="both", rel="file.txt", arch_exempt=frozenset()):
        root = self.make_dir()
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text + "\n")
        return leak.scan(root, [rel], self.patterns, scope=scope, arch_exempt=arch_exempt)

    def test_every_pattern_has_a_failing_fixture(self):
        self.assertEqual(sorted(SAMPLES), sorted(p.name for p in self.patterns))

    def test_each_sample_is_found_by_its_own_pattern(self):
        for name, sample in SAMPLES.items():
            with self.subTest(pattern=name):
                scope = "generated" if name.endswith("placeholder") else "both"
                findings = self.scan_text(sample, scope=scope)
                self.assertIn(name, [f.pattern for f in findings])

    def test_findings_name_the_location_and_never_the_match(self):
        findings = self.scan_text("line one\n" + SAMPLES["email"], rel="sub/f.txt")
        self.assertEqual([(f.path, f.line) for f in findings], [("sub/f.txt", 2)])
        for finding in findings:
            self.assertNotIn("someone", str(finding))
            self.assertNotIn("example.org", str(finding))

    def test_platform_names_are_found_in_both_forms(self):
        for tail in ("infrastructure", "platform"):
            with self.subTest(tail=tail):
                findings = self.scan_text("see ai-lab-" + tail)
                self.assertIn("platform-repository-name", [f.pattern for f in findings])

    def test_clean_text_has_no_findings(self):
        text = "Use ${{ github.event.repository.private }} and docs/cr/CR-<n>/ and ADR-<n>.\n" \
               "Versions 1.2.3 and " + ".".join(["10", "20", "30", "40", "50"]) + " are not addresses; arch-a is a neutral name."
        self.assertEqual(self.scan_text(text, scope="generated"), [])

    def test_ipv6_needs_eight_groups_or_a_compressed_form(self):
        compressed = "addr " + "2001:db8" + "::" + "1"
        self.assertIn("ipv6", [f.pattern for f in self.scan_text(compressed)])
        for text in ("mac " + ":".join(["de", "ad", "be", "ef", "00", "01"]),
                     "list 12:30:45:10:20:30", "at 12:30:45 and 08:15:00",
                     "path a::b is not an address"):
            with self.subTest(text=text):
                self.assertEqual(self.scan_text(text), [])

    def test_placeholder_patterns_apply_to_generated_trees_only(self):
        sample = SAMPLES["token-placeholder"]
        self.assertEqual(self.scan_text(sample, scope="both"), [])
        self.assertTrue(self.scan_text(sample, scope="generated"))

    def test_architecture_names_are_allowed_only_in_data_files(self):
        sample = SAMPLES["fixed-architecture"] + " " + SAMPLES["fixed-runner-label"]
        self.assertEqual(
            self.scan_text(sample, rel="config/project.yaml", arch_exempt=ARCH_DATA_FILES), []
        )
        self.assertTrue(self.scan_text(sample, rel="docs/x.md", arch_exempt=ARCH_DATA_FILES))
        # identifier patterns still apply inside a data file
        again = self.scan_text(SAMPLES["email"], rel="config/project.yaml", arch_exempt=ARCH_DATA_FILES)
        self.assertEqual([f.pattern for f in again], ["email"])

    def test_a_name_exemption_covers_one_pattern_in_one_file(self):
        exempt = {"config/project.yaml": frozenset({"platform-repository-name"})}
        name = SAMPLES["platform-repository-name"]
        self.assertEqual(self.scan_text(name, rel="config/project.yaml"), self.scan_text(name, rel="config/project.yaml")[:1])
        self.assertEqual(self.scan_text_with(name, "config/project.yaml", exempt), [])
        self.assertTrue(self.scan_text_with(name, "docs/x.md", exempt))
        # another pattern still applies in the exempt file
        again = self.scan_text_with(SAMPLES["platform-record-number"], "config/project.yaml", exempt)
        self.assertEqual([f.pattern for f in again], ["platform-record-number"])
        # and a generated tree is scanned without any exemption
        self.assertTrue(self.scan_text_with(name, "config/project.yaml", None, scope="generated"))

    def scan_text_with(self, text, rel, name_exempt, scope="both"):
        root = self.make_dir()
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text + "\n")
        return leak.scan(root, [rel], self.patterns, scope=scope, name_exempt=name_exempt)

    def test_binary_files_are_skipped(self):
        root = self.make_dir()
        (root / "blob.bin").write_bytes(b"\0\1\2" + SAMPLES["email"].encode())
        self.assertEqual(leak.scan(root, ["blob.bin"], self.patterns, scope="both"), [])

    def test_malformed_pattern_files_are_rejected(self):
        root = self.make_dir()
        cases = {
            "bad regex": '[[pattern]]\nname="a"\ngroup="platform"\nscope="both"\nregex="("\n',
            "bad scope": '[[pattern]]\nname="a"\ngroup="platform"\nscope="x"\nregex="a"\n',
            "bad group": '[[pattern]]\nname="a"\ngroup="x"\nscope="both"\nregex="a"\n',
            "duplicate": '[[pattern]]\nname="a"\ngroup="platform"\nscope="both"\nregex="a"\n' * 2,
            "empty": "",
        }
        for name, text in cases.items():
            with self.subTest(name):
                path = root / "p.toml"
                path.write_text(text)
                with self.assertRaises(leak.PatternError):
                    leak.load_patterns(path)


class PayloadAndGeneratedTreeTests(TempDirTestCase):
    def setUp(self):
        self.patterns = leak.load_patterns(PATTERNS)

    def test_payload_has_no_identifier_and_no_platform_reference(self):
        manifest = tm.load_manifest(ROOT / ".template" / "manifest.toml")
        files = payload_files(manifest, helpers.tracked_paths())
        self.assertGreater(len(files), 3)
        findings = leak.scan(ROOT, files, self.patterns, scope="both", arch_exempt=ARCH_DATA_FILES,
                             name_exempt=PAYLOAD_NAME_EXEMPT)
        self.assertEqual([str(f) for f in findings], [])

    def test_template_only_files_are_not_scanned(self):
        manifest = tm.load_manifest(ROOT / ".template" / "manifest.toml")
        files = payload_files(manifest, helpers.tracked_paths())
        self.assertFalse([f for f in files if f.startswith("template-dev/")])

    def test_generated_trees_are_clean_for_both_configurations(self):
        for name in helpers.CONFIGS:
            with self.subTest(config=name):
                tree, result = self.generate(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                files = tm.walk_files(tree)
                findings = leak.scan(tree, files, self.patterns, scope="generated",
                                     arch_exempt=ARCH_DATA_FILES)
                self.assertEqual([str(f) for f in findings], [])

    def test_a_seeded_identifier_makes_the_scan_fail(self):
        for pattern in ("ocid", "ipv4", "email", "github-token"):
            with self.subTest(pattern=pattern):
                tree, result = self.generate("neutral")
                self.assertEqual(result.returncode, 0, result.stderr)
                with open(tree / "README.md", "a", encoding="utf-8") as handle:
                    handle.write(SAMPLES[pattern] + "\n")
                findings = leak.scan(tree, tm.walk_files(tree), self.patterns,
                                     scope="generated", arch_exempt=ARCH_DATA_FILES)
                self.assertEqual([(f.path, f.pattern) for f in findings], [("README.md", pattern)])

    def test_a_platform_reference_in_the_payload_makes_the_scan_fail(self):
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        (tree / "docs" / "PROJECT.md").write_text(SAMPLES["platform-repository-name"] + "\n")
        findings = leak.scan(tree, tm.walk_files(tree), self.patterns, scope="generated",
                             arch_exempt=ARCH_DATA_FILES)
        self.assertEqual([f.pattern for f in findings], ["platform-repository-name"])

    def test_generated_configuration_values_are_not_reported_as_architectures(self):
        # The generated data file lists the configured architectures; fixture names are neutral,
        # and a real name in that file alone is allowed.
        tree, result = self.generate("neutral")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = tree / "config" / "project.yaml"
        data.write_text(data.read_text() + "# " + SAMPLES["fixed-architecture"] + "\n")
        findings = leak.scan(tree, tm.walk_files(tree), self.patterns, scope="generated",
                             arch_exempt=ARCH_DATA_FILES)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
