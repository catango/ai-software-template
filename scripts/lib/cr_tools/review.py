"""scripts/review-threads: the review feedback of a pull request, from the approver only.

    review-threads PR [--page N]               the approver's review threads and review summaries, as one line of compact JSON
    review-threads reply THREAD-ID COMMIT      one reply on one such thread, naming the commit that addressed it

The approver is read from `config/project.yaml` of the BASE commit of the pull request (never the working tree): `approver_login` and
the immutable account ID `approver_id`, which this script needs (it refuses when the ID is missing). An item of GitHub is kept ONLY
when its author is the account with that ID (a user, not a bot; the login is not compared, so a renamed approver is kept and a
look-alike or a recycled login is not) and it was not edited by any other account (see `edited_by_another`; the reading is
documented in template-dev/docs/review-feedback.md). Everything else is not printed: only the COUNT of excluded items by reason is
(`look-alike` login, `bot`, `machine` account, `edited` by another account or by an editor that cannot be established, `other`
accounts, and `unread` comments that were not fetched). Text of any other account is data and never reaches the output.

The script refuses a pull request that the machine account did not author and a pull request whose head is not a branch of this
repository (a fork, or a deleted fork). Its output is paged: a page closes at 5 items (threads, then review
summaries) or once 4 KiB is reached, never splits an item, and holds at least one; the items on later pages are named on a `cut:`
line and fetched with `--page`. The approver's text is sanitized (control, bidirectional, and zero-width characters dropped) and never
shortened.

`reply` posts one comment on a thread that the listing would print, saying which commit addressed it (a fixed text with the
40-character SHA, which must be on the head of the pull request). It is the one write of this script. The script resolves nothing,
submits no review, and merges nothing: the approver does that. Exit codes: 0 done, 1 refused, 2 usage, 3 GitHub error. With
--fixture DIR nothing is fetched (see client.py).
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from pr_contract.errors import ApiError, ConfigError, InputError, NotFound, NotReadable
from pr_contract.sanitize import clean

from . import common
from .common import Refused
from .identity import read_identities

SCRIPT = "review-threads"
PAGE_ITEMS = 5
PAGE_BYTES = 4096
EDIT_HISTORY_LIMIT = 100
MAX_PATH = 200
REASONS = ("look-alike", "bot", "machine", "edited", "other", "unread")
STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING")
_THREAD_ID = re.compile(r"PRRT_[A-Za-z0-9_-]{1,90}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_DROP = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
_CONFUSABLE = str.maketrans("01|5", "olls")


def text_clean(value):
    """Text of GitHub for the output: line ends become LF, control, zero-width, bidirectional, and unassigned characters are dropped
    (a tab and a line feed stay: they are layout). Nothing is shortened: the approver's words are never cut."""
    text = str(value).replace("\r\n", "\n")
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in _DROP)


def _levenshtein(a, b):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = row
    return previous[-1]


def looks_like(login, approver_login):
    """True for a login that is the approver's, or one or two characters from it (case, hyphens, and 0/1/5 for o/l/s ignored)."""
    def norm(value):
        return value.lower().translate(_CONFUSABLE).replace("-", "").replace("_", "")
    a, b = norm(login), norm(approver_login)
    return a == b or (len(b) >= 6 and _levenshtein(a, b) <= 2)


def is_approver(actor, identities):
    """The account with the approver's immutable ID. A bot has an ID space of its own, so only a user counts."""
    return actor is not None and actor["type"] == "User" and actor["id"] is not None and actor["id"] == identities.approver_id


