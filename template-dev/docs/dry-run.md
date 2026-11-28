# Dry run (plan row 9)

An implementation note for plan row 9 (`dry-run`), the last implementation PR of CR-39. It records what the row built, how the
steps of design section 12 (a) are covered, the design section 11 table checked against the implementation, what earlier rows could
not prove and this row could, what only CI or the approver's real run can prove, the points for the approver, and the follow-ups
that stay open. It changes no decision and edits no design record. The report of the runs of this PR is in the PR body (T-14, T-16 (a)).

## What the row built

| Area | Files | Class |
|---|---|---|
| The dry run | `template-dev/dry-run.sh`, `template-dev/lib/dry_run_checks.py` | template-only |
| Tests | `template-dev/tests/test_dry_run.py` (`run-tests.sh dry-run`, also in `all`), `template-dev/tests/skills/test_generated_tree.py` | template-only |
| CI of the template | job `generate` (two architectures by two configurations); `skills` and `sync` as matrices over both architectures; the aggregate `template-ci` needs `generate` | template-only |
| Records | `release-checklist.md`, this note | template-only |
| Shared | the exemptions of the leak scan moved from `test_leak.py` into `template-dev/lib/leak.py`, so the test and the dry run use one definition | template-only |

No payload file changed: the manifest needed no new pattern, and a generated repository is the same as before.

## The steps of design section 12 (a) and who runs them

Section 8 of the design gives each template job its steps: `generate` is "steps 1 to 4 and 8", `sync` is step 6, `skills` is steps 5 and 7.
`template-dev/dry-run.sh` therefore drives exactly steps 1 to 4 and 8, and the other steps stay where the design puts them.

| Design step | Where it runs | Both architectures | Both configurations |
|---|---|---|---|
| 1 export without history | `dry-run.sh` step 1 | `generate` | neutral, second |
| 2 `initialize-project --config`, no template-only path, tree classified | `dry-run.sh` step 2 | `generate` | neutral, second |
| 3 `git init`, commit, `quality-gate.sh --strict` | `dry-run.sh` step 3 | `generate` | neutral, second |
| 4 `new-cr 1 sample`; `cr-layout` passes it, fails a missing file and an unqualified reference | `dry-run.sh` step 4 | `generate` | neutral, second |
| 5 `pr-contract` against fixture PRs | `test_cases.py` (`skills`); the same cases against a generated tree: `test_generated_tree.py` | `skills` | the generated-tree test runs both |
| 6 sync | `template-dev/tests/sync/` (`sync`) | `sync` | second only (see point 4) |
| 7 `review-threads --fixture` | `tests/skills/test_review_threads.py` (`skills`) | `skills` | not configuration-dependent |
| 8 leak scan | `dry-run.sh` step 8 | `generate` | neutral, second |
| 9 steps 1 to 8 on both architectures, two configurations | the matrices of `generate`, `skills`, `sync` | yes | as above |
| 10 a report with one line per step | the output of `dry-run.sh` and the PR body | | |

The brief for this row said the script drives "the pr-contract rules and the sync". The design text is exact about which steps it drives, so
those two stay in the jobs that already run them, now on both architectures; the one thing that was missing, `pr-contract` against a
generated tree, is `test_generated_tree.py`.

## The script

`template-dev/dry-run.sh --template DIR --config FILE --out EMPTY_DIR`. Exit status: 0 every step passed, 1 a step failed, 2 a refusal.

- **Refusals, before anything is written** (exit 2, no report line): a missing value or an unknown argument; a template that is not the
  top of a git repository with `.template/manifest.toml`; a missing configuration; an `--out` that does not exist, is a link or a file,
  is inside the template, or is not empty (a hidden file counts).
- **Writes only inside `--out`**, and says so on standard error: `OUT/repo` is the generated repository, `OUT/work` holds the logs
  (`init.log`, `gate.log`, ...), the scratch files, the cache and a throw-away home. The template is only read. Everything that runs in the
  generated repository (`initialize-project`, git, the gate, `new-cr`) runs with `TMPDIR`, `HOME`, and the cache directories inside
  `OUT/work`, git without the machine's configuration, and no bytecode. The export and the leak scan read the template with the caller's own
  git configuration, so a file that the caller's git ignores is not exported. (A first version isolated the environment for the whole script;
  the first real run showed that this exported a locally ignored file, which `initialize-project` refused as unclassed. A test covers it.)
