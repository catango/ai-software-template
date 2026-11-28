"""Builders for the template-sync tests (standard library only, no network).

A local git repository acts as the template: tags `v1.0.0` (A), `v1.1.0` (B, annotated), `v1.2.0` (C) on the default branch, `v1.3.0` on a
side branch that is not in the default branch. A tree generated from A by
`scripts/initialize-project` with the second fixture configuration acts as the project. The template repository is built once per run
and copied for each test; the project is built once and copied too.

Tags are written as ref files, not with `git tag`: the gate forbids that command in any script, and these are throwaway repositories.
"""
import atexit
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(1, str(ROOT / "scripts" / "lib"))
sys.path.insert(2, str(ROOT / "template-dev" / "lib"))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("template_test_helpers", ROOT / "template-dev" / "tests" / "helpers.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

SCRIPT = ROOT / "scripts" / "template-sync"
SOURCE = "example-org/starter-template"  # `template.source` of the second fixture configuration
CONFIG = base.CONFIGS["second"]
PLATFORM_ENTRIES = ["Bash(example-cli delete *)", "Bash(example-cli apply *)"]  # deny.platform.txt of the second configuration

CORE_REMOVED_IN_B = "Bash(gh secret *)"
CORE_ADDED_IN_B = "Bash(example-core-new *)"

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "noreply", "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "noreply",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def env(extra=None):
    out = {"PATH": os.environ["PATH"], "HOME": "/nonexistent", "LANG": "C.UTF-8", **GIT_ENV}
    out.update(extra or {})
    return out


def git(directory, *args, check=True, extra_env=None, input=None):
    done = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, env=env(extra_env), input=input)
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read(directory, rel):
    return (Path(directory) / rel).read_bytes()


def write(directory, rel, data, mode=None):
    path = Path(directory) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    if mode is not None:
        path.chmod(mode)
    return path


def edit(directory, rel, function):
    text = read(directory, rel).decode()
    new = function(text)
    assert new != text, f"the edit of {rel} changed nothing"
    write(directory, rel, new)


def set_version(repo, version):
    edit(repo, ".template/manifest.toml", lambda t: t.replace(f'version = "{_manifest_version(repo)}"', f'version = "{version}"', 1))


def _manifest_version(repo):
    return tomllib.loads(read(repo, ".template/manifest.toml").decode())["version"]


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def set_tag(repo, name, sha):
    """A lightweight tag as a ref file (the gate forbids the command in scripts)."""
    path = Path(repo) / ".git" / "refs" / "tags" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sha + "\n")


def set_annotated_tag(repo, name, sha):
    body = f"object {sha}\ntype commit\ntag {name}\ntagger tester <noreply> 1767225600 +0000\n\nrelease {name}\n"
    object_id = git(repo, "mktag", input=body).strip()
    set_tag(repo, name, object_id)


def set_branch(repo, name, sha):
    path = Path(repo) / ".git" / "refs" / "heads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sha + "\n")


def archive(repo, ref, destination):
    destination.mkdir(parents=True, exist_ok=True)
    data = subprocess.run(["git", "-C", str(repo), "archive", ref], capture_output=True, check=True, env=env()).stdout
    subprocess.run(["tar", "-x", "-C", str(destination)], input=data, check=True)
    return destination


# ---------------------------------------------------------------------------------------------------- the template

_cache = {}


def _scratch():
    if "scratch" not in _cache:
        directory = tempfile.mkdtemp(prefix="template-sync-fixtures-")
        atexit.register(shutil.rmtree, directory, True)
        _cache["scratch"] = Path(directory)
    return _cache["scratch"]


def _build_template():
    root = _scratch() / "remotes"
    repo = root / SOURCE
    base.export_template(repo)
    git(repo, "init", "-q", "-b", "main")
    set_version(repo, "1.0.0")
    write(repo, "scripts/lib/template_sync_fixture_removed.py", "# removed in B\n")
    shas = {"A": commit(repo, "A")}
    set_tag(repo, "v1.0.0", shas["A"])

    # B (v1.1.0)
    edit(repo, ".claude/rules/bash.md", lambda t: t + "- A line that only version B has.\n")
    edit(repo, ".claude/rules/workflows.md", lambda t: t + "- Another line that only version B has.\n")
    edit(repo, ".github/workflows/pr-contract.yml", lambda t: t.replace("name: pr-contract\n", "name: pr-contract\n# a comment of version B\n", 1))
    (repo / "scripts/lib/template_sync_fixture_removed.py").unlink()
    edit(repo, "docs/PROJECT.md", lambda t: t + "\nA seeded line that only version B has.\n")
    write(repo, "scripts/lib/template_sync_fixture_added.py", "# added in B\n", 0o755)
    write(repo, "docs/NEW-SEEDED.md", "# A seeded file that is new in B\n")
    write(repo, "scripts/checks.d/zz-new.sh", "#!/usr/bin/env bash\nexit 0\n", 0o755)  # inside an existing seeded pattern
    write(repo, "template-dev/docs/only-in-b.md", "template-only\n")
    edit(repo, ".template/manifest.toml", lambda t: t.replace('seeded = [\n', 'seeded = [\n  "docs/NEW-SEEDED.md",\n', 1))
    _deny(repo, remove=CORE_REMOVED_IN_B, add=[CORE_ADDED_IN_B, PLATFORM_ENTRIES[1]])
    set_version(repo, "1.1.0")
    shas["B"] = commit(repo, "B")
    set_annotated_tag(repo, "v1.1.0", shas["B"])

    # C (v1.2.0)
    edit(repo, ".claude/rules/bash.md", lambda t: t + "- A line that only version C has.\n")
    set_version(repo, "1.2.0")
    shas["C"] = commit(repo, "C")
    set_tag(repo, "v1.2.0", shas["C"])

    # v1.3.0 on a side branch that main never contains
    git(repo, "checkout", "-q", "-b", "side")
    set_version(repo, "1.3.0")
    shas["SIDE"] = commit(repo, "side")
    set_tag(repo, "v1.3.0", shas["SIDE"])
    git(repo, "checkout", "-q", "main")
    git(repo, "repack", "-a", "-d", "-q")  # one pack instead of thousands of loose objects: copying it per test is cheap
    return root, shas


