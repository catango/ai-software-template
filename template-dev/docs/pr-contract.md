# pr-contract (plan row 3)

An implementation note for plan row 3 (`pr-contract`) of the software template design. It records what the row built, how the
seeded code differs from its origin, what it leaves to later rows, and the points where the design, the security baseline, and the
code disagree. It changes no decision. The seed commit is the second entry of `template-dev/provenance.toml`.

## What the row adds

| Area | Files | Class |
|---|---|---|
| Checker | `scripts/pr-contract`, `scripts/pr_contract/` (`api`, `cli`, `config`, `markdown`, `model`, `rules`, `template_rules`, `sanitize`, `errors`) | owned |
| PR body | `scripts/pr-body`, `.github/PULL_REQUEST_TEMPLATE/{design,implementation,change,template-sync,init}.md` | owned |
| Evidence | `scripts/gate-evidence` (the producer of the PC-9 line) | owned |
| Workflow | `.github/workflows/pr-contract.yml` (job `pr-contract`, the required name) | owned |
| Configuration | `config/project.yaml` of the template gains `github`, and `workflow.design_repos` and `workflow.operator_paths` | seeded |
| Tests | `template-dev/tests/pr_contract/` (unit tests, 34 fixture cases under `fixtures/cases/`), `template-dev/run-tests.sh skills` | template-only |
| CI of the template | job `skills` in `.github/workflows/template-ci.yml`, added to the aggregate `template-ci` | template-only |

The manifest needed no new pattern: every new file already had exactly one class. The leak scan gained one narrow exemption (see
point 8 below).

## The rules

| Rule | Where it is decided | Source of the policy |
|---|---|---|
| PC-1 sections, PC-2 type and prefix | `rules.py` | the template files of the checkout that runs the script (the base) |
| PC-3 references | `rules.py` | `workflow.design_repos` of the base commit; the API for the PRs |
| PC-4, PC-5 record roots | `rules.py` | `workflow.docs_roots` of the base commit (default `docs/`); an unreadable key fails |
| PC-6, PC-7 | `rules.py` | fixed in the code (workflow section, dependency files and `uses:` bumps of one action) |
| PC-8 operator paths | `rules.py` | `workflow.operator_paths` of the base commit; `[]` makes it inactive; a missing or malformed key fails |
| PC-9 evidence | `rules.py` | `architectures` of the base commit, the repository's private flag from the API; no default |
| TC-1 template-sync, TC-2 init | `template_rules.py` | `.template/manifest.toml` of the base commit (and of the head for added paths of a sync); `.template/state.toml` |

The types are `design`, `implementation`, `change`, `template-sync` (`template-sync/v<MAJOR>.<MINOR>.<PATCH>`), `init`
(`init/v<MAJOR>.<MINOR>.<PATCH>`), and Dependabot. No other file of the head is read: the configuration, the manifest, and the
templates all come from the base commit, so a PR cannot widen the policy that judges it. A `head/config/project.yaml` that widens
every key sits in each fixture case and is never read.

## How the seeded code was changed

- Comments name no record of the origin repository; messages and rule ids (`PC-1` to `PC-9`) stay.
- The origin's types `infra` and `toolchain`, its operator paths, its manifest paths, and its default architecture are gone: the
  generic code holds no platform reference, host, or fixed architecture. `MANIFEST_PATHS` (PC-7) names the template's own lock file.
- The HTTP client is replaced by `gh api` with argument arrays (`api.GhClient`): no shell, a filtered environment, a timeout, a
  size cap, every endpoint written out in the call (the gate check `forbidden-api` reads them). A 404, 401, or 403 is "not
  readable"; a rate limit or any other failure is an error (exit 3), never a pass.
- The configuration reader reads the nested form that `initialize-project` writes (`workflow:` with the two lists,
  `architectures:`), not the flat keys of the origin. It is strict inside those keys and skips every other key.
- PC-3 takes qualified references. PC-8 and PC-9 read the base configuration as before. PC-9 has no default for a missing
  `architectures` key: it fails closed.
