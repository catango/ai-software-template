# CR and ADR templates, `new-cr`, and `cr-layout` (plan row 5)

An implementation note for plan row 5 (`cr-templates`) of the software template design. It records what the row built, the
decisions the design left open, and the points for the approver. It changes no decision. Acceptance criterion: T-7.

## What the row adds

| Area | Files | Class |
|---|---|---|
| CR skeleton | `docs/cr/_template/{README,impact-analysis,design,acceptance-criteria,implementation-plan}.md` | owned |
| ADR skeleton | `docs/adr/_template.md` | owned |
| Script | `scripts/new-cr` | owned |
| Gate check (class `template`) | `scripts/checks/cr-layout.sh`, `scripts/checks/cr_layout.py` | owned |
| Locator | `docs/README.md` | seeded |
| Tests | `cr-layout`, `new-cr`, and the template tests in `tests/quality-gate/run.sh` (fixtures made at run time from the real skeleton); two tests in `template-dev/tests/test_manifest.py` | owned, template-only |
| Changed | `scripts/initialize-project` (no longer writes the locator), `.claude/rules/docs.md` (the directory is `docs/cr/CR-<n>/`), `tests/quality-gate/catalogue.tsv` (`cr-layout` is `present`) | owned |

The manifest needed no new pattern: `docs/README.md`, `docs/cr/_template/`, `docs/adr/_template.md`, and `scripts/new-cr` were already
classed, and the two check files fall under `scripts/checks/`.

## Decisions

1. **Where `new-cr` writes.** `<root>cr/CR-<n>/` for the first entry of `workflow.docs_roots` in `config/project.yaml`
   (`docs/` in a generated repository), read with the same strict reader as `pr-contract`. `--root` takes another entry of that
   list and nothing else. In this repository the first root is `docs/`, and the payload may hold no CR instance there (`manifest`
   fails on one), so the template's own change requests are made with `scripts/new-cr --root template-dev/docs/ <n> <slug>`.
   The directory has no slug (ADR-003 section 3); the slug names the branches and is printed, and written once into the CR
   `README.md`. The skeleton is always `docs/cr/_template/`, whatever the root. Nothing is created before every check has passed
   (arguments, root, skeleton, symbolic links on the path, existence of the target), and each file is opened exclusively.
2. **One source for the locator.** `docs/README.md` is a tracked seeded file. `initialize-project` used to write its own text for
   it; it now refuses a tree where the file is missing and keeps the file byte for byte (the same way it keeps `docs/PROJECT.md`).
   The text is therefore in one place, the `size-budget` class `docs-readme` covers it in the template as well, TC-2 is
   unchanged (the file is seeded, never owned, and an init PR has no reason to change it), and a test compares a generated copy
   with the tracked file. `docs/PLATFORM.md` keeps its constant because a preset replaces it.
3. **Where `cr-layout` reads its roots.** From `config/project.yaml` of the base commit (`GATE_BASE_REF`), like `operator-contract`:
   a head that drops a root from `docs_roots` does not take its change requests out of the check. An unreadable base, an option-like
   value, a base without the file, and a malformed `docs_roots` fail closed. Without `GATE_BASE_REF` the working tree is read.
4. **Scope.** The default file list. The check does not use the list to find change requests: it walks `<root>cr/` in the tree, so
   a changed-files run (`--base`) still sees the files of a change request that did not change (and a deleted file, which the
   list leaves out). The fixtures under `tests/*/fixtures/` are not under a record root, so `# scope: all` would change nothing.
5. **The template's own records.** `template-dev/docs/` is the second root of the template's configuration, so it is covered now.
   With no instance there, and no `cr/` directory at all, the check passes; the first change request there is judged like any
   other (the fixtures include a second root and an empty tree).
6. **What counts as an unqualified reference.** The design says a local `#<n>` and a qualified reference are accepted and "nothing
   else". Prose cannot be parsed for every possible reference, so the check rejects the two forms that name a repository without
   saying whose it is: `<repo>#<n>` and `<repo>/ADR-<n>` (also `CR`). Accepted: `#<n>`, `<owner>/<repo>#<n>`, `<repo>:ADR-<n>`; a bare
   `ADR-<n>` or `CR-<n>` is a record of this repository. Link targets, URLs, and fenced code are not read. This is a choice the
   approver may tighten or loosen (see the questions below).
