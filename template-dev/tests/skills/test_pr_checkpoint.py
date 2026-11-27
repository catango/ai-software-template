"""scripts/pr-checkpoint (T-12): fixture tests of show, tick and ready, and the exact calls to GitHub."""
import json
import os
import subprocess
import sys
import unittest

import helpers
from helpers import APPROVER, BASE_SHA, HEAD_SHA, MACHINE, NODE_ID, REPO, STRANGER

from cr_tools import checkpoint
from cr_tools.client import GhClient

SCRIPT = helpers.ROOT / "scripts" / "pr-checkpoint"


class Base(helpers.Case):
    def setUp(self):
        super().setUp()
        self.add(5)

    def add(self, number, **kwargs):
        kwargs.setdefault("node_id", NODE_ID)
        if number == 5:
            kwargs.setdefault("head_ref", "impl/CR-40-other")
        return helpers.add_pr(self.fix, number, **kwargs)

    def body(self, number=5):
        return self.stored(f"pr-{number}.json")["body"]


class Show(Base):
    def test_prints_checklist_ci_and_commits(self):
        base, head = helpers.make_repo(self.tree)
        self.add(6, head_sha=head, base_sha=base, body=helpers.implementation_body(ticked=2))
        helpers.write(self.fix / f"check-runs-{head}.json", {"check_runs": [
            {"id": 1, "name": "quality-gate", "status": "completed", "conclusion": "success"},
            {"id": 2, "name": "pr-contract", "status": "completed", "conclusion": "failure"},
            {"id": 3, "name": "slow", "status": "in_progress", "conclusion": None},
        ]})
        helpers.write(self.fix / "annotations-2.json", [{"message": f"finding {n}"} for n in range(5)])
        code, out, err = helpers.run_checkpoint(self, "show", "6")
        self.assertEqual((code, err), (0, ""), out)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("checkpoint: PR #6 (draft), 2/4 done, next 3"), lines[0])
        self.assertIn("  [x] 1 Plan item 1", lines)
        self.assertIn("  [ ] 3 CI green", lines)
        self.assertIn("  quality-gate: success", lines)
        self.assertIn("  pr-contract: failure", lines)
        self.assertIn("  slow: running", lines)
        self.assertEqual([line for line in lines if "finding" in line], [f"    ! finding {n}" for n in range(3)])
        self.assertIn("    ci-failed: +2 more", lines)
        commits = lines[lines.index("commits since the merge base (3):") + 1:-1]
        self.assertEqual([line.split(" ", 3)[3] for line in commits[:3]], ["third", "second", "first"])  # not the base commit itself
        self.assertTrue(lines[-1].startswith("pr-checkpoint: show #6: 2/4 done"), lines[-1])
        self.assertLessEqual(len(lines[-1]), 120)

    def test_the_latest_run_of_a_name_counts(self):
        helpers.write(self.fix / f"check-runs-{HEAD_SHA}.json", {"check_runs": [
            {"id": 9, "name": "gate", "status": "completed", "conclusion": "success"},
            {"id": 4, "name": "gate", "status": "completed", "conclusion": "failure"},
        ]})
        _, out, _ = helpers.run_checkpoint(self, "show", "5")
        self.assertIn("  gate: success", out)
        self.assertNotIn("gate: failure", out)

    def test_output_is_bounded_and_data_is_sanitized(self):
        items = "\n".join(f"- [ ] item {n} " + "x" * 300 + "\x1b[31m::error::boom" for n in range(30))
        body = f"<!-- checkpoint:start -->\n{items}\n<!-- checkpoint:end -->\n"
        self.add(7, body=body)
        helpers.write(self.fix / f"check-runs-{HEAD_SHA}.json", {"check_runs": [{"id": 1, "name": "n" * 200, "status": "completed", "conclusion": "failure"}]})
        helpers.write(self.fix / "annotations-1.json", [{"message": "\x00‮::warning::" + "y" * 500}] * 9)
        code, out, _ = helpers.run_checkpoint(self, "show", "7")
        self.assertEqual(code, 0)
        self.assertLessEqual(len(out.encode()), 2048 + 120)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("‮", out)
        self.assertNotIn("::error", out)
        self.assertTrue(all(len(line) <= 140 for line in out.splitlines()))

    def test_no_number_uses_the_open_pull_request_of_the_branch(self):
        base, head = helpers.make_repo(self.tree)
        self.add(8, head_ref="impl/CR-40-thing", head_sha=head, base_sha=base)
        code, out, err = helpers.run_checkpoint(self, "show")
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("PR #8", out)

    def test_no_number_and_two_pull_requests_is_an_error(self):
        helpers.make_repo(self.tree)
        self.add(8, head_ref="impl/CR-40-thing")
        self.add(9, head_ref="impl/CR-40-thing")
        code, _, err = helpers.run_checkpoint(self, "show")
        self.assertEqual(code, 2)
        self.assertIn("single open pull request", err)

    def test_no_commit_data_is_reported_not_guessed(self):
        _, out, _ = helpers.run_checkpoint(self, "show", "5")
        self.assertIn("unavailable", out)


