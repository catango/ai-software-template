# Documentation

The locator for the records of this repository. Read this file first. Open a change request by its `README.md`, then the other
files by their outline (`rg -n '^#' <file>`) and by line range. Open an ADR by heading and range. Do not read a record whole.

| Path | Content |
|---|---|
| [PLATFORM.md](PLATFORM.md) | Where the project runs and the constraints of its platform |
| [PROJECT.md](PROJECT.md) | The conventions of this project |
| `cr/CR-<n>/` | One change request per issue number of this repository, with the five files of the skeleton |
| [cr/_template/](cr/_template/) | The skeleton. Create a change request with `scripts/new-cr <n> <slug>` |
| `adr/ADR-<n>-<slug>.md` | Architecture decision records, numbered in this repository from one |
| [adr/_template.md](adr/_template.md) | The skeleton of an ADR |

The directories that hold `cr/` and `adr/` are listed as `workflow.docs_roots` in `config/project.yaml`. `scripts/new-cr` writes
into the first one. A reference to another repository is always qualified: `<owner>/<repo>#<n>` for an issue or a pull request,
`<repo>:ADR-<n>` for an ADR. The gate check `cr-layout` fails an unqualified one.

## Index

| Record | Status | Note |
|---|---|---|
| none yet | | |
