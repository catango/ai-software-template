"""The pull request body of a sync: the template-sync PR template (`scripts/pr-body template-sync`) with every section filled in, the
section "Workflow change justification" when a workflow changes (PC-6), and the two items of the checkpoint that the run itself did."""
import re

from pr_contract import markdown
from pr_contract.sanitize import clean

from .errors import Refused
from .plan import KINDS, WORKFLOW_PREFIXES, changed_paths, workflow_paths

HEADINGS = {
    "added": "Owned files added",
    "changed": "Owned files changed by the template",
    "drift": "Owned files changed locally, overwritten",
    "uses": "Owned files with drift limited to uses lines, overwritten (listed again in the section below)",
    "removed": "Owned files removed",
    "seeded": "Seeded files that are new in this version, added once",
}
SHOWN = 200
TICKED = ("Sync run and state file updated", "Changed files listed by class")


def _paths(paths, cap=SHOWN):
    """One line per path, cut at `cap` with a count; `cap` None lists every path."""
    if not paths:
        return "none\n"
    shown = paths if cap is None else paths[:cap]
    lines = "".join(f"- `{path}`\n" for path in shown)
    return lines + (f"- and {len(paths) - len(shown)} more\n" if len(shown) < len(paths) else "")


def fill(text, name, content):
    """Replace the content of the section `## name` (its comments included) with `content`."""
    pattern = re.compile(rf"(^## {re.escape(name)}\n)(.*?)(?=^## |\Z)", re.S | re.M)
    if not pattern.search(text):
        raise ValueError(f"the template has no section {name}")
    return pattern.sub(lambda match: match.group(1) + content.rstrip("\n") + "\n\n", text, count=1)


def build(template_text, plan, source, old_version, old_commit, new_tag, new_commit, entries):
    seeded = [c.path for c in plan.changes if c.kind == "seeded"]
    by_kind = {kind: [c.path for c in plan.changes if c.kind == kind] for kind in KINDS}
    counts = ", ".join(f"{len(by_kind[kind])} {kind}" for kind in ("added", "changed", "drift", "uses", "removed", "seeded") if by_kind[kind])
    summary = (f"The template moves from v{old_version} to {new_tag} ({counts or 'no file changes'}; the version and the hashes in "
               f"`.template/state.toml` are updated). {len(plan.honoured)} override(s) honoured. Seeded files that exist are not changed.")
    versions = f"- From: v{old_version} (commit {old_commit or 'not recorded'})\n- To: {new_tag} (tag commit {new_commit})\n"
    # rule TC-1 wants every new seeded path listed here, so that list is never cut
    classes = "".join(f"{HEADINGS[kind]}:\n\n{_paths(by_kind[kind], None if kind == 'seeded' else SHOWN)}\n" for kind in KINDS)
    classes += "Seeded files that exist in the project are never changed or removed by a sync.\n"
    if plan.notes:
        classes += "\nNotes:\n\n" + "".join(f"- {clean(note, 200)}\n" for note in plan.notes)
    if plan.honoured:
        overrides = "".join(
            f"- `{path}`: {clean(entries[path].reason, 200)} ({clean(entries[path].pr, 80)}); kept as it is, recorded as `override` in `.template/state.toml`\n"
            if path in entries else f"- `{path}`\n" for path in plan.honoured)
    else:
        overrides = "none\n"
    if plan.unused:
        overrides += "\nEntries that matched no owned file of this version (not recorded):\n\n" + _paths(plan.unused)
    drift = _paths(by_kind["uses"])
    body = template_text
    for name, content in (("Summary", summary), ("Versions", versions), ("Changed files by class", classes), ("Overrides honoured", overrides),
                          ("Drift limited to uses lines", drift),
                          ("Release notes", f"https://github.com/{source}/releases/tag/{new_tag}\n")):
        body = fill(body, name, content)
    workflows = workflow_paths(plan)
    if workflows:
        justification = (f"The template release {new_tag} changes these workflow files, which are owned and come from the tag: "
                         + ", ".join(f"`{path}`" for path in workflows[:20]) + (f" and {len(workflows) - 20} more" if len(workflows) > 20 else "")
                         + ". Each change is reviewed against the release notes before the merge; nothing of the tag was run by the sync.\n")
        body = body.replace("## Checkpoint\n", f"## Workflow change justification\n{justification}\n## Checkpoint\n", 1)
    for item in TICKED:
        if f"- [ ] {item}\n" not in body:
            raise ValueError(f"the template has no checkpoint item {item!r}")
        body = body.replace(f"- [ ] {item}\n", f"- [x] {item}\n", 1)
    if len(body) > markdown.MAX_BODY or body.count("\n") > markdown.MAX_LINES:
        raise Refused(f"the pull request body would be too large ({len(body)} bytes, the limit is {markdown.MAX_BODY}): {len(seeded)} new seeded "
                      "paths must all be listed in it and are not cut; release them in smaller steps")
    return body