- `gate-evidence` takes the `uname -m` values of an architecture from `[architectures]` of `toolchain/ci-manifest.toml`, so the
  script names no architecture.
- `--fixture DIR` replaces `--fixtures`; the fixture layout has a `base/` and a `head/` tree and `repos/<owner>/<name>/` for other
  repositories.
- The tests of the origin were ported (rules, markdown, hostile input, the API client, the CLI, PC-9, PC-8 from configuration,
  the workflow, the policy commands, `gate-evidence`). The two tests of the configuration reader were rewritten for the nested form.
  New: PC-3 with qualified references, TC-1, TC-2, the case fixtures.

## The workflow

`pr-contract.yml` runs on `pull_request` with read scopes only and no secret. It checks out the base commit and the head commit
(in `head/`, read-only while the checks run, no persisted credentials), runs `scripts/pr-contract` from the base with the token
as `GH_TOKEN` for `gh`, then runs the policy checks of the base commit against the head files with `GATE_BASE_REF` set. A checker
missing on the base commit is a failure; there is no bootstrap skip. Six checks run: `workflow-policy`,
`no-claude-in-workflows`, `forbidden-api`, and, since the follow-ups below, `operator-contract`, `cr-layout` and `owned-drift`.
`dependabot-config` (created by the seeding PR) joins the list in a separate PR after that one is merged: a checker missing on the
base commit fails the job.

## Left to later rows

- The policy check `dependabot-config`: the seeding PR created it, and it joins the list in `pr-contract.yml` in a separate PR after
  that one is merged. (`owned-drift` joined it in the follow-up below.)
- The private-repository checklist of the PR templates (CR-43 S-16). The `codeowners` check exists since the seeding PR.
- The dry-run step that runs these cases against a generated tree (row 9): `PR_CONTRACT_TREE=<tree> template-dev/run-tests.sh skills`
  already does it for `test_cases.py`, and a run against a generated tree passed while this row was written.
- A real run on GitHub: nothing here proves the `gh api` calls against the live API, the read-only token reading a design
  repository, or the workflow itself. Only CI and the real run of design section 12 (b) can.

## Points for the approver

1. **TC-1 and a new seeded file. Decided 2026-11-27 (option B), see "TC-1 and TC-2 after the decisions of 2026-11-27" below.**
   Design section 4 (step 3) said a sync adds a seeded file that is new in the tag and absent locally, while TC-1 allowed only new
   `owned` paths. The approver chose "owned or seeded".
2. **PC-3 across repositories.** The issue and the design PR must be in the same repository (the design PR's "Change request"
   links an issue of its own repository), and the base-branch comparison applies to this repository only. The text of PC-3 does
   not say either; both follow from "linked to the same issue".
3. **PC-9 has no default.** The origin treated a missing `architectures` key as one architecture with a runner. The template
   fails closed, because the configuration schema requires the key. The architecture name pattern of PC-9 and `gate-evidence` is
   now the schema's (`[a-z0-9][a-z0-9_-]{0,19}`); the origin's did not allow a hyphen, which the schema does.
4. **The check fails on its own introducing PR.** With no bootstrap skip (ADR-009 decision 14), the first PR that adds
   `pr-contract.yml` has a base commit without the checker, so the job fails with "missing on the base commit". The approver
   merges on judgement; the check is not required until the user makes it so.
5. **A private design repository.** The read-only token of a pull request run is scoped to this repository. A listed design
   repository that is private to another owner cannot be read, and PC-3 fails closed (verification item V3-4 of ADR-003 is not
   closed by this row; it needs a real run).
6. **Scripts without a GitHub call.** `scripts/pr-body` and `scripts/gate-evidence` make no call, so they carry no `--offline`
   flag (T-13 says every script has `--fixture` or `--offline`). `scripts/pr-contract` has `--fixture`.
