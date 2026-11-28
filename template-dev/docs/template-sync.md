# template-sync (plan row 8)

An implementation note for plan row 8 (`template-sync`) of the software template design. It records what the row built, the reading of
`owned-drift` that the design leaves to the approver (the first point below), where the design was silent and which reading was chosen,
the exact follow-up for `pr-contract.yml`, what is not proven here, and the steps that only the approver can take. It changes no decision.

## Points for the approver

1. **The `owned-drift` reading for `uses:` changes (design section 8 "my reading, to confirm", section 14).** Implemented exactly as the
   design words it: a change limited to `uses:` lines of an owned workflow (a Dependabot pin bump) passes `owned-drift` without an
   entry in `.template/overrides.toml`. It lives in one file, `scripts/lib/template_sync/uses_drift.py`, as the constant
   `OWNED_DRIFT_ALLOWS_USES_ONLY = True`.
   - **If you choose the other reading**, set it to `False`. A pin bump of an owned workflow then fails `owned-drift` like any other
     change to an owned file and needs an entry (path, reason, pull request) until the next sync overwrites the file. The fixture
     cases `a pin bump ... passes` in `tests/quality-gate/run.sh` (function `test_owned_drift`) then flip; one case already runs with the
     constant at `False` (it edits a copy). `template-sync` is the same either way: it overwrites such drift and lists it under "Drift
     limited to uses lines".
   - **What the design is silent on, and the strict reading chosen** (`SAME_ACTION_ONLY`): the bumped line must name the same action
     (as PC-7 does for a Dependabot PR); only the pin and the trailing comment may change; a local action has no pin; the number of
     lines and every other line stay the same. And "limited to `uses:` lines" is measured against the synced version (the file at the
     last commit that changed `state.toml`, accepted only when it has the recorded hash), so a second bump on top of a first one passes,
     and a bump on a history that does not contain the sync commit (a shallow clone) fails safe.
2. **The first sync of a project from the same version.** A tag of the recorded version is accepted only with the recorded commit. If the
   only differences are local drift, the sync refuses: a sync PR would leave `state.toml` unchanged, which TC-1 rejects ("not updated").
3. **A new owned path where the project has a file that its manifest does not class owned** (for example a seeded file that the tag
   turns into an owned one) is refused as a whole, because TC-1 rejects a change of a path that is not owned in the base manifest. The
   fix is a "sync twice" MAJOR release, or the project moves its file first.
4. **The manifest version of a tag must equal the tag** (`.template/manifest.toml` says "set when a release tag is created"). The sync
   refuses a tag whose manifest names another version. The release checklist has to set it before the approver tags.
5. **What "new seeded path" means.** A seeded path of the tag is added once when it is absent from the tree **and** either the project's
   own manifest has no seeded pattern for it, or lists it by its exact name while the template commit recorded in `state.toml` did not
   have the file (a manifest can name a file before a release ships it: that is how `.github/dependabot.yml`, `.github/CODEOWNERS` and
   `SECURITY.md` reached projects generated before they were seeded). A file inside a seeded directory that the project already has
   (`scripts/checks.d/`) is never added; a path the project deleted is never added back, because the recorded commit had it. When no
   commit is recorded, or it is not within the clone (the same 500-commit bound), the sync keeps the old rule and adds nothing for a
   path that the project's manifest already names. `.github/CODEOWNERS` is written as `* @<approver_login>` from the project's
   `config/project.yaml`, never from the tag's file (which names the template's approver); when the configuration has no valid
   approver login the file is not added and the plan says so. The sync script that runs is the project's own, so a project reaches this
   behaviour with the sync that follows the one that brought the new script ("sync twice").
6. **`--check` counts every tag `v<MAJOR>.<MINOR>.<PATCH>`**, not only those in the default branch (that needs a clone). The sync itself
   refuses a tag outside the default branch.
7. **The tag must be within the newest 500 commits of the default branch** of the template (the clone is bounded to that depth). An older
   tag is refused as "not contained". The bound is `MAX_DEPTH` in `scripts/lib/template_sync/source.py`.
