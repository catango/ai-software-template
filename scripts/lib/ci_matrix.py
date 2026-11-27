#!/usr/bin/env python3
"""Plan of the CI matrix of `.github/workflows/ci.yml`. Standard library only.

  ci_matrix.py plan --manifest FILE --private true|false < config.json

Reads the configuration (config/project.yaml as JSON, made by `yq -o=json`) on stdin and the architectures that the CI
manifest declares. For every configured architecture it picks the runner label for this repository: `runner_private` when the
repository is private, else `runner`. The private flag is the repository's own (`github.event.repository.private`), never a
value of the configuration. An architecture without a label for this repository is planned with run "false" on the base
runner: its job writes "not run: <arch>" and runs nothing else.

Prints the matrix as one line of JSON: {"include": [{"arch": ..., "runner": ..., "run": "true"|"false"}, ...]}. Under GitHub
Actions it also writes `matrix=<json>` to $GITHUB_OUTPUT. Errors name the problem, never a value, and exit 1.
"""
import argparse
import json
import os
import re
import sys
import tomllib

sys.dont_write_bytecode = True

# The runner of the plan and aggregate jobs and of a job that has nothing to run. It names no architecture and no release.
BASE_RUNNER = "ubuntu-latest"
ARCH = re.compile(r"[a-z0-9][a-z0-9_-]{0,19}")
LABEL = re.compile(r"[A-Za-z0-9._-]{1,60}")


class PlanError(Exception):
    pass


def declared_architectures(manifest_path):
    try:
        with open(manifest_path, "rb") as handle:
            table = tomllib.load(handle).get("architectures")
    except (OSError, tomllib.TOMLDecodeError):
        raise PlanError("the CI manifest cannot be read") from None
    if not isinstance(table, dict) or not table:
        raise PlanError("the CI manifest declares no architectures")
    return set(table)


def plan(config, declared, private):
    if private not in ("true", "false"):
        raise PlanError("the private flag of the repository is missing or not true or false")
    entries = config.get("architectures") if isinstance(config, dict) else None
    if not isinstance(entries, list) or not entries:
        raise PlanError("the configuration lists no architectures")
    include, seen = [], set()
    for index, entry in enumerate(entries, 1):
        label = f"architectures entry {index}"
        if not isinstance(entry, dict):
            raise PlanError(f"{label}: must be a table")
        name = entry.get("name")
        if not isinstance(name, str) or not ARCH.fullmatch(name):
            raise PlanError(f"{label}: the name is missing or invalid")
        if name in seen:
            raise PlanError(f"{label}: the name is listed twice")
        seen.add(name)
        if name not in declared:
            raise PlanError(f"{label}: the CI manifest has no artifacts for this architecture")
        for key in ("runner", "runner_private"):
            value = entry.get(key)
            if not isinstance(value, str) or (value != "" and not LABEL.fullmatch(value)):
                raise PlanError(f"{label}: {key} must be a runner label or an empty string")
        chosen = entry["runner_private"] if private == "true" else entry["runner"]
        include.append({"arch": name, "runner": chosen or BASE_RUNNER, "run": "true" if chosen else "false"})
    return {"include": include}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ci_matrix.py")
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--manifest", required=True)
    plan_parser.add_argument("--private", required=True)
    args = parser.parse_args(argv)
    try:
        try:
            config = json.load(sys.stdin)
        except ValueError:
            raise PlanError("the configuration is not one JSON document") from None
        matrix = plan(config, declared_architectures(args.manifest), args.private)
    except PlanError as error:
        print(f"ci_matrix: {error}", file=sys.stderr)
        return 1
    text = json.dumps(matrix, separators=(",", ":"))
    print(text)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"matrix={text}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