def edited_by_another(item, identities):
    """True when the item must be excluded as edited. The strictest sound reading of "not edited by any other account":

    an item never edited has no edit mark at all (no time, no last editor, no creation edit, an empty history). An item with any mark is
    kept only when its time is set, its LAST editor and EVERY editor of the complete history are the approver's account. `editor`
    alone is the last editor only, so the history decides. An unknown history, a revision or last editor that is missing or has no ID, marks
    that contradict each other, a history that is not complete, and one at the limit of 100 edits (the oldest intermediate edits are
    removed by GitHub beyond it) cannot establish who edited, so the item is excluded."""
    history = item["history"]
    if history is None:
        return True
    marked = item["last_edited_at"] is not None or item["editor"] is not None or item["created_edit"] or history["total"] > 0 or bool(history["editors"])
    if not marked:
        return False
    if item["last_edited_at"] is None or not is_approver(item["editor"], identities):
        return True
    if history["more"] or history["total"] == 0 or history["total"] != len(history["editors"]) or history["total"] >= EDIT_HISTORY_LIMIT:
        return True
    return not all(is_approver(editor, identities) for editor in history["editors"])


def verdict(item, identities):
    """None when the item is the approver's and not edited by another account; otherwise the reason it is excluded."""
    author = item["author"]
    if is_approver(author, identities):
        return "edited" if edited_by_another(item, identities) else None
    if author is None:
        return "other"
    if author["type"] == "Bot" or author["login"].lower().endswith("[bot]"):
        return "bot"
    if identities.is_machine(author["login"]):
        return "machine"
    if author["id"] is not None and looks_like(author["login"], identities.approver_login):
        return "look-alike"
    return "other"


def judged_pull(client, number):
    """(pull request, identities) after the checks that decide whether anything is read: the identities of the BASE commit (with the
    approver's ID), the machine account as author, and a head branch of this repository."""
    pull = client.pull(number)
    try:
        identities = read_identities(client.config_text(pull["base_sha"]))
    except ConfigError:
        raise Refused("the identities cannot be read from config/project.yaml of the base commit") from None
    if identities.approver_id is None:
        raise Refused("approver_id is missing in config/project.yaml of the base commit")
    if not identities.is_machine(pull["author"]):
        raise Refused("the pull request is not authored by the machine account")
    own = client.repo.lower()
    if not pull["head_repo"] or not pull["base_repo"] or pull["head_repo"].lower() != own or pull["base_repo"].lower() != own:
        raise Refused("the pull request is from a fork")
    return pull, identities


def collect(client, number, identities):
    """(threads, reviews, excluded): the kept items in listing order (unresolved threads, resolved threads, review summaries) and the counts."""
    excluded = dict.fromkeys(REASONS, 0)
    unresolved, resolved = [], []
    for thread in client.review_threads(number):
        texts = []
        for item in thread["comments"]:
            reason = verdict(item, identities)
            if reason is not None:
                excluded[reason] += 1
            elif text_clean(item["body"]).strip():
                texts.append(text_clean(item["body"]))
        excluded["unread"] += max(0, thread["comments_total"] - len(thread["comments"]))
        if texts:
            entry = {
                "thread": thread["id"], "path": common.plain(thread["path"], MAX_PATH), "line": thread["line"], "outdated": thread["outdated"],
                "resolved": thread["resolved"], "text": "\n\n".join(texts),
            }
            (resolved if thread["resolved"] else unresolved).append(entry)
    reviews = []
    fetched, total = client.reviews(number)
    for item in fetched:
        reason = verdict(item, identities)
        if reason is not None:
            excluded[reason] += 1
        elif text_clean(item["body"]).strip():
            reviews.append({"review": item["id"], "state": item["state"] if item["state"] in STATES else "UNKNOWN", "text": text_clean(item["body"])})
    excluded["unread"] += max(0, total - len(fetched))
    return unresolved + resolved, reviews, excluded


def size_of(item):
    return len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def paginate(items):
    """Pages of (kind, item): a page closes at PAGE_ITEMS items or once PAGE_BYTES is reached, never splits an item, holds at least one."""
    pages, current, used = [], [], 0
    for entry in items:
        current.append(entry)
        used += size_of(entry[1])
        if len(current) >= PAGE_ITEMS or used >= PAGE_BYTES:
            pages.append(current)
            current, used = [], 0
    if current or not pages:
        pages.append(current)
    return pages