- **No network, no GitHub, no credential.** A test scans the script for the commands that would. A real run in a network namespace without
  a network interface (`unshare -rn`) passed with both configurations (see the PR body). The gate tools must be on `PATH`: the job installs
  them from the CI manifest first, and a local run needs them installed.
- **Step 1** exports the tracked files plus the untracked files that are not ignored, skipping tracked files that are gone from the disk,
  so it also works on uncommitted work (the way `helpers.export_template` does). On a clean checkout it is the content of `git archive HEAD`.
- **Step 2** runs `scripts/initialize-project --config` with the template commit recorded in `state.toml`, then checks the tree: no
  `template-dev/`, no template-only path, every file has exactly one class, no CR or ADR instance.
- **Step 3** makes a git repository of the generated tree (one commit), runs `./scripts/quality-gate.sh --strict` (every check, a missing
  tool fails with exit 3), and fails if the gate left a change in the repository.
- **Step 4** works in a copy of the generated repository (so the sample CR is not in the tree that step 8 scans): `new-cr 1 sample`,
  `cr-layout` through the gate's entry point passes it, fails it when a file is removed, and fails it when `design.md` gets an unqualified
  reference to another repository. Only exit status 1 counts as the expected failure.
- **Step 8** scans the payload (every file of the template not classed template-only, with the exemptions of the `leak` test) and the
  generated tree (every pattern, including the placeholders). Findings name pattern and location, never the matched text.
- A step that cannot run because an earlier one failed is reported as `skip`, and a run with a skip does not pass.

### The report

Standard output holds only the report. Diagnostics (the end of a failing log) go to standard error.

```text
dry-run: step=<n> name=<name> result=<pass|fail|skip> [detail=<text>]
dry-run: summary result=<pass|fail> steps=<n> passed=<n> failed=<n> skipped=<n> config=<file name> arch=<machine name>
```

The steps are `1 export`, `2 initialize`, `3 gate`, `4 new-cr`, and `8 leak`, numbered as in the design. `arch` is the machine name of the
host (`x86_64`, `aarch64`), so a CI log shows on which architecture a run happened. The detail is at most 200 printable characters.

## What this row proves that earlier rows could not