class Refusals(Base):
    def test_a_pull_request_of_another_author_is_refused_by_every_command(self):
        self.add(6, author=STRANGER)
        for argv in (("show", "6"), ("tick", "1", "6"), ("ready", "6")):
            code, out, _ = helpers.run_checkpoint(self, *argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(out.strip(), "pr-checkpoint: refused: the pull request is not authored by the machine account")
        self.assertEqual(self.body(6), helpers.implementation_body())

    def test_the_approver_is_not_the_machine_account(self):
        self.add(6, author=APPROVER)
        self.assertEqual(helpers.run_checkpoint(self, "show", "6")[0], 1)

    def test_a_login_that_differs_only_in_case_is_the_same_account(self):
        self.add(6, author=MACHINE.upper())
        self.assertEqual(helpers.run_checkpoint(self, "show", "6")[0], 0)

    def test_identities_come_from_the_base_commit(self):
        runner = helpers.FakeRunner({"contents/config/project.yaml": {"type": "file", "encoding": "base64", "size": 3, "content": "e30="}})
        runner.answers["pulls/5"] = helpers.pr_json(5, node_id=NODE_ID)
        client = GhClient(REPO, runner=runner)
        code, out, _ = helpers.run_checkpoint(self, "show", "5", client=client)
        self.assertEqual(code, 1, out)
        configs = [call for call, _ in runner.calls if "contents/config/project.yaml" in " ".join(call)]
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0][2], f"repos/{REPO}/contents/config/project.yaml?ref={BASE_SHA}")

    def test_an_unreadable_configuration_refuses(self):
        (self.fix / "base" / "config" / "project.yaml").write_text("schema_version: 1\n")
        self.assertEqual(helpers.run_checkpoint(self, "show", "5")[0], 1)
        (self.fix / "base" / "config" / "project.yaml").unlink()
        self.assertEqual(helpers.run_checkpoint(self, "tick", "1", "5")[0], 1)

    def test_marker_problems_refuse(self):
        cases = {
            "none": "No checkpoint here.\n- [ ] item\n",
            "no end": "<!-- checkpoint:start -->\n- [ ] item\n",
            "reversed": "<!-- checkpoint:end -->\n- [ ] item\n<!-- checkpoint:start -->\n",
            "two blocks": helpers.implementation_body() + "\n<!-- checkpoint:start -->\n- [ ] other\n<!-- checkpoint:end -->\n",
            "not alone": "text <!-- checkpoint:start -->\n- [ ] item\n<!-- checkpoint:end -->\n",
        }
        for name, body in cases.items():
            self.add(20, body=body) if name == "none" else None
            helpers.write(self.fix / "pr-5.json", helpers.pr_json(5, body=body, node_id=NODE_ID))
            for argv in (("show", "5"), ("tick", "1", "5")):
                code, out, _ = helpers.run_checkpoint(self, *argv)
                self.assertEqual(code, 1, (name, argv, out))
            self.assertEqual(self.body(), body, name)

    def test_an_oversized_body_is_refused(self):
        helpers.write(self.fix / "pr-5.json", helpers.pr_json(5, body=helpers.implementation_body(extra_after="x" * 70000), node_id=NODE_ID))
        self.assertEqual(helpers.run_checkpoint(self, "show", "5")[0], 1)

    def test_usage_errors_exit_2(self):
        for argv in (("tick",), ("tick", "1", "zero"), ("frobnicate",), ("show", "0"), ("tick", "99", "5"), ("tick", "no such item", "5")):
            self.assertEqual(helpers.run_checkpoint(self, *argv)[0], 2, argv)

    def test_a_missing_pull_request_is_an_api_error(self):
        code, _, err = helpers.run_checkpoint(self, "show", "99")
        self.assertEqual(code, 3)
        self.assertIn("not found", err)