def _deny(repo, remove, add):
    settings = json.loads(read(repo, ".claude/settings.json"))
    deny = [e for e in settings["permissions"]["deny"] if e != remove]
    deny += [e for e in add if e not in deny]
    settings["permissions"]["deny"] = deny
    write(repo, ".claude/settings.json", json.dumps(settings, indent=2) + "\n")
    lines = read(repo, ".claude/required-deny.txt").decode().splitlines()
    lines = [line for line in lines if line.strip() != remove] + [e for e in add if e not in lines]
    write(repo, ".claude/required-deny.txt", "\n".join(lines) + "\n")


def template():
    """(path of the directory that holds `<owner>/<name>`, {name: commit}) of the shared template repository. Do not modify it."""
    if "template" not in _cache:
        _cache["template"] = _build_template()
    return _cache["template"]


def _build_project():
    root, shas = template()
    tree = archive(root / SOURCE, "v1.0.0", _scratch() / "project")
    done = base.run_init(tree, CONFIG, "--template-commit", shas["A"])
    assert done.returncode == 0, done.stderr
    git(tree, "init", "-q", "-b", "main")
    commit(tree, "initial")
    git(tree, "repack", "-a", "-d", "-q")
    return tree


def project():
    """The shared project generated from A. Do not modify it."""
    if "project" not in _cache:
        _cache["project"] = _build_project()
    return _cache["project"]


class Fixture:
    """A private copy of the template remotes and of the project for one test."""

    def __init__(self, testcase):
        tmp = tempfile.TemporaryDirectory(prefix="template-sync-test-")
        testcase.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        root, self.shas = template()
        self.remotes = self.tmp / "remotes"
        self.remote = self.remotes / SOURCE
        # only the repository, not its working tree: git reads a tag from it, and a test that edits the template calls worktree()
        shutil.copytree(root / SOURCE / ".git", self.remote / ".git", symlinks=True)
        self.tree = self.tmp / "project"
        shutil.copytree(project(), self.tree, symlinks=True)

    def run(self, *args, tree=None, fixture=True, extra_env=None, path_prefix=None):
        command = [sys.executable, "-B", str(Path(tree or self.tree) / "scripts" / "template-sync"), *args]
        if fixture:
            command += ["--fixture", str(self.remotes)]
        run_env = env(extra_env)
        if path_prefix:
            run_env["PATH"] = f"{path_prefix}{os.pathsep}{run_env['PATH']}"
        return subprocess.run(command, cwd=tree or self.tree, capture_output=True, text=True, env=run_env)

    def git(self, *args, **kwargs):
        return git(self.tree, *args, **kwargs)

    def commit_local(self, message="local"):
        return commit(self.tree, message)

    def head(self):
        return self.git("rev-parse", "HEAD").strip()

    def branch(self):
        return self.git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def clean(self):
        return self.git("status", "--porcelain", "--untracked-files=all") == ""

    def snapshot(self):
        return base.snapshot(self.tree)

    def state(self):
        return tomllib.loads(read(self.tree, ".template/state.toml").decode())

    def merge_branch(self, name):
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--ff-only", name)

    def worktree(self):
        """Check the main branch of this test's copy of the template out, so that files of it can be edited."""
        if not (self.remote / ".template").exists():
            git(self.remote, "checkout", "-q", "-f", "main")
        return self.remote

    def new_remote_commit(self, version, edits, tag=None, message="extra"):
        """A commit on the main branch of this test's copy of the template; edits is {path: text}."""
        self.worktree()
        for rel, text in edits.items():
            write(self.remote, rel, text)
        if version:
            set_version(self.remote, version)
        sha = commit(self.remote, message)
        if tag:
            set_tag(self.remote, tag, sha)
        return sha


def owned_paths(tree_or_repo, manifest_text=None):
    """The paths of a tree that its own manifest classes as owned."""
    manifest = base.template_manifest.parse_manifest(manifest_text or read(tree_or_repo, ".template/manifest.toml").decode())
    return {p for p in base.template_manifest.walk_files(tree_or_repo) if manifest.classify(p) == ["owned"]}