7. **Extras.** The check also holds `docs/cr/_template/` to the layout (so a broken skeleton fails before `new-cr` copies it),
   reads every Markdown file of a change request and of `<root>adr/` for the reference rule, and fails a directory under `cr/` that
   is not `CR-<n>` or `_template`. A plan row must name an ID that `acceptance-criteria.md` defines, not any ID-shaped word.

## Size budgets (checked against ADR-010)

| File | Budget | Size |
|---|---|---|
| `docs/cr/_template/README.md` (`cr-readme`) | 50 lines, 4096 bytes | 25 lines, about 1 KiB |
| `docs/README.md` (`docs-readme`) | 80 lines, 8192 bytes | 23 lines, about 1.2 KiB |

`.template/budgets.toml` matches ADR-010 for both classes. The other CR files and the ADR template are reported, not gated, as
ADR-010 decision 2 says; the largest is `adr/_template.md` at 44 lines. No file is near a budget.

## Points for the approver

- **Unqualified references (decision 6).** Is rejecting `<repo>#<n>` and `<repo>/ADR-<n>` the intended reading of "nothing else"?
- **`ADR-<n>` of the same repository.** The check accepts a bare `ADR-<n>`. If the approver wants every ADR reference qualified,
  `cr-layout` would need the repository's own name, which the configuration does not hold.
- **The criteria ID form.** `AC-1` (letters, a hyphen, digits) is what the skeleton uses and the check accepts any prefix, so
  a project may use `T-1`. A different form (`AC1`) fails.
- **TC-1 and TC-2 questions** stay open and are not touched here. `docs/README.md` was already a seeded file in the manifest.

## Review follow-up

A review of the merged row found four nits; each is fixed with a test in `tests/quality-gate/run.sh`. No design decision changes: the
design text of `cr-layout` ("every plan row names a criterion ID") is met by point 4 as written.

1. **The cap of 200 Markdown files fails closed.** `cr-layout` still reads at most 200 Markdown files of a change request directory
   (a bound on work), but reaching the cap is now a finding ("more than 200 Markdown files; not all were checked"). Exactly 200 pass.
2. **`new-cr` leaves no partial directory.** The five files are written into a hidden sibling directory (`.CR-<n>.new-<pid>`) that is
   renamed to `CR-<n>/` when all are written. On any error the run removes what it created (the temporary directory, and `cr/` or the
   root when this run made them), and never anything that existed before. The refusal when the target exists, and the containment
   checks, are unchanged and come first.
3. **`<n>` is filled in only where it is the issue number.** The script replaces `CR-<n>` (also in `design/CR-<n>-<slug>` and
   `impl/CR-<n>-<slug>`) and `#<n>` when they stand alone, and nothing else, so `ADR-<n>`, `<repo>:ADR-<n>`, and
   `<owner>/<repo>#<n>` keep their `<n>`. The skeleton did not change: all its `<n>` are in the replaced forms, and a test shows none is
   left after the replacement.
4. **Only a criterion prefix counts in a plan cell.** A token of the `Acceptance criteria` cell is a criterion reference when its
   prefix is one that `acceptance-criteria.md` of that change request uses; `PC-8` or `ADR-5` next to `AC-1` is prose. A defined prefix
   with an undefined number (`AC-99`) still fails, and a cell with no token of a defined prefix still fails ("names no criterion ID").
   Chosen over an explicit list form because it is the strictest reading that needs no new syntax and keeps the design wording. Its limit:
   a typo that changes the prefix (`AD-1` for `AC-1`) next to a valid ID is prose and passes; with no valid ID in the cell it still fails.

## Not in this row

`scripts/cr-context` and `scripts/cr-criteria` (row 6) and the `start-cr` Skill (row 6) are the readers and starters of these
records. `owned-drift` (row 8). `cr-layout` does not check statuses, the issue number against the directory, or the ADR header.