8. **PC-8.** The sync does not add the section "Operator script review". If a path of `operator_paths` changes with a release, the PR
   body needs it by hand.
9. **The pull request is opened with `POST repos/<owner>/<name>/pulls`** (`draft: true`), not with `gh pr create`, which the design names,
   for the same reason the other scripts avoid `gh pr edit` and `gh pr ready` (the Projects classic error). The design's wording is a
   description of the effect; say so if you want the command.

## What the row adds

| Area | Files | Class |
|---|---|---|
| Script | `scripts/template-sync` (a thin wrapper) | owned (already in the manifest) |
| Library | `scripts/lib/template_sync/` (`cli`, `source`, `plan`, `state`, `overrides`, `settings`, `uses_drift`, `body`, `publish`, `fsops`, `versions`, `errors`) | owned |
| Skill | `.claude/skills/sync-template/SKILL.md` | owned |
| Gate check | `scripts/checks/owned-drift.sh`, `owned_drift.py`; fixtures in `tests/quality-gate/fixtures/owned-drift/`; cases in `tests/quality-gate/run.sh` | owned |
| Tests | `template-dev/tests/sync/`, `template-dev/tests/skills/test_sync_template_skill.py`, `template-dev/run-tests.sh sync` (also in `all`) | template-only |
| CI of the template | job `sync` in `template-ci.yml`, added to the aggregate `template-ci` | template-only |

The manifest needed no new pattern: every new file has exactly one class (`test_sync_files.py` asserts it). The gate catalogue lists
`owned-drift` as `present`.

## The script

`scripts/template-sync [--to] v<MAJOR>.<MINOR>.<PATCH> [--dry-run] [--fixture DIR]` and `scripts/template-sync --check [--fixture DIR]`.
Exit codes: 0 done, 1 refused, 2 usage, 3 the source could not be read or a step failed. One summary line per run, at most 120 characters.

- **The repository's own copy does everything.** The tag is read as git objects (`ls-tree`, `cat-file`); it is never checked out, so no
  attribute, filter, hook, or script of the template exists on disk before the checks, and none runs. A test makes the tag carry a
  script that writes a marker and asserts that the marker is never written.
- **The source** is `template.source` of `.template/state.toml`. There is no option for it (`--source` is a usage error).
- **Every git call that reaches the source** runs with `credential.helper=` (the empty value resets the list), `core.hooksPath=/dev/null`,
  `protocol.allow=never` with only https allowed (and `file` for a fixture), `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=true`, no user or system
  configuration, a throw-away HOME, and an environment without tokens. A test puts a stand-in for `git` on `PATH` that logs every call and
  asserts all of this on the real (non-fixture) code path, with no network use.
