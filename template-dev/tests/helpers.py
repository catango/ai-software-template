"""Shared helpers for the template tests (standard library only)."""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "template-dev" / "lib"))

import template_manifest  # noqa: E402

FIXTURES = ROOT / "template-dev" / "fixtures"
NEUTRAL = FIXTURES / "config" / "neutral.toml"
SECOND = FIXTURES / "config" / "second" / "config.toml"
CONFIGS = {"neutral": NEUTRAL, "second": SECOND}


def tracked_paths():
    """Paths of the template: tracked files plus new files that are not ignored.

    Mirrors what "Use this template" would copy once the work is committed, and lets the
    tests run on uncommitted work.
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout.decode()
    return sorted(p for p in out.split("\0") if p and ((ROOT / p).is_file() or (ROOT / p).is_symlink()))


def export_template(dest):
    """Copy the template tree without history into dest (a new directory)."""
    for rel in tracked_paths():
        target = Path(dest) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target, follow_symlinks=False)
    return Path(dest)


def run_init(tree, config, *args, bytecode=False):
    """Run the tree's own scripts/initialize-project; returns the CompletedProcess.

    With bytecode=True the run is as a user's would be: no -B and bytecode writing enabled
    in the environment.
    """
    command = [sys.executable] + ([] if bytecode else ["-B"])
    command += [str(Path(tree) / "scripts" / "initialize-project"), "--config", str(config), *args]
    env = dict(os.environ)
    if bytecode:
        env["PYTHONDONTWRITEBYTECODE"] = ""
    return subprocess.run(command, cwd=tree, capture_output=True, text=True, env=env)


def snapshot(tree):
    """Map of relative path to SHA-256 for every file in tree (no .git)."""
    result = {}
    for rel in template_manifest.walk_files(tree):
        path = Path(tree) / rel
        data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
        result[rel] = hashlib.sha256(data).hexdigest()
    return result


class TempDirTestCase(unittest.TestCase):
    def make_dir(self):
        tmp = tempfile.TemporaryDirectory(prefix="template-test-")
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def generate(self, config_name="neutral", extra_files=None, bytecode=False):
        """Export the template, optionally add files, run init; returns (tree, result)."""
        tree = export_template(self.make_dir() / "tree")
        for rel, text in (extra_files or {}).items():
            target = tree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return tree, run_init(tree, CONFIGS[config_name], bytecode=bytecode)
