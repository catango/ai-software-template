"""Rules PC-1 to PC-9 (the PR contract) and the dispatch of TC-1 and TC-2 (template_rules.py).

Messages are fixed text plus sanitized paths. Which PR types exist, which sections a type needs, and every policy input
(`design_repos`, `operator_paths`, `architectures`, the manifest) come from files of the BASE commit.
"""
import re
from dataclasses import dataclass
from datetime import datetime

from . import config, markdown
from .api import MAX_FILES
from .errors import ApiError, ConfigError, InputError, LimitExceeded, NearMissKey, NotFound, NotReadable
from .sanitize import clean

PATH_CAP = 10
_VERSION = r"v([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})"
BRANCHES = (
    (re.compile(r"design/CR-([0-9]{1,9})-[^\s]+"), "design"),
    (re.compile(r"impl/CR-([0-9]{1,9})-[^\s]+"), "implementation"),
    (re.compile(r"change/[^\s]+"), "change"),
    (re.compile(rf"template-sync/{_VERSION}"), "template-sync"),
    (re.compile(rf"init/{_VERSION}"), "init"),
)
# Approved records under a record root (`workflow.docs_roots`): a CR's design and acceptance criteria, and every ADR. The owned
# templates (`cr/_template/`, `adr/_template.md`) are files of the template, not records.
RECORD = re.compile(r"cr/(?!_template/)[^/]+/(design|acceptance-criteria)\.md")
ADR_TEMPLATE = "adr/_template.md"
WORKFLOW_PREFIXES = (".github/workflows/", ".github/actions/")
JUSTIFICATION = "Workflow change justification"
OPERATOR_REVIEW = "Operator script review"
OPERATOR_KEY_MESSAGE = "operator_paths missing or invalid in the base configuration"
DOCS_ROOTS_MESSAGE = "docs_roots missing or invalid in the base configuration"
DESIGN_REPOS_MESSAGE = "design_repos missing or invalid in the base configuration"

MANIFEST_NAMES = frozenset({
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt",
    "Pipfile", "Pipfile.lock", "pyproject.toml", "poetry.lock", "uv.lock", "go.mod", "go.sum", "Cargo.toml",
    "Cargo.lock", "Gemfile", "Gemfile.lock", "composer.json", "composer.lock", ".terraform.lock.hcl",
})
MANIFEST_PATHS = frozenset({"toolchain/python/ci.lock"})
_REQUIREMENTS = re.compile(r"requirements[A-Za-z0-9_.-]*\.txt")
_USES_LINE = re.compile(r"\s*(?:-\s+)?uses:\s+\S+(?:\s+#.*)?")
MAX_PATCH_LINE = 500

# PC-9: one line per architecture without a runner, in the section "Architecture evidence".
EVIDENCE_SECTION = "Architecture evidence"
GATE_COMMAND = "./scripts/quality-gate.sh --strict"
_HEX40 = re.compile(r"[0-9a-f]{40}")
EVIDENCE = re.compile(
    r"evidence: (?P<arch>[a-z0-9][a-z0-9_-]{0,19}) \| (?P<machine>[A-Za-z0-9_.-]{1,32}) \| "
    r"(?P<os>[a-z0-9._-]{1,32} [A-Za-z0-9._+~-]{1,32}) \| (?P<sha>[0-9a-f]{40}) \| (?P<state>clean|dirty) \| "
    r"(?P<command>[^|\n]{1,100}) \| exit (?P<code>[0-9]{1,3}) \| (?P<time>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)")


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str


def classify(pr):
    """The PR type used in the report: a template type, 'dependabot', or 'unknown'."""
    if is_dependabot(pr):
        return "dependabot"
    return derive_type(pr.head_ref)[0] or "unknown"


def is_dependabot(pr):
    return pr.author == "dependabot[bot]" and pr.head_ref.startswith("dependabot/")


