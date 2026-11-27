---
paths:
  - "docs/**"
---

# Documentation, change requests, and ADRs

- `docs/README.md` is the locator. Open a change request by its `README.md` first, then the other files by their outline
  (`rg -n '^#'`), and an ADR by heading and range. Do not read a record whole.
- A change request is a directory `docs/cr/<n>/` with the five files of `docs/cr/_template/`, created with `scripts/new-cr <n> <slug>`.
  The number is the issue number of this repository. ADRs are numbered in this repository, starting at one.
- A design PR changes only the records of its change request and the records it amends (rule PC-4). An implementation or change PR
  never edits an approved `design.md`, `acceptance-criteria.md`, or ADR (rule PC-5): propose a new design PR.
- A reference to another repository is always qualified: `<owner>/<repo>#<n>` or `<repo>:<record>`. Never a bare number.
- Acceptance criteria have unique IDs; every row of an implementation plan names the criteria it covers.
- Keep each file within its size budget (`.template/budgets.toml`). Split a long record by section instead of growing it.
- A record states its status, date, and the decision it supports. Do not put credentials, addresses, or personal data in it.
