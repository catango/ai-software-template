"""Structural rules of workflow-policy (rules WP-1 to WP-16). Used by workflow-policy.sh.

The shell turns each workflow into JSON with `yq -o=json` and calls this file in two modes:

  policy CONFIG_JSON                  reads allowed_actions and the runner labels from the parsed configuration and prints
                                      "A <repos>" and "L <labels>" lines (a key that is absent or invalid falls back to the
                                      default); an invalid key is also a finding, and the exit status is 1. The allowed runner
                                      labels are the optional key runner_labels, or else every runner and runner_private of
                                      architectures, plus the base labels of this file (BASE_LABELS).
  scan "<labels>" PATH JSON [PATH JSON]...
                                      the rules WP-10 to WP-13, WP-16, and the `cache:` input of WP-14 on the parsed files.

WP-16 also keeps the required names honest: in ci.yml the aggregate `quality-gate` needs every
other job of the file and runs `if: always()` when there is one, and no required job has a job-level `if:` or a
`continue-on-error`.

Findings are "path:line: WP-n: message". A finding never repeats a value from the workflow, only the rule. Line numbers are
found by looking for the key in the source text of the job and fall back to the line of the job, then to line 1.
"""
import json
import re
import sys

CONFIG = "config/project.yaml"
DEFAULT_ALLOWED = ("actions/checkout",)
# The jobs that only read the configuration or the results of other jobs (the plan and the aggregate of ci.yml) run before
# any configured label is known, so they use this hosted base label. It names no architecture and no operating system release.
BASE_LABELS = ("ubuntu-latest",)
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
LABEL_RE = re.compile(r"[A-Za-z0-9_.-]+")
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
MATRIX_REF = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}")
EXPRESSION = re.compile(r"\$\{\{.*?\}\}", re.S)
# Required check names and the only workflow file that may define each (WP-16).
# The required names that are the aggregate job of their file (it needs every other job of the file and always runs).
AGGREGATES = ("quality-gate", "template-ci")
REQUIRED = {"pr-contract": "pr-contract.yml", "quality-gate": "ci.yml", "template-ci": "template-ci.yml"}
WORKFLOWS = ".github/workflows/"


# ---------------------------------------------------------------- policy

def policy(config_path):
    findings = []
    try:
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError):
        print(f"{CONFIG}:1: policy: the configuration cannot be read as one YAML document; the defaults apply")
        config = None
        findings.append(1)
    if config is not None and not isinstance(config, dict):
        print(f"{CONFIG}:1: policy: the configuration is not a mapping; the defaults apply")
        config = None
        findings.append(1)
    config = config or {}
    allowed = _list(config, "allowed_actions", REPO_RE, DEFAULT_ALLOWED, "WP-4", "owner/repo strings", findings)
    if "runner_labels" in config:
        labels = _list(config, "runner_labels", LABEL_RE, (), "WP-13", "runner label strings", findings)
    else:
        labels = _architecture_labels(config, findings)
    labels = tuple(dict.fromkeys(tuple(labels) + BASE_LABELS))
    print("A " + " ".join(allowed))
    print("L " + " ".join(labels))
    return 1 if findings else 0


def _architecture_labels(config, findings):
    """The runner labels of the architectures list of the configuration (public and private runners)."""
    if "architectures" not in config:
        return ()
    entries = config["architectures"]
    labels = []
    bad = not (isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries))
    if not bad:
        for entry in entries:
            for key in ("runner", "runner_private"):
                value = entry.get(key, "")
                if isinstance(value, str) and (value == "" or LABEL_RE.fullmatch(value)):
                    if value:
                        labels.append(value.lower())
                else:
                    bad = True
    if bad:
        findings.append(1)
        print(f"{CONFIG}:1: WP-13: architectures must be a list of tables with runner label strings; only the base label applies")
        return ()
    return labels


def _list(config, key, pattern, default, rule, shape, findings):
    if key not in config:
        return default
    value = config[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and pattern.fullmatch(item) for item in value):
        print(f"{CONFIG}:1: {rule}: {key} must be a list of {shape}; the default applies")
        findings.append(1)
        return default
    return tuple(item.lower() for item in value)


# ---------------------------------------------------------------- scan

