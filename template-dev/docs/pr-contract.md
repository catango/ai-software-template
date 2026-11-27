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
missing on the base commit is a failure; there is no bootstrap skip. The three checks that exist (`workflow-policy`,
`no-claude-in-workflows`, `forbidden-api`) run. `owned-drift`, `operator-contract`, and `dependabot-config` join the list with the
rows that create them.

## Left to later rows

- Policy checks `owned-drift` (row 8), `operator-contract` (row 4), and `dependabot-config` (no row names it; the CR-43 plan puts it
  in the template's PR 2, and row 2 did not seed it): they join the list in `pr-contract.yml`.
- The private-repository checklist of the PR templates (CR-43 S-16) and the `codeowners` check (CR-43 plan, template PR 4).
- The dry-run step that runs these cases against a generated tree (row 9): `PR_CONTRACT_TREE=<tree> template-dev/run-tests.sh skills`
  already does it for `test_cases.py`, and a run against a generated tree passed while this row was written.
- A real run on GitHub: nothing here proves the `gh api` calls against the live API, the read-only token reading a design
  repository, or the workflow itself. Only CI and the real run of design section 12 (b) can.

## Points for the approver

1. **TC-1 and a new seeded file.** Design section 4 (step 3) says a sync adds a seeded file that is new in the tag and absent
   locally. TC-1 (design section 4, ADR-008 decision 4) says a template-sync PR adds only paths that are new and `owned` in the
   head manifest. They disagree. The code follows the text of TC-1 and refuses such a PR (a fixture case and a test say so). If
   the sync should add new seeded files, TC-1 needs "owned or seeded"; that is a design change, and row 8 meets it first.
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
| 5 | TC-1 and a new seeded file disagree with design section 4 | A design question, unchanged | A fixture documents the refusal; the question is in the body of the follow-up PR |
| 6 | PC-7 accepted any `uses:` change, including another action | Real | The removed and added lines must name the same actions in order; a new, removed, swapped or different action fails |
| 7 | One punctuation mark, or a bare checklist marker, satisfied the non-empty rule | Real for punctuation and markers; not for checklist text | List, quote and checkbox markers do not count and at least one letter or digit is needed. An item with text still counts, because the Checkpoint section of every template is a checklist by design; an untouched template checklist cannot be told from a real one |

The first row of the table needed no new manifest pattern. The configuration of a generated repository now carries `docs_roots`.
