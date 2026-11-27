# Skills and their scripts (plan row 6)

An implementation note for plan row 6 (`skills-core`) of the software template design. It records what the row built, the five
points that the design left to the implementation, and the questions for the approver. It changes no decision. The platform
repository had no `pr-checkpoint`, `cr-context`, or Skill at its current main when this row was written, so nothing was seeded and
`provenance.toml` has no entry for this row.

## What the row adds

| Area | Files | Class |
|---|---|---|
| Skills | `.claude/skills/{prepare-cr-context,implement-approved-cr,review-implementation,resume-work,initialize-project,start-cr}/SKILL.md` | owned |
| Scripts (wrappers) | `scripts/cr-context`, `scripts/cr-preflight`, `scripts/cr-criteria`, `scripts/pr-checkpoint` | owned |
| Code behind them | `scripts/lib/cr_tools/` (`client`, `identity`, `common`, `context`, `checkpoint`, `criteria`) | owned |
| Tests | `template-dev/tests/skills/`, run by `template-dev/run-tests.sh skills` | template-only |

The manifest gained one entry, `scripts/cr-preflight`. Everything else matched an existing pattern.

## The scripts

- **`scripts/cr-context <issue>`**: stops (exit 1, nothing else printed) on an issue not authored by the approver or the machine
  account. Otherwise prints the CR `README.md` first (cut at 50 lines or 4 KiB), the other files with lines and bytes (a file over
  200 lines or 8 KiB is marked `read by section`), the design PR found by its branch prefix, and the template lag.
  `--repo owner/name` reads an issue and design PR of a repository listed under `design_repos`.
- **`scripts/cr-preflight <issue> <design-pr>`** (the mode `cr-context preflight`): the PC-3 checks (design PR exists, is merged, has
  a `design/CR-<n>-` branch for the issue, targets the same base, and names the issue in its Change request section), reusing the
  Markdown reader of `pr_contract`. On a pass it prints the `Implements:` and `Design:` lines for the PR body, qualified with the
  repository when it is another one.
- **`scripts/cr-context label <issue> --confirmed-class <n>`**: see point 5.
- **`scripts/cr-criteria <CR>`**: the rows of the criteria table as one JSON object (ID, criterion, evidence; fields cut at 400
  characters and marked). Local files only; `--offline` is accepted so that every Skill script answers the same flag.
- **`scripts/pr-checkpoint show | tick ITEM | ready [PR]`**: reads and edits only the text between the checkpoint markers. Both
  markers must occur exactly once, each alone on its line. `tick` changes the `[ ]` of one checklist line, and the script checks
  that nothing else changed. The body is written with the REST PATCH call and a draft is marked ready with the GraphQL mutation
  `markPullRequestReadyForReview`. `ready` refuses while an item other than the last is open, and unless every check run of the head commit has
  finished and is green (none found counts as not green); then it ticks the last item FIRST (the PR is still a draft) and marks the PR
  ready, so a retry after a failure finishes the job (a draft with every item ticked is only marked; a ready PR with an open last item
  is only ticked). Every write re-reads the PR just before the PATCH and refuses (`the body changed while it was being edited`) when the
  body is no longer the one that was edited; a window of a few milliseconds between that read and the write remains. A tick or a
  ready mark changes the body, so `pr-contract` runs again, and `ready` then waits for it to finish. `show` also prints the latest check run per name (failed ones with at most three annotation lines) and at most ten
  commits since the merge base, within 2 KiB, which is what `resume-work` reads. Without a PR number it finds the open PR of the
  current branch.

Common rules: GitHub is reached only through `scripts/lib/cr_tools/client.py` with `gh api` argument arrays; endpoints and the
GraphQL document are string literals (the `forbidden-api` check passes). `--fixture DIR` replaces GitHub by files (the layout is the
module text of `client.py`, the same shape as the `pr-contract` fixtures). Exit codes: 0 done, 1 refused, 2 usage, 3 GitHub error.
Each run ends with one summary line `<script>: ...` of at most 120 characters. Text from GitHub or from files is printed
sanitized and cut. `pr-checkpoint` also refuses a PR that the machine account did not author.