class Source:
    """The text of one file, with the line range of each job."""

    def __init__(self, text):
        self.lines = text.split("\n")
        self.jobs = {}
        self._index_jobs()

    def _index_jobs(self):
        start = None
        job_indent = None
        current = None
        for number, line in enumerate(self.lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if start is None:
                if indent == 0 and re.match(r"jobs:\s*(#.*)?$", stripped):
                    start = number
                continue
            if indent == 0:
                break
            if job_indent is None:
                job_indent = indent
            if indent == job_indent:
                match = re.match(r"""["']?([^\s"':]+)["']?\s*:""", stripped)
                if match:
                    if current:
                        self.jobs[current[0]] = (current[1], number - 1)
                    current = (match.group(1), number)
        if current:
            self.jobs[current[0]] = (current[1], len(self.lines))

    def find(self, job_id, pattern, nth=0):
        """Line of the nth match of PATTERN inside the job (the whole file when job_id is None)."""
        first, last = self.jobs.get(job_id, (1, len(self.lines))) if job_id else (1, len(self.lines))
        hits = [
            number
            for number in range(first, last + 1)
            if re.search(pattern, self.lines[number - 1]) and not self.lines[number - 1].lstrip().startswith("#")
        ]
        if hits:
            return hits[min(nth, len(hits) - 1)]
        return first

    def job_line(self, job_id):
        return self.jobs.get(job_id, (1, 1))[0]


def steps_of(container):
    steps = container.get("steps") if isinstance(container, dict) else None
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def action_repo(step):
    uses = step.get("uses")
    if not isinstance(uses, str):
        return ""
    return "/".join(uses.split("@", 1)[0].lower().split("/")[:2])


def is_false(value):
    return value is False or (isinstance(value, str) and value.strip().lower() == "false")


def check_steps(path, source, job_id, steps, findings):
    checkouts = 0
    caches = 0
    for step in steps:
        repo = action_repo(step)
        with_ = step.get("with") if isinstance(step.get("with"), dict) else {}
        if repo == "actions/checkout":
            if not is_false(with_.get("persist-credentials")):
                findings.append((path, source.find(job_id, r"uses:\s*[\"']?actions/checkout", checkouts), "WP-10",
                                 "actions/checkout needs persist-credentials: false"))
            checkouts += 1
        if repo.startswith("actions/setup-") and "cache" in with_:
            value = with_.get("cache")
            if not (value is None or value == "" or is_false(value)):
                findings.append((path, source.find(job_id, r"^\s*cache:", caches), "WP-14",
                                 "the cache input of an actions/setup-* action is not allowed in v1"))
            caches += 1


def runner_values(job, key):
    """Values a `${{ matrix.KEY }}` can take, or None when the matrix is not a literal."""
    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict):
        return None
    values = []
    wanted = key.lower()
    for name, value in matrix.items():
        if name.lower() == wanted:
            if not isinstance(value, list):
                return None
            values.extend(value)
    include = matrix.get("include", [])
    if isinstance(include, str):
        return None
    for entry in include if isinstance(include, list) else []:
        if isinstance(entry, dict):
            values.extend(value for name, value in entry.items() if name.lower() == wanted)
    return values or None


def planned_matrix(path, job, jobs):
    """True for the one dynamic matrix that WP-13 accepts: the planned matrix of ci.yml.

    The job has `runs-on: ${{ matrix.runner }}` and `matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}`, needs the job `plan`,
    and `plan` is a job of the same file on a base label, without a job-level `if:`, whose output `matrix` is the output of its
    step `plan`. The labels are then chosen at run time by scripts/lib/ci_matrix.py from the architectures of the configuration;
    this check cannot see them. Nothing else may take a runner from a computed matrix.
    """
    if path != WORKFLOWS + "ci.yml":
        return False
    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, str) or re.sub(r"\s+", "", matrix) != "${{fromJSON(needs.plan.outputs.matrix)}}":
        return False
    if "plan" not in _needs(job):
        return False
    plan = jobs.get("plan")
    if not isinstance(plan, dict) or "if" in plan or plan.get("runs-on") not in BASE_LABELS:
        return False
    outputs = plan.get("outputs")
    value = outputs.get("matrix") if isinstance(outputs, dict) else None
    return isinstance(value, str) and re.sub(r"\s+", "", value) == "${{steps.plan.outputs.matrix}}"


