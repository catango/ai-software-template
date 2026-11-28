# Manifest and configuration schema (skeleton)

An implementation note for plan row 1 of the software template design. The design fixes the
classes, the one-configuration rule and the `template-only` removal; it leaves the file
formats to the implementation. This note records what the skeleton chose. It changes no
decision of the design.

## Manifest (`.template/manifest.toml`)

- TOML, read with the standard library. Three lists under `[classes]`: `owned`, `seeded`,
  `template-only`.
- Entries are patterns, not one line per file, so later pull requests add files without
  editing the manifest in most cases. Syntax: an exact path; `dir/` for everything below it;
  `*` and `?` inside one path segment; `**` across segments.
- A tracked file must match exactly one class. No match and two matches both fail the
  `manifest` test. A pattern that matches no file is allowed (the design lists files that later
  pull requests add).
- `template-dev/layout.toml` lists one representative path per row of design section 3 with its
  expected class, so a pattern that would put a future file in two classes fails now.
- `.template/state.toml` and `.template/overrides.toml` are classed `seeded` but the template
  never tracks them: `initialize-project` writes `state.toml`, and refuses to run when it exists.
- `docs/adr/` and `docs/cr/` instances are not classed. The template tracks none, so one fails
  the `manifest` test.
- `LICENSE` is classed `seeded`: it is copied once and the project may replace it. The
  approver may prefer another class.

## Configuration (`--config`)

- TOML. The schema is `.template/config.schema.toml` (owned, declarative); the validator is
  `scripts/lib/template_config.py`. Unknown keys, missing required keys and wrong types fail
  before anything is written.
- Preset files (`docs/PLATFORM.md`, `.claude/deny.platform.txt`) are named by relative paths
  inside the configuration's own directory. An absolute path or one that leaves that directory
  is refused.
- `initialize-project` writes `config/project.yaml` from the validated configuration.
  Generation-only keys (`visibility`, the preset file paths, `post_init_actions`) are not copied into it.

- `workflow.docs_roots` (optional, default `["docs/"]`) names the directories that hold the CR and ADR records. The paths of
  `workflow.operator_paths` and `workflow.docs_roots` have the narrow form that `pr-contract` can read from `config/project.yaml`
  (see `template-dev/docs/pr-contract.md`); the schema refuses anything wider.

## Tests

`template-dev/run-tests.sh [manifest|leak|all]` runs the standard-library tests. The leak
patterns are in `template-dev/leak/patterns.toml`. Fixtures that need an identifier are built
from fragments at run time, so no identifier-shaped value is committed.

## `initialize-project`

- `--config FILE` is required. `--dry-run` prints the plan and writes nothing. `--root DIR`
  initialises another tree (the tests and the later dry run use it). `--template-commit SHA`
  records the template commit in `.template/state.toml`; without it the key is omitted,
  because a copy made by "Use this template" has no link to a commit.
- Order: refuse when `.template/state.toml` exists; read the manifest; validate the
  configuration and the preset files; refuse when any file has no class or two; remove the
  `template-only` paths and the directories they leave empty; write the seeded files; write
  `.template/state.toml` last (version, source, SHA-256 of every owned file, empty
  `[overrides]`).
- A seeded file that the copy already has (`README.md`, `config/project.yaml`, `.github/CODEOWNERS`, `docs/PLATFORM.md`,
  `.claude/deny.platform.txt`) is overwritten from the configuration. `docs/README.md` and `docs/PROJECT.md` are seeded files
  that the template carries and `initialize-project` keeps as they are (plan row 5 moved the locator out of the script: the
  tracked file is the only source; init refuses a tree where it is missing).
  Without preset files, `docs/PLATFORM.md` gets a neutral text and `deny.platform.txt` is empty.
- The walk skips `.git` and `__pycache__`, and the script disables bytecode writing, so a
  direct run leaves no bytecode in the repository. A symbolic link to a directory counts as a
  path: removed when `template-only`, refused when kept. Writes never create directories
  outside the root.
- It prints the configured post-init actions, then the rulesets for a public repository or a
  note for a private one. It imports only local-file modules and starts no process; the
  `manifest` test checks that on the syntax tree.

## Seeded community and security files

The manifest classes three files as seeded that the template now carries: `.github/dependabot.yml`, `.github/CODEOWNERS`, and
`SECURITY.md`. They are written once and are the project's from then on.

- `.github/dependabot.yml`: version updates only, an entry for `github-actions` on `/`. It names no other ecosystem: the template has
  no `builder` Python profile, only the CI manifest and `toolchain/python/ci.in` and `ci.lock`. A project adds an entry for each
  ecosystem it uses. Nothing is merged automatically (rule PC-7 governs the `dependabot/` branches). The gate check
  `dependabot-config` rejects a file without the Actions entry or with `open-pull-requests-limit: 0`.
- `.github/CODEOWNERS`: exactly `* @<approver_login>`. The template's own copy names the template's approver. `initialize-project`
  writes the line again from `github.approver_login` of the configuration it is given (also in the dry run listing); a generated tree
  never keeps the template's line. This is what makes the approver's review count under code-owner review, which the printed
  `main-protection` ruleset requires. The gate check `codeowners` compares the file with `config/project.yaml`.
- `SECURITY.md`: generic text (private vulnerability reporting through the Security tab, no credentials in a report, what to expect,
  scope). It has no address and no name.
- The `approver_login` pattern of the configuration schema is stricter than before: it must end in a letter or digit and has at most 39
  characters, the form that the `codeowners` check and the public-repo hygiene check accept for the one line. A login outside it fails
  before anything is written (exit 2). `owner` and `machine_login` keep the looser pattern.
- Init PR (TC-2): the CODEOWNERS rewrite is a write of a seeded file, so it needs no exception.
- Sync (`template-sync.md`, point 5): the three paths reach a project generated before this release as new seeded paths. The sync
  writes `.github/CODEOWNERS` from the project's own `approver_login`, never from the tag's file.
- Tests: `test_manifest.py` (written from the configuration, dry run listing, invalid logins), `test_dry_run.py` (the generated-tree
  check), `template-dev/tests/sync/test_sync_seeded_files.py`, and the fixtures of the two checks in `tests/quality-gate/`.

## Points for the approver

These are choices the design left to the implementation or where it is ambiguous.

1. `LICENSE` is classed `seeded` (see above).
2. The printed rulesets render ADR-009 decision 8 minimally: `main-protection` has no
   bypass, one approving review with code-owner review, and the two required checks with the
   GitHub Actions application as source (no "up to date" requirement); `release-tags` covers
   `v*` tags with the repository administrator role as the only bypass. Parameters the
   decision does not state use conservative values. The JSON has not been applied to a
   repository. It needs checking before the real run of design section 12 (b).
3. The payload scan lets only `config/project.yaml` carry architecture names and runner
   labels, because the configuration supplies them. The template's own copy of that file
   (needed from the `pr-contract` pull request on) is classed `seeded` by the design and is
   covered by the same exemption.
4. `visibility` is used while generating and is not written to `config/project.yaml`: the
   design says workflows read `github.event.repository.private`, not the configuration.
5. **Design wording (the seeding PR).** Design section 3 lists `dependabot.yml` among the seeded files and decision D9-3 says that
   `dependabot.yml` is the only community or security file that ADR-008 ships in v1; section 8 does not name the checks
   `dependabot-config` and `codeowners`. The manifest already classed `CODEOWNERS` and `SECURITY.md` as seeded, so the seeding PR ships
   all three and both checks. `CODEOWNERS` is what makes the approver's review count under code-owner review. The approver decides
   whether the design text changes; it was not edited.
