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

No change request or ADR of this repository exists yet.