def check_runs_on(path, source, job_id, job, labels, findings, jobs=None):
    line = source.find(job_id, r"^\s*runs-on:")

    def bad(message):
        findings.append((path, line, "WP-13", message))

    runs_on = job.get("runs-on")
    if runs_on is None:
        if "uses" not in job:
            bad("the job has no runs-on")
        return
    candidates = []
    if isinstance(runs_on, str):
        candidates = [runs_on]
    elif isinstance(runs_on, list):
        candidates = runs_on
    elif isinstance(runs_on, dict):
        if "group" in runs_on:
            bad("runs-on group: is not allowed (use a label from runner_labels)")
        extra = runs_on.get("labels", [])
        candidates = [extra] if isinstance(extra, str) else extra if isinstance(extra, list) else [None]
    else:
        bad("runs-on is not a label, a list of labels, or a mapping")
        return
    for candidate in candidates:
        if not isinstance(candidate, str):
            bad("runs-on has a value that is not a string")
            continue
        if "${{" in candidate:
            match = MATRIX_REF.fullmatch(candidate.strip())
            values = runner_values(job, match.group(1)) if match else None
            if values is None and match and match.group(1) == "runner" and planned_matrix(path, job, jobs or {}):
                continue
            if values is None:
                bad("runs-on is an expression that cannot be resolved to a literal list (use a literal or a matrix value)")
                continue
            for value in values:
                _label(value, labels, bad)
        else:
            _label(candidate, labels, bad)


def _label(value, labels, bad):
    if not isinstance(value, str):
        bad("runs-on has a matrix value that is not a string")
    elif value.strip().lower() == "self-hosted":
        bad("a self-hosted runner label is not allowed")
    elif value.strip().lower() not in labels:
        bad("runs-on has a label outside runner_labels")


def check_images(path, source, job_id, job, findings):
    line = source.find(job_id, r"^\s*(container|image):")
    images = []
    container = job.get("container")
    if isinstance(container, str):
        images.append(container)
    elif isinstance(container, dict):
        images.append(container.get("image"))
    elif container is not None:
        images.append(None)
    services = job.get("services")
    if isinstance(services, dict):
        for service in services.values():
            images.append(service.get("image") if isinstance(service, dict) else service)
    elif services is not None:
        images.append(None)
    for image in images:
        if not (isinstance(image, str) and DIGEST_RE.search(image.strip())):
            findings.append((path, line, "WP-12", "a container or service image is not pinned by digest (@sha256:)"))


def required_name_findings(path, source, job_id, job, defs, findings):
    name = job.get("name") if isinstance(job.get("name"), str) else job_id
    if "${{" in name:
        parts = EXPRESSION.split(name)
        pattern = ".*".join(re.escape(part) for part in parts)
        for required in REQUIRED:
            if re.fullmatch(pattern, required, re.S):
                findings.append((path, source.find(job_id, r"^\s*name:"), "WP-16",
                                 f"the job name is an expression that can equal the required check name {required}"))
        return
    if name in REQUIRED:
        defs.setdefault(name, []).append((path, source.find(job_id, r"^\s*name:") if "name" in job else source.job_line(job_id)))


def check_required(defs, scanned, findings):
    for name, expected in REQUIRED.items():
        expected_path = WORKFLOWS + expected
        found = defs.get(name, [])
        inside = [item for item in found if item[0] == expected_path]
        for path, line in found:
            if path != expected_path:
                findings.append((path, line, "WP-16", f"the required check name {name} is defined outside {expected_path}"))
        for path, line in inside[1:]:
            findings.append((path, line, "WP-16", f"the required check name {name} is defined by more than one job"))
        if expected_path in scanned and not inside:
            findings.append((expected_path, 1, "WP-16", f"the required check name {name} is not defined by a job of this file"))

JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _is_always(value):
    if not isinstance(value, str):
        return False
    text = value.strip()
    match = re.fullmatch(r"\$\{\{(.*)\}\}", text, re.S)
    return (match.group(1) if match else text).strip() == "always()"


def _needs(job):
    needs = job.get("needs")
    if isinstance(needs, str):
        return {needs}
    return {item for item in needs if isinstance(item, str)} if isinstance(needs, list) else set()


def _job_text(job_id):
    return f"the job {job_id}" if JOB_ID.fullmatch(job_id) else "a job"


