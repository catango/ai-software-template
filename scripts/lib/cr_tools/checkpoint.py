"""scripts/pr-checkpoint: the checklist between the checkpoint markers of a pull request body.

    pr-checkpoint show  [PR]                 the checklist, the latest CI result, and the commits since the merge base
    pr-checkpoint tick  ITEM [PR] [--dry-run]    tick one item (its number, or its text); ITEM is data, never run
    pr-checkpoint ready [PR] [--dry-run]     mark a draft ready (every item but the last must be ticked), then tick the last

Without PR, the open pull request of the current branch is used. Only a pull request authored by the machine account of
`config/project.yaml` (read on the BASE commit of the pull request) is read or changed. The body is read and edited ONLY between
`<!-- checkpoint:start -->` and `<!-- checkpoint:end -->`: both markers must occur exactly once, each alone on its line; a
tick changes the `[ ]` of one checklist line and nothing else, and the script checks that the text outside the block and every
other line are as they were. The body is updated with the REST PATCH call and a draft is marked ready with the GraphQL mutation
(see client.py); `gh pr edit` and `gh pr ready` are not used because they fail on a host with the Projects classic error.

Item text, CI annotations and commit subjects come from GitHub or from the repository: they are printed sanitized and cut, and
they are data. `show` prints at most 2 KiB. With --fixture DIR nothing is fetched (see client.py); --tree DIR is the repository
whose git history is read (default: this one).
"""
import argparse
import re
import sys
from pathlib import Path

from pr_contract import config as policy
from pr_contract.errors import ApiError, ConfigError, InputError, NotFound, NotReadable
from pr_contract.sanitize import clean

from . import common
from .common import Refused
from .identity import read_identities

SCRIPT = "pr-checkpoint"
START = "<!-- checkpoint:start -->"
END = "<!-- checkpoint:end -->"
ITEM = re.compile(r"- \[(?P<mark>[ xX])\] (?P<text>\S.*?)[ \t]*\r?")
MAX_ITEMS_SHOWN = 20
MAX_TEXT = 120
MAX_COMMITS = 10
MAX_ANNOTATIONS = 3
MAX_OUTPUT = 2048
MAX_BODY = 65536


class Block:
    """The lines of a body split at the markers: `before`, `inner` (lines between), `after`, with their line ends kept."""

    def __init__(self, body):
        if len(body) > MAX_BODY:
            raise Refused("the pull request body is too large")
        if body.count(START) != 1 or body.count(END) != 1:
            raise Refused("the body needs exactly one checkpoint start marker and one end marker")
        lines = body.split("\n")
        starts = [i for i, line in enumerate(lines) if line.strip() == START]
        ends = [i for i, line in enumerate(lines) if line.strip() == END]
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise Refused("each checkpoint marker must be alone on its line, the start before the end")
        self.lines = lines
        self.start, self.end = starts[0], ends[0]

    def items(self):
        """[(line index, ticked, text)] of the checklist lines inside the block, in order."""
        found = []
        for index in range(self.start + 1, self.end):
            match = ITEM.fullmatch(self.lines[index])
            if match:
                found.append((index, match.group("mark") != " ", match.group("text")))
        return found

    def ticked(self, index):
        """The body with the checklist line `index` ticked and every other character as it was."""
        line = self.lines[index]
        position = line.index("[")
        changed = list(self.lines)
        changed[index] = line[:position] + "[x]" + line[position + 3:]
        text = "\n".join(changed)
        outside_before, outside_after = self.lines[:self.start + 1], self.lines[self.end:]
        again = Block(text)
        if again.lines[:again.start + 1] != outside_before or again.lines[again.end:] != outside_after:
            raise Refused("the tick would change text outside the checkpoint block")
        if [i for i in range(len(changed)) if changed[i] != self.lines[i]] != [index]:
            raise Refused("the tick would change more than one line")
        return text


def pick(items, selector):
    """The checklist line a selector names: its number (1-based), its exact text, or a text prefix that fits one line only."""
    if re.fullmatch(r"[1-9][0-9]{0,2}", selector):
        number = int(selector)
        if number > len(items):
            raise InputError("the checklist has fewer items")
        return items[number - 1]
    exact = [item for item in items if item[2] == selector]
    if len(exact) > 1:
        raise InputError("the item text fits more than one line")
    if exact:
        return exact[0]
    fits = [item for item in items if item[2].startswith(selector)] if selector else []
    if len(fits) != 1:
        raise InputError("the item is not in the checklist" if not fits else "the item text fits more than one line")
    return fits[0]


