---
paths:
  - ".github/workflows/**"
  - ".github/actions/**"
---

# GitHub workflows

- Pin every `uses:` to a full 40-character commit SHA with the version in a trailing comment. Only the actions the policy allows;
  a new one needs its own PR with the justification and the approver's allowlist entry.
- Top-level `permissions: {}`; give a job only the read scopes it needs. Never a `write` scope.
- Trigger on `pull_request`. Never `pull_request_target` or `workflow_run`. Run a PR's own code only with a read-only token and
  no secrets, and never expose a secret to a step that runs it.
- No step, action, or secret that invokes Claude or any model. The gate checks `no-claude-in-workflows` and `workflow-policy` enforce this.
- `actions/checkout` with `persist-credentials: false`. A timeout on every job. No cache, artifact, or script-eval action.
- Never put `${{ ... }}` in a `run:` value: pass values through `env:` and use the shell variable.
- `runs-on` is a configured runner label or a matrix value; the architectures and labels come from `config/project.yaml`.
- The required check names are `quality-gate` (the aggregate job of `ci.yml`) and `pr-contract`. Each is defined by exactly one job,
  always runs, and has no `paths` filter, job-level skip, or `continue-on-error`.
- A PR that changes a workflow needs the "Workflow change justification" section of the PR template filled in (rule PC-6).
- Owned workflows are replaced by template-sync; a project change to one needs an entry in `.template/overrides.toml`.
