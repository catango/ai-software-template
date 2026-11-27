"""Command line: pr-contract --repo OWNER/NAME --pr NUMBER [--fixture DIR] [--templates DIR].

Exit codes: 0 pass, 1 rule violations, 2 usage or input error, 3 API or transient error.
Errors fail closed: an API failure never counts as a pass.
"""
import argparse
import os
import re
import sys
from pathlib import Path

from . import rules
from .api import FixtureClient, GhClient, check_repo
from .errors import ApiError, InputError, LimitExceeded, ReadError
from .sanitize import clean, span

DEFAULT_TEMPLATES = Path(__file__).resolve().parents[2] / ".github" / "PULL_REQUEST_TEMPLATE"
_NUMBER = re.compile(r"[0-9]{1,9}")
USAGE = "usage: pr-contract --repo OWNER/NAME --pr NUMBER [--fixture DIR] [--templates DIR]"


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(USAGE)


def _parse(argv):
    parser = _Parser(prog="pr-contract", add_help=False)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--fixture")
    parser.add_argument("--templates")
    args = parser.parse_args(argv)
    if not _NUMBER.fullmatch(args.pr) or int(args.pr) < 1:
        raise InputError("invalid --pr (expected a positive integer)")
    return check_repo(args.repo), int(args.pr), args


def _write_summary(label, number, violations):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    result = "pass" if not violations else f"{len(violations)} violation{'s' if len(violations) != 1 else ''}"
    lines = ["### pr-contract", "", f"PR #{number} (type: {label}): **{result}**", ""]
    if violations:
        lines += ["| Rule | Finding |", "|---|---|"]
        lines += [f"| {v.rule} | `{span(v.message, 160)}` |" for v in violations]
        lines.append("")
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        print("pr-contract: warning: job summary not written", file=sys.stderr)


def main(argv=None, client=None):
    try:
        repo, number, args = _parse(sys.argv[1:] if argv is None else argv)
        templates = Path(args.templates) if args.templates else DEFAULT_TEMPLATES
        if not templates.is_dir():
            raise InputError("templates directory not found")
        if client is None:
            client = FixtureClient(args.fixture, repo) if args.fixture else GhClient(repo)
        pr = client.pull(number)
        violations = rules.evaluate(pr, client, templates)
    except InputError as error:
        print(f"pr-contract: error: {clean(error, 200)}", file=sys.stderr)
        return 2
    except (ApiError, ReadError, LimitExceeded) as error:
        print(f"pr-contract: error: {clean(error, 200)}", file=sys.stderr)
        return 3
    label = rules.classify(pr)
    print(f"pr-contract: PR #{pr.number} (type: {label})")
    for violation in violations:
        print(f"FAIL {violation.rule}: {clean(violation.message, 200)}")
    if violations:
        print(f"pr-contract: {len(violations)} violation{'s' if len(violations) != 1 else ''}")
    else:
        print("pr-contract: pass")
    _write_summary(label, pr.number, violations)
    return 1 if violations else 0