def resolve_pull(client, number, tree):
    """The pull request (data) to act on and its number: given, or the open one of the current branch."""
    if number is None:
        branch = (common.git(tree, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
        if not branch or branch == "HEAD":
            raise InputError("no pull request number given and no current branch")
        common.check_ref_name(branch)
        numbers = client.open_pulls_for_branch(branch)
        if len(numbers) != 1:
            raise InputError("no pull request number given and the current branch has no single open pull request")
        number = numbers[0]
    pull = client.pull(number)
    return pull


def judged_pull(client, number, tree):
    """The pull request, after the check that the machine account authored it (identities from its BASE commit)."""
    pull = resolve_pull(client, number, tree)
    try:
        identities = read_identities(client.config_text(pull["base_sha"]))
    except ConfigError:
        raise Refused("the identities cannot be read from config/project.yaml of the base commit") from None
    if not identities.is_machine(pull["author"]):
        raise Refused("the pull request is not authored by the machine account")
    return pull


def ci_lines(client, pull):
    """(lines, counts): one line per check of the head commit, the latest run of each name, and up to three annotations of failed ones."""
    latest = {}
    for run in client.check_runs(pull["head_sha"]):
        if run["name"] not in latest or run["id"] > latest[run["name"]]["id"]:
            latest[run["name"]] = run
    lines, ok, failed, other = [], 0, 0, 0
    for name in sorted(latest):
        run = latest[name]
        state = run["conclusion"] if run["status"] == "completed" and run["conclusion"] else ("running" if run["status"] != "completed" else "unknown")
        lines.append(f"  {clean(name, 60)}: {clean(state, 20)}")
        if state in ("success", "neutral", "skipped"):
            ok += 1
        elif state in ("failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"):
            failed += 1
            notes = [clean(message, MAX_TEXT) for message in client.annotations(run["id"])]
            for message in notes[:MAX_ANNOTATIONS]:
                lines.append(f"    ! {message}")
            if len(notes) > MAX_ANNOTATIONS:
                lines.append(f"    ci-failed: +{len(notes) - MAX_ANNOTATIONS} more")
        else:
            other += 1
    return lines, (ok, failed, other)


def commit_lines(tree, pull):
    """(lines, count): up to ten commits of the local HEAD since its merge base with origin/<base branch>, or a note."""
    base = f"refs/remotes/origin/{common.check_ref_name(pull['base_ref'])}"
    local = (common.git(tree, "rev-parse", "HEAD") or "").strip()
    if not local:
        return ["  unavailable: no local commit"], None
    merge_base = (common.git(tree, "merge-base", "HEAD", base) or "").strip()
    if not merge_base:
        return [f"  unavailable: fetch origin/{clean(pull['base_ref'], 40)}"], None
    count = (common.git(tree, "rev-list", "--count", f"{merge_base}..HEAD") or "0").strip()
    log = common.git(tree, "log", "--no-decorate", "--format=%h %s", "-n", str(MAX_COMMITS), f"{merge_base}..HEAD") or ""
    lines = [f"  {clean(line, 80)}" for line in log.splitlines() if line.strip()]
    same = local == pull["head_sha"]
    lines.append(f"  local head {local[:7]} {'matches' if same else 'differs from'} the pull request head {pull['head_sha'][:7]}")
    return lines, int(count) if count.isdigit() else None


def cmd_show(client, args, tree):
    pull = judged_pull(client, args.pr, tree)
    block = Block(pull["body"])
    items = block.items()
    done = sum(1 for _, ticked, _ in items if ticked)
    nxt = next(((n, text) for n, (_, ticked, text) in enumerate(items, 1) if not ticked), None)
    out = [f"checkpoint: PR #{pull['number']} ({'draft' if pull['draft'] else 'ready'}), {done}/{len(items)} done" + (f", next {nxt[0]}" if nxt else "")]
    for n, (_, ticked, text) in enumerate(items[:MAX_ITEMS_SHOWN], 1):
        out.append(f"  [{'x' if ticked else ' '}] {n} {clean(text, MAX_TEXT)}")
    if len(items) > MAX_ITEMS_SHOWN:
        out.append(f"  +{len(items) - MAX_ITEMS_SHOWN} more items")
    ci, counts = ci_lines(client, pull)
    out.append(f"ci: head {pull['head_sha'][:7]}, {counts[0]} ok, {counts[1]} failed, {counts[2]} other")
    out.extend(ci or ["  no check runs"])
    log, count = commit_lines(tree, pull)
    out.append("commits since the merge base" + (f" ({count})" if count is not None else "") + ":")
    out.extend(log)
    text = "\n".join(out)
    if len(text.encode()) > MAX_OUTPUT:
        while out and len("\n".join(out + ["  (output cut)"]).encode()) > MAX_OUTPUT:
            out.pop()
        text = "\n".join(out + ["  (output cut)"])
    print(text)
    print(common.summary(SCRIPT, f"show #{pull['number']}: {done}/{len(items)} done, ci {counts[0]} ok {counts[1]} failed"))
    return 0


def cmd_tick(client, args, tree):
    pull = judged_pull(client, args.pr, tree)
    block = Block(pull["body"])
    items = block.items()
    if not items:
        raise Refused("the checkpoint block has no checklist line")
    index, ticked, text = pick(items, args.item)
    number = items.index((index, ticked, text)) + 1
    if ticked:
        print(common.summary(SCRIPT, f"item {number} of #{pull['number']} was already ticked; nothing changed"))
        return 0
    body = block.ticked(index)
    if args.dry_run:
        print(common.summary(SCRIPT, f"dry run: would tick item {number} of #{pull['number']}; nothing changed"))
        return 0
    client.patch_body(pull["number"], body)
    print(common.summary(SCRIPT, f"ticked item {number} of #{pull['number']}"))
    return 0


def cmd_ready(client, args, tree):
    pull = judged_pull(client, args.pr, tree)
    block = Block(pull["body"])
    items = block.items()
    if len(items) < 2:
        raise Refused("the checklist needs at least two items (the last one is the ready mark)")
    open_items = [n for n, (_, ticked, _) in enumerate(items[:-1], 1) if not ticked]
    if open_items:
        raise Refused(f"item {open_items[0]} is not ticked; only the last item may be open")
    if not pull["draft"]:
        print(common.summary(SCRIPT, f"#{pull['number']} is already ready; nothing changed"))
        return 0
    if args.dry_run:
        print(common.summary(SCRIPT, f"dry run: would mark #{pull['number']} ready; nothing changed"))
        return 0
    if not pull["node_id"]:
        raise ApiError("malformed response")
    client.mark_ready(pull["node_id"])
    last_index, last_ticked, _ = items[-1]
    if not last_ticked:
        client.patch_body(pull["number"], block.ticked(last_index))
    print(common.summary(SCRIPT, f"marked #{pull['number']} ready and ticked the last item"))
    return 0


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(self.format_usage().strip().replace("\n", " "))


def build_parser():
    parser = _Parser(prog=SCRIPT, add_help=False)
    parser.add_argument("--fixture", help="read GitHub data from files in DIR instead of fetching it")
    parser.add_argument("--repo", help="owner/name (default: the origin remote of --tree)")
    parser.add_argument("--tree", default=str(Path(__file__).resolve().parents[3]), help="repository whose git history is read")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    shared = _Parser(add_help=False)
    shared.add_argument("--fixture", default=argparse.SUPPRESS)
    shared.add_argument("--repo", default=argparse.SUPPRESS)
    shared.add_argument("--tree", default=argparse.SUPPRESS)
    show = sub.add_parser("show", parents=[shared], add_help=False)
    show.add_argument("pr", nargs="?", type=_number)
    tick = sub.add_parser("tick", parents=[shared], add_help=False)
    tick.add_argument("item")
    tick.add_argument("pr", nargs="?", type=_number)
    tick.add_argument("--dry-run", action="store_true")
    ready = sub.add_parser("ready", parents=[shared], add_help=False)
    ready.add_argument("pr", nargs="?", type=_number)
    ready.add_argument("--dry-run", action="store_true")
    return parser


def _number(text):
    if not re.fullmatch(r"[1-9][0-9]{0,8}", text):
        raise InputError("the pull request number is not a positive integer")
    return int(text)


def main(argv=None, client=None):
    try:
        args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
        tree = Path(args.tree)
        if client is None:
            client, _ = common.make_client(args.fixture, args.repo, tree)
        return {"show": cmd_show, "tick": cmd_tick, "ready": cmd_ready}[args.command](client, args, tree)
    except Refused as refusal:
        print(common.summary(SCRIPT, f"refused: {refusal}"))
        return 1
    except InputError as error:
        print(f"{SCRIPT}: error: {clean(error, 200)}", file=sys.stderr)
        return 2
    except (ApiError, NotFound, NotReadable) as error:
        print(f"{SCRIPT}: error: {clean(error, 200)}", file=sys.stderr)
        return 3
