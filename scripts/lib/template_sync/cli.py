"""scripts/template-sync: bring a project up to a release (a tag) of the template that it was generated from.

    template-sync [--to] TAG [--dry-run] [--fixture DIR] [--tree DIR]     sync to TAG = v<MAJOR>.<MINOR>.<PATCH>
    template-sync --check [--fixture DIR] [--tree DIR]                    the template lag; reads only, writes nothing

This is the repository's OWN reviewed copy: it does every check and the copy, and nothing of the template (its scripts, workflows, hooks)
is run. The source is `template.source` of `.template/state.toml` and never an argument. See source.py for how the tag is read.

A sync refuses (exit 1) unless: the tree is clean, on the default branch, with `.template/state.toml` valid; the target is a tag of the
source whose commit is contained in the source's default branch and whose manifest names the same version; the target is not older than the
recorded version, and a tag of the recorded version names the recorded commit (a moved tag is refused); `.template/overrides.toml` is valid;
and nothing in the plan is a change that rule TC-1 would reject. Then it creates the branch `template-sync/<tag>`, overwrites, adds and
removes owned files, adds each seeded file that is new once, honours each entry of `.template/overrides.toml` (and records it as `override`),
regenerates `.claude/settings.json`, updates `.template/state.toml`, commits, pushes that branch, and opens a draft pull request from
`scripts/pr-body template-sync`. It never merges, never pushes the default branch, never creates a tag, and never writes
`.template/overrides.toml`. `--dry-run` prints the plan and writes nothing.

With `--fixture DIR` the template source is the local repository `DIR/<owner>/<name>`, nothing is pushed, and the pull request that would
be opened is written to `DIR/created-pr.json`. Tests use it; it needs no network and no credential.

`--check` prints, as its FIRST line, the template lag in plain words (scripts/lib/cr_tools/context.py shows it), then one summary line.

Exit status: 0 done, 1 refused, 2 usage, 3 the template source could not be read or a step failed. Each run ends with one summary line
`template-sync: ...` of at most 120 characters. Text of the template and of the project is printed sanitized.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import template_manifest
from cr_tools import common
from pr_contract.sanitize import clean

from . import body as body_module, fsops, overrides as overrides_module, plan as plan_module, publish, state as state_module, versions
from .errors import Refused, SourceError, UsageError
from .source import Source

PROG = "template-sync"
STATE = plan_module.STATE
OVERRIDES = plan_module.OVERRIDES
MANIFEST = ".template/manifest.toml"
TARGET_HELP = "a tag name v<MAJOR>.<MINOR>.<PATCH>"


USAGE = "usage: template-sync [--to] v<MAJOR>.<MINOR>.<PATCH> [--dry-run] [--fixture DIR] | template-sync --check [--fixture DIR]"


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(f"{USAGE} ({clean(message, 80)})")


def build_parser():
    parser = _Parser(prog=PROG, add_help=False, allow_abbrev=False)
    parser.add_argument("target", nargs="?")
    parser.add_argument("--to", dest="to")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fixture")
    parser.add_argument("--tree", default=str(Path(__file__).resolve().parents[3]))
    return parser


def _read(tree, rel):
    path = Path(tree) / rel
    if path.is_symlink() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def load_state(tree):
    text = _read(tree, STATE)
    if text is None:
        raise Refused(f"{STATE} is missing or not valid")
    try:
        return state_module.parse(text), text
    except state_module.StateError as error:
        raise Refused(f"{STATE} is missing or not valid ({error})") from None


def load_overrides(tree):
    text = _read(tree, OVERRIDES)
    if text is None:
        return {}
    try:
        return overrides_module.parse(text)
    except overrides_module.OverridesError as error:
        raise Refused(f"{OVERRIDES}: {error}") from None


def load_manifest(tree):
    text = _read(tree, MANIFEST)
    try:
        if text is None:
            raise template_manifest.ManifestError("missing")
        return template_manifest.parse_manifest(text)
    except template_manifest.ManifestError:
        raise Refused(f"{MANIFEST} of the project is missing or not valid") from None


# ------------------------------------------------------------------------------------------------------------------ check

def mode_check(args, tree):
    """The lag. The first line is printed whatever happens."""
    try:
        state, _ = load_state(tree)
    except Refused:
        print("unknown: .template/state.toml is missing or not valid")
        print(common.summary(PROG, "refused: .template/state.toml is missing or not valid"))
        return 1
    source = Source(state.source, args.fixture)
    try:
        tags = source.tags()
    except SourceError:
        print("unknown: the template source could not be read")
        print(common.summary(PROG, "error: cannot read the template source"))
        return 3
    finally:
        source.close()
    found = []
    for name in tags:
        try:
            found.append(versions.parse_tag(name))
        except ValueError:
            pass
    recorded = versions.parse_version(state.version)
    line = versions.lag_line(recorded, found)
    print(line)
    print(common.summary(PROG, f"check ok, recorded {versions.format_tag(recorded)}, newest {versions.format_tag(max(found)) if found else 'none'}"))
    return 0


# ------------------------------------------------------------------------------------------------------------------ sync

def render_plan(plan, old, new_tag, commit):
    lines = [f"Template sync plan: v{old} -> {new_tag} (tag commit {commit})"]
    for kind in plan_module.KINDS:
        paths = [c.path for c in plan.changes if c.kind == kind]
        if paths:
            lines.append(f"{body_module.HEADINGS[kind]} ({len(paths)}):")
            lines += [f"  {path}" for path in paths[:40]] + ([f"  and {len(paths) - 40} more"] if len(paths) > 40 else [])
    lines.append(f"Overrides honoured ({len(plan.honoured)}):" if plan.honoured else "Overrides honoured: none")
    lines += [f"  {path}" for path in plan.honoured]
    for path in plan.unused:
        lines.append(f"Override entry for {path}: not an owned file of this version, not recorded")
    lines += [f"Note: {clean(note, 200)}" for note in plan.notes]
    return lines


def mode_sync(args, tree):
    try:
        wanted = versions.parse_tag(args.target)
    except ValueError:
        raise UsageError(f"the target must be {TARGET_HELP}") from None
    tag = args.target
    state, old_text = load_state(tree)
    entries = load_overrides(tree)
    manifest = load_manifest(tree)
    repo = publish.Repo(tree)
    repo.check_work_tree()
    repo.check_clean()
    default = repo.check_on_default_branch()
    branch = f"template-sync/{tag}"
    current = versions.parse_version(state.version)
    if wanted < current:
        raise Refused(f"downgrade: {tag} is older than the recorded version v{state.version}")

    source = Source(state.source, args.fixture)
    try:
        tags = source.tags()
        if tag not in tags:
            raise Refused(f"no tag named {tag} in the template source (a branch or a commit is not a target)")
        commit = tags[tag]
        if wanted == current:
            if state.commit is None:
                raise Refused(f"no commit is recorded for v{state.version} in {STATE}, so a tag of the same version cannot be checked")
            if commit != state.commit:
                raise Refused(f"the tag {tag} moved: it names {commit[:12]} but {STATE} records {state.commit[:12]} for the same version")
        release = source.fetch(tag, commit)
        try:
            target = template_manifest.parse_manifest(release.manifest_text)
        except template_manifest.ManifestError:
            raise Refused("the manifest of the tag is not valid") from None
        if versions.parse_version(target.version) != wanted:
            raise Refused(f"the manifest of the tag names version {clean(target.version, 20)}, not {tag[1:]}")
        plan = plan_module.build(tree, state, entries, manifest, release._replace(manifest=target))
    finally:
        source.close()

    changed_state = plan.new_state != old_text
    if plan.changes or changed_state:
        repo.check_branch_free(branch)  # a branch left over from a merged sync does not stand in the way of "nothing to do"
    lines = render_plan(plan, state.version, tag, commit)
    if not plan.changes and not changed_state:
        print("\n".join(lines))
        print(f"Nothing to do: the project is at {tag} ({commit[:12]}) and every owned file is as the tag ships it.")
        print(common.summary(PROG, f"nothing to do, already at {tag}"))
        return 0
    if not changed_state:
        raise Refused(f"the tag is the version already recorded and only {len(plan.changes)} file(s) differ: a sync PR would leave "
                      f"{STATE} unchanged, which rule TC-1 rejects; restore the files by hand or sync to a newer tag")
    pr_body = _body(tree, plan, state, tag, commit, entries)  # refuses a body that would not fit, before anything is written
    print("\n".join(lines))
    if args.dry_run:
        print(common.summary(PROG, f"dry run v{state.version} -> {tag}: {len(plan.changes)} file(s), {len(plan.honoured)} override(s), nothing written"))
        return 0

    online = not args.fixture
    if online:
        repo.check_remote_state(default, branch)
    return _apply(args, tree, repo, plan, state, tag, commit, default, branch, pr_body, online)


def _body(tree, plan, state, tag, commit, entries):
    done = subprocess.run([str(Path(tree) / "scripts" / "pr-body"), "template-sync"], capture_output=True, text=True, check=False,
                          env={"PATH": "/usr/bin:/bin"}, cwd=tree)
    if done.returncode != 0 or not done.stdout:
        raise SourceError("scripts/pr-body template-sync failed")
    return body_module.build(done.stdout, plan, state.source, state.version, state.commit, tag, commit, entries)


def _apply(args, tree, repo, plan, state, tag, commit, default, branch, pr_body, online):
    root = Path(tree).resolve()
    written = sorted(plan.writes) + [STATE]
    added = [c.path for c in plan.changes if c.kind in ("added", "seeded")]
    repo.start_branch(branch)
    try:
        for path in sorted(plan.writes):
            data, executable = plan.writes[path]
            fsops.safe_write(root, path, data, 0o755 if executable else 0o644)
        for path in plan.removals:
            fsops.safe_remove(root, path)
        fsops.safe_write(root, STATE, plan.new_state.encode(), 0o644)
        sha = repo.commit(sorted(set(written) | set(plan.removals)), f"Sync the template to {tag}\n\nFrom v{state.version}. Tag commit {commit}.")
    except (OSError, SourceError, Refused):
        repo.rollback(default, branch, added)
        raise SourceError("the sync failed; the working tree is back on the default branch") from None
    title = f"Template sync to {tag}"
    out = [f"Committed {sha[:12]} on {branch}."]
    if not online:
        record = Path(args.fixture) / "created-pr.json"
        record.write_text(json.dumps({"title": title, "head": branch, "base": default, "draft": True, "body": pr_body}, indent=2) + "\n")
        out.append("Fixture mode: nothing was pushed; the draft pull request that would be opened is in created-pr.json.")
        print("\n".join(out))
        print(common.summary(PROG, f"v{state.version} -> {tag} on {branch}, {len(plan.changes)} file(s), pull request recorded"))
        return 0
    try:
        repo.push(branch)
        url = repo.open_pull_request(repo.repository(), branch, default, title, pr_body)
    except (SourceError, Refused) as error:
        print("\n".join(out))
        print(common.summary(PROG, f"error: the branch {branch} is committed here; pushing it or opening the pull request failed ({error})"))
        return 3
    out.append(f"Draft pull request: {clean(url, 200) or '(opened; no address returned)'}")
    print("\n".join(out))
    print(common.summary(PROG, f"v{state.version} -> {tag} on {branch}, {len(plan.changes)} file(s), draft PR opened"))
    return 0


def _say(text):
    """The summary line; a message too long for it is printed in full, sanitized, on the line before."""
    if len(f"{PROG}: {text}") > 120:
        print(clean(f"{PROG}: {text}", 600))
    print(common.summary(PROG, text))


def main(argv):
    try:
        args = build_parser().parse_args(list(argv))
        tree = Path(args.tree).resolve()
        if args.check:
            if args.target is not None or args.to is not None or args.dry_run:
                raise UsageError("--check takes no target and no --dry-run")
            return mode_check(args, tree)
        if args.target is not None and args.to is not None:
            raise UsageError("give the target once")
        args.target = args.target if args.target is not None else args.to
        if args.target is None:
            raise UsageError(f"give the target ({TARGET_HELP}) or --check")
        return mode_sync(args, tree)
    except Refused as refusal:
        _say(f"refused: {refusal}")
        return 1
    except UsageError as error:
        print(f"{PROG}: error: {clean(error, 200)}", file=sys.stderr)
        return 2
    except SourceError as error:
        _say(f"error: {error}")
        return 3
