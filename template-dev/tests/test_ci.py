"""T-3 (gate part): the CI matrix, the `not run` summary, and the private-flag condition of `.github/workflows/ci.yml`.

Text-level checks of the workflow (the standard library has no YAML parser; the gate check workflow-policy checks the same
rules on a parsed form), the planner `scripts/lib/ci_matrix.py` on fixture configurations, and the commands of the workflow's
own steps, cut out of it and run with the values GitHub would give them. The aggregate follows the structure of the required
check `quality-gate` (it needs every other job, always runs, and fails unless each needed job succeeded).
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
PLANNER = ROOT / "scripts" / "lib" / "ci_matrix.py"
MANIFEST = ROOT / "toolchain" / "ci-manifest.toml"
EXPECTED_JOBS = ["plan", "gate", "quality-gate"]


def jobs(text):
    """Job id -> its lines (the body at deeper indentation than the id), in file order."""
    result, current, in_jobs = {}, None, False
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line and not line.startswith(" ") and not line.startswith("#"):
            break
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            current = match.group(1)
            result[current] = []
        elif current:
            result[current].append(line)
    return result


def job_keys(body):
    return {m.group(1): m.group(2) for line in body if (m := re.match(r"^    ([a-z-]+):\s*(.*)$", line))}


def step(body, name):
    """(keys, script) of the step called NAME: the `if:` and `env:` lines and the dedented `run: |` block."""
    for index, line in enumerate(body):
        if line.strip() == f"- name: {name}":
            break
    else:
        raise AssertionError(f"no step called {name}")
    end = next((i for i in range(index + 1, len(body)) if re.match(r"^      - ", body[i])), len(body))
    chunk = body[index:end]
    run = next(i for i, line in enumerate(chunk) if line.strip() in ("run: |",) or line.strip().startswith("run: "))
    script = []
    if chunk[run].strip() == "run: |":
        for line in chunk[run + 1:]:
            if line.strip() and not line.startswith(" " * 10):
                break
            script.append(line[10:])
    else:
        script.append(chunk[run].strip()[len("run: "):])
    keys = {m.group(1): m.group(2) for line in chunk if (m := re.match(r"^        ([a-z]+):\s*(.*)$", line))}
    return keys, "\n".join(script).rstrip() + "\n"


def run_planner(config, private, manifest=MANIFEST, output=None):
    env = {"PATH": os.environ["PATH"]}
    if output:
        env["GITHUB_OUTPUT"] = str(output)
    return subprocess.run([sys.executable, "-I", "-B", str(PLANNER), "plan", "--manifest", str(manifest), "--private", private],
                          input=json.dumps(config), capture_output=True, text=True, env=env, check=False)


def arch(name, runner, private):
    return {"name": name, "runner": runner, "runner_private": private}


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = CI.read_text()
        self.jobs = jobs(self.text)

    def test_the_jobs_of_the_file(self):
        self.assertEqual(sorted(self.jobs), sorted(EXPECTED_JOBS))

    def test_the_matrix_job_is_named_per_architecture(self):
        keys = job_keys(self.jobs["gate"])
        self.assertEqual(keys["name"], "quality-gate-${{ matrix.arch }}")
        self.assertEqual(keys["runs-on"], "${{ matrix.runner }}")
        self.assertIn("matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}", "\n".join(self.jobs["gate"]))
        self.assertIn("fail-fast: false", "\n".join(self.jobs["gate"]))

    def test_the_condition_reads_the_private_flag_of_the_repository(self):
        plan = "\n".join(self.jobs["plan"])
        self.assertIn("PRIVATE: ${{ github.event.repository.private }}", plan)
        self.assertEqual(self.text.count("${{ github.event.repository.private }}"), 1)
        self.assertNotRegex(self.text, r"visibility|vars\.|inputs\.")
        keys, script = step(self.jobs["plan"], "Plan the matrix")
        self.assertNotIn("${{", script)
        self.assertIn('--private "$PRIVATE"', script)

    def test_the_aggregate_needs_every_other_job_and_always_runs(self):
        keys = job_keys(self.jobs["quality-gate"])
        needs = sorted(item.strip() for item in keys["needs"].strip("[]").split(","))
        self.assertEqual(needs, sorted(job for job in self.jobs if job != "quality-gate"))
        self.assertEqual(keys["if"], "always()")
        self.assertEqual(keys["permissions"], "{}")
        self.assertNotIn("uses:", "\n".join(self.jobs["quality-gate"]))

    def test_no_job_is_skipped_or_tolerated(self):
        for job_id, body in self.jobs.items():
            if job_id != "quality-gate":
                self.assertNotIn("if", job_keys(body), job_id)
            self.assertNotIn("continue-on-error", job_keys(body), job_id)
        for banned in ("paths:", "paths-ignore:", "pull_request_target", "workflow_run", "secrets."):
            self.assertNotIn(banned, self.text)

    def test_the_job_names_of_the_required_checks_are_defined_once(self):
        self.assertEqual(len(re.findall(r"^  quality-gate:\s*$", self.text, re.M)), 1)
        self.assertNotIn("name: quality-gate\n", self.text)

    def test_a_leg_without_a_runner_does_not_run_the_gate(self):
        body = self.jobs["gate"]
        for name in ("Install the gate tools", "Quality gate"):
            self.assertEqual(step(body, name)[0]["if"], "matrix.run == 'true'")
        self.assertEqual(step(body, "Not run")[0]["if"], "matrix.run == 'false'")

    def test_the_workflow_names_no_architecture_and_invokes_no_assistant(self):
        self.assertNotRegex(self.text, r"(?i)amd64|arm64|aarch64|x86[_-]64|ubuntu-\d|claude|anthropic")


class BaseCommit(unittest.TestCase):
    """The plan and the gate read the repository's policy from the base commit of a pull request, never from its head.

    The steps' own commands run in a throw-away repository: a stand-in `yq` that passes JSON through, a stand-in gate that
    prints the base reference it was given, and the real planner and CI manifest.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name) / "repo"
        (self.repo / "scripts" / "lib").mkdir(parents=True)
        (self.repo / "toolchain").mkdir()
        (self.repo / "config").mkdir()
        (self.repo / "scripts" / "lib" / "ci_matrix.py").write_bytes(PLANNER.read_bytes())
        (self.repo / "toolchain" / "ci-manifest.toml").write_bytes(MANIFEST.read_bytes())
        gate = self.repo / "scripts" / "quality-gate.sh"
        gate.write_text('#!/usr/bin/env bash\necho "base=${GATE_BASE_REF:-unset}"\n')
        gate.chmod(0o755)
        self.bin = pathlib.Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        yq = self.bin / "yq"
        yq.write_text('#!/usr/bin/env bash\n# stand-in: the configuration of these tests is JSON, which is YAML\nif [[ ${!#} == - ]]; then cat; else cat "${!#}"; fi\n')
        yq.chmod(0o755)
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        env = {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_AUTHOR_NAME": "t",
               "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        return subprocess.run(["git", *args], cwd=self.repo, env=env, capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, config):
        if config is None:
            (self.repo / "config" / "project.yaml").unlink(missing_ok=True)
        else:
            (self.repo / "config" / "project.yaml").write_text(json.dumps(config))
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "c")
        return self.git("rev-parse", "HEAD")

    def run_step(self, job, name, env):
        _, script = step(jobs(CI.read_text())[job], name)
        full = {"PATH": f"{self.bin}:{os.environ['PATH']}", "RUNNER_TEMP": self.tmp.name, "GIT_CONFIG_GLOBAL": "/dev/null", **env}
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script], cwd=self.repo, env=full,
                              capture_output=True, text=True, check=False)

    PUBLIC = {"architectures": [arch("amd64", "runner-a", "runner-a"), arch("arm64", "runner-b", "")]}

    def test_both_checkouts_fetch_the_base_commit(self):
        for job in ("plan", "gate"):
            body = "\n".join(jobs(CI.read_text())[job])
            self.assertIn("fetch-depth: 0", body, job)
        text = CI.read_text()
        self.assertEqual(text.count("BASE_SHA: ${{ github.event.pull_request.base.sha }}"), 2)

    def test_the_gate_gets_the_base_reference_of_a_pull_request(self):
        base = self.commit(self.PUBLIC)
        head = self.commit({"architectures": [arch("amd64", "widened", "")]})
        done = self.run_step("gate", "Quality gate", {"BASE_SHA": base})
        self.assertEqual(done.stdout.strip(), f"base={base}", done.stderr)
        self.assertNotEqual(base, head)

    def test_the_gate_reads_the_working_tree_on_a_push_and_before_a_configuration_exists(self):
        base = self.commit(None)
        self.commit(self.PUBLIC)
        self.assertEqual(self.run_step("gate", "Quality gate", {"BASE_SHA": base}).stdout.strip(), "base=unset")
        self.assertEqual(self.run_step("gate", "Quality gate", {"BASE_SHA": ""}).stdout.strip(), "base=unset")

    def plan(self, base_sha):
        env = {"PRIVATE": "false", "BASE_SHA": base_sha, "GITHUB_OUTPUT": str(pathlib.Path(self.tmp.name) / "out")}
        done = self.run_step("plan", "Plan the matrix", env)
        return done.returncode, [e["run"] for e in json.loads(done.stdout)["include"]] if done.returncode == 0 else done.stderr

    def test_a_head_that_blanks_a_runner_does_not_change_the_plan_of_its_own_pull_request(self):
        base = self.commit(self.PUBLIC)
        self.commit({"architectures": [arch("amd64", "runner-a", "runner-a"), arch("arm64", "", "")]})
        self.assertEqual(self.plan(base), (0, ["true", "true"]))

    def test_a_push_plans_from_the_working_tree(self):
        self.commit(self.PUBLIC)
        self.commit({"architectures": [arch("amd64", "runner-a", "runner-a"), arch("arm64", "", "")]})
        self.assertEqual(self.plan(""), (0, ["true", "false"]))

    def test_without_a_configuration_at_the_base_the_head_is_used(self):
        base = self.commit(None)
        self.commit(self.PUBLIC)
        self.assertEqual(self.plan(base), (0, ["true", "true"]))