def derive_type(head_ref):
    """(kind, number): the PR type of a branch name, and the CR number of a design or implementation branch."""
    if len(head_ref) <= 255:
        for pattern, kind in BRANCHES:
            match = pattern.fullmatch(head_ref)
            if match:
                return kind, int(match.group(1)) if kind in ("design", "implementation") else None
    return None, None


def branch_version(head_ref):
    """The version of a template-sync or init branch as 'MAJOR.MINOR.PATCH', else None."""
    if len(head_ref) <= 255:
        for pattern, kind in BRANCHES[3:]:
            match = pattern.fullmatch(head_ref)
            if match:
                return ".".join(match.groups())
    return None


def template_sections(kind, templates_dir):
    """{heading: text} of the template of a PR type, from the directory of the checkout that runs the script."""
    try:
        text = (templates_dir / f"{kind}.md").read_text(encoding="utf-8")
    except OSError:
        raise InputError("PR template not found") from None
    return markdown.parse(text).sections


def template_headings(kind, templates_dir):
    return list(template_sections(kind, templates_dir))


def hinted(message, error):
    """The fixed message of a rule, with the misspelled key when the configuration has one (sanitized, short)."""
    if isinstance(error, NearMissKey):
        return f"{message} (key {clean(error.key, 40)} looks like a misspelling)"
    return message


class BaseConfig:
    """The parsed configuration of the base commit, read at most once per evaluation: PC-3, PC-8 and PC-9 take their policy from
    it. An unreadable file (ConfigError) is remembered and raised for each rule, so each fails closed; an ApiError is not
    caught."""

    def __init__(self, client, sha):
        self._client, self._sha, self._done, self._value = client, sha, False, None

    def get(self):
        if not self._done:
            try:
                self._value = self._client.base_config(self._sha)
            except ConfigError as error:
                self._value = error
            self._done = True
        if isinstance(self._value, ConfigError):
            raise self._value
        return self._value


def check_operator_paths(files, parsed, base):
    """PC-8: a path under `operator_paths` needs the non-empty section. Deleted paths and both sides of a rename count. A missing
    or malformed key is a violation (we cannot tell what is an operator path); an explicit empty value makes the rule inactive."""
    try:
        entries = config.operator_paths(base.get())
    except ConfigError as error:
        return [Violation("PC-8", hinted(OPERATOR_KEY_MESSAGE, error))]
    if not any(config.is_operator_path(path, entries) for path in touched(files)):
        return []
    review = parsed.sections.get(OPERATOR_REVIEW, "") if parsed is not None else ""
    if markdown.has_content(review):
        return []
    return [Violation("PC-8", f"operator change without a non-empty '{OPERATOR_REVIEW}' section")]


def touched(files):
    for item in files:
        yield item.path
        if item.previous:
            yield item.previous


def under_roots(path, roots):
    return any(path.startswith(root) for root in roots)


def is_approved_record(path, roots):
    for root in roots:
        if path.startswith(root):
            rest = path[len(root):]
            if RECORD.fullmatch(rest) or (rest.startswith("adr/") and rest != ADR_TEMPLATE):
                return True
    return False


def capped(rule, template, paths):
    out = [Violation(rule, template.format(clean(path))) for path in paths[:PATH_CAP]]
    if len(paths) > PATH_CAP:
        out.append(Violation(rule, f"and {len(paths) - PATH_CAP} more paths"))
    return out