| Earlier note | The point | Now |
|---|---|---|
| `pr-contract.md` | "The dry-run step that runs these cases against a generated tree (row 9)" | Proven locally and in `skills` on both architectures: `test_generated_tree.py` |
| `template-sync.md`, `gate.md` | "CI of the template and of a generated repository: ... run only in CI" | The `generate` and `sync` legs run in CI on both architectures; the local part is in the PR body |
| `gate.md` point 6 | A configuration with an architecture the CI manifest does not declare fails the plan | Unchanged; both fixture configurations use declared names and the dry run's gate step passes with each |
| `gate.md` point 8 | `template-ci.yml` had no `generate`, `sync` or `skills` | All three exist now |
| `manifest-and-config.md` | The printed rulesets are not applied | Still not provable here (needs a repository) |
| `review-feedback.md` | The queries, the cursor alphabet, the editor fields | Still not provable: needs a real pull request (the approver's step 4) |
| `skills-core.md`, `instructions.md` | T-12 on the lab VM, the `gh pr comment` fallback | Still open (Stage 6, the real run) |

## Design section 11, checked

Each assumption: does the implemented template rely on it as written, with the evidence, and any deviation. The design is not edited.

| ID | As written? | Evidence | Deviation |
|---|---|---|---|
| D9-1 required checks | **Partly** | `ci.yml` names the matrix jobs `quality-gate-<arch>` and the aggregate `quality-gate`; `pr-contract.yml` defines `pr-contract`; `template-ci.yml` defines `template-ci`. `test_ci.py`, `workflow-policy` rule WP-16 | The printed `main-protection` ruleset (`scripts/initialize-project`) requires `pr-contract` and `quality-gate`, not the `quality-gate-<arch>` names. The aggregate needs every matrix leg and fails on a skipped one, so each leg is covered, and a leg for an architecture without a runner exists but says `not run`; ADR-008 decision 9 names the aggregate as the required check. The design text of D9-1 says the list also covers the leg names. Which reading holds is the approver's; the required-check names were not read from the repository (reading rulesets is denied here) |
| D9-2 Actions hardening | **Yes, with one exception** | `scripts/checks/workflow-policy.sh` and `workflow_policy.py`, seeded; fixtures in `tests/quality-gate/` | WP-13 has a narrow exception for the planned matrix of `ci.yml` (`gate.md`, point 1). The design says "the current rules"; the exception is new. `template-ci.yml` uses literal matrices only, so it needs none |
| D9-3 security features, community files | **No** | `.template/manifest.toml` classes `.github/dependabot.yml`, `.github/CODEOWNERS`, and `SECURITY.md` as seeded; none of the three exists in the template or in a generated tree | The design says `dependabot.yml` is shipped (seeded); ADR-008 decision 1 says `SECURITY.md` and CODEOWNERS are seeded. No row built them (`gate.md`: `dependabot-config` and `codeowners` are not seeded for that reason). A generated repository therefore has no Dependabot configuration, no CODEOWNERS (the printed ruleset asks for code-owner review, which then has no owner), and no `SECURITY.md`. Open follow-up |
| D9-4 fork PRs, third-party Actions | **Yes** | `pr-contract.yml`: trigger `pull_request`, read scopes only, no secrets, the base commit's checkers judge the head (`test_workflow.py`); `workflow-policy` allows only pinned actions | None |
| D9-5 cross-repository checkout | **Yes (withdrawn)** | No workflow checks out another repository (`generate` and the other jobs check out only this one); tools come from the checksum-verified CI manifest | `pr-contract` reads a design repository through the API for PC-3, read-only; that is not a checkout |
| D9-6 host-reported evidence | **Yes** | PC-9, `scripts/gate-evidence`, the section "Architecture evidence" of the PR templates; `test_pc9.py`, `test_gate_evidence.py`; the `not run: <arch>` line of `ci.yml` | None. Informational, accepted by the approver per PR |
| D9-7 printed rulesets | **Unverified** | `scripts/initialize-project` prints `main-protection` and `release-tags`; `test_manifest.py` checks the text | The JSON was never applied to a repository and could not be compared with the platform's rulesets here (reading rulesets is denied). Point 2 of `manifest-and-config.md` stands |
| D9-8 `forbidden-api`, `operator-contract`, `owned-drift` | **Yes** | The three checks exist in `scripts/checks/` and run in the gate of every generated repository; the dry run's step 3 runs all 19 | None. `owned-drift` also has the `uses:` reading of `template-sync.md` point 1 |
| D10-1 budgets | **Yes, with more classes** | `.template/budgets.toml`: `CLAUDE.md` 120 lines, a rule 40, a `SKILL.md` 60, as the placeholders say; `size-budget` | The file also has classes for the imported docs, the Skill description, `docs/README.md`, and a CR `README.md` (`instructions.md`, `cr-templates.md`). The always-loaded group (20 KiB), `output-budget`, and `skill-layout` of ADR-010 are not built (open) |
| D10-2 checkpoint format, metrics | **Yes** | `pr-checkpoint` reads and edits only the text between the ADR-003 markers (`test_pr_checkpoint.py`); every script prints one summary line (`cr_tools/common.summary`; the Bash rule) | None. No metrics script exists; ADR-010 adds one later |
| D5-1 profile names | **Yes (closed by Q7)** | `toolchain/ci-manifest.toml` is the template's own manifest, not a host profile, with the same schema; tests in `template-dev/tests/toolchain/`; every tool has artifacts and a SHA-256 for `amd64` and `arm64` | None |
| D3-1 template-sync and init rules | **Yes (closed by Q8)** | TC-1 and TC-2 in `scripts/pr_contract/template_rules.py` with the cases of `template-dev/tests/pr_contract/`, including the decisions of 2026-11-27 (`catango/ai-lab-platform#80`) | None |

T-14 also asks for "every download in the CI manifest has amd64 and arm64 artifacts with checksums" and "fetches no other repository": the
`engine` job and the `toolchain` check of the gate prove the first; the second is the D9-5 row.

## Not proven by this PR

- **On the arm64 runner.** Nothing here ran on ARM64. The workspace is x86-64. The `generate`, `skills` and `sync` legs on the
  ARM64 runner are the proof of T-3 (the generated CI passes on ARM64) and of the ARM64 half of T-16 (a); they run in template CI.
- **The required-check names** `pr-contract`, `quality-gate`, `template-ci` as the ruleset has them: reading rulesets with `gh api` is
  denied in this workspace, so this was not looked at. The approver checks them (release checklist).
- **Everything of section 12 (b)**: the init PR under a real `pr-contract` run, a failing then passing change PR, a real review round, a
  real template-sync PR from a second tag, the printed rulesets applied, the tag ruleset, the preset, and the AC1-1 allowlist.
- **The tags.** No tag exists, so `template-sync --check` shows no lag against a real tag; the sync test uses local tags.

## Points for the approver

1. **D9-3 is a real gap**: no `dependabot.yml`, CODEOWNERS, or `SECURITY.md` is shipped although the design and ADR-008 say so and the
   manifest classes them. A small follow-up PR can add them (seeded files need a TC-1 reading: they are new seeded paths of a release).
   Decide whether the first tag waits for it.
2. **D9-1**: whether the ruleset should list `quality-gate-<arch>` as well as the aggregate (see the table).
3. **`dry-run.sh` takes minutes**, because step 3 runs the whole gate, including the fixture suites of `tests/quality-gate/`, in the
   generated repository. The `generate` legs have a 40-minute timeout; a local run took about three minutes.
4. **Two configurations for `sync`.** The sync fixtures use the second configuration only (its preset files and its second architecture
   are what the settings merge and the TC-1 file lists need). The neutral configuration is covered by `generate` (initialization and the
   gate), not by the sync test. Parameterizing the sync fixtures by configuration would change what many of its assertions mean.
5. **A private template repository** has no hosted ARM64 runner, and `template-ci.yml` has literal labels (a computed matrix is not allowed
   in a workflow by `workflow-policy`). The legs would wait for a runner. The design's `not run: <arch>` rule applies to generated
   repositories (`ci.yml`), where the plan reads the private flag. The template repository is public.
6. **Dry-run speed versus `all`.** `run-tests.sh all` now includes `test_dry_run.py` (about two minutes, with a patched gate).

## Follow-ups that stay open

Not part of this PR. The brief for this row spoke of six known follow-ups without listing them. The notes of the earlier rows name the five
below, and the approver should check this list against the six they have in mind.

1. `owned-drift` on the base-run list of `.github/workflows/pr-contract.yml`, and the explicit `GITHUB_HEAD_REF` of the base-run step
   (`template-sync.md`, "Follow-up PR"). Separate, because a workflow cannot add a checker that its own base lacks.
2. `dependabot-config` on the same list, with the seeded `dependabot.yml`, CODEOWNERS, `SECURITY.md`, and the `codeowners` check
   (`gate.md`, `pr-contract.md`; D9-3 above).
3. The private-repository checklist of the PR templates (CR-43 S-16; `pr-contract.md`).
4. ADR-010 items: the always-loaded 20 KiB group, `output-budget`, `skill-layout`, the extended `size-budget`, and `scripts/metrics`
   (`instructions.md`, `skills-core.md`).
5. PC-8: the sync PR body does not add the section "Operator script review" when a path of `operator_paths` changes with a release
   (`template-sync.md`, point 8).

## User-only steps still open

- Review of this report and of the PR.
- The tag ruleset, the required checks, and the first tag with the manifest `version` set before it; a second tag for the sync test.
- The preset change PR in the platform repository, and the AC1-1 allowlist for the run.
- The real run of design section 12 (b), its clean-up, and the PR links recorded in the last implementation PR (T-16 (b)).
