# Release checklist

Template-only. For the approver, before the first tag and before every tag. It lists actions only. Claude Code performs none of them:
it creates no tag, repository, ruleset, collaborator, or setting (design sections 4 and 12). Commands run in a checkout of the
template at the commit that is to be tagged.

## Once, before the first tag

- [ ] The tag ruleset is applied to the template repository: only the approver creates `v*` tags, and a tag cannot be moved or deleted
      (design section 4, step 1; ADR-008 decision 2). Print the ruleset that `initialize-project` shows for application repositories
      and adapt its `release-tags` part.
- [ ] The required checks of the template repository are `pr-contract`, `quality-gate`, and `template-ci`, each defined by one job
      that always runs. Compare the names with the ruleset. No required check may name a job that does not exist (the job names of the
      matrix legs are `quality-gate-<arch>`, `generate-<arch>-<config>`, `skills-<arch>`, and `sync-<arch>`; they are not required
      names, the aggregates are).
- [ ] The template repository is public, so the hosted ARM64 runner of `config/project.yaml` exists for the legs `generate`, `skills`,
      and `sync` of `template-ci.yml`. In a private repository those legs would not start (design section 9, D9-6).
- [ ] The preset change PR of the platform repository is reviewed and merged (identities, `deny.platform.txt`, `docs/PLATFORM.md`,
      architectures and runner labels, post-init actions, and the check that the core deny list plus the platform list equals the
      platform's guardrail list). It is the prerequisite of the real run below.
- [ ] The printed rulesets of `initialize-project` (the JSON of `main-protection` and `release-tags`) are checked against the
      platform's settings matrix. They have not been applied to a repository by any test.

## For every tag

- [ ] `.template/manifest.toml` has `version` equal to the tag without the `v` (`template-sync` refuses a tag whose manifest names another
      version). The change is a normal PR.
- [ ] The tag commit is on the default branch (`template-sync` refuses a tag outside it, and within 500 commits of its tip).
- [ ] `template-ci` is green on that commit: `manifest`, `leak`, `engine`, `ci`, `skills-amd64`, `skills-arm64`, `sync-amd64`, `sync-arm64`,
      and the four legs `generate-<arch>-<config>`.
- [ ] `template-dev/run-tests.sh all` passes in a checkout of that commit.
- [ ] The dry run passes with both configurations, with an empty output directory each time (a few minutes each, because the gate runs
      in the generated repository):

  ```bash
  template-dev/dry-run.sh --template . --config template-dev/fixtures/config/neutral.toml --out "$(mktemp -d)"
  template-dev/dry-run.sh --template . --config template-dev/fixtures/config/second/config.toml --out "$(mktemp -d)"
  ```

  Every line must say `result=pass` and the last line `summary result=pass`.
- [ ] The lag that a repository of the previous version would see is the one expected: in a generated repository,
      `scripts/template-sync --check` prints a first line that names the new tag.
- [ ] The release notes exist (the sync PR links to them) and name a MAJOR release with a "sync twice" note when a release needs a new
      `scripts/template-sync` before the sync can run.
- [ ] The tag is created by the approver. The first tag is created after the manifest `version` was set to it; a second tag follows for
      the sync test of the real run.

## The real run (design section 12 (b), T-16 (b))

A throwaway public repository with the platform's preset. Claude Code never creates it.

- [ ] Create the repository with "Use this template" from the first tag. Add the machine account as a collaborator and update the
      AC1-1 allowlist of the platform for the duration of the run. Apply the printed branch and tag rulesets before the init PR merges.
- [ ] Step 1, generation: `initialize-project` ran with the preset; its post-init actions are printed.
- [ ] Step 2, an init PR (`init/v<MAJOR>.<MINOR>.<PATCH>`) passes `pr-contract` (TC-2).
- [ ] Step 3, a change PR with a known violation fails `pr-contract` and, once corrected, passes.
- [ ] Step 4, one review round: an approver comment and a machine-account comment; only the approver's is acted on
      (`address-review-feedback`, `review-threads`). Look at a real GraphQL cursor and at what GitHub shows for an edited comment.
- [ ] Step 5, one template-sync PR from the second tag (`scripts/template-sync --to v<MAJOR>.<MINOR>.<PATCH>`) passes `pr-contract`
      (TC-1) and `owned-drift`.
- [ ] Clean-up: delete the throwaway repository, remove the machine account as collaborator, restore the AC1-1 allowlist.
- [ ] Record the links of the PRs of steps 2 to 5 and the removal in the last implementation PR (T-16).