class Planner(unittest.TestCase):
    TWO = {"architectures": [arch("amd64", "ubuntu-24.04", "ubuntu-24.04"), arch("arm64", "ubuntu-24.04-arm", "")]}

    def plan(self, config, private="false", **kw):
        done = run_planner(config, private, **kw)
        return done.returncode, (json.loads(done.stdout) if done.returncode == 0 else None), done.stderr

    def test_a_public_repository_runs_every_architecture_on_its_runner(self):
        code, matrix, _ = self.plan(self.TWO, "false")
        self.assertEqual(code, 0)
        self.assertEqual(matrix["include"], [
            {"arch": "amd64", "runner": "ubuntu-24.04", "run": "true"},
            {"arch": "arm64", "runner": "ubuntu-24.04-arm", "run": "true"}])

    def test_a_private_repository_has_no_runner_for_an_empty_private_label(self):
        code, matrix, _ = self.plan(self.TWO, "true")
        self.assertEqual(code, 0)
        self.assertEqual(matrix["include"][0], {"arch": "amd64", "runner": "ubuntu-24.04", "run": "true"})
        self.assertEqual(matrix["include"][1], {"arch": "arm64", "runner": "ubuntu-latest", "run": "false"})

    def test_the_flag_decides_and_the_configuration_does_not_carry_it(self):
        flipped = {"architectures": [arch("amd64", "", "ubuntu-24.04")]}
        self.assertEqual(self.plan(flipped, "false")[1]["include"][0]["run"], "false")
        self.assertEqual(self.plan(flipped, "true")[1]["include"][0]["run"], "true")
        with_key = dict(self.TWO, visibility="private", private=True)
        self.assertEqual(self.plan(with_key, "false")[1], self.plan(self.TWO, "false")[1])

    def test_an_unreadable_flag_fails_closed(self):
        for value in ("", "True", "1", "yes", "null"):
            with self.subTest(value=value):
                self.assertEqual(self.plan(self.TWO, value)[0], 1)

    def test_a_malformed_configuration_fails_and_names_the_problem(self):
        cases = {
            "no key": {},
            "not a mapping": [],
            "empty list": {"architectures": []},
            "not a list": {"architectures": "amd64"},
            "entry not a table": {"architectures": ["amd64"]},
            "no name": {"architectures": [{"runner": "x", "runner_private": "x"}]},
            "bad name": {"architectures": [arch("Bad Name", "x", "x")]},
            "duplicate": {"architectures": [arch("amd64", "x", "x"), arch("amd64", "y", "y")]},
            "undeclared architecture": {"architectures": [arch("riscv", "x", "x")]},
            "runner not a string": {"architectures": [{"name": "amd64", "runner": ["x"], "runner_private": ""}]},
            "runner missing": {"architectures": [{"name": "amd64", "runner_private": ""}]},
            "bad label": {"architectures": [arch("amd64", "a b", "")]},
            "shell in label": {"architectures": [arch("amd64", "x;y", "")]},
        }
        for name, config in cases.items():
            with self.subTest(name):
                code, _, err = self.plan(config)
                self.assertEqual(code, 1)
                self.assertTrue(err.startswith("ci_matrix: "), err)

    def test_a_self_hosted_label_is_refused_in_any_case(self):
        for label in ("self-hosted", "Self-Hosted", "SELF-HOSTED"):
            for key in ("runner", "runner_private"):
                with self.subTest(label=label, key=key):
                    entry = {"name": "amd64", "runner": "ubuntu-24.04", "runner_private": "ubuntu-24.04", key: label}
                    code, _, err = self.plan({"architectures": [entry]}, "true" if key == "runner_private" else "false")
                    self.assertEqual(code, 1)
                    self.assertIn("self-hosted", err)

    def test_the_unselected_label_is_judged_too(self):
        entry = {"name": "amd64", "runner": "ubuntu-24.04", "runner_private": "self-hosted"}
        self.assertEqual(self.plan({"architectures": [entry]}, "false")[0], 1)

    def test_a_label_outside_runner_labels_is_refused_when_the_key_is_present(self):
        config = {"runner_labels": ["ubuntu-24.04"], "architectures": [arch("amd64", "ubuntu-24.04", ""), arch("arm64", "other-runner", "")]}
        code, _, err = self.plan(config)
        self.assertEqual(code, 1)
        self.assertIn("runner_labels", err)
        config["architectures"][1]["runner"] = ""
        self.assertEqual(self.plan(config)[0], 0)
        self.assertEqual(self.plan(dict(config, runner_labels="ubuntu-24.04"))[0], 1)
        self.assertEqual(self.plan(dict(config, runner_labels=["ubuntu-24.04", 3]))[0], 1)
        self.assertEqual(self.plan(dict(config, runner_labels=["self-hosted"]))[0], 1)

    def test_the_error_names_no_value(self):
        secret = "hunter2-label"
        _, _, err = self.plan({"architectures": [arch("amd64", "bad label " + secret, "")]})
        self.assertNotIn(secret, err)

    def test_a_bad_json_document_or_manifest_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "m.toml"
            bad.write_text("schema_version = 1\n")
            self.assertEqual(self.plan(self.TWO, manifest=bad)[0], 1)
            self.assertEqual(self.plan(self.TWO, manifest=pathlib.Path(tmp) / "missing.toml")[0], 1)
        done = subprocess.run([sys.executable, "-I", "-B", str(PLANNER), "plan", "--manifest", str(MANIFEST), "--private", "false"],
                              input="{", capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 1)

    def test_architectures_that_the_manifest_does_not_declare_are_refused_with_the_reason(self):
        """A generated repository whose configuration names another architecture needs artifacts for it in the CI manifest."""
        code, _, err = self.plan({"architectures": [arch("arch-a", "runner-a", "runner-a")]})
        self.assertEqual(code, 1)
        self.assertIn("the CI manifest has no artifacts for this architecture", err)

    def test_one_architecture_configuration_is_planned(self):
        code, matrix, _ = self.plan({"architectures": [arch("amd64", "ubuntu-24.04", "")]}, "true")
        self.assertEqual((code, matrix["include"][0]["run"]), (0, "false"))

    def test_the_output_is_written_for_the_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "output"
            code, matrix, _ = self.plan(self.TWO, "false", output=out)
            self.assertEqual(code, 0)
            line = out.read_text().strip()
            self.assertTrue(line.startswith("matrix="))
            self.assertEqual(json.loads(line[len("matrix="):]), matrix)

    def test_this_repository_configuration_is_plannable_for_both_visibilities(self):
        config = subprocess.run(["python3", "-I", "-c", "import tomllib"], check=False).returncode  # tomllib is present
        self.assertEqual(config, 0)
        text = (ROOT / "config" / "project.yaml").read_text()
        names = re.findall(r'^  - name: "([^"]+)"', text, re.M)
        self.assertEqual(len(names), len(set(names)))
        runners = re.findall(r'^    runner: "([^"]*)"', text, re.M)
        private = re.findall(r'^    runner_private: "([^"]*)"', text, re.M)
        config = {"architectures": [arch(n, r, p) for n, r, p in zip(names, runners, private)]}
        for flag in ("true", "false"):
            self.assertEqual(self.plan(config, flag)[0], 0)


