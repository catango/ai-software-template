<!-- PR type: implementation. Branch: impl/CR-<n>-<slug>. Rule PC-1 requires every section below, non-empty. -->
<!-- Both references name an issue and a merged design PR of this repository (#<n>) or of a repository listed under `design_repos` in config/project.yaml (<owner>/<repo>#<n>) (PC-3). -->
<!-- Do not edit approved design.md, acceptance-criteria.md, or ADRs here (PC-5). Propose a new design PR instead. -->
<!-- If .github/workflows/ or .github/actions/ change, add a section "Workflow change justification" (PC-6). -->
<!-- If a path listed under the key `operator_paths` of config/project.yaml changes (the value on the base commit counts), add a section "Operator script review" (PC-8): credentials, commands, modes, calls, output, resources, release tag. No identifiers, addresses, or names. -->

Implements: #<issue>
Design: #<merged design PR>

## Summary
<!-- What this PR implements, in 2-3 sentences. -->

## Scope
<!-- Which parts of implementation-plan.md this PR covers. Use "Closes #<issue>" only in the final PR. -->

## Acceptance criteria
<!-- Map each criterion from acceptance-criteria.md to evidence (test, output, file). -->

## Validation
<!-- Quality gate and CI results. Summarize; do not paste raw logs. -->

## Architecture evidence
<!-- PC-9: for each architecture in `architectures` (config/project.yaml of the base commit) that has no runner for this repository, paste the one line that `scripts/gate-evidence <arch>` prints on a host of that architecture, at the current head of this PR (edit the body after every push). Format: evidence: <arch> | <machine> | <os> | <40-hex SHA> | clean | <gate command> | exit 0 | <UTC time> -->
<!-- When every architecture has a runner, write "n/a: every architecture has a runner". -->

## Checkpoint
<!-- checkpoint:start -->
- [ ] Plan item 1
- [ ] Quality gate passes locally
- [ ] CI green
- [ ] Marked ready for review
<!-- checkpoint:end -->