- **Preconditions**, in order: the target is a tag name; `state.toml` and `overrides.toml` are valid; the manifest of the project is
  valid; a clean tree (untracked files count, ignored ones do not); the default branch is checked out; no downgrade; the tag exists in the
  source (a branch of that name does not count); a tag of the recorded version names the recorded commit; its commit is contained in the
  default branch; its manifest names its version; every path of it has exactly one class; no symbolic link or submodule where a file is
  expected; the branch `template-sync/<tag>` does not exist (online: also not on origin, and the default branch equals origin's).
- **The plan** (`plan.py`) overwrites, adds and removes owned files, never changes a seeded file (a new one is added once), honours each
  entry of `overrides.toml`, regenerates `.claude/settings.json` as the tag's file plus the entries of the seeded `deny.platform.txt` that
  it lacks, and refuses what TC-1 would reject. A second run after the merge finds "nothing to do".
- **Honoured overrides** are recorded in `[overrides]` of `state.toml` as `"<path>" = "override"`, and `[owned]` keeps the hash of the
  file as the tag ships it (so the divergence stays visible to `owned-drift`). A file that the tag removes and an entry keeps is recorded
  in `[overrides]` only. An entry that names no owned file of the version is listed and not recorded.
- **Publishing.** Branch, one commit (hooks of the project are not run), a push of that branch only, and a draft PR whose body is
  `scripts/pr-body template-sync` with every section filled in: versions and tag commit, changed files by class, overrides honoured,
  drift limited to `uses:` lines, the release-notes link, and the section "Workflow change justification" when a workflow changes. The two
  checkpoint items that the run did are ticked, because a checkpoint left as the template ships it fails PC-1. The list of new seeded paths is never cut (TC-1
  needs each one in the body); the other lists stop at 200 with a count; a body over the limit of `pr-contract` (64 KiB) is refused, before
  anything is written, with a message that says so. On a failed write the tree goes back to a clean default branch.
- **Hooks.** Every git call of the script runs with `core.hooksPath=/dev/null` and `core.fsmonitor=false` (one place, `Repo.git`). A sync
  overwrites owned files, and a project can point `core.hooksPath` at one (a release that ships `.githooks/pre-push`): nothing the template
  wrote may run during the sync. **What the project's own hooks lose:** they do not run for the script's branch, commit, push and rollback;
  they run again for every later command of the people who use the repository. The cost is small (the commit is the script's own, and the
  gate runs in CI), the alternative is that a hook from the tag runs with the machine account's credentials. It never merges, tags, or pushes the default branch (`forbidden-api` passes on the library).
- **`--fixture DIR`**: the source is `DIR/<owner>/<name>`, nothing is pushed, and the pull request is written to `DIR/created-pr.json`.

## `owned-drift`

`scripts/checks/owned-drift.sh` (class `template`, order 92). Rules, in the order the code applies them:

| Case | Result |
|---|---|
| No `state.toml` in the tree and none at the base | nothing to judge (exit 77, shown as SKIP) |
| `GATE_BASE_REF` set and unreadable, or option-like | fails closed |
| An owned file of the base `state.toml` differs from its hash (or is removed) and the PR changes it, with no entry in the head `overrides.toml` | finding |
| The same, with an entry | passes |
| The PR does not touch the file, although it differs from the hash | passes (it is the business of the PR that made it); without a base (a push) the tree is judged as a whole and fails |
| A recorded override has no entry, or an entry was dropped while the file still differs | finding |
| A PR on `template-sync/v<x>` that changes `state.toml` to version `<x>`, **greater than the base's**, whose files match the hashes it records, leaves `overrides.toml` alone, and whose head `state.toml` drops no path of the base `[owned]` that is still in the tree (unless it is recorded as an override, or the head manifest classes it seeded) | exempt |
| The same, but a path of the base `[owned]` is gone from the head `[owned]` while its file is still there | finding `dropped from [owned] ... but still present` (otherwise a changed file could leave the check by deleting its line) |
| The same, at the version of the base or a lower one | finding; the script never produces such a PR |
| A PR on `init/v<x>` that adds `state.toml` (the base has none) with matching hashes | exempt |
| `state.toml` added or removed otherwise | finding |
| A change limited to `uses:` lines of the same actions | passes (the reading above) |

The hashes come from the base commit, so rewriting `state.toml` in the same PR does not help. The branch name comes from
`GATE_HEAD_REF`, else `GITHUB_HEAD_REF` (set by Actions on a pull request). The exemption trusts a branch name, as the ADR says, together
with `pr-contract`, which judges what such a PR may touch (TC-1, TC-2); `owned-drift` itself checks that the state and the files agree.
**No check can verify that the content of a synced file equals the tag**: that stays with the approver's review of the sync PR (the
body lists every file by class, and the diff of the owned files is the review).
The entries are read from the head: a project declares a divergence in the PR that makes it, and the entry is reviewed there.

### Follow-up PR: `owned-drift` on the base-run list of `pr-contract.yml`

**Done** in the follow-up PR `impl/CR-39-owned-drift-base-run`; the account of the change is in `template-dev/docs/pr-contract.md`
("`owned-drift` joins the list"). It was not done in this PR, on purpose: a checker that is missing on the base commit fails
`pr-contract`, so the PR that adds `owned-drift` could not also add it to the list. The spec of that PR was:

1. In `.github/workflows/pr-contract.yml`, step "Run the policy checks of the base against the head", add `owned-drift` to the id list:
   `for id in workflow-policy "no-cl""aude-in-workflows" forbidden-api operator-contract cr-layout owned-drift; do`. Update the comment above
   it: `owned-drift` joins (it reads the hashes from the base commit); only `dependabot-config` is still to come.
2. Give that step the branch name, as env, never inside the script text: `GATE_HEAD_REF: ${{ github.event.pull_request.head.ref }}` next to
   `GATE_BASE_REF`. (`GITHUB_HEAD_REF` is set by Actions as well; the explicit variable keeps the check independent of that.)
3. Make the base commit's history available, so that the synced version of a workflow can be found for the pin-bump reading: set
   `fetch-depth: 0` on the first `actions/checkout` (the base) and fetch the base commit into `head/` without `--depth=1`. Without it a
   pin bump needs an entry in that job (the check fails safe); the job `quality-gate` of `ci.yml` has full history already.
4. In `template-dev/tests/pr_contract/test_policy_commands.py`: copy `scripts/lib/template_sync` and `scripts/lib/template_manifest.py`
   into the base layout of `setUp`, add `owned-drift` to the list asserted by `test_the_loop_runs_every_check_of_the_list`, and add two
   tests: an owned file changed in the head, with a `state.toml` rewritten to match, fails; and a head that replaces the checker and adds
   an entry for its own change still fails when the base `owned-drift` is what judges (the base's copy runs).
5. PC-6 applies to that PR: "Workflow change justification".

## Criteria

- **T-9**: `template-dev/tests/sync/test_sync_apply.py` (version A to B with a project that has local edits, an override, a pin bump, a
  seeded edit; every item of T-9; the file list passes `check_template_sync` and a changed or removed seeded file fails it; a second
  run changes nothing; a dry run writes nothing; A to C), and `test_sync_units.py` (the pure parts).
- **T-10**: `test_sync_refusals.py` (a non-tag, a branch of a tag's name, a tag outside the default branch, a downgrade, a moved tag, a
  malformed tag and 20 other malformed targets, a state file that is missing or corrupt in 14 ways, a dirty tree, a source that is not
  the recorded one, malformed overrides, a symbolic link and a submodule in the tag; the clone's git calls; nothing of the clone runs).
- **Review of the first version** (the coordinating session): `exempt_sync` dropped-path and version rules (gate cases and the end-to-end check
  over a real sync in `test_sync_apply.py`), hooks that must not run (`test_sync_hooks.py`), the seeded list through TC-1 with 201 paths
  and the refusal of a body over the limit (`test_sync_body.py`).
- **T-17**: `tests/quality-gate/run.sh owned-drift` (24 fixture directories, one passing and one failing case per rule, and the cases
  that need a base commit and a branch name).
- **The lag hook**: `test_sync_refusals.py::TemplateLag` (the first line of `--check`, and `cr-context` showing it).

## Not proven here

- A real sync on GitHub: the clone over https, the push, and the draft PR through `gh api` were exercised only through stand-ins and
  local repositories (the real-mode git calls through a stand-in for `git`; the push and the PR call as argument arrays with a fake
  runner). The real run waits for row 9 and the approver.
- That `GITHUB_HEAD_REF` is what the base-run step sees on a fork PR; the follow-up sets the variable explicitly.
- The depth of 500 commits against a real template history.
- CI of the template and of a generated repository: the job `sync` and the gate on both architectures run only in CI.

## User-only steps still open

- The tag ruleset on the template (only the approver creates `v*` tags) and the first two tags (after row 9), with the manifest version
  set to the tag before each (point 4).
- The `owned-drift` reading (point 1), and the follow-up PR above after the merge.
- The real run of design section 12 (b): one template-sync PR from a second tag in a throwaway repository, with the printed rulesets.
