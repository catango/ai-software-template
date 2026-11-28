"""Static part of the gate check `owned-drift` (used by owned-drift.sh). Standard library only.

Usage: owned_drift.py [--base=COMMIT | --base-unreadable] [--head-ref=NAME] ROOT
Findings are "path:line: message" on stdout; exit 1 when there are findings, 77 when there is nothing to judge (no state.toml).

The policy is `.template/state.toml` of the base commit (the hashes of the owned files as the last sync or init wrote them); the entries
of `.template/overrides.toml` are those of the head, because a project declares a divergence in the pull request that makes it, and the
entry (path, reason, pr) is reviewed there. See owned-drift.sh for the rules and scripts/lib/template_sync/uses_drift.py for the reading
of `uses:` lines.
"""
import hashlib
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))

from template_sync import overrides as overrides_module  # noqa: E402
from template_sync import state as state_module  # noqa: E402
from template_sync import uses_drift, versions  # noqa: E402
import template_manifest  # noqa: E402

STATE = ".template/state.toml"
OVERRIDES = ".template/overrides.toml"
SYNC_BRANCH = re.compile(r"template-sync/v([0-9]+\.[0-9]+\.[0-9]+)")
INIT_BRANCH = re.compile(r"init/v[0-9]+\.[0-9]+\.[0-9]+")
MAX_FILE = 8 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    """Output bytes of `git -C root <args>`, None when git fails (not a repository, no such object)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/nonexistent"), "LANG": "C",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}
    try:
        done = subprocess.run(["git", "-C", root, *args], capture_output=True, env=env, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def at_commit(root, commit, path):
    return git(root, "cat-file", "blob", f"{commit}:{path}")


def tree_file(root, path):
    """The bytes of a regular file of the tree, None when there is none (a link or a directory counts as none)."""
    target = os.path.join(root, path)
    if os.path.islink(target) or not os.path.isfile(target):
        return None
    try:
        with open(target, "rb") as handle:
            return handle.read(MAX_FILE + 1)
    except OSError:
        return None


def parse_state(data):
    return state_module.parse(data.decode("utf-8")) if data is not None else None


def load_overrides(data, findings):
    if data is None:
        return {}
    try:
        return overrides_module.parse(data.decode("utf-8", "replace"))
    except overrides_module.OverridesError as error:
        findings.append(f"{OVERRIDES}:1: {error}")
        return None


def consistent(root, state, entries, findings):
    """What a sync or init PR records is what it wrote: every owned file matches its hash, unless it is declared as an override, and
    every override recorded has an entry."""
    for path, digest in sorted(state.owned.items()):
        if path in state.overrides:
            continue
        data = tree_file(root, path)
        if data is None or sha(data) != digest:
            findings.append(f"{path}:1: does not match its hash in {STATE}; a template-sync or init pull request records what it writes")
    for path in sorted(state.overrides):
        if path not in entries:
            findings.append(f"{OVERRIDES}:1: {path} is recorded as an override in {STATE} but has no entry")


def run(root, base, base_unreadable, head_ref):
    findings = []
    if base_unreadable:
        return ["%s:1: cannot read the base commit; the hashes come from it, so the check fails closed" % STATE]
    head_text = tree_file(root, STATE)
    base_text = at_commit(root, base, STATE) if base else None
    if base is None:
        if head_text is None:
            return None
        policy_text, start = head_text, "HEAD"
    else:
        if base_text is None:
            if head_text is None:
                return None
            return init_pull_request(root, head_text, head_ref)
        policy_text, start = base_text, base
        if head_text is None:
            return [f"{STATE}:1: the file is removed"]
    try:
        policy = parse_state(policy_text)
    except (state_module.StateError, UnicodeDecodeError) as error:
        return [f"{STATE}:1: not valid: {error}"]
    entries = load_overrides(tree_file(root, OVERRIDES), findings)
    if entries is None:
        return findings
    base_entries = {}
    if base:
        base_entries = load_overrides(at_commit(root, base, OVERRIDES), [])
        base_entries = base_entries if base_entries is not None else {}

    if base and head_text != base_text:
        match = SYNC_BRANCH.fullmatch(head_ref or "")
        if match and exempt_sync(root, head_text, match.group(1), policy, at_commit(root, base, OVERRIDES), tree_file(root, OVERRIDES), entries, findings):
            return findings

    for path in sorted(policy.overrides):
        if path not in entries:
            findings.append(f"{OVERRIDES}:1: {path} is recorded as an override in {STATE} but has no entry")
    reference = None
    for path, recorded in sorted(policy.owned.items()):
        data = tree_file(root, path)
        current = sha(data) if data is not None else None
        if current == recorded or path in entries:
            continue
        if base and path not in base_entries:
            before = at_commit(root, base, path)
            if (before is None and data is None) or (before is not None and current == sha(before)):
                continue  # this pull request does not change it
        if data is not None and uses_only(root, path, data, recorded, start):
            continue
        findings.append(f"{path}:1: owned file {'removed' if data is None else 'changed'} without an entry in {OVERRIDES}")
    return findings


def uses_only(root, path, data, recorded, start):
    """Whether `data` differs from the synced version of `path` only in `uses:` lines of the same actions. The synced version is the file at
    the last commit that changed state.toml (reachable from `start`); it counts only when it has the recorded hash."""
    if not uses_drift.OWNED_DRIFT_ALLOWS_USES_ONLY or not uses_drift.applies_to(path):
        return False
    log = git(root, "log", "-n", "1", "--format=%H", start, "--", STATE)
    commit = (log or b"").decode("ascii", "replace").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return False
    synced = at_commit(root, commit, path)
    if synced is None or sha(synced) != recorded:
        return False
    try:
        return uses_drift.is_uses_only(synced.decode("utf-8"), data.decode("utf-8"))
    except UnicodeDecodeError:
        return False


def seeded_in_head(root, path):
    """Whether the manifest of the tree classes `path` as seeded: a release may turn an owned file into a seeded one, which then stays."""
    text = tree_file(root, ".template/manifest.toml")
    try:
        return text is not None and template_manifest.parse_manifest(text.decode("utf-8")).classify(path) == ["seeded"]
    except (template_manifest.ManifestError, UnicodeDecodeError):
        return False


def exempt_sync(root, head_text, branch_version, base, base_overrides_text, head_overrides_text, entries, findings):
    """The exemption of a template-sync pull request. True when it holds; otherwise findings say why and the normal rules go on.
    It holds when the state records the version of the branch and a version greater than the base's, the files match the hashes the
    state records, overrides.toml is unchanged, and every path that the base state recorded and the head state no longer lists is
    gone from the tree (a real removal), recorded as an override that has an entry, or seeded in the head manifest. No check can tell
    that the content equals the tag: that stays with the review of the pull request."""
    try:
        head = parse_state(head_text)
    except (state_module.StateError, UnicodeDecodeError) as error:
        findings.append(f"{STATE}:1: not valid: {error}")
        return False
    held = True
    if head.version != branch_version:
        findings.append(f"{STATE}:1: a template-sync pull request must record the version of its branch ({branch_version})")
        held = False
    if versions.parse_version(head.version) <= versions.parse_version(base.version):
        findings.append(f"{STATE}:1: a template-sync pull request must record a version greater than the base's ({base.version})")
        held = False
    for path in sorted(set(base.owned) - set(head.owned)):
        if path in head.overrides or not os.path.lexists(os.path.join(root, path)) or seeded_in_head(root, path):
            continue
        findings.append(f"{path}:1: dropped from [owned] of {STATE} but still present")
        held = False
    if base_overrides_text != head_overrides_text:
        findings.append(f"{OVERRIDES}:1: a template-sync pull request must not change {OVERRIDES}")
        held = False
    mismatches = []
    consistent(root, head, entries, mismatches)
    findings.extend(mismatches)
    return held and not mismatches


def init_pull_request(root, head_text, head_ref):
    if not INIT_BRANCH.fullmatch(head_ref or ""):
        return [f"{STATE}:1: added outside an init pull request (the branch is not init/v<MAJOR>.<MINOR>.<PATCH>)"]
    findings = []
    try:
        state = parse_state(head_text)
    except (state_module.StateError, UnicodeDecodeError) as error:
        return [f"{STATE}:1: not valid: {error}"]
    entries = load_overrides(tree_file(root, OVERRIDES), findings)
    if entries is None:
        return findings
    consistent(root, state, entries, findings)
    return findings


def main(argv):
    base, unreadable, head_ref, roots = None, False, "", []
    for arg in argv:
        if arg.startswith("--base="):
            base = arg[7:]
        elif arg == "--base-unreadable":
            unreadable = True
        elif arg.startswith("--head-ref="):
            head_ref = arg[11:]
        else:
            roots.append(arg)
    root = roots[0] if roots else "."
    findings = run(root, base, unreadable, head_ref)
    if findings is None:
        return 77
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