class Tick(Base):
    def lines(self, body):
        return body.split("\n")

    def test_tick_by_number_changes_one_line_only(self):
        before = self.body()
        code, out, err = helpers.run_checkpoint(self, "tick", "2", "5")
        self.assertEqual((code, err), (0, ""), out)
        after = self.body()
        changed = [(a, b) for a, b in zip(self.lines(before), self.lines(after)) if a != b]
        self.assertEqual(changed, [("- [ ] Quality gate passes locally", "- [x] Quality gate passes locally")])
        self.assertEqual(len(self.lines(before)), len(self.lines(after)))
        self.assertEqual(out.strip(), "pr-checkpoint: ticked item 2 of #5")

    def test_tick_by_exact_text_and_by_unique_prefix(self):
        helpers.run_checkpoint(self, "tick", "CI green", "5")
        self.assertIn("- [x] CI green", self.body())
        helpers.run_checkpoint(self, "tick", "Quality", "5")
        self.assertIn("- [x] Quality gate passes locally", self.body())

    def test_an_ambiguous_prefix_is_refused_and_nothing_changes(self):
        before = self.body()
        code, _, err = helpers.run_checkpoint(self, "tick", "P", "5")  # "Plan item 1" only: unique
        self.assertEqual(code, 0, err)
        helpers.write(self.fix / "pr-5.json", helpers.pr_json(5, node_id=NODE_ID, body=before.replace("- [ ] CI green", "- [ ] Plan item 2")))
        before = self.body()
        code, _, err = helpers.run_checkpoint(self, "tick", "Plan", "5")
        self.assertEqual(code, 2)
        self.assertIn("more than one", err)
        self.assertEqual(self.body(), before)

    def test_tick_twice_changes_nothing_the_second_time(self):
        helpers.run_checkpoint(self, "tick", "1", "5")
        once = self.body()
        code, out, _ = helpers.run_checkpoint(self, "tick", "1", "5")
        self.assertEqual(code, 0)
        self.assertIn("already ticked", out)
        self.assertEqual(self.body(), once)

    def test_text_outside_the_block_and_other_lines_stay_byte_for_byte(self):
        body = ("Before\r\n\r\n- [ ] not in the block <!-- checkpoint:start -->x\r\n- [ ] kept outside\r\n<!-- checkpoint:start -->\r\n"
                "- [ ] one\r\nfree text - [ ] two\r\n  - [ ] indented\r\n- [ ] two  \r\n<!-- checkpoint:end -->\r\nAfter - [ ] item\r\n")
        body = body.replace(" <!-- checkpoint:start -->x", "")
        helpers.write(self.fix / "pr-5.json", helpers.pr_json(5, body=body, node_id=NODE_ID))
        code, out, _ = helpers.run_checkpoint(self, "tick", "two", "5")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.body(), body.replace("- [ ] two  \r\n", "- [x] two  \r\n"))

    def test_only_the_block_is_looked_at(self):
        body = "- [ ] outside\n<!-- checkpoint:start -->\n- [ ] inside\n<!-- checkpoint:end -->\n"
        helpers.write(self.fix / "pr-5.json", helpers.pr_json(5, body=body, node_id=NODE_ID))
        self.assertEqual(helpers.run_checkpoint(self, "tick", "outside", "5")[0], 2)
        self.assertEqual(helpers.run_checkpoint(self, "tick", "1", "5")[0], 0)
        self.assertEqual(self.body(), body.replace("- [ ] inside", "- [x] inside"))

    def test_dry_run_changes_nothing(self):
        before = self.body()
        code, out, _ = helpers.run_checkpoint(self, "tick", "1", "5", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertEqual(self.body(), before)

    def test_item_text_is_data_never_run(self):
        body = "<!-- checkpoint:start -->\n- [ ] $(touch pwned); `touch pwned`\n<!-- checkpoint:end -->\n"
        helpers.write(self.fix / "pr-5.json", helpers.pr_json(5, body=body, node_id=NODE_ID))
        helpers.run_checkpoint(self, "show", "5")
        helpers.run_checkpoint(self, "tick", "1", "5")
        self.assertFalse((self.tree / "pwned").exists())
        self.assertFalse((helpers.ROOT / "pwned").exists())


class Ready(Base):
    def test_refuses_while_an_item_other_than_the_last_is_open(self):
        helpers.write(self.fix / "pr-5.json", helpers.pr_json(5, node_id=NODE_ID, body=helpers.implementation_body(ticked=2)))
        code, out, _ = helpers.run_checkpoint(self, "ready", "5")
        self.assertEqual(code, 1)
        self.assertIn("item 3 is not ticked", out)
        self.assertTrue(self.stored("pr-5.json")["draft"])

    def test_marks_ready_and_ticks_the_last_item(self):
        helpers.write(self.fix / "pr-5.json", helpers.pr_json(5, node_id=NODE_ID, body=helpers.implementation_body(ticked=3)))
        code, out, err = helpers.run_checkpoint(self, "ready", "5")
        self.assertEqual((code, err), (0, ""), out)
        stored = self.stored("pr-5.json")
        self.assertFalse(stored["draft"])
        self.assertIn("- [x] Marked ready for review", stored["body"])
        self.assertEqual(stored["body"], helpers.implementation_body(ticked=4))

    def test_dry_run_and_already_ready(self):
        helpers.write(self.fix / "pr-5.json", helpers.pr_json(5, node_id=NODE_ID, body=helpers.implementation_body(ticked=3)))
        self.assertEqual(helpers.run_checkpoint(self, "ready", "5", "--dry-run")[0], 0)
        self.assertTrue(self.stored("pr-5.json")["draft"])
        helpers.write(self.fix / "pr-5.json", helpers.pr_json(5, node_id=NODE_ID, draft=False, body=helpers.implementation_body(ticked=3)))
        code, out, _ = helpers.run_checkpoint(self, "ready", "5")
        self.assertIn("already ready", out)
        self.assertIn("- [ ] Marked ready for review", self.body())


class Calls(Base):
    """The exact calls: the REST PATCH and the GraphQL mutation, as argument arrays with literal endpoints."""

    def runner(self, body):
        return helpers.FakeRunner({
            "contents/config/project.yaml": {"type": "file", "encoding": "base64", "size": len(helpers.CONFIG), "content": __import__("base64").b64encode(helpers.CONFIG.encode()).decode()},
            "-X PATCH": {"number": 5},
            "graphql": {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}},
            "pulls/5": helpers.pr_json(5, node_id=NODE_ID, body=body),
        })

    def test_tick_is_one_rest_patch_with_the_whole_body_on_stdin(self):
        body = helpers.implementation_body()
        runner = self.runner(body)
        code, out, _ = helpers.run_checkpoint(self, "tick", "1", "5", client=GhClient(REPO, runner=runner))
        self.assertEqual(code, 0, out)
        writes = runner.writes()
        self.assertEqual(len(writes), 1)
        argv, data = writes[0]
        self.assertEqual(argv, ["gh", "api", "-X", "PATCH", f"repos/{REPO}/pulls/5", "--input", "-"])
        self.assertEqual(json.loads(data), {"body": body.replace("- [ ] Plan item 1", "- [x] Plan item 1")})

    def test_ready_is_the_graphql_mutation_then_a_patch(self):
        body = helpers.implementation_body(ticked=3)
        runner = self.runner(body)
        code, out, _ = helpers.run_checkpoint(self, "ready", "5", client=GhClient(REPO, runner=runner))
        self.assertEqual(code, 0, out)
        writes = runner.writes()
        self.assertEqual([("graphql" in a) for a, _ in writes], [True, False])
        query = [part for part in writes[0][0] if part.startswith("query=")][0]
        self.assertIn("markPullRequestReadyForReview", query)
        self.assertIn("id=" + NODE_ID, writes[0][0])
        self.assertEqual(" ".join(writes[1][0][:4]), "gh api -X PATCH")

    def test_no_call_is_gh_pr_edit_or_gh_pr_ready(self):
        runner = self.runner(helpers.implementation_body(ticked=3))
        helpers.run_checkpoint(self, "tick", "4", "5", client=GhClient(REPO, runner=runner))
        helpers.run_checkpoint(self, "ready", "5", client=GhClient(REPO, runner=runner))
        for argv, _ in runner.calls:
            self.assertEqual(" ".join(argv[:2]), "gh api", argv)

    def test_a_graphql_answer_that_is_still_a_draft_fails(self):
        runner = self.runner(helpers.implementation_body(ticked=3))
        runner.answers["graphql"] = {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": True}}}}
        code, _, err = helpers.run_checkpoint(self, "ready", "5", client=GhClient(REPO, runner=runner))
        self.assertEqual(code, 3, err)

    def test_a_failed_call_is_exit_3_and_prints_fixed_text(self):
        runner = self.runner(helpers.implementation_body())
        runner.answers["-X PATCH"] = (1, b"", b"gh: secret-looking text (HTTP 500)")
        code, _, err = helpers.run_checkpoint(self, "tick", "1", "5", client=GhClient(REPO, runner=runner))
        self.assertEqual(code, 3)
        self.assertNotIn("secret-looking", err)


class Static(unittest.TestCase):
    """T-12: the script's calls, read from its source."""

    def sources(self):
        return {path.name: path.read_text(encoding="utf-8") for path in (helpers.ROOT / "scripts" / "lib" / "cr_tools").glob("*.py")} | {"pr-checkpoint": SCRIPT.read_text(encoding="utf-8")}

    def test_no_source_calls_gh_pr_edit_or_gh_pr_ready(self):
        import ast
        for name, text in self.sources().items():
            tree = ast.parse(text)
            docstrings = {id(node.body[0].value) for node in ast.walk(tree) if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body
                          and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if id(node) in docstrings:
                    continue
                if isinstance(node, (ast.List, ast.Tuple)):
                    words = [element.value for element in node.elts if isinstance(element, ast.Constant)]
                    self.assertFalse(words[:2] == ["gh", "pr"], (name, words))
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotRegex(node.value, r"\bgh\s+pr\s+(edit|ready)\b", name)
                    self.assertNotRegex(node.value, r"\bgh\s+(repo|release|secret|variable|ruleset)\b", name)

    def test_the_two_calls_are_present_and_literal(self):
        text = self.sources()["client.py"]
        self.assertIn('"-X", "PATCH", f"repos/{self.repo}/pulls/{number}"', text)
        self.assertIn("markPullRequestReadyForReview(input: {pullRequestId: $id})", text)

    def test_the_script_runs_as_a_program_and_leaves_no_bytecode(self):
        import tempfile
        with tempfile.TemporaryDirectory() as fixture, tempfile.TemporaryDirectory() as tree:
            helpers.make_fixture(fixture)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="")
            done = subprocess.run([sys.executable, str(SCRIPT), "show", "5", "--fixture", fixture, "--repo", REPO, "--tree", tree],
                                  capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 3, done.stdout)  # no pr-5.json in the fixture
        self.assertIn("not found", done.stderr)
        self.assertEqual(list(helpers.ROOT.rglob("__pycache__")), [])


if __name__ == "__main__":
    unittest.main()