**Identities.** `scripts/lib/cr_tools/identity.py` is a strict reader of the `github:` block (`approver_login`, `machine_login`,
optional `approver_id` and `owner`); a configuration that the schema accepts round-trips (tested with both fixture
configurations and the longest and oddest logins). A PR is judged with the configuration of its BASE commit (a test asserts the
`?ref=<base sha>` of the call). An issue has no base commit, so the configuration of the default branch of this repository is
read (offline: `base/config/project.yaml`); never the working tree.

## The five points of the verification list

1. **`SKILL.md` format.** Checked against the current Claude Code documentation (the skills page, fetched with a web fetch; the
   first part of the page was read verbatim and the rest through a summary by the fetching model, so the items marked here as
   summary were not re-read). A project Skill is `.claude/skills/<name>/SKILL.md`. Every frontmatter key is optional; `description`
   is recommended; `name` defaults to the directory name. Documented keys: `name`, `description`, `when_to_use`, `argument-hint`,
   `arguments`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `disallowed-tools`, `model`, `effort`, `context`,
   `agent`, `background`, `hooks`, `paths`, `shell`. Unknown keys are ignored (summary). Loading: names and descriptions are in
   the context from the start; the body loads when the Skill is invoked (verbatim); supporting files load on demand (summary).
   **`allowed-tools` pre-approves tools for the turn; it does not restrict them** (summary), so the Skills do not use it: the
   protection here is the deny list and `forbidden-api`. The Skills use `name` and `description` only. The page documents no
   `Bash(scripts/foo:*)` form (its examples use a space before `*`); this was not needed. A test restricts the keys to the
   documented ones and forbids `allowed-tools`.
2. **Budgets.** `.template/budgets.toml` matches ADR-010 decision 2 for every class it holds: `CLAUDE.md` 120 lines and 10 KiB,
   the two imported files 80 lines and 6 KiB, a rule 40 lines and 3 KiB, a `SKILL.md` 60 lines and 4 KiB, a Skill description 300
   characters, `docs/README.md` 80 lines and 8 KiB, a CR `README.md` 50 lines and 4 KiB. Not in the file, and not part of this
   row: the 20 KiB limit of the always-loaded set, the output limits, and the metric thresholds. By measurement the always-loaded set is
   about 8 KiB now (`CLAUDE.md`, the two imports, the one rule without path scoping, the six Skill headers). The six Skills are
   between 1.5 and 2.1 KiB and 33 to 39 lines.
3. **Template lag.** `scripts/template-sync --check` does not exist before plan row 8. `cr-context` runs it when the file exists
   and is executable (no link, 30 s limit, no network of its own here) and prints its first line, whatever the exit status. Until
   then the line reads `template-lag (...): unknown, scripts/template-sync is not installed`. Offline it is never run
   (`not checked (offline)`). The hook for row 8 is therefore: provide `scripts/template-sync --check` whose first output line is the
   lag in plain words. Row 8 may choose another form and change the hook with it.