7. **Version of an init PR.** TC-2 does not compare the branch version of an init PR with anything; TC-1 compares the branch
   version of a sync PR with the version in `.template/state.toml` of the head commit. For a PR from a fork the head file is read
   by commit SHA from this repository, which GitHub should serve for a fork commit; that is not verified.
8. **Leak scan exemption.** The template's own `config/project.yaml` lists the repository of its design records under
   `workflow.design_repos` (PC-3 needs it for the template's own implementation PRs). The payload scan skips the pattern
   `platform-repository-name` for that one file; every other pattern still applies, and a generated tree gets no exemption.
9. **Identities.** The template's own `config/project.yaml` carries the approver's login and account ID and the machine account's
   login. They were taken from this repository's merged pull request data (the author and the merger of the previous row's PR),
   and the approver is to confirm them. `pr-contract` itself reads none of them; later rows (`review-threads`, `codeowners`,
   `template-sync`) do.
10. **The job `skills` of `template-ci`** is added here (the plan lists it with row 9) because T-8 names it as the evidence and
    the tests need a job to run in. Row 9 adds the other jobs.

## Follow-up after the review of the first merge

A review of the merged row found seven points. Each was checked against the code before it was changed.

| # | Finding | Verdict | Change |
|---|---|---|---|
| 1 | PC-5 protected `docs/cr/_template/*.md` and `docs/adr/_template.md`, which plan row 5 must add | Real | The owned templates are not approved records (`rules.RECORD`, `rules.ADR_TEMPLATE`); a real record next to them is still protected |
| 2 | PC-4 and PC-5 hard-coded `docs/`, so the template's own records under `template-dev/docs/` failed PC-4 and were unprotected under PC-5 | Real | New key `workflow.docs_roots` (default `docs/`), read from the base commit, in the reader, the schema, `initialize-project`, and the template's own configuration (`docs/` and `template-dev/docs/`). A malformed key fails closed |
| 3 | The reader accepted a narrower `operator_paths` than the schema | Real | The schema field now has the reader's form (`item_pattern`, `max_length`); a test compares the schema and the reader on every string of a small alphabet up to length 5, and a full configuration round-trips |
| 4 | A 401 or 403 counted as "file absent" in `file_text` (TC-2 passed on a refused read) | Real | `errors.NotReadable`: only a 404 returns `None`; a refused read is an error (exit 3) |
| 5 | TC-1 and a new seeded file disagree with design section 4 | A design question | Decided 2026-11-27 (option B) and implemented, see the section below |
| 6 | PC-7 accepted any `uses:` change, including another action | Real | The removed and added lines must name the same actions in order; a new, removed, swapped or different action fails |
| 7 | One punctuation mark, or a bare checklist marker, satisfied the non-empty rule | Real for punctuation and markers; not for checklist text | List, quote and checkbox markers do not count and at least one letter or digit is needed. An item with text still counts, because the Checkpoint section of every template is a checklist by design; an untouched template checklist cannot be told from a real one |

The first row of the table needed no new manifest pattern. The configuration of a generated repository now carries `docs_roots`.

## Second follow-up (review of the first follow-up)

| # | Finding | Verdict | Change |
|---|---|---|---|
| 1 | A misspelled `workflow` key (`docs_root:`) was skipped, so an absent `docs_roots` meant the default and a typo silently dropped the protection of `template-dev/docs/` | Real | A key within two edits of a policy key (or equal to one ignoring case and hyphens) is an error, `NearMissKey`, at the top level (`workflows`, `architecture`) and in `workflow`. The violation of every rule that reads the configuration names the key. Keys far from the three are the project's own and are still skipped (a project may keep `merge_method` there) |
| 2 | `NotReadable` was a subclass of `NotFound`, so a future `except NotFound` would catch a refused read | Real (a hazard, not a bug today) | Both derive from a new `ReadError`; the handlers that mean "cannot be read" name both, the CLI catches `ReadError`; a test fails if a refused read is caught as not found |
| 3 | A Checkpoint left exactly as the template ships it passed PC-1 | Real | Chosen: a section equal to the template's own text (blank lines and trailing spaces ignored, case, order, and box state compared) counts as not filled in, against the template of the base checkout: "section is the unedited template text". Ticking one box, or editing, adding, removing or reordering an item, makes it the author's, so a change PR that keeps the three default items and ticks them passes |

