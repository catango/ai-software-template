<!-- PR type: init. Branch: init/v<MAJOR>.<MINOR>.<PATCH>. Rule PC-1 requires every section below, non-empty. -->
<!-- The first change after "Use this template". Rule TC-2: only template-only paths are deleted, only seeded files, the configuration file, and .template/state.toml are written, and the base branch has no .template/state.toml yet. One exception: .claude/settings.json may get entries appended to permissions.deny, each listed in .claude/deny.platform.txt, and no other change. -->
<!-- Removing the template's own workflow is a workflow change: the section "Workflow change justification" is required (PC-6). -->
<!-- If a path listed under the key `operator_paths` of config/project.yaml changes (the value on the base commit counts), add a section "Operator script review" (PC-8). No identifiers, addresses, or names. -->

## Summary
<!-- That this is the first change after generation, in 1-2 sentences. -->

## Configuration
<!-- Which configuration file scripts/initialize-project was run with (a description, not its values). -->

## Post-init actions
<!-- The actions that scripts/initialize-project printed, and which are done. Rulesets are applied by the approver before this PR merges. -->

## Workflow change justification
<!-- The template's own workflow is removed; say what else changed under .github/workflows/, if anything. -->

## Validation
<!-- ./scripts/quality-gate.sh --strict, and what could not be run. -->

## Checkpoint
<!-- checkpoint:start -->
- [ ] Generated and initialised
- [ ] Quality gate passes locally
- [ ] Post-init actions listed
- [ ] Marked ready for review
<!-- checkpoint:end -->