def evaluate(pr, client, templates_dir):
    from . import template_rules

    dependabot = is_dependabot(pr)
    base = BaseConfig(client, pr.base_sha)
    kind, branch_number = (None, None) if dependabot else derive_type(pr.head_ref)
    violations = []

    files = None
    if pr.changed_files < MAX_FILES:
        try:
            files = client.pull_files(pr.number)
        except LimitExceeded:
            files = None
    if files is None:
        violations.append(Violation("PC-*", "too many changed files to verify (3000 or more)"))
    if dependabot:
        return violations + (check_dependabot(files) if files is not None else [])

    parsed = None
    if len(pr.body) > markdown.MAX_BODY or pr.body.count("\n") > markdown.MAX_LINES:
        violations.append(Violation("PC-1", "body too large to check"))
    else:
        parsed = markdown.parse(pr.body)

    if kind is None:
        violations.append(Violation("PC-2", "branch prefix not recognised"))
    if parsed is not None:
        if parsed.marker is None:
            violations.append(Violation("PC-2", "PR type marker missing"))
        elif kind is not None and parsed.marker != kind:
            violations.append(Violation("PC-2", f"marker type {clean(parsed.marker, 20)} does not match branch type {kind}"))
        if kind is not None:
            violations.extend(check_sections(kind, parsed, templates_dir))
        if kind == "design":
            violations.extend(check_design_reference(parsed, branch_number))
        if kind == "implementation":
            violations.extend(check_implementation(pr, parsed, branch_number, client, base))
        if kind in ("implementation", "change"):
            violations.extend(check_evidence(pr, parsed, client, base))

    if files is not None:
        if kind in ("design", "implementation", "change"):
            rule = "PC-4" if kind == "design" else "PC-5"
            try:
                roots = config.docs_roots(base.get())
            except ConfigError as error:
                violations.append(Violation(rule, hinted(DOCS_ROOTS_MESSAGE, error)))
            else:
                if kind == "design":
                    violations.extend(capped("PC-4", "path outside the record roots: {}", [p for p in touched(files) if not under_roots(p, roots)]))
                else:
                    violations.extend(capped("PC-5", "approved file changed: {}", [p for p in touched(files) if is_approved_record(p, roots)]))
        if any(p.startswith(WORKFLOW_PREFIXES) for p in touched(files)):
            justification = parsed.sections.get(JUSTIFICATION, "") if parsed is not None else ""
            if not markdown.has_content(justification):
                violations.append(Violation("PC-6", f"workflow change without a non-empty '{JUSTIFICATION}' section"))
        violations.extend(check_operator_paths(files, parsed, base))
        if kind == "template-sync":
            violations.extend(template_rules.check_template_sync(pr, files, client, parsed))
        if kind == "init":
            violations.extend(template_rules.check_init(pr, files, client))
    return violations


def check_sections(kind, parsed, templates_dir):
    """PC-1. A section is filled in when it has content and is not the template's own text: the unchecked default items of a
    Checkpoint are what the template ships, not what the author wrote. Ticking a box, or changing, adding or removing an item,
    makes it the author's."""
    out = []
    for heading, shipped in template_sections(kind, templates_dir).items():
        if heading not in parsed.sections:
            out.append(Violation("PC-1", f"section missing: {clean(heading)}"))
        elif not markdown.has_content(parsed.sections[heading]):
            out.append(Violation("PC-1", f"section empty: {clean(heading)}"))
        elif markdown.has_content(shipped) and markdown.same_text(parsed.sections[heading], shipped):
            out.append(Violation("PC-1", f"section is the unedited template text: {clean(heading)}"))
    return out


def check_design_reference(parsed, branch_number):
    refs = [r for r in markdown.references(parsed.sections.get("Change request", "")) if r.repo is None]
    if branch_number is None:
        return []
    if not refs:
        return [Violation("PC-2", "issue reference missing in the Change request section")]
    if refs[0].number != branch_number:
        return [Violation("PC-2", "branch CR number does not match the issue in the Change request section")]
    return []


def _label(ref):
    return f"{ref.repo}#{ref.number}" if ref.repo else f"#{ref.number}"


def _same_repo(own, ref):
    return ref.repo is None or ref.repo.lower() == own.lower()


