"""Guard for the apply mode of an operator script: refuse unless the work tree is clean and HEAD is a release tag that is
contained in the default branch. Standard library only; runs local git commands and needs no network.

An operator script calls `require_release(args.apply)` first. A dry run (`apply` false) is always allowed. Otherwise:

  - the work tree has no modified or untracked file;
  - a release tag points at HEAD: `v<MAJOR>.<MINOR>.<PATCH>`, annotated or lightweight;
  - HEAD is an ancestor of (or equal to) the default branch. The default branch is the `default_branch` argument (a ref, for
    example `origin/main`), or else the target of `refs/remotes/origin/HEAD`; when neither exists the guard refuses.

The guard creates no tag and fetches nothing: the remote-tracking ref is as current as the last fetch of the person who runs it.

Limit of the default (offline) behaviour: the tag and the default branch are read from this clone, so a tag made on the machine that
runs the script passes. The trust anchor of a release is the approver's tag on the remote. `verify_remote` (command line
`--verify-remote REMOTE`, a remote name or URL) closes that gap: it also asks the remote with `git ls-remote --tags` and refuses
unless the remote has one of the release tags at HEAD on the same commit. It needs the remote to be reachable, so it is optional and
the operator rule says to use it for an apply run on a machine that can reach the remote.

Command line: operator_guard.py [--repo DIR] [--default-branch REF] [--verify-remote REMOTE]
exit 0 and the tag name, or exit 1 and the reason.
"""
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True

RELEASE_TAG = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}")
TIMEOUT = 30
COMMIT = "^" + "{commit}"  # peel a ref to its commit
PEEL = "^" + "{}"  # the peeled form of a tag in the list of a remote


class GuardError(Exception):
    """The apply run is refused; the message says why and holds no repository content beyond a tag name."""


def _git(repo, *args):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/"), "LC_ALL": "C",
           "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        done = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise GuardError("git cannot be run") from error
    return done.returncode, done.stdout


def check_release(repo=".", default_branch=None, verify_remote=None):
    """The release tag at HEAD. Raises GuardError when the apply run must be refused."""
    if not isinstance(repo, str) or not repo or repo.startswith("-"):
        raise GuardError("the repository path is not valid")
    code, _ = _git(repo, "rev-parse", "--verify", "--quiet", "HEAD" + COMMIT)
    if code != 0:
        raise GuardError("not a git work tree with a commit at HEAD")
    code, out = _git(repo, "status", "--porcelain", "--untracked-files=all")
    if code != 0 or out.strip():
        raise GuardError("the work tree is not clean: an apply run is made from an unmodified release")
    code, out = _git(repo, "for-each-ref", "--points-at", "HEAD", "--format=%(refname)", "refs/tags/")
    tags = sorted(ref[len("refs/tags/"):] for ref in out.split("\n") if ref.startswith("refs/tags/"))
    release = [tag for tag in tags if RELEASE_TAG.fullmatch(tag)]
    if code != 0 or not release:
        raise GuardError("HEAD is not a release tag (v<MAJOR>.<MINOR>.<PATCH>): an apply run is made from a release")
    if default_branch is None:
        code, out = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        default_branch = out.strip() if code == 0 else None
    if not default_branch or not REF.fullmatch(default_branch):
        raise GuardError("the default branch is not known: pass it, or set refs/remotes/origin/HEAD")
    code, _ = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{default_branch}{COMMIT}")
    ref = f"refs/remotes/{default_branch}"
    if code != 0:
        code, _ = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{default_branch}{COMMIT}")
        ref = f"refs/heads/{default_branch}"
        if code != 0:
            raise GuardError("the default branch does not exist in this repository")
    code, _ = _git(repo, "merge-base", "--is-ancestor", "HEAD", ref)
    if code != 0:
        raise GuardError(f"the release tag {release[-1]} is not contained in the default branch")
    if verify_remote is not None:
        _verify_remote(repo, verify_remote, release)
    return release[-1]


def _verify_remote(repo, remote, release):
    """Require one of the release tags at HEAD on the remote, at the same commit."""
    if not isinstance(remote, str) or not remote or remote.startswith("-") or "\n" in remote or "::" in remote:
        raise GuardError("the remote is not valid")
    code, head = _git(repo, "rev-parse", "--verify", "--quiet", "HEAD" + COMMIT)
    if code != 0:
        raise GuardError("not a git work tree with a commit at HEAD")
    patterns = [f"refs/tags/{tag}" for tag in release] + [f"refs/tags/{tag}{PEEL}" for tag in release]
    code, out = _git(repo, "-c", "protocol.ext.allow=never", "ls-remote", "--tags", "--", remote, *patterns)
    if code != 0:
        raise GuardError("the remote cannot be read: the tag was not verified")
    # a lightweight tag lists the commit; an annotated tag lists the tag object and, peeled, the commit
    if not any(line.split("\t")[0] == head.strip() for line in out.split("\n") if "\t" in line):
        raise GuardError("the release tag is not found on the remote at HEAD")


def require_release(apply, repo=".", default_branch=None, verify_remote=None):
    """Return when the run may go on. A dry run always may; an apply run needs `check_release` to pass, else SystemExit(1)."""
    if not apply:
        return None
    try:
        return check_release(repo, default_branch, verify_remote)
    except GuardError as error:
        raise SystemExit(f"operator_guard: refused: {error}") from None


def main(argv):
    repo, default_branch, verify_remote = ".", None, None
    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag == "--repo" and args:
            repo = args.pop(0)
        elif flag == "--default-branch" and args:
            default_branch = args.pop(0)
        elif flag == "--verify-remote" and args:
            verify_remote = args.pop(0)
        else:
            print("usage: operator_guard.py [--repo DIR] [--default-branch REF] [--verify-remote REMOTE]", file=sys.stderr)
            return 2
    try:
        print(check_release(repo, default_branch, verify_remote))
    except GuardError as error:
        print(f"operator_guard: refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
