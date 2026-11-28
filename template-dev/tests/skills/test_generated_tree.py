"""Step 5 of design section 12 (a) on a generated tree: the fixture cases of `pr-contract` (design, implementation, change,
template-sync, init; passing and failing bodies and paths, including TC-1 and TC-2) run against the copy of `scripts/pr-contract` and
the PR templates that `initialize-project` leaves in a repository generated from each configuration (`PR_CONTRACT_TREE`, which
test_cases.py reads). The cases carry their own configuration, so the result depends on the generated code only.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.dont_write_bytecode = True

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("template_test_helpers", ROOT / "template-dev" / "tests" / "helpers.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


class GeneratedTreeCases(unittest.TestCase):
    def test_the_fixture_cases_pass_against_a_tree_generated_from_each_configuration(self):
        for name, config in base.CONFIGS.items():
            with self.subTest(config=name):
                tmp = pathlib.Path(tempfile.mkdtemp(prefix="generated-tree-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                tree = base.export_template(tmp / "tree")
                done = base.run_init(tree, config)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertFalse((tree / "template-dev").exists())
                env = {"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1", "PR_CONTRACT_TREE": str(tree)}
                run = subprocess.run(
                    [sys.executable, "-B", "-s", "-m", "unittest", "test_cases"],
                    cwd=ROOT / "template-dev" / "tests" / "pr_contract", env=env, capture_output=True, text=True,
                )
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn("OK", run.stderr)


if __name__ == "__main__":
    unittest.main()