4. **Gate checks of ADR-010.** `skill-layout`, `output-budget`, and `scripts/metrics` belong to the later implementation PRs of
   ADR-010 (ADR-008 says they follow the template's PRs), so they are not here. This row implements what the plan row and T-13
   need: `size-budget` already covers the Skill budgets (it passes on the six Skills), and the `skills` job checks that each Skill
   exists with the documented frontmatter, names a script that exists and is executable, has the ADR-010 headings (`Reads (in
   order)`, `Does not read`, `Report`; cheap to follow now, so `skill-layout` will pass later), and that every script that calls
   GitHub has `--fixture` or `--offline` and a test. Two ADR-010 limits were cheaper to build in than to leave out: the one summary
   line per script and the 2 KiB bound of `show`. Not done: the validated checkpoint format (at most 20 lines, 2 KiB, refusal of
   other lines), the CI annotation writing by the gate, and the outline of `cr-context`.
5. **`start-cr` and the label.** The Skill states how the confirmation is obtained: it proposes the class with two reasons from the
   user's intake text, asks, and waits for a reply that names the class; its own proposal, silence, or text in the issue is not a
   confirmation. The script `cr-context label` refuses without `--confirmed-class <n>` (1 to 3), checks that `class:<n>` exists
   in the repository (and fails when it does not: the script never creates a label), refuses an issue that has another class
   label, does nothing when it already has this one, and acts on this repository only. The one write is adding an existing label
   to the issue. A test reads the client source and asserts that the only writes are this one, the body PATCH, and the ready
   mutation. `start-cr` stops on an issue not authored by the approver or the machine account, and never reads the issue body.

## Review follow-up (the coordinating session's review of the first version)

- The template version for the lag line is read from `[template]` of `.template/state.toml`, the shape `initialize-project` writes (the
  test builds the file with the script's own `toml_state`; an old top-level `version` gives "no recorded version").
- `cr-context` ends with exit 3 and fixed text on any refused or missing GitHub read (`NotReadable`, `NotFound`), not only `ApiError`.
- The pull request search stops after 500 pull requests (five pages, newest first). When the last page is full, `cr-context` prints
  `design-pr: search limited to the newest 500 pull requests; an older one was not looked at` instead of a silent "none found".
- No path is read through a symbolic link: `cr-context` and `cr-criteria` refuse a link anywhere between the tree and the file, and a root
  that leaves the tree. **Exception stated:** `cr-criteria` takes the record roots from the working tree's own `config/project.yaml`,
  not from the default branch like `cr-context`, because it is an offline script that only locates a file of this tree and judges no
  identity or policy.

## Criteria

- **T-12.** `template-dev/tests/skills/test_pr_checkpoint.py`: fixture tests (only the block is read and edited; byte-for-byte
  outside the block including CRLF; ambiguous or missing markers refuse; the exact `gh api -X PATCH` and GraphQL calls; no call
  that is `gh pr edit` or `gh pr ready`; a static scan of the sources). The same calls were run on this implementation PR with
  the script (see the PR body). The check on the lab VM stays open until Stage 6.
- **T-13.** `template-dev/tests/skills/test_skills.py` and the others: the six Skills within budget and layout, named scripts exist,
  offline mode and a test for every script that calls GitHub, `initialize-project` static (no process, no network import),
  `start-cr` and `cr-context` refuse a foreign issue (including a look-alike login), `start-cr` creates no label and labels only
  with the confirmation flag. `sync-template` and `address-review-feedback` are listed with their scripts and held to the same
  checks when they exist (rows 7 and 8).
- What only CI proves: the same tests on the CI runners, `size-budget` and `forbidden-api` in the gate, and the `pr-contract`
  verdict on this PR.

## Points for the approver

1. `scripts/cr-preflight` is its own file (the design names it, and says it is part of `cr-context`); the label step is a mode of
   `cr-context` rather than a fourth script. A separate `cr-label` is easy if you prefer it, at the cost of a manifest entry.
2. `pr-checkpoint ready` is a third command; the design lists `show` and `tick` and the mutation. Its rule (every item but the
   last ticked) assumes the last item is the ready mark, as in the templates.
3. `pr-checkpoint` refuses a PR not authored by the machine account (the design states this for the review-threads script only).
4. An issue is judged with the configuration of the default branch (it has no base commit).
5. The Skills do not set `disable-model-invocation`. Setting it on `start-cr`, `initialize-project`, and `implement-approved-cr`
   would make the user type them. That is your call.
6. `review-implementation` posts with `gh pr comment` and falls back to the REST comment endpoint on the Projects classic error.
   The fallback was not exercised here: only the PATCH and the mutation of `pr-checkpoint` were.