class Generated(unittest.TestCase):
    """A repository generated from either fixture configuration can plan its CI matrix."""

    def test_generated_trees_plan_for_both_visibilities(self):
        import helpers
        for name, config in helpers.CONFIGS.items():
            with self.subTest(config=name), tempfile.TemporaryDirectory() as tmp:
                tree = helpers.export_template(pathlib.Path(tmp) / "tree")
                done = helpers.run_init(tree, config)
                self.assertEqual(done.returncode, 0, done.stderr)
                text = (tree / "config" / "project.yaml").read_text()
                names = re.findall(r'^  - name: "([^"]+)"', text, re.M)
                runners = re.findall(r'^    runner: "([^"]*)"', text, re.M)
                private = re.findall(r'^    runner_private: "([^"]*)"', text, re.M)
                self.assertTrue(names)
                entries = {"architectures": [arch(n, r, p) for n, r, p in zip(names, runners, private)]}
                for flag in ("true", "false"):
                    done = run_planner(entries, flag, manifest=tree / "toolchain" / "ci-manifest.toml")
                    self.assertEqual(done.returncode, 0, done.stderr)


class Commands(unittest.TestCase):
    """The commands of the workflow's own steps, run with the values GitHub would give them."""

    def run_script(self, script, env, cwd):
        full = {"PATH": os.environ["PATH"], **env}
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script], env=full, capture_output=True,
                              text=True, check=False, cwd=cwd)

    def test_not_run_writes_the_summary(self):
        _, script = step(jobs(CI.read_text())["gate"], "Not run")
        with tempfile.TemporaryDirectory() as tmp:
            summary = pathlib.Path(tmp) / "summary.md"
            done = self.run_script(script, {"ARCH": "arch-z", "GITHUB_STEP_SUMMARY": str(summary)}, tmp)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("not run: arch-z", done.stdout)
            self.assertEqual(summary.read_text().strip(), "not run: arch-z")

    def aggregate(self, results, plan=None):
        needs = {name: {"result": result, "outputs": {}} for name, result in results.items()}
        if plan is not None and "plan" in needs:
            needs["plan"]["outputs"] = {"matrix": json.dumps(plan)}
        _, script = step(jobs(CI.read_text())["quality-gate"], "Fail unless every needed job succeeded")
        with tempfile.TemporaryDirectory() as tmp:
            summary = pathlib.Path(tmp) / "summary.md"
            done = self.run_script(script, {"NEEDS": json.dumps(needs), "GITHUB_STEP_SUMMARY": str(summary)}, tmp)
            return done.returncode, done.stdout + done.stderr, summary.read_text() if summary.exists() else ""

    GREEN = {"plan": "success", "gate": "success"}
    PLAN = {"include": [{"arch": "arch-a", "runner": "x", "run": "true"}, {"arch": "arch-b", "runner": "ubuntu-latest", "run": "false"}]}

    def test_all_green_passes_and_lists_the_architectures_not_run(self):
        code, out, summary = self.aggregate(self.GREEN, self.PLAN)
        self.assertEqual(code, 0, out)
        self.assertIn("not run: arch-b", out)
        self.assertIn("not run: arch-b", summary)
        self.assertNotIn("not run: arch-a", out)
        self.assertIn("| gate | success |", summary)

    def test_all_green_with_every_architecture_run_lists_none(self):
        plan = {"include": [{"arch": "arch-a", "runner": "x", "run": "true"}]}
        code, out, summary = self.aggregate(self.GREEN, plan)
        self.assertEqual(code, 0, out)
        self.assertNotIn("not run", out + summary)

    def test_one_failure_fails_and_is_named(self):
        for result in ("failure", "cancelled", "skipped"):
            for name in self.GREEN:
                with self.subTest(result=result, job=name):
                    results = dict(self.GREEN, **{name: result})
                    code, out, summary = self.aggregate(results, self.PLAN)
                    self.assertEqual(code, 1, out)
                    self.assertIn(name, out)
                    self.assertIn(result, summary)

    def test_an_unreadable_plan_does_not_hide_a_result(self):
        for plan in (None, "{", "[]"):
            with self.subTest(plan=plan):
                needs = {"plan": {"result": "success", "outputs": {"matrix": plan}}, "gate": {"result": "failure", "outputs": {}}}
                _, script = step(jobs(CI.read_text())["quality-gate"], "Fail unless every needed job succeeded")
                with tempfile.TemporaryDirectory() as tmp:
                    done = self.run_script(script, {"NEEDS": json.dumps(needs)}, tmp)
                    self.assertEqual(done.returncode, 1, done.stdout)

    def test_an_unknown_result_or_an_empty_or_malformed_context_fails(self):
        self.assertEqual(self.aggregate(dict(self.GREEN, gate=""))[0], 1)
        self.assertEqual(self.aggregate({})[0], 1)
        _, script = step(jobs(CI.read_text())["quality-gate"], "Fail unless every needed job succeeded")
        for needs in ("not json", "[]", "null", ""):
            with self.subTest(needs=needs), tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(self.run_script(script, {"NEEDS": needs}, tmp).returncode, 1)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_script(script, {}, tmp).returncode, 1)


if __name__ == "__main__":
    unittest.main()
