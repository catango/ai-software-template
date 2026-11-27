<!-- PR type: template-sync. Branch: template-sync/v<MAJOR>.<MINOR>.<PATCH>. Rule PC-1 requires every section below, non-empty. -->
<!-- Opened by scripts/template-sync from `scripts/pr-body template-sync`. Rule TC-1: only paths that are owned in the base manifest change or are removed, only new paths that are owned in the head manifest are added, .template/state.toml is updated with the branch version, and .template/overrides.toml is not changed. -->
<!-- If .github/workflows/ or .github/actions/ change, add a section "Workflow change justification" (PC-6). -->
<!-- If a path listed under the key `operator_paths` of config/project.yaml changes (the value on the base commit counts), add a section "Operator script review" (PC-8). -->

## Summary
<!-- What the update brings, in 2-3 sentences. -->

## Versions
<!-- The version in .template/state.toml before and after, and the tag commit. -->

## Changed files by class
<!-- Owned files added, changed, and removed. Seeded files are never changed by a sync. -->

## Overrides honoured
<!-- Each entry of .template/overrides.toml that kept a file as it was, or "none". -->

## Drift limited to uses lines
<!-- Owned workflow files whose only difference was a uses: line, overwritten and listed here, or "none". -->

## Release notes
<!-- A link to the release notes of the tag. -->

## Checkpoint
<!-- checkpoint:start -->
- [ ] Sync run and state file updated
- [ ] Changed files listed by class
- [ ] Quality gate passes locally
- [ ] Marked ready for review
<!-- checkpoint:end -->