The choice for 3 is a reading of "non-empty" in PC-1, not a new rule: a draft PR opened from `scripts/pr-body` with the other
sections filled in but the checkpoint untouched is flagged until the first item is ticked.

## TC-1 and TC-2 after the decisions of 2026-11-27

The approver decided two design questions (design section 4 and section 13, Q15 and Q16; acceptance criteria T-9 and T-20). The
code is in `template_rules.py`; the tests are `TC1SeededAdditions` and `TC2Settings` in `test_template_rules.py`, the cases
`template-sync-*` and `init-*` under `fixtures/cases/`, and `InitPullRequestTC2` in `template-dev/tests/test_instructions.py`.

### TC-1: a sync may add a new seeded path (option B)

A template-sync PR may add a path that is `owned` or `seeded` in the **head** manifest (the new version is the only place that knows
its new files). For an added path that is seeded there:

1. it must be **absent from the base commit** (`file_text` at the base SHA is `None`); otherwise the finding is "added seeded path
   exists on the base commit: `<path>`";
2. it must be **listed in the PR body**, in the section "Changed files by class". A path counts as listed when it appears in the
   text of that section as a whole token, in any form (list item, code span, table cell, fenced block). A longer path or name that
   contains it (`<path>.bak`, `x<path>`, `<path>x`, a different case) does not count; the characters next to the path decide: a path
   character (`/`, `.`, `-`, `_`), or a letter, digit or combining mark of any script (`é<path>`, `<path>é`), makes it part of
   something else, while a space or punctuation of any script (`<path>。`, `「<path>」`) does not. Text in an HTML comment or in
   another section does not count. The finding is "added seeded path is not listed under 'Changed files by class': `<path>`".

