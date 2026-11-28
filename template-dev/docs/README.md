# Template records

This directory holds the records of the template repository itself. It is `template-only`
(see `.template/manifest.toml`): `scripts/initialize-project` removes it, so no generated
repository carries it, and the payload keeps no change request or ADR instance under `docs/`.

## Numbering

- A change request is numbered by the issue number in this repository. Its files go in
  `template-dev/docs/cr/CR-<n>/`.
- ADRs start at `ADR-001` in this repository, in `template-dev/docs/adr/`.
- A reference to another repository is always qualified: `<owner>/<repo>#<n>` for issues and
  pull requests, `<repo>:ADR-<n>` for ADRs.

## Index

| Record | Where | Note |
|---|---|---|
| Software template design, ADR, acceptance criteria (T-1 to T-22), implementation plan | `catango/ai-lab-platform#39` (design: `catango/ai-lab-platform#40`) | Stays in the platform repository as the one transitional exception. Not copied here |
| [Manifest and configuration schema](manifest-and-config.md) | this directory | How the skeleton (plan row 1) fixes what the design left open |
| [Quality gate, install engine, and CI](gate.md) | this directory | What plan row 2 built, how the seeded code changed, and the points for the approver |
| [pr-contract](pr-contract.md) | this directory | What plan row 3 built (rules PC-1 to PC-9, TC-1, TC-2), how the seeded code changed, and the points for the approver |
| [instructions](instructions.md) | this directory | What plan row 4 built (`CLAUDE.md`, rules, guardrails, operator contract) |
| [CR and ADR templates](cr-templates.md) | this directory | What plan row 5 built (`new-cr`, `cr-layout`, the skeletons, the locator) and the points for the approver |
| [Skills and their scripts](skills-core.md) | this directory | What plan row 6 built (six Skills, `cr-context`, `cr-preflight`, `cr-criteria`, `pr-checkpoint`), the five verification points, and the points for the approver |
| [Review feedback](review-feedback.md) | this directory | What plan row 7 built (`address-review-feedback`, `review-threads`), how GitHub exposes an edit by another account, and the points for the approver |
| [template-sync](template-sync.md) | this directory | What plan row 8 built (`template-sync`, `sync-template`, `owned-drift`, the `sync` test), the `owned-drift` reading for `uses:` changes, the follow-up for `pr-contract.yml`, and the points for the approver |
| [Dry run](dry-run.md) | this directory | What plan row 9 built (`dry-run.sh`, the jobs `generate`, `sync`, `skills` on both architectures), the design section 11 table checked, the report format, the open follow-ups, and the points for the approver |
| [Release checklist](release-checklist.md) | this directory | What the approver does before the first tag and for every tag, and the real run of design section 12 (b) |

No change request or ADR of this repository exists yet.
