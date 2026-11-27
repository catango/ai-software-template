"""CR-43 S-9: scripts/gate-evidence prints one line of the design section 7 format from the host's own values, and nothing that
identifies the host.

The script runs in a throw-away repository whose quality gate is a stub, so the real gate is never started. A fake `uname` stands
in for another architecture. The line is also fed to the PC-9 parser, so the script and the rule cannot drift apart.
"""
import getpass
import os
import pathlib
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import unittest

import helpers
from pr_contract import rules

SCRIPT = helpers.ROOT / "scripts" / "gate-evidence"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "noreply",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "noreply",
}
STUB = """#!/usr/bin/env bash
# Stub gate: records its arguments, prints host data on both streams, and exits with STUB_RC.
printf '%s\\n' "$*" >"$STUB_ARGS"
printf 'stdout from %s in %s as %s\\n' "$(hostname 2>/dev/null)" "$PWD" "${USER:-}"
printf 'stderr from %s %s\\n' "$(hostname 2>/dev/null)" "$STUB_ADDRESS" >&2
if [[ -n ${STUB_DIRTY:-} ]]; then : >untracked-file; fi
exit "${STUB_RC:-0}"
"""
FAKE_UNAME = """#!/usr/bin/env bash
if [[ ${1:-} == -m ]]; then echo "$FAKE_MACHINE"; else /usr/bin/uname "$@"; fi
"""
# Built from parts so that this file holds no address literal (public-repo-hygiene).
FAKE_ADDRESS = ".".join(["10", "20", "30", "40"])
IPV4 = re.compile(r"\b[0-9]{1,3}(?:\.[0-9]{1,3}){3}\b")


def host_os():
    values = {}
    for line in pathlib.Path("/etc/os-release").read_text().splitlines():
        key, _, value = line.partition("=")
        values[key] = value.strip("\"'")
    return f"{values.get('ID', 'unknown')} {values.get('VERSION_ID', 'unknown')}"


