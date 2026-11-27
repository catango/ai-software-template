# CR-<n> Implementation plan

Starts only after the design pull request is merged. Each pull request runs in its own session and declares the issue and the
design pull request in its first lines.

## 1. The PRs

| # | PR (branch slug) | Content | Needs | Acceptance criteria |
|---|---|---|---|---|
| 1 | <slug> | <what the pull request adds> | <what must exist first> | AC-1 |

## 2. Checklist (each PR keeps its own)

- [ ] Draft PR opened with the implementation template, `Implements:` and `Design:` filled in
- [ ] Fixtures and tests written first for the PR's criteria
- [ ] Implementation done
- [ ] `./scripts/quality-gate.sh` passes; any skipped check is reported
- [ ] CI green
- [ ] Marked ready for review

## 3. User-only actions

| When | Action |
|---|---|
| <before or after which PR> | <what only the approver or the user can do> |

## 4. What this plan does not do

- <A change, decision, or setting that is outside this plan.>