def check_implementation(pr, parsed, branch_number, client, base):
    """PC-3. A reference is `#<n>` (this repository) or `<owner>/<repo>#<n>` in a repository listed under `design_repos` of the
    base configuration. The issue and the design PR must be in the same repository. A listed repository that cannot be read
    fails the check."""
    out = []
    issue, design = parsed.implements, parsed.design
    if issue is None:
        out.append(Violation("PC-3", "Implements: #<issue> reference missing"))
    elif branch_number is not None and branch_number != issue.number:
        out.append(Violation("PC-2", "branch CR number does not match Implements: issue"))
    if design is None:
        out.append(Violation("PC-3", "Design: #<design PR> reference missing"))
    if issue is None or design is None:
        return out
    own = client.repo
    issue_repo = own if _same_repo(own, issue) else issue.repo
    design_repo = own if _same_repo(own, design) else design.repo
    if issue_repo.lower() != design_repo.lower():
        return out + [Violation("PC-3", "Implements and Design are in different repositories")]
    foreign = design_repo.lower() != own.lower()
    if foreign:
        try:
            listed = config.design_repos(base.get())
        except ConfigError as error:
            return out + [Violation("PC-3", hinted(DESIGN_REPOS_MESSAGE, error))]
        if design_repo.lower() not in {entry.lower() for entry in listed}:
            return out + [Violation("PC-3", f"repository {clean(design_repo)} is not listed in design_repos")]
        try:
            client.repo_readable(design_repo)
        except (NotFound, NotReadable):
            return out + [Violation("PC-3", f"repository {clean(design_repo)} cannot be read")]
    where = design_repo if foreign else None
    try:
        if client.issue_kind(issue.number, where) != "issue":
            out.append(Violation("PC-3", f"{_label(issue)} is a pull request, not an issue"))
    except NotFound:
        out.append(Violation("PC-3", f"issue {_label(issue)} not found"))
    except NotReadable:
        out.append(Violation("PC-3", f"issue {_label(issue)} cannot be read"))
    out.extend(check_design_pr(pr, issue.number, design.number, client, where, design_repo))
    return out


def check_design_pr(pr, issue, design, client, where, design_repo):
    if where is None and design == pr.number:
        return [Violation("PC-3", "Design reference points to this PR")]
    label = f"{where}#{design}" if where else f"#{design}"
    try:
        design_pr = client.pull(design, where)
    except NotFound:
        return [Violation("PC-3", f"design PR {label} not found")]
    except NotReadable:
        return [Violation("PC-3", f"design PR {label} cannot be read")]
    out = []
    if not design_pr.merged:
        out.append(Violation("PC-3", f"design PR {label} is not merged"))
    if where is None and design_pr.base_ref != pr.base_ref:
        out.append(Violation("PC-3", f"design PR {label} has a different base branch"))
    match = re.fullmatch(r"design/CR-([0-9]{1,9})-[^\s]+", design_pr.head_ref) if len(design_pr.head_ref) <= 255 else None
    if not match or int(match.group(1)) != issue:
        out.append(Violation("PC-3", f"design PR {label} is not a design branch for CR {issue}"))
    body = design_pr.body
    linked = False
    if len(body) <= markdown.MAX_BODY and body.count("\n") <= markdown.MAX_LINES:
        for ref in markdown.references(markdown.parse(body).sections.get("Change request", "")):
            if ref.number == issue and (ref.repo is None or ref.repo.lower() == design_repo.lower()):
                linked = True
    if not linked:
        out.append(Violation("PC-3", f"design PR {label} does not reference issue {issue} in its Change request section"))
    return out


def evidence_lines(section):
    """(valid matches, count of malformed) for the lines of a section that start with `evidence:`."""
    good, bad = [], 0
    for line in section.split("\n"):
        line = line.strip()
        if not line.startswith("evidence:"):
            continue
        match = EVIDENCE.fullmatch(line)
        if match and _valid_time(match.group("time")):
            good.append(match)
        else:
            bad += 1
    return good, bad