def host_arch_name():
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(os.uname().machine, os.uname().machine)


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "git or bash is not installed")
class GateEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gate-evidence-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = pathlib.Path(self.tmp) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "scripts" / "gate-evidence")
        gate = self.repo / "scripts" / "quality-gate.sh"
        gate.write_text(STUB)
        for path in (gate, self.repo / "scripts" / "gate-evidence"):
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.bin = pathlib.Path(self.tmp) / "bin"
        self.bin.mkdir()
        fake = self.bin / "uname"
        fake.write_text(FAKE_UNAME)
        fake.chmod(0o755)
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")

    def git(self, *args):
        env = {**os.environ, **GIT_ENV}
        return subprocess.run(["git", *args], cwd=self.repo, env=env, capture_output=True, text=True, check=True).stdout.strip()

    def run_script(self, *args, rc=0, machine=None, dirty=False):
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}" if machine else os.environ["PATH"], "HOME": self.tmp, "USER": "someone",
               "STUB_ARGS": str(pathlib.Path(self.tmp) / "args"), "STUB_ADDRESS": FAKE_ADDRESS, "STUB_RC": str(rc), **GIT_ENV}
        if machine:
            env["FAKE_MACHINE"] = machine
        if dirty:
            env["STUB_DIRTY"] = "1"
        return subprocess.run(["bash", str(self.repo / "scripts" / "gate-evidence"), *args], cwd=self.tmp, env=env,
                              capture_output=True, text=True, timeout=60)

    def fields(self, stdout):
        return stdout.rstrip("\n").split(" | ")

    def test_prints_exactly_one_line_in_the_design_format(self):
        result = self.run_script(host_arch_name())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        line = result.stdout.rstrip("\n")
        self.assertTrue(rules.EVIDENCE.fullmatch(line), line)
        fields = self.fields(result.stdout)
        self.assertEqual(len(fields), 8)
        self.assertEqual(fields[0], f"evidence: {host_arch_name()}")
        self.assertEqual(fields[1], os.uname().machine)
        self.assertEqual(fields[2], host_os())
        self.assertEqual(fields[3], self.git("rev-parse", "HEAD"))
        self.assertEqual(fields[4], "clean")
        self.assertEqual(fields[5], helpers.GATE_COMMAND)
        self.assertEqual(fields[6], "exit 0")
        self.assertRegex(fields[7], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

    def test_the_gate_is_run_in_strict_mode_from_the_repository_root(self):
        self.run_script(host_arch_name())
        self.assertEqual((pathlib.Path(self.tmp) / "args").read_text().strip(), "--strict")

    def test_the_line_passes_pc9_for_the_head_it_names(self):
        result = self.run_script(host_arch_name())
        config = {"architectures": [{"name": host_arch_name(), "runner": "", "runner_private": ""}]}
        client = helpers.MemClient(config=config)
        pr = helpers.make_pr(head_sha=self.git("rev-parse", "HEAD"),
                             body=helpers.body("implementation", top=helpers.IMPL_TOP,
                                               sections={"Architecture evidence": result.stdout}))
        client.files[pr.number] = [helpers.fc("scripts/x")]
        found = [v for v in rules.evaluate(pr, client, helpers.TEMPLATES) if v.rule == "PC-9"]
        self.assertEqual(found, [])

    def test_no_host_name_address_user_or_path_in_any_output(self):
        for rc in (0, 1):
            with self.subTest(rc=rc):
                result = self.run_script(host_arch_name(), rc=rc)
                text = result.stdout + result.stderr
                fields = self.fields(result.stdout)
                scrubbed = text
                for allowed in (fields[1], fields[2], fields[5]):
                    scrubbed = scrubbed.replace(allowed, "")
                for value in (socket.gethostname(), getpass.getuser(), "someone", self.tmp, str(self.repo), str(pathlib.Path.home())):
                    if len(value) >= 3:
                        self.assertNotIn(value, scrubbed)
                self.assertIsNone(IPV4.search(scrubbed), scrubbed)
                self.assertNotIn(FAKE_ADDRESS, text)
                self.assertNotIn("stdout from", text)
                self.assertNotIn("stderr from", text)

    def test_a_failing_gate_gives_a_line_with_its_status_and_the_same_status(self):
        result = self.run_script(host_arch_name(), rc=3)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.fields(result.stdout)[6], "exit 3")
        self.assertEqual(result.stdout.count("\n"), 1)

    def test_pc9_rejects_that_failing_line(self):
        result = self.run_script(host_arch_name(), rc=3)
        match = rules.EVIDENCE.fullmatch(result.stdout.rstrip("\n"))
        self.assertTrue(match)
        self.assertNotEqual(int(match.group("code")), 0)

    def test_an_untracked_file_makes_the_line_dirty(self):
        result = self.run_script(host_arch_name(), dirty=True)
        self.assertEqual(self.fields(result.stdout)[4], "dirty")
        (self.repo / "untracked-file").unlink()

    def test_a_modified_tracked_file_makes_the_line_dirty(self):
        with open(self.repo / "scripts" / "quality-gate.sh", "a", encoding="utf-8") as handle:
            handle.write("# edited after the commit\n")
        result = self.run_script(host_arch_name())
        self.assertEqual(self.fields(result.stdout)[4], "dirty")

    def test_arm64_on_an_aarch64_host(self):
        for name in ("arm64", "aarch64"):
            with self.subTest(name=name):
                result = self.run_script(name, machine="aarch64")
                self.assertEqual(result.returncode, 0, result.stderr)
                fields = self.fields(result.stdout)
                self.assertEqual((fields[0], fields[1]), (f"evidence: {name}", "aarch64"))

    def test_an_architecture_that_is_not_this_host_is_refused_and_the_gate_does_not_run(self):
        for name, machine in (("arm64", "x86_64"), ("amd64", "aarch64"), ("riscv64", "x86_64")):
            with self.subTest(name=name, machine=machine):
                result = self.run_script(name, machine=machine)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("does not match this host", result.stderr)
                self.assertFalse((pathlib.Path(self.tmp) / "args").exists())

    def test_an_architecture_without_an_alias_must_equal_uname(self):
        result = self.run_script("riscv64", machine="riscv64")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_usage_errors_exit_2_and_print_nothing_on_stdout(self):
        for args in ((), ("a", "b"), ("ARM64",), ("arm 64",), ("-x",), ("",), ("arm64;id",), ("a" * 40,)):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("usage: scripts/gate-evidence", result.stderr)
                self.assertFalse((pathlib.Path(self.tmp) / "args").exists())

    def test_the_script_writes_nothing_and_is_shellcheck_clean(self):
        before = self.git("status", "--porcelain")
        self.run_script(host_arch_name())
        self.assertEqual(self.git("status", "--porcelain"), before)
        if shutil.which("shellcheck"):
            checked = subprocess.run(["shellcheck", "-x", str(SCRIPT)], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout)

    def test_the_script_has_strict_mode_and_is_executable(self):
        text = SCRIPT.read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env bash"))
        self.assertIn("set -euo pipefail", text)
        self.assertTrue(os.access(SCRIPT, os.X_OK))


if __name__ == "__main__":
    unittest.main()
