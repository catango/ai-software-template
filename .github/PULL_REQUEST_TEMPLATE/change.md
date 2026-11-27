<!-- PR type: change (Class 1 or 2). Branch: change/<issue-or-short-id>-<slug>. Rule PC-1 requires every section below, non-empty. -->
<!-- If .github/workflows/ or .github/actions/ change, add a section "Workflow change justification" (PC-6). -->
<!-- If a path listed under the key `operator_paths` of config/project.yaml changes (the value on the base commit counts), add a section "Operator script review" (PC-8): credentials, commands, modes, calls, output, resources, release tag. No identifiers, addresses, or names. -->

## Summary
<!-- What changes and why, in 2-3 sentences. -->

## Class
<!-- 1 or 2, with a one-line justification. When in doubt, use the higher class. -->

## Acceptance criteria
<!-- Class 2: testable criteria. Class 1: write "n/a: trivial change". -->

## Validation
<!-- Linters and checks run, and what could not be run. -->

## Architecture evidence
<!-- PC-9: for each architecture in `architectures` (config/project.yaml of the base commit) that has no runner for this repository, paste the one line that `scripts/gate-evidence <arch>` prints on a host of that architecture, at the current head of this PR (edit the body after every push). Format: evidence: <arch> | <machine> | <os> | <40-hex SHA> | clean | <gate command> | exit 0 | <UTC time> -->
<!-- When every architecture has a runner, write "n/a: every architecture has a runner". -->

## Checkpoint
<!-- checkpoint:start -->
- [ ] Change implemented
- [ ] Validation run
- [ ] Marked ready for review
<!-- checkpoint:end -->