def check_required_jobs(path, source, jobs, findings):
    """WP-16 for the file that defines a required name: no skip, no tolerated failure, a complete aggregate."""
    expected = {WORKFLOWS + file: name for name, file in REQUIRED.items()}
    name = expected.get(path)
    if name is None:
        return
    required = [
        job_id for job_id, job in jobs.items()
        if isinstance(job, dict) and (job.get("name") if isinstance(job.get("name"), str) else job_id) == name
    ]
    aggregate = required[0] if name in AGGREGATES and required else None
    others = [job_id for job_id in jobs if job_id != aggregate] if aggregate else []
    for job_id in (required[:1] if aggregate is None else [aggregate] + others):
        job = jobs[job_id]
        if not isinstance(job, dict):
            continue
        coe = job.get("continue-on-error")
        if "continue-on-error" in job and not is_false(coe):
            findings.append((path, source.find(job_id, r"^\s*continue-on-error:"), "WP-16",
                             f"{_job_text(job_id)} has continue-on-error; a required job and a job the aggregate needs must fail the check"))
        if job_id != aggregate and "if" in job:
            findings.append((path, source.find(job_id, r"^\s*if:"), "WP-16",
                             f"{_job_text(job_id)} has a job-level if:; a required job and a job the aggregate needs always run"))
    if aggregate is None:
        return
    job = jobs[aggregate]
    if not isinstance(job, dict):
        return
    if "if" in job and not _is_always(job.get("if")):
        findings.append((path, source.find(aggregate, r"^\s*if:"), "WP-16",
                         f"the aggregate {name} has an if: other than always(); it would be skipped when a needed job fails"))
    if others and "if" not in job:
        findings.append((path, source.job_line(aggregate), "WP-16",
                         f"the aggregate {name} has no if: always(); it would be skipped when a needed job fails"))
    needed = _needs(job)
    for job_id in others:
        if job_id not in needed:
            findings.append((path, source.find(aggregate, r"^\s*needs:"), "WP-16",
                             f"{_job_text(job_id)} of {path.rsplit('/', 1)[-1]} is not in the needs of the aggregate {name}"))


def scan_file(path, data, source, labels, defs, findings):
    if not isinstance(data, dict):
        findings.append((path, 1, "policy", "the file is not a YAML mapping; the structural rules were not checked"))
        return
    if not path.startswith(WORKFLOWS):
        runs = data.get("runs")
        check_steps(path, source, None, steps_of(runs), findings)
        return
    jobs = data.get("jobs")
    if jobs is None:
        return
    if not isinstance(jobs, dict):
        findings.append((path, 1, "policy", "jobs is not a mapping; the structural rules were not checked"))
        return
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        reusable = "uses" in job
        if not reusable:
            timeout = job.get("timeout-minutes")
            if timeout is None or timeout is False or (isinstance(timeout, (int, float)) and timeout <= 0) or timeout == "":
                findings.append((path, source.job_line(job_id), "WP-11", "the job has no timeout-minutes"))
        check_steps(path, source, job_id, steps_of(job), findings)
        check_images(path, source, job_id, job, findings)
        check_runs_on(path, source, job_id, job, labels, findings, jobs)
        required_name_findings(path, source, job_id, job, defs, findings)
    check_required_jobs(path, source, jobs, findings)


def scan(labels, pairs):
    labels = set(labels.lower().split())
    findings = []
    defs = {}
    scanned = set()
    for path, json_path in zip(pairs[0::2], pairs[1::2]):
        try:
            with open(json_path, encoding="utf-8") as handle:
                data = json.load(handle)
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except (OSError, ValueError):
            findings.append((path, 1, "policy", "the file is not one YAML document; the structural rules were not checked"))
            continue
        scanned.add(path)
        scan_file(path, data, Source(text), labels, defs, findings)
    check_required(defs, scanned, findings)
    for path, line, rule, message in sorted(findings, key=lambda item: (item[0], item[1], item[2])):
        print(f"{path}:{line}: {rule}: {message}")
    return 1 if findings else 0


def main(argv):
    if len(argv) == 3 and argv[1] == "policy":
        return policy(argv[2])
    if len(argv) >= 3 and argv[1] == "scan" and len(argv) % 2 == 1:
        return scan(argv[2], argv[3:])
    print("usage: workflow_policy.py policy CONFIG_JSON | scan LABELS PATH JSON [PATH JSON]...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