Everything else is unchanged: changing, removing or renaming a path that is seeded in the base manifest is a violation ("path is not
owned in the base manifest"), as are template-only and unclassed added paths (the message is now "added path is neither owned nor
seeded in the head manifest"; a path that matches more than one class there is named "added path has more than one class in the head
manifest"), any change to `.template/overrides.toml`, a `.template/state.toml` that is not updated, or one whose
version differs from the branch version. A sync that changes the owned `.claude/settings.json` passes TC-1, because the base manifest
says owned (design Q16: the sync writes the tag's file plus the entries of the project's seeded list that the tag's list lacks); TC-1
does not look inside the file. Findings are capped at `rules.PATH_CAP` with a summary line, and paths are sanitized.

### TC-2: an init PR may append entries to `permissions.deny` (option 1)

An init PR may modify `.claude/settings.json` (status modified, owned in the base manifest) and nothing else outside the seeded files.
The file is read at the base and at the head SHA with `client.file_text` (the head list `.claude/deny.platform.txt` too), and passes
only when all of these hold:

- both versions are valid JSON objects without duplicate keys or `NaN`, and `permissions.deny` is a list of strings in each (a file
  that is missing, not JSON, or has another shape is a violation, never a pass);
- everything except `permissions.deny` is unchanged. The comparison is of JSON text with sorted keys, so `false`, `0`, `1` and `1.0`
  are different values, and the formatting of the file does not matter;
- the base's `permissions.deny` entries are a prefix of the head's, in the same order: an entry removed, changed, reordered, or an
  entry inserted before an existing one is a violation;
- every appended entry is a line of the head's `.claude/deny.platform.txt` (one entry per line; blank lines and lines starting with
  `#` are skipped; spaces at the ends of a line are ignored; lines are split with `str.splitlines()`, as `initialize-project` splits
  them when it appends the entries, so a line break such as U+2028 or a form feed also separates two entries) and is not a duplicate of
  another entry.

Messages: "`.claude/settings.json` changes something other than appended permissions.deny entries", "an existing permissions.deny entry is
removed, changed, or reordered", "appended deny entry is not listed in `.claude/deny.platform.txt`: `<entry>`", "appended deny entry
duplicates another entry: `<entry>`", and "`.claude/settings.json` at the head (or of the base commit) is missing / is not valid JSON /
has no permissions.deny list of strings". Entries are sanitized and limited to 80 characters, at most `rules.PATH_CAP` are named, then
"and N more entries". The settings file is read from the head only when the PR modifies it; an added, removed or renamed settings file,
or one that is not owned in the base manifest, gets the old finding ("written path is not seeded"). All other TC-2 rules are unchanged.

The head's list is trusted on purpose: it is a seeded file the init PR writes, and the approver reviews it like any other file of
that PR. The check guarantees that the file `settings.json` gains no entry that the seeded list does not name, and that nothing is
loosened. It does not judge whether an entry in the seeded list is a good one.

### The base-run list of `pr-contract.yml` (follow-up of the instructions PR)

The workflow runs the checkers of the base commit against the head files. `operator-contract` and `cr-layout` are added to its list
now that the base contains them (the instructions PR could not add a checker that did not exist on its own base). Each reads its
policy from the base configuration (`operator_paths` and `operator_commands`; `docs_roots`), so a PR cannot widen what judges it.
`claude-md` and `deny-list` are **not** added, following the follow-up spec in `template-dev/docs/instructions.md`, points 6 and 7:
they have no policy input to read from the base, and the base's copy would compare head files with each other, as the head's copy
does. The step test (`test_policy_commands.py`) gets the base layout the two checks need (`scripts/pr_contract` and a configuration
with `operator_paths`, `operator_commands` and `docs_roots`), asserts all five checks run, that a violation under the base
`operator_paths` fails, that a head which replaces the checker, empties `operator_paths` and adds a violation still fails, the same
for `cr-layout`, that `operator-contract` fails closed without `operator_paths`, and that a missing checker fails for each id.

#### `owned-drift` joins the list (follow-up of the template-sync PR)

`owned-drift` is the sixth check of the list (the spec is in `template-dev/docs/template-sync.md`, "Follow-up PR"). It reads the hashes of
`.template/state.toml` from the base commit, so a PR that edits an owned file and rewrites `state.toml` to match is still found. The step
gets two additions: `GATE_HEAD_REF` (the branch name, from `github.event.pull_request.head.ref`, **only in `env:`**, never in the script
text; the check passes it as one quoted argument and matches it against fixed patterns, so it is plain text to the shell) and the history
of the base commit (`fetch-depth: 0` on the first checkout, and the fetch into `head/` without `--depth=1`), so that the synced version of
an owned workflow can be found for the pin-bump reading. Cost: the base checkout fetches the whole history instead of one commit; the time grows with the
history, which is small for the template (not measured on a hosted runner; the job has a timeout of 10 minutes); `persist-credentials: false` stays, so the token is gone from the checkout
after its own fetch and nothing reads it afterwards, and the permissions and the pinned action SHAs are unchanged. The tests are in
`test_policy_commands.py` (an owned file changed with a matching `state.toml`; a head that replaces the checker; `template-sync/v<x>` with
a consistent state and with a dropped hash; a missing checker; a branch name made of shell metacharacters) and `test_workflow.py`.

The PR that makes this change is judged by the `pr-contract` of its base, whose list does not have `owned-drift`: the new list takes effect
from the next PR after it is merged. On that PR itself `owned-drift` runs only through the `quality-gate` job of `ci.yml`.

The gate check `deny-list` (`scripts/checks/deny_list.py`) still splits the list on `\n` only. It was left alone on purpose: a preset
line with an exotic break (for example two entries separated by a form feed) is read there as one entry that is not in
`settings.json`, so the gate fails closed and names the line, instead of passing. Aligning it is a change to another owned file and
its fixtures, and is not part of this PR.
