"""`dry-run` test: template-dev/dry-run.sh (design section 12 (a), steps 1 to 4 and 8; T-2, T-3, T-14, T-16 (a), T-21).

The script runs as a process, exactly as the `generate` job runs it. A complete run of the gate takes minutes, so these tests run
the script against a copy of the template whose `scripts/quality-gate.sh` is patched to answer at once unless it is asked for one
check (`--only`, which is how step 4 runs `cr-layout`). The patch is a test device in a throwaway copy; a real run of the script
on the real template, with the real gate, is what the PR body reports and what the `generate` job runs.

The workflow tests read `.github/workflows/template-ci.yml` at text level, like test_ci.py does for ci.yml.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers
import test_ci
from helpers import ROOT, CONFIGS

DRY_RUN = ROOT / "template-dev" / "dry-run.sh"
HELPER = ROOT / "template-dev" / "lib" / "dry_run_checks.py"
WORKFLOW = ROOT / ".github" / "workflows" / "template-ci.yml"

LINE = re.compile(r"^dry-run: step=(?P<step>\d+) name=(?P<name>[a-z0-9-]+) result=(?P<result>pass|fail|skip)(?: detail=(?P<detail>.*))?$")
SUMMARY = re.compile(
    r"^dry-run: summary result=(?P<result>pass|fail) steps=(?P<steps>\d+) passed=(?P<passed>\d+) failed=(?P<failed>\d+) "
    r"skipped=(?P<skipped>\d+) config=(?P<config>\S+) arch=(?P<arch>\S+)$"
)
STEPS = [("1", "export"), ("2", "initialize"), ("3", "gate"), ("4", "new-cr"), ("8", "leak")]

# Inserted after `set -euo pipefail` in the copy of the gate: the whole gate answers at once; a run for one check goes on.
GATE_STUB = (
    '\nif [[ -n ${DRY_RUN_TEST_GATE:-} && " $* " != *" --only "* ]]; then\n'
    '  echo "stub gate: $DRY_RUN_TEST_GATE"\n  exit "$DRY_RUN_TEST_GATE"\nfi\n'
)

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "noreply",
    "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "noreply",
}


def git(directory, *args):
    env = {"PATH": os.environ["PATH"], "HOME": "/nonexistent", **GIT_ENV}
    return subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True, text=True, env=env).stdout


def tree_hashes(root, skip=(".git",)):
    out = {}
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in skip:
            continue
        out[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else "dir-or-link"
    return out


def report(stdout):
    """(step lines by number, summary match) of a dry-run report; every line must match the format."""
    steps, summary = {}, None
    for line in stdout.splitlines():
        match = LINE.match(line)
        if match:
            steps[match["step"]] = match
            continue
        summary = SUMMARY.match(line)
        assert summary, f"a line outside the report format: {line!r}"
    return steps, summary


class Template:
    """One committed copy of the template, built once; tests copy it and patch files."""

    root = None

    @classmethod
    def build(cls):
        if cls.root is None:
            tmp = tempfile.mkdtemp(prefix="dry-run-template-")
            cls.root = Path(tmp) / "template"
            helpers.export_template(cls.root)
            gate = cls.root / "scripts" / "quality-gate.sh"
            text = gate.read_text()
            marker = "set -euo pipefail\n"
            assert text.count(marker) == 1
            gate.write_text(text.replace(marker, marker + GATE_STUB, 1))
            git(cls.root, "init", "-q", "-b", "main")
            git(cls.root, "add", "-A")
            git(cls.root, "commit", "-q", "-m", "template")
        return cls.root


def tearDownModule():
    if Template.root is not None:
        shutil.rmtree(Template.root.parent, ignore_errors=True)


class DryRunCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dry-run-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name in ("home", "tmp", "cwd", "out"):
            (self.tmp / name).mkdir()

    def template_copy(self, patch=None):
        """A copy of the committed template; patch(dir) may change files before they are committed again."""
        dest = self.tmp / "template"
        if dest.exists() and patch is None:
            return dest
        shutil.copytree(Template.build(), dest, symlinks=True)
        if patch:
            patch(dest)
            git(dest, "add", "-A")
            git(dest, "commit", "-q", "-m", "patched", "--allow-empty")
        return dest

    def environment(self, gate, extra_env):
        env = {"PATH": os.environ["PATH"], "HOME": str(self.tmp / "home"), "TMPDIR": str(self.tmp / "tmp"), "LANG": "C.UTF-8"}
        if gate is not None:
            env["DRY_RUN_TEST_GATE"] = str(gate)
        env.update(extra_env or {})
        return env

    def run_raw(self, *args, gate=0, extra_env=None):
        """The script with exactly these arguments."""
        return subprocess.run([str(DRY_RUN), *args], capture_output=True, text=True, env=self.environment(gate, extra_env),
                              cwd=self.tmp / "cwd")

    def run_script(self, *, gate=0, template=None, out=None, config="neutral", config_file=None, extra_env=None):
        template = template if template is not None else self.template_copy()
        out = out if out is not None else self.tmp / "out"
        config_file = config_file if config_file is not None else CONFIGS[config]
        return self.run_raw("--template", str(template), "--config", str(config_file), "--out", str(out), gate=gate, extra_env=extra_env)


class Refusals(DryRunCase):
    """The script refuses before it does anything: exit status 2, no report line, and nothing written."""

    def assert_refused(self, done, text):
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertIn(text, done.stderr)

    def test_a_non_empty_out_directory_is_refused_and_left_as_it_was(self):
        (self.tmp / "out" / "keep.txt").write_text("mine\n")
        before = tree_hashes(self.tmp / "out")
        self.assert_refused(self.run_script(), "not empty")
        self.assertEqual(tree_hashes(self.tmp / "out"), before)

    def test_a_hidden_file_makes_the_out_directory_non_empty(self):
        (self.tmp / "out" / ".hidden").write_text("x\n")
        self.assert_refused(self.run_script(), "not empty")

    def test_an_out_directory_that_does_not_exist_is_refused(self):
        self.assert_refused(self.run_script(out=self.tmp / "missing"), "--out")
        self.assertFalse((self.tmp / "missing").exists())

    def test_an_out_path_that_is_a_file_or_a_link_is_refused(self):
        (self.tmp / "file").write_text("x\n")
        self.assert_refused(self.run_script(out=self.tmp / "file"), "--out")
        (self.tmp / "link").symlink_to(self.tmp / "out")
        self.assert_refused(self.run_script(out=self.tmp / "link"), "--out")
        self.assertEqual(os.listdir(self.tmp / "out"), [])

    def test_an_out_directory_inside_the_template_is_refused(self):
        template = self.template_copy()
        inside = template / "scratch"
        inside.mkdir()
        self.assert_refused(self.run_script(template=template, out=inside), "inside")
        self.assertEqual(os.listdir(inside), [])

    def test_a_missing_template_is_refused(self):
        self.assert_refused(self.run_script(template=self.tmp / "nothing"), "--template")
        self.assertEqual(os.listdir(self.tmp / "out"), [])

    def test_a_directory_that_is_not_a_template_repository_is_refused(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assert_refused(self.run_script(template=plain), "--template")
        # a git repository that is not the template (no manifest)
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q")
        self.assert_refused(self.run_script(template=other), "--template")
        self.assertEqual(os.listdir(self.tmp / "out"), [])

    def test_a_missing_configuration_is_refused(self):
        self.assert_refused(self.run_script(config_file=self.tmp / "none.toml"), "--config")
        self.assertEqual(os.listdir(self.tmp / "out"), [])

    def test_usage_errors(self):
        for args in ([], ["--template"], ["--bogus"], ["--template", "a", "--config", "b"],
                     ["--template", "a", "--template", "b", "--config", "c", "--out", "d"], ["positional"]):
            with self.subTest(args=args):
                done = self.run_raw(*args)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertEqual(done.stdout, "")
                self.assertIn("usage", done.stderr.lower())

    def test_help_exits_zero_and_says_where_it_writes(self):
        done = self.run_raw("--help")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--out", done.stdout)
        self.assertIn("writes only inside", done.stdout)


class HappyRun(DryRunCase):
    def run_ok(self, config):
        done = self.run_script(config=config)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def test_both_configurations_pass_with_one_line_per_step_and_a_summary(self):
        for config in CONFIGS:
            with self.subTest(config=config):
                (self.tmp / "out").mkdir(exist_ok=True)
                for child in (self.tmp / "out").iterdir():
                    shutil.rmtree(child)
                done = self.run_ok(config)
                steps, summary = report(done.stdout)
                self.assertEqual([(s, steps[s]["name"]) for s in steps], STEPS)
                self.assertEqual({m["result"] for m in steps.values()}, {"pass"})
                self.assertEqual(summary["result"], "pass")
                self.assertEqual((summary["steps"], summary["passed"], summary["failed"], summary["skipped"]), ("5", "5", "0", "0"))
                self.assertEqual(summary["config"], CONFIGS[config].name)
                self.assertEqual(summary["arch"], subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip())
                self.assertEqual(done.stdout.splitlines()[-1], summary.string)

    def test_the_generated_repository_is_a_committed_git_repository_without_template_only_paths(self):
        self.run_ok("neutral")
        repo = self.tmp / "out" / "repo"
        self.assertEqual(sorted(os.listdir(self.tmp / "out")), ["repo", "work"])
        self.assertEqual(git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")
        self.assertEqual(git(repo, "status", "--porcelain").strip(), "")
        self.assertEqual(len(git(repo, "log", "--format=%H").split()), 1)
        self.assertFalse((repo / "template-dev").exists())
        self.assertFalse((repo / ".github" / "workflows" / "template-ci.yml").exists())
        self.assertTrue((repo / ".template" / "state.toml").is_file())
        # the exported tree has no history of the template
        self.assertNotEqual(git(repo, "log", "--format=%s").strip(), "template")

    def test_the_template_commit_is_recorded_in_the_state_file(self):
        template = self.template_copy()
        self.run_ok_with(template)
        state = (self.tmp / "out" / "repo" / ".template" / "state.toml").read_text()
        self.assertIn(git(template, "rev-parse", "HEAD").strip(), state)

    def run_ok_with(self, template):
        done = self.run_script(template=template)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_sample_change_request_is_made_in_a_copy_and_not_in_the_generated_repository(self):
        self.run_ok("second")
        repo = self.tmp / "out" / "repo"
        self.assertFalse((repo / "docs" / "cr" / "CR-1").exists())
        self.assertTrue((self.tmp / "out" / "work" / "cr-trial" / "docs" / "cr" / "CR-1" / "design.md").is_file())

    def test_a_dirty_template_is_exported_as_it_is_on_disk(self):
        template = self.template_copy()
        (template / "docs" / "PROJECT.md").write_text("# Project\n\nA marker line for the export.\n")
        (template / "template-dev" / "untracked-note.md").write_text("note\n")
        self.run_ok_with(template)
        self.assertIn("A marker line for the export.", (self.tmp / "out" / "repo" / "docs" / "PROJECT.md").read_text())

    def test_a_file_that_the_callers_git_ignores_is_not_exported(self):
        # An untracked file that no class covers would make initialize-project refuse; the caller's own ignore rules apply to the export.
        ignore = self.tmp / "home" / ".config" / "git" / "ignore"
        ignore.parent.mkdir(parents=True)
        ignore.write_text("local-note.txt\n")
        template = self.template_copy()
        (template / "local-note.txt").write_text("mine\n")
        self.run_ok_with(template)
        self.assertFalse((self.tmp / "out" / "repo" / "local-note.txt").exists())

    def test_nothing_is_written_outside_the_out_directory(self):
        template = self.template_copy()
        before = tree_hashes(template)
        self.run_ok_with(template)
        self.assertEqual(tree_hashes(template), before)
        for name in ("home", "tmp", "cwd"):
            self.assertEqual(os.listdir(self.tmp / name), [], name)
        self.assertEqual(git(template, "status", "--porcelain").strip(), "")
        self.assertEqual(list(template.rglob("*.pyc")), [])
        self.assertEqual(list((self.tmp / "out").rglob("*.pyc")), [])

    def test_the_run_needs_no_network_credential_or_github_call(self):
        text = DRY_RUN.read_text() + HELPER.read_text()
        for banned in ("curl", "wget", "gh api", "gh pr", "gh repo", "git push", "git fetch", "git clone", "git remote", "urllib",
                       "socket", "http.client", "GH_TOKEN", "GITHUB_TOKEN"):
            self.assertNotIn(banned, text, banned)

    def test_the_environment_of_the_caller_is_not_printed(self):
        done = self.run_script(extra_env={"DRY_RUN_SECRET_PROBE": "probe-value-1234"})
        self.assertEqual(done.returncode, 0)
        self.assertNotIn("probe-value-1234", done.stdout + done.stderr)


class FailingSteps(DryRunCase):
    def failing(self, template=None, gate=0, config="neutral"):
        done = self.run_script(template=template, gate=gate, config=config)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        steps, summary = report(done.stdout)
        self.assertEqual(summary["result"], "fail")
        return done, steps, summary

    def test_a_failing_gate_fails_the_run_and_names_step_3(self):
        done, steps, summary = self.failing(gate=1)
        self.assertEqual({k: v["result"] for k, v in steps.items()}, {"1": "pass", "2": "pass", "3": "fail", "4": "pass", "8": "pass"})
        self.assertEqual((summary["passed"], summary["failed"], summary["skipped"]), ("4", "1", "0"))
        self.assertIn("quality-gate", steps["3"]["detail"])
        self.assertIn("stub gate", done.stderr)  # the tail of the gate output goes to stderr, not into the report

    def test_a_configuration_that_init_refuses_fails_step_2_and_skips_the_rest(self):
        bad = self.tmp / "bad.toml"
        bad.write_text('schema_version = 1\n[project]\nname = "x"\n')
        done = self.run_script(config_file=bad)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        steps, summary = report(done.stdout)
        self.assertEqual({k: v["result"] for k, v in steps.items()}, {"1": "pass", "2": "fail", "3": "skip", "4": "skip", "8": "skip"})
        self.assertEqual((summary["passed"], summary["failed"], summary["skipped"]), ("1", "1", "3"))
        self.assertIn("initialize-project", done.stderr)

    def test_an_identifier_in_a_seeded_file_fails_the_leak_step_without_printing_it(self):
        value = "someone" + "@" + "example" + ".org"

        def patch(template):
            (template / "docs" / "PROJECT.md").write_text(f"# Project\n\nContact {value} for access.\n")

        done, steps, summary = self.failing(template=self.template_copy(patch))
        self.assertEqual(steps["8"]["result"], "fail")
        self.assertEqual(steps["3"]["result"], "pass")
        self.assertIn("docs/PROJECT.md:3: email", done.stderr)
        self.assertNotIn(value, done.stdout + done.stderr)
        self.assertEqual(summary["failed"], "1")

    def test_a_platform_reference_and_an_unfilled_placeholder_fail_the_leak_step(self):
        for name, text in (("platform", "see ai-lab-" + "infrastructure for details"), ("placeholder", "name: @@" + "PROJECT_NAME" + "@@")):
            with self.subTest(case=name):
                self.setUp()

                def patch(template, text=text):
                    (template / "docs" / "PROJECT.md").write_text(f"# Project\n\n{text}\n")

                done, steps, _ = self.failing(template=self.template_copy(patch))
                self.assertEqual(steps["8"]["result"], "fail")
                self.assertIn("docs/PROJECT.md:3:", done.stderr)

    def test_a_change_request_check_that_accepts_a_broken_request_fails_step_4(self):
        def patch(template):
            (template / "scripts" / "checks" / "cr_layout.py").write_text("import sys\nsys.exit(0)\n")

        done, steps, _ = self.failing(template=self.template_copy(patch))
        self.assertEqual(steps["4"]["result"], "fail")
        self.assertIn("missing", steps["4"]["detail"])

    def test_a_change_request_check_that_rejects_a_good_request_fails_step_4(self):
        def patch(template):
            (template / "scripts" / "checks" / "cr_layout.py").write_text("import sys\nprint('x:1: always')\nsys.exit(1)\n")

        done, steps, _ = self.failing(template=self.template_copy(patch))
        self.assertEqual(steps["4"]["result"], "fail")
        self.assertIn("sample", steps["4"]["detail"])

    def test_a_missing_tool_is_a_failure_and_not_a_pass(self):
        # the gate exits 3 for a missing tool under --strict; only exit 0 passes
        done, steps, _ = self.failing(gate=3)
        self.assertEqual(steps["3"]["result"], "fail")
        self.assertIn("3", steps["3"]["detail"])

    def test_the_report_of_a_failing_run_still_ends_with_the_summary(self):
        done, _, summary = self.failing(gate=1)
        self.assertEqual(done.stdout.splitlines()[-1], summary.string)


class Helper(unittest.TestCase):
    """The checks of steps 1, 2 and 8 as functions of the helper, on small fabricated trees."""

    def run_helper(self, *args):
        env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8"}
        return subprocess.run([sys.executable, "-I", "-B", str(HELPER), *args], capture_output=True, text=True, env=env)

    def make_tree(self, files):
        tmp = Path(tempfile.mkdtemp(prefix="dry-run-helper-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        generated = helpers.export_template(tmp / "tree")
        for rel in files:
            (generated / rel).parent.mkdir(parents=True, exist_ok=True)
            (generated / rel).write_text(files[rel])
        return generated

    def generated(self):
        tree = helpers.export_template(Path(tempfile.mkdtemp(prefix="dry-run-helper-")) / "tree")
        self.addCleanup(shutil.rmtree, tree.parent, ignore_errors=True)
        result = helpers.run_init(tree, CONFIGS["neutral"])
        self.assertEqual(result.returncode, 0, result.stderr)
        return tree

    def test_tree_passes_a_generated_tree(self):
        done = self.run_helper("tree", str(self.generated()))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_tree_fails_on_a_template_only_path(self):
        for rel in ("template-dev/notes.md", ".github/workflows/template-ci.yml"):
            with self.subTest(path=rel):
                tree = self.generated()
                (tree / rel).parent.mkdir(parents=True, exist_ok=True)
                (tree / rel).write_text("x\n")
                done = self.run_helper("tree", str(tree))
                self.assertEqual(done.returncode, 1)
                self.assertIn(rel, done.stdout)

    def test_tree_fails_on_a_change_request_or_adr_instance_and_on_an_unclassed_file(self):
        for rel in ("docs/cr/CR-7/README.md", "docs/adr/ADR-001-x.md", "stray.txt"):
            with self.subTest(path=rel):
                tree = self.generated()
                (tree / rel).parent.mkdir(parents=True, exist_ok=True)
                (tree / rel).write_text("x\n")
                done = self.run_helper("tree", str(tree))
                self.assertEqual(done.returncode, 1)
                self.assertIn(rel, done.stdout)

    def test_leak_passes_the_real_template_and_a_generated_tree(self):
        done = self.run_helper("leak", str(ROOT), str(self.generated()))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_leak_reports_the_group_and_location_never_the_text(self):
        tree = self.generated()
        (tree / "docs" / "PROJECT.md").write_text("x\n" + "see ai-lab-" + "platform\n" + "built for " + "arm" + "64\n")
        done = self.run_helper("leak", str(ROOT), str(tree))
        self.assertEqual(done.returncode, 1)
        self.assertIn("docs/PROJECT.md:2: platform-repository-name", done.stdout)
        self.assertIn("docs/PROJECT.md:3: fixed-architecture", done.stdout)
        self.assertNotIn("ai-lab", done.stdout)

    def test_the_architecture_names_of_the_data_files_are_allowed(self):
        tree = self.generated()
        data = tree / "config" / "project.yaml"
        data.write_text(data.read_text() + "# " + "arm" + "64\n")
        self.assertEqual(self.run_helper("leak", str(ROOT), str(tree)).returncode, 0)

    def test_export_copies_tracked_and_untracked_files_and_not_history_or_ignored_files(self):
        tmp = Path(tempfile.mkdtemp(prefix="dry-run-helper-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        repo, out = tmp / "t", tmp / "o"
        repo.mkdir()
        out.mkdir()
        git(repo, "init", "-q")
        (repo / "a.txt").write_text("a\n")
        (repo / "gone.txt").write_text("g\n")
        (repo / ".gitignore").write_text("ignored.txt\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "x")
        (repo / "gone.txt").unlink()  # tracked, deleted on disk
        (repo / "new.txt").write_text("n\n")  # untracked
        (repo / "ignored.txt").write_text("i\n")
        (repo / "link").symlink_to("a.txt")
        done = self.run_helper("export", str(repo), str(out))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(os.listdir(out)), [".gitignore", "a.txt", "link", "new.txt"])
        self.assertTrue((out / "link").is_symlink())
        self.assertFalse((out / ".git").exists())

    def test_unknown_commands_and_arguments_are_usage_errors(self):
        self.assertEqual(self.run_helper().returncode, 2)
        self.assertEqual(self.run_helper("bogus").returncode, 2)
        self.assertEqual(self.run_helper("tree").returncode, 2)


class StaticChecks(unittest.TestCase):
    def test_the_script_follows_the_bash_rules(self):
        text = DRY_RUN.read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("\nset -euo pipefail\n", text)
        self.assertNotRegex(text, r"\beval\b")
        self.assertNotRegex(text, r"bash -c|sh -c")
        self.assertTrue(os.access(DRY_RUN, os.X_OK))

    def test_the_script_is_template_only(self):
        sys.path.insert(0, str(ROOT / "scripts" / "lib"))
        import template_manifest as tm
        manifest = tm.load_manifest(ROOT / ".template" / "manifest.toml")
        for rel in ("template-dev/dry-run.sh", "template-dev/lib/dry_run_checks.py", "template-dev/docs/release-checklist.md",
                    "template-dev/docs/dry-run.md", "template-dev/tests/test_dry_run.py"):
            self.assertEqual(manifest.classify(rel), ["template-only"], rel)


class Workflow(unittest.TestCase):
    """The jobs `generate`, `sync` and `skills` of template-ci.yml: both architectures, both configurations, and the aggregate."""

    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.jobs = test_ci.jobs(self.text)

    def matrix(self, job):
        return "\n".join(self.jobs[job])

    def labels_of_the_configuration(self):
        text = (ROOT / "config" / "project.yaml").read_text()
        return dict(re.findall(r'- name: "([a-z0-9-]+)"\n    runner: "([a-z0-9.-]+)"', text))

    def test_the_jobs_of_the_file(self):
        self.assertEqual(sorted(self.jobs), sorted(["manifest", "leak", "engine", "ci", "skills", "sync", "generate", "template-ci"]))

    def test_the_aggregate_needs_every_other_job(self):
        keys = test_ci.job_keys(self.jobs["template-ci"])
        needs = sorted(item.strip() for item in keys["needs"].strip("[]").split(","))
        self.assertEqual(needs, sorted(j for j in self.jobs if j != "template-ci"))
        self.assertEqual(keys["if"], "always()")

    def test_generate_runs_both_architectures_and_both_configurations(self):
        body = self.matrix("generate")
        self.assertIn("arch: [amd64, arm64]", body)
        self.assertIn("config: [neutral, second]", body)
        self.assertIn("fail-fast: false", body)
        self.assertEqual(test_ci.job_keys(self.jobs["generate"])["runs-on"], "${{ matrix.runner }}")

    def test_sync_and_skills_run_both_architectures(self):
        for job in ("sync", "skills"):
            with self.subTest(job=job):
                self.assertIn("arch: [amd64, arm64]", self.matrix(job))
                self.assertEqual(test_ci.job_keys(self.jobs[job])["runs-on"], "${{ matrix.runner }}")

    def test_the_runner_labels_are_those_of_the_configuration(self):
        labels = self.labels_of_the_configuration()
        self.assertEqual(sorted(labels), ["amd64", "arm64"])
        for job in ("generate", "sync", "skills"):
            body = self.matrix(job)
            for arch, runner in labels.items():
                self.assertRegex(body, rf"- arch: {arch}\n\s+runner: {re.escape(runner)}\n", f"{job} {arch}")

    def test_every_job_is_pinned_read_only_with_a_timeout_and_no_persisted_credentials(self):
        for job, body in self.jobs.items():
            with self.subTest(job=job):
                keys = test_ci.job_keys(body)
                text = "\n".join(body)
                self.assertIn("timeout-minutes", keys)
                for line in re.findall(r"^\s+- uses:.*$|^\s+uses:.*$", text, re.M):
                    self.assertRegex(line, r"uses:\s*[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
                if job == "template-ci":
                    self.assertEqual(keys["permissions"], "{}")
                else:
                    self.assertIn("contents: read", text)
                    self.assertIn("persist-credentials: false", text)
                self.assertNotRegex(text, r"(?m)^\s+[a-z-]+: write\s*$")

    def test_generate_runs_the_script_with_the_matrix_values_through_the_environment(self):
        body = self.jobs["generate"]
        keys, script = test_ci.step(body, "Dry run")
        self.assertNotIn("${{", script)
        self.assertIn("template-dev/dry-run.sh", script)
        self.assertIn('--config "$CONFIG_FILE"', script)
        self.assertIn('--out "', script)
        self.assertIn("template-dev/fixtures/config/neutral.toml", self.matrix("generate"))
        self.assertIn("template-dev/fixtures/config/second/config.toml", self.matrix("generate"))

    def test_generate_installs_the_gate_tools_from_the_ci_manifest_for_its_architecture(self):
        keys, script = test_ci.step(self.jobs["generate"], "Install the gate tools")
        self.assertIn("python3 -I toolchain/install install --profile gate", script)
        self.assertIn('--arch "$ARCH"', script)

    def test_no_job_is_skipped_or_tolerated_and_no_other_repository_is_fetched(self):
        for job, body in self.jobs.items():
            if job != "template-ci":
                self.assertNotIn("if", test_ci.job_keys(body), job)
            self.assertNotIn("continue-on-error", test_ci.job_keys(body), job)
        for banned in ("pull_request_target", "workflow_run", "secrets.", "git clone", "curl", "wget", "claude", "anthropic"):
            self.assertNotIn(banned, self.text.lower(), banned)
        self.assertNotRegex(self.text, r"(?m)^\s*(?:run:\s*|- run:\s*)?gh\s")


if __name__ == "__main__":
    unittest.main()
