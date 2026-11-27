"""T-13: the Skills of design section 6 exist within their budgets, name scripts that exist, and the scripts have an offline mode and a test.

The two Skills of later plan rows (the review-feedback Skill and the template-sync Skill) are listed with their scripts: their
checks run as soon as the files exist and do not fail while they are absent.
"""
import ast
import os
import pathlib
import re
import tomllib
import unittest

import helpers

ROOT = helpers.ROOT
SKILLS = ROOT / ".claude" / "skills"
TESTS = pathlib.Path(__file__).resolve().parent

# Skill -> the script that does its work (design section 6), and whether the script calls GitHub.
DESIGN = {
    "initialize-project": ("scripts/initialize-project", False),
    "prepare-cr-context": ("scripts/cr-context", True),
    "implement-approved-cr": ("scripts/cr-preflight", True),
    "review-implementation": ("scripts/cr-criteria", True),
    "resume-work": ("scripts/pr-checkpoint", True),
    "start-cr": ("scripts/new-cr", False),
}
LATER = {"address-review-feedback": ("scripts/review-threads", True), "sync-template": ("scripts/template-sync", True)}
DOCUMENTED_KEYS = {"name", "description", "when_to_use", "argument-hint", "arguments", "disable-model-invocation", "user-invocable",
                   "allowed-tools", "disallowed-tools", "model", "effort", "context", "agent", "background", "hooks", "paths", "shell"}
NETWORK_WORDS = re.compile(r"\b(urllib|http\.client|requests|socket|curl|wget)\b")


def budgets():
    data = tomllib.loads((ROOT / ".template" / "budgets.toml").read_text())["class"]
    return data["skill"], data["skill-description"]


def present():
    table = dict(DESIGN)
    table.update({name: spec for name, spec in LATER.items() if (SKILLS / name / "SKILL.md").exists()})
    return table


def frontmatter(text):
    lines = text.split("\n")
    if lines[0] != "---":
        return None, text
    end = lines.index("---", 1)
    keys = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        keys[key.strip()] = value.strip()
    return keys, "\n".join(lines[end + 1:])


def library_text(script):
    """The text of a script and, for a wrapper of cr_tools, of the library behind it."""
    text = (ROOT / script).read_text(encoding="utf-8")
    if "cr_tools" in text:
        text += "".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "scripts" / "lib" / "cr_tools").glob("*.py")))
    return text


class SkillFiles(unittest.TestCase):
    def test_every_skill_of_design_section_6_that_this_row_covers_exists(self):
        for name in DESIGN:
            self.assertTrue((SKILLS / name / "SKILL.md").is_file(), name)

    def test_no_skill_is_missing_from_the_table_and_none_is_an_extra(self):
        found = {path.parent.name for path in SKILLS.glob("*/SKILL.md")}
        self.assertEqual(found - set(DESIGN) - set(LATER), set())

    def test_frontmatter_and_budgets(self):
        skill, description = budgets()
        for name in present():
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            keys, body = frontmatter(text)
            self.assertIsNotNone(keys, name)
            self.assertEqual(keys.get("name"), name)
            self.assertTrue(keys.get("description"), name)
            self.assertLessEqual(len(keys["description"]), description["max_chars"], name)
            self.assertLessEqual(set(keys), DOCUMENTED_KEYS, name)
            self.assertLessEqual(text.count("\n"), skill["max_lines"], name)
            self.assertLessEqual(len(text.encode()), skill["max_bytes"], name)
            self.assertNotIn("allowed-tools", keys, f"{name}: allowed-tools pre-approves tools; this template relies on the deny list")

    def test_the_layout_of_adr_010_headings(self):
        for name in present():
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            for heading in ("## Reads (in order)", "## Does not read", "## Report"):
                self.assertIn(heading + "\n", text, f"{name}: {heading}")

    def test_the_named_script_exists_and_is_executable(self):
        for name, (script, _) in present().items():
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(script, text, f"{name} must name {script}")
            self.assertTrue((ROOT / script).is_file(), script)
            self.assertTrue(os.access(ROOT / script, os.X_OK), script)

    def test_every_script_a_skill_mentions_exists(self):
        for name in present():
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            for script in sorted(set(re.findall(r"scripts/[a-z][a-z-]*", text))):
                self.assertTrue((ROOT / script).is_file(), f"{name}: {script}")

    def test_no_skill_tells_the_reader_to_use_a_command_that_fails_here_or_is_the_approvers(self):
        for name in present():
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotRegex(text, r"gh pr (edit|ready|merge|review)\b", name)
            self.assertNotRegex(text, r"--no-verify|--force|git tag|sudo", name)

    def test_every_skill_says_that_github_text_is_data(self):
        for name in present():
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8").lower()
            self.assertTrue("data" in text, name)