def _valid_time(text):
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def check_evidence(pr, parsed, client, base=None):
    """PC-9. Which architectures need a line comes from the configuration of the BASE commit and the repository's private flag;
    the head of the PR is never consulted for policy."""
    base = base or BaseConfig(client, pr.base_sha)
    try:
        candidates = [a for a in config.architectures(base.get()) if not (a.runner and a.runner_private)]
    except ConfigError as error:
        return [Violation("PC-9", hinted("architectures in the base configuration is missing or invalid", error))]
    if not candidates:
        return []
    private = client.repo_private()
    required = [a.name for a in candidates if not a.runner_for(private)]
    if not required:
        return []
    if not _HEX40.fullmatch(pr.head_sha):
        raise ApiError("malformed response")
    lines, malformed = evidence_lines(parsed.sections.get(EVIDENCE_SECTION, ""))
    out = []
    out.extend(Violation("PC-9", "malformed evidence line") for _ in range(min(malformed, PATH_CAP)))
    for arch in required:
        mine = [m for m in lines if m.group("arch") == arch]
        if not mine:
            out.append(Violation("PC-9", f"no evidence line for architecture {arch}"))
        elif len(mine) > 1:
            out.append(Violation("PC-9", f"more than one evidence line for architecture {arch}"))
        else:
            out.extend(check_line(arch, mine[0], pr.head_sha))
    return out


def check_line(arch, match, head_sha):
    out = []
    if match.group("sha") != head_sha:
        out.append(Violation("PC-9", f"evidence for {arch}: SHA is not the head of the PR"))
    if match.group("state") != "clean":
        out.append(Violation("PC-9", f"evidence for {arch}: working tree is not clean"))
    if match.group("command") != GATE_COMMAND:
        out.append(Violation("PC-9", f"evidence for {arch}: command is not the quality gate"))
    if int(match.group("code")) != 0:
        out.append(Violation("PC-9", f"evidence for {arch}: exit status is not 0"))
    return out


def allowed_manifest(path):
    name = path.rsplit("/", 1)[-1]
    return path in MANIFEST_PATHS or name in MANIFEST_NAMES or bool(_REQUIREMENTS.fullmatch(name))


_USES_NAME = re.compile(r"\s*(?:-\s+)?uses:\s+(\S+)")


def uses_name(line):
    """The action of a `uses:` line: the reference up to the `@` (a bump changes what follows it, never this)."""
    return _USES_NAME.match(line).group(1).split("@", 1)[0]


def uses_only_patch(patch):
    """True when every changed line is a `uses:` line and the removed and added lines name the same actions in the same order:
    a bump of a pin, never a different action, a new one, or a removed one."""
    removed, added = [], []
    for line in patch.split("\n"):
        if len(line) > MAX_PATCH_LINE:
            return False
        if line == "" or line.startswith((" ", "@@", "\\")):
            continue
        if line[0] not in "+-" or not _USES_LINE.fullmatch(line[1:]):
            return False
        (removed if line[0] == "-" else added).append(uses_name(line[1:]))
    return bool(removed) and removed == added


def check_dependabot(files):
    if not files:
        return [Violation("PC-7", "no changed files to verify")]
    out = []
    for item in files:
        paths = [item.path] + ([item.previous] if item.previous else [])
        if all(allowed_manifest(p) for p in paths):
            continue
        is_workflow = item.path.startswith(".github/workflows/") and item.path.endswith((".yml", ".yaml"))
        if not is_workflow or item.previous:
            out.append(Violation("PC-7", f"path not in the dependency allowlist: {clean(item.path)}"))
        elif item.status != "modified":
            out.append(Violation("PC-7", f"workflow file added or removed: {clean(item.path)}"))
        elif item.patch is None:
            out.append(Violation("PC-7", f"patch unavailable for {clean(item.path)}"))
        elif not uses_only_patch(item.patch):
            out.append(Violation("PC-7", f"workflow change is not limited to uses: lines: {clean(item.path)}"))
    return out