def cmd_list(client, args):
    pull, identities = judged_pull(client, args.pr)
    threads, reviews, excluded = collect(client, args.pr, identities)
    pages = paginate([("thread", item) for item in threads] + [("review", item) for item in reviews])
    if args.page > len(pages):
        raise InputError(f"there is no page {args.page}; the output has {len(pages)}")
    shown = pages[args.page - 1]
    out = {
        "pr": pull["number"], "page": args.page, "pages": len(pages),
        "threads": [item for kind, item in shown if kind == "thread"], "reviews": [item for kind, item in shown if kind == "review"],
        "excluded": excluded,
    }
    print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    later = sum(len(page) for page in pages[args.page:])
    if later:
        first, last = args.page + 1, len(pages)
        where = f"page {first}" if first == last else f"pages {first}-{last}"
        print(f"cut: {later} more items on {where} (review-threads {pull['number']} --page {first})")
    count = len(shown)
    print(common.summary(SCRIPT, f"#{pull['number']} page {args.page}/{len(pages)}: {count} items, {sum(excluded.values())} excluded"))
    return 0


def cmd_reply(client, args):
    located = client.thread_pull(args.thread)
    if located is None or located[0].lower() != client.repo.lower():
        raise Refused("not a review thread of this repository")
    pull, identities = judged_pull(client, located[1])
    threads, _, _ = collect(client, located[1], identities)
    if args.thread not in {item["thread"] for item in threads}:
        raise Refused("not a thread that review-threads prints")
    if not client.is_ancestor(args.commit, pull["head_sha"]):
        raise Refused("the commit is not on the pull request head")
    client.reply_to_thread(args.thread, f"Addressed in commit {args.commit}.")
    print(common.summary(SCRIPT, f"replied on a thread of #{pull['number']}: commit {args.commit[:7]}"))
    return 0


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(self.format_usage().strip().replace("\n", " "))


def _number(text):
    if not re.fullmatch(r"[1-9][0-9]{0,8}", text):
        raise InputError("the pull request number or page is not a positive integer")
    return int(text)


def _thread(text):
    if not _THREAD_ID.fullmatch(text):
        raise InputError("the thread id is not a review thread id")
    return text


def _commit(text):
    if not _COMMIT.fullmatch(text):
        raise InputError("the commit must be a full 40-character lowercase hexadecimal SHA")
    return text


def build_parser(reply):
    parser = _Parser(prog=SCRIPT, add_help=False)
    parser.add_argument("--fixture", help="read GitHub data from files in DIR instead of fetching it")
    parser.add_argument("--repo", help="owner/name (default: the origin remote of --tree)")
    parser.add_argument("--tree", default=str(Path(__file__).resolve().parents[3]), help="repository whose origin remote names the repository")
    if reply:
        parser.add_argument("reply")
        parser.add_argument("thread", type=_thread)
        parser.add_argument("commit", type=_commit)
    else:
        parser.add_argument("pr", type=_number)
        parser.add_argument("--page", type=_number, default=1)
    return parser


def main(argv=None, client=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser(bool(argv) and argv[0] == "reply").parse_args(argv)
        if client is None:
            client, _ = common.make_client(args.fixture, args.repo, Path(args.tree))
        return (cmd_reply if hasattr(args, "thread") else cmd_list)(client, args)
    except Refused as refusal:
        print(common.summary(SCRIPT, f"refused: {refusal}"))
        return 1
    except InputError as error:
        print(f"{SCRIPT}: error: {clean(error, 200)}", file=sys.stderr)
        return 2
    except (ApiError, NotFound, NotReadable) as error:
        print(f"{SCRIPT}: error: {clean(error, 200)}", file=sys.stderr)
        return 3