class ScriptsHaveAnOfflineModeAndATest(unittest.TestCase):
    def test_scripts_that_call_github_have_fixture_or_offline(self):
        for name, (script, calls_github) in present().items():
            if calls_github:
                text = library_text(script)
                self.assertTrue("--fixture" in text or "--offline" in text, f"{script} has neither --fixture nor --offline")

    def test_scripts_without_an_offline_flag_start_no_network_call(self):
        """initialize-project, new-cr and pr-body only read and write local files; they call no network service, so they need no mode."""
        for script in ("scripts/initialize-project", "scripts/new-cr", "scripts/pr-body"):
            text = (ROOT / script).read_text(encoding="utf-8")
            self.assertNotRegex(text, NETWORK_WORDS, script)
            self.assertNotRegex(text, r"""["']gh["']|\bgh\s+api\b""", script)

    def test_every_script_a_skill_names_has_a_test_that_names_it(self):
        sources = "".join(path.read_text(encoding="utf-8") for path in TESTS.glob("test_*.py"))
        sources += "".join(path.read_text(encoding="utf-8") for path in (ROOT / "template-dev" / "tests").glob("test_*.py"))
        sources += "".join(path.read_text(encoding="utf-8") for path in (ROOT / "template-dev" / "tests" / "pr_contract").glob("test_*.py"))
        for name, (script, _) in present().items():
            base = script.split("/")[-1]
            self.assertIn(base, sources, f"no test names {script}")

    def test_the_fixture_run_of_every_github_script_exists_as_a_test_file(self):
        names = {path.name for path in TESTS.glob("test_*.py")}
        self.assertLessEqual({"test_pr_checkpoint.py", "test_cr_context.py", "test_cr_criteria.py"}, names)


class InitializeProjectCreatesNothingOnGitHub(unittest.TestCase):
    """T-13 and T-20, static: the script starts no process and imports no network library, so it can create no label, ruleset,
    repository, collaborator, or setting; it prints proposals."""

    def test_imports_and_calls(self):
        tree = ast.parse((ROOT / "scripts" / "initialize-project").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertEqual(imported & {"subprocess", "socket", "urllib", "http", "requests", "ssl", "ftplib", "smtplib", "ctypes"}, set())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                self.assertNotIn((node.func.value.id, node.func.attr), {("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "spawnl")})

    def test_its_skill_creates_nothing_either(self):
        text = (SKILLS / "initialize-project" / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"Does not do[\s\S]*Create a label, ruleset, repository, collaborator, or setting")
        self.assertNotRegex(text, r"gh (label|repo|ruleset|api)")


class StartCrLabelsOnlyAfterConfirmation(unittest.TestCase):
    """T-13, static: the one label write adds an existing label to an issue; nothing creates a label."""

    def client_tree(self):
        return ast.parse((ROOT / "scripts" / "lib" / "cr_tools" / "client.py").read_text(encoding="utf-8"))

    def write_calls(self):
        """The argument arrays that write (a method flag, the graphql endpoint, or a body from stdin)."""
        found = []
        for node in ast.walk(self.client_tree()):
            if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "gh":
                parts = [ast.unparse(element) for element in node.elts]
                if "'-X'" in parts or "'graphql'" in parts:
                    found.append(parts)
        return found

    def test_the_writes_are_exactly_these_three(self):
        calls = self.write_calls()
        described = sorted((c[3] if c[2] == "'-X'" else "graphql", c[4] if c[2] == "'-X'" else "") for c in calls)
        self.assertEqual(described, sorted([("'POST'", "f'repos/{self.repo}/issues/{number}/labels'"), ("'PATCH'", "f'repos/{self.repo}/pulls/{number}'"), ("graphql", "")]))

    def test_no_label_is_created_anywhere(self):
        for path in (ROOT / "scripts" / "lib" / "cr_tools").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"\bgh\s+label\b", path.name)
            self.assertNotIn("repos/{self.repo}/labels'", text.replace('"', "'"), path.name)
        self.assertNotIn("/labels\"", (ROOT / "scripts" / "lib" / "cr_tools" / "client.py").read_text().replace("issues/{number}/labels", ""))

    def test_the_label_step_needs_the_explicit_flag(self):
        text = (ROOT / "scripts" / "lib" / "cr_tools" / "context.py").read_text(encoding="utf-8")
        self.assertIn("if args.confirmed_class is None:", text)
        self.assertLess(text.index("if args.confirmed_class is None:"), text.index("setup.client.add_label"))

    def test_the_skill_obtains_the_confirmation_in_the_session(self):
        text = (SKILLS / "start-cr" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--confirmed-class", text)
        self.assertRegex(text, r"confirm the class and wait for their reply")
        self.assertRegex(text, r"never creates a label|It never creates a label")
        self.assertRegex(text, r"STOP")
        self.assertRegex(text, r"issue body[\s\S]*data")

    def test_cr_context_and_start_cr_both_stop_on_a_foreign_issue(self):
        self.assertIn("scripts/cr-context", (SKILLS / "start-cr" / "SKILL.md").read_text())
        self.assertIn("refused: STOP", (SKILLS / "prepare-cr-context" / "SKILL.md").read_text())
        self.assertIn("refused: STOP", (SKILLS / "start-cr" / "SKILL.md").read_text())


class LaterSkills(unittest.TestCase):
    def test_a_later_skill_that_exists_is_held_to_the_same_rules(self):
        for name, (script, _) in LATER.items():
            if (SKILLS / name / "SKILL.md").exists():
                self.assertTrue((ROOT / script).is_file(), script)

    def test_the_manifest_classes_the_new_files_once(self):
        import template_manifest
        manifest = template_manifest.load_manifest(ROOT / ".template" / "manifest.toml")
        for path in ("scripts/cr-context", "scripts/cr-preflight", "scripts/cr-criteria", "scripts/pr-checkpoint", "scripts/lib/cr_tools/client.py",
                     ".claude/skills/start-cr/SKILL.md"):
            self.assertEqual(manifest.classify(path), ["owned"], path)


if __name__ == "__main__":
    unittest.main()
