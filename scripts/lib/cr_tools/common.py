"""Shared pieces of the Skill scripts: the exit codes, the client choice, the repository name, and `git` reads.

Exit codes: 0 done, 1 refused (a rule says stop: the script prints why), 2 usage or input error, 3 GitHub or transient error.
A refusal and an error never count as a pass, and no message holds text from GitHub except what the caller sanitizes.
"""
import os
import re
import subprocess
import unicodedata

from pr_contract import api as base_api
from pr_contract.errors import ApiError, InputError
from pr_contract.sanitize import clean

from .client import FixtureClient, GhClient

GIT_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM")
_REMOTE = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*://[^/]+/|[^/@\s]+@[^:/\s]+:|[^/\s]+:)?(?P<repo>[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?")
_REF = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]{0,99}")
EXAMPLE_REPO = "example-owner/sample-app"


class Refused(Exception):
    """A rule says stop. The message is fixed text and is printed on the summary line."""


def git(tree, *args, timeout=20):
    """Output of `git -C tree <args>` (argument array, no shell); None when git fails or is missing."""
    env = {key: os.environ[key] for key in GIT_ENV_KEEP if key in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        done = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, env=env, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace")


def derive_repo(tree):
    """owner/name from the `origin` remote of the tree."""
    url = (git(tree, "config", "--get", "remote.origin.url") or "").strip()
    match = _REMOTE.fullmatch(url)
    if not match:
        raise InputError("cannot read the repository from the origin remote; pass --repo owner/name")
    return base_api.check_repo(match.group("repo"))


def make_client(fixture, repo, tree):
    """(client, repo): the offline client over `fixture`, or the `gh api` client. `repo` defaults to the origin remote of the tree
    (the offline client falls back to a fixed example name)."""
    if repo is None:
        repo = derive_repo(tree) if not fixture else _example_or_derived(tree)
    repo = base_api.check_repo(repo)
    return (FixtureClient(fixture, repo) if fixture else GhClient(repo)), repo


def _example_or_derived(tree):
    try:
        return derive_repo(tree)
    except InputError:
        return EXAMPLE_REPO


def check_ref_name(name):
    if not isinstance(name, str) or not _REF.fullmatch(name) or ".." in name or name.endswith(("/", ".lock")):
        raise ApiError("malformed response")
    return name


def summary(script, text):
    """The one summary line of a run: `<script>: <text>`, at most 120 characters."""
    return clean(f"{script}: {text}", 120)


_CONTROL = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def plain(value, limit=300):
    """A line of repository text for the output: control, zero-width and bidirectional characters dropped, cut at `limit`."""
    text = "".join(ch for ch in str(value) if unicodedata.category(ch) not in _CONTROL or ch == "\t")
    return text if len(text) <= limit else text[: limit - 3] + "..."
