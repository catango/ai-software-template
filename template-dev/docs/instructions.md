# Instructions, guardrails, and the operator contract (plan row 4)

An implementation note for plan row 4 (`instructions`) of the software template design. It records what the row built, the
choices the design left open, the verification items, and the points where the design, the security baseline, and the documentation
disagree. It changes no decision. The seed commit is the fourth entry of `template-dev/provenance.toml`.

## What the row adds

| Area | Files | Class |
|---|---|---|
| Instruction file | `CLAUDE.md` (ends with the two imports) | owned |
| Seeds | `docs/PLATFORM.md`, `docs/PROJECT.md`, `.claude/deny.platform.txt` (empty) | seeded |
| Guardrails | `.claude/settings.json`, `.claude/required-deny.txt` | owned |
| Rules | `.claude/rules/{public-repo,bash,workflows,docs,multi-arch,operator}.md` | owned |
| Gate checks (class `template`) | `claude-md`, `deny-list`, `operator-contract` (`.sh` and `.py` each) under `scripts/checks/` | owned |
| Operator guard | `scripts/lib/operator_guard.py` | owned |
| Configuration | key `workflow.operator_commands` in the schema, the writer, the reader, and the template's own `config/project.yaml` | owned, seeded |
| Fixtures and tests | `tests/quality-gate/fixtures/{claude-md,deny-list,operator-contract}/`, new functions in `tests/quality-gate/run.sh`, `template-dev/tests/test_instructions.py` | owned, template-only |

The manifest needed no new pattern; every new file already had exactly one class.

## How the template carries the seeds, and how init writes them

- `docs/PLATFORM.md` in the template is the neutral text that `initialize-project` writes when no preset is given
  (`NEUTRAL_PLATFORM_DOC`); a test fails if the two differ. Init overwrites it from the preset when there is one (row 1, unchanged).
- `docs/PROJECT.md` is a seed that init does not touch: the copy made by "Use this template" carries the template's text, and the
  project owns it from then on. It holds no import.
- `.claude/deny.platform.txt` is an empty file in the template, which is what init writes without a preset.
- Neither seed is a template-only file, so the `leak` scan covers both. The platform-neutral text passes it.
- **Platform deny entries reach `settings.json` at generation.** The design wants `deny-list` to find the entries of the seeded list in
  `settings.json`, which is owned and identical everywhere. A preset with entries would therefore fail the gate in its first PR. Init now
  adds the entries of the preset list to `permissions.deny` once (after the core entries, in file order), records the SHA-256 of the file
  as written, and refuses an entry that is not of the form `Tool` or `Tool(specifier)`. Without a preset the file is byte-identical to the
  template's. Template-sync (row 8) overwrites owned files, so it would drop those entries unless it keeps them: see point 2 below.

## The checks

| Check | Reads | Fails on |
|---|---|---|
| `claude-md` | `CLAUDE.md`, the two imported files, `.template/state.toml` when it exists | a missing file; an import list other than the two lines `@docs/PLATFORM.md`, `@docs/PROJECT.md` at the end; an import in an imported file; a tracked `CLAUDE.md` other than the root one (including `.claude/CLAUDE.md`) or any `CLAUDE.local.md`; a missing instruction-trust section, Never section, or precedence sentence; a change-request or decision number in `CLAUDE.md`; a hash that differs from `state.toml` |
| `deny-list` | `.claude/settings.json`, `required-deny.txt`, `deny.platform.txt` | an entry of either list that `permissions.deny` lacks (compared as exact strings); a missing or malformed file; an entry that is not a deny rule |
| `operator-contract` | `config/project.yaml` of the base commit; the files under `operator_paths` | see below |

`claude-md` parses an import as Claude Code does (see the verification results) and is stricter on purpose. Size budgets are not checked
by `claude-md`: `size-budget` already covers the classes `claude-md`, `imported-doc`, and `rule`, and the three checks run in the same gate.

### `operator-contract`

The check is the generic rewrite on `operator_paths` (design section 12). The platform's check was specific to its own operator scripts
and its endpoint list and was not copied; only the idea of a base-commit policy and a fail-closed reader was kept.

- **Inactive** for an explicit empty `operator_paths` (the check exits 0 without judging a file). **Fails closed** when the configuration
  of the base commit is missing, unreadable, or lacks `operator_paths`, and when the base commit cannot be resolved.
- **Policy from the base.** `quality-gate.sh --base` sets `GATE_BASE_REF`, and so does the base-run step of `pr-contract.yml` (the check
  was added to it in the TC amendments PR, point 7); the check reads `config/project.yaml` from that commit with the strict reader of
  `pr_contract.config` (the same reader PC-8 uses) and never from the head. `scripts/pr_contract/` must sit beside the checks.
- **Python rules** (the syntax tree, nothing is run): no `shell=True`, `executable=`, `os.system`, `os.popen`, `os.exec*`, `os.spawn*`, `pty`, `eval`,
  `exec`, or dynamic import; a subprocess needs a literal argument list whose program is a bare name listed in `operator_commands`;
  no token read from the command line (an option or a variable named like a credential; `pat` and `auth` count as words of their own,
  so `GH_PAT` and `AUTH_HEADER` match and `path` and `author` do not) or from the environment (a credential-like
  key, a key that is not a literal, or the whole environment); an entry point defines `--apply` as a `store_true` flag that is off by
  default, so the dry run is the default; `set_defaults(apply=...)` may only say False. A file with no entry point (a library module) is not asked for `--apply`.
- A **shell script** under `operator_paths` is a finding: the rules cannot be checked in shell. Other files (data) are not judged.
- **The allowlist.** The design says "a subprocess outside the configured allowlist" and names no key. ADR-009 decision 14 names the key
  `operator_endpoints`, but that is a list of hosts that an operator script may call, which is a different thing. This row adds the
  optional key `workflow.operator_commands` (program names, default empty, so no subprocess is allowed). It is in the schema, the
  writer, and the strict reader, and the base commit supplies it. The host list is **not** implemented: see point 3.
- Static rules reject what they cannot read. A command built at run time fails, and no run-time guard backs it up in this row.

### `operator_guard.py`

`require_release(apply)` returns at once for a dry run. For an apply run it needs a clean work tree, a release tag
`v<MAJOR>.<MINOR>.<PATCH>` at `HEAD` (annotated or lightweight), and `HEAD` contained in the default branch: the `default_branch`
argument, else the target of `refs/remotes/origin/HEAD`, else the guard refuses. It runs local `git` commands with a literal argument
list, creates no tag, and fetches nothing. **Limit of the offline default:** the tag and the default branch are read from the local clone, so a tag
made on the machine that runs the script passes. The optional `--verify-remote REMOTE` (parameter `verify_remote`) also runs
`git ls-remote --tags` and needs one of the release tags at `HEAD` on the remote at the same commit (an annotated tag by the commit it points
at; a remote helper transport is refused). It is optional because it needs the remote to be reachable; `operator.md` tells an operator
who can reach it to use it. The test uses a throwaway bare repository as the remote. Its test uses throwaway repositories, and the tests write tag refs as files because the gate
forbids `git tag` in scripts.

## What the core deny list holds

The list is `.claude/required-deny.txt`: 111 entries, including a pattern for every GraphQL mutation name that `forbidden-api` rejects (a mutation name is a write by its nature, so the pattern catches no read; a test keeps the two files in step). It is the generic part only: merge and review, repository creation and settings,
rulesets, branch protection, secrets and variables, environments, collaborators, webhooks and deploy keys, tag refs and releases, the
credential directory of the GitHub CLI, pushes to the default branch and force pushes, `git tag`, `sudo`, commands that print the
environment, the SSH client family, and the key directory. It names no infrastructure tool, cloud, host, or architecture; a test
checks that. Two groups are **not** in it, on purpose:

- The Terraform, OpenTofu, and cloud-CLI rules. ADR-001 section 5 calls them "generic entries of the core list"; the design (section 5)
  and this row's brief put them in the platform's list. They are in neither list here. See point 1.
- Commit statuses, check runs, alert states, and security-feature endpoints. ADR-001 section 5 asks for a deny of `gh api` calls that
  *set* them, but one pattern cannot tell a write from a read of the same path, and ADR-010 decision 6 has Claude Code read check-run
  annotations through `gh api`. They stay under `ask` in `settings.json`, and `forbidden-api` rejects them in scripts (row 2).

## Verification results (design section 14)

Checked on 2026-11-27 against the current Claude Code documentation (the memory, permissions, and settings pages).

| Item | Result |
|---|---|
| Import syntax | `@path/to/import`; "relative paths resolve relative to the file containing the import". `@docs/PLATFORM.md` in the root `CLAUDE.md` therefore resolves to the root `docs/` |
| Nesting | "a maximum depth of four hops". The design says two imports and no nesting; that is stricter, and `claude-md` enforces it. The design did not state the documented depth; this is not a contradiction |
| Code | "Import parsing skips Markdown code spans and fenced code blocks". `claude-md` does the same, so `` `docs/PLATFORM.md` `` in backticks is text, and an `@x` in a code block is not an import |
| Approval | A dialog appears only for an import that resolves outside the working directory. Both imports are inside it |
| Loading | Imported files "load together with the CLAUDE.md that imports them", at the start of the session |
| Missing import | Not found in the pages read. `claude-md` fails when either imported file is missing, so the behaviour does not matter here |
| Rule scoping key | `paths:` in the frontmatter (not `globs`); "`paths` is the only field Claude Code reads from a rule". A YAML list or a comma-separated string. The rules here use a list of quoted globs |
| Rule loading | A rule without `paths` loads at launch; a rule with `paths` loads when Claude reads, writes, or edits a matching file. `**/*.sh` matches in subdirectories, `scripts/**` matches everything below `scripts/`. Brace patterns are supported (not used). Rules are found recursively. An unparsable frontmatter makes the rule load as if it had no `paths` |
| Deny syntax | `Tool(specifier)`. `Bash(cmd *)` needs the space before the star (`Bash(cmd*)` also matches `cmdx`); `Bash(git push * main)` is the documented form with the star after the subcommand. Rules are evaluated deny, then ask, then allow, and deny and ask match any subcommand of a compound command. A deny in any scope wins over an allow elsewhere. `Read` and `Edit` rules use gitignore syntax, `~/` is the home directory |
| Deny limits | The documentation says deny rules do not cover a script that reads or writes files itself or a subprocess. The design says the same (section 5) |
| Settings keys | `permissions.disableBypassPermissionsMode` is the string `"disable"` (not a boolean), as the platform file has it. `enableAllProjectMcpServers`: the detailed entry was **not** read; the key is used as the platform file uses it, with `false`. This stays open |
| `CLAUDE.local.md` | Still loaded next to `CLAUDE.md` and meant to be gitignored; the gate sees only a tracked one. A host-local file stays the residual risk of design section 5. Subdirectory `CLAUDE.md` files load on demand, and `.claude/CLAUDE.md` is also read, so `claude-md` rejects both |

The documentation contradicts nothing in the design. The design's wording "key names verified against the documentation" is met: the
scoping key is `paths`.

**ADR-010 budgets.** `.template/budgets.toml` already holds the values of ADR-010 decision 2 (`CLAUDE.md` 120 lines and 10 KiB,
the imported files 80 lines and 6 KiB, a rule 40 lines and 3 KiB, a `SKILL.md` 60 lines and 4 KiB, a Skill description 300
characters, `docs/README.md` 80 lines and 8 KiB, a CR `README.md` 50 lines and 4 KiB); nothing needed to change. The files of this row
use: `CLAUDE.md` 59 lines and 3.9 KiB, `PLATFORM.md` 6 lines, `PROJECT.md` 18 lines, the largest rule 25 lines and 1.8 KiB. **Not
built:** the 20 KiB limit on the always-loaded set (decision 2), which needs the Skill names (row 6) and belongs to the later
`size-budget` extension of ADR-010. The loading order for Skills (decision 4) is a row 6 matter. The context-discipline numbers of
decision 9 are in `CLAUDE.md`.

## Points for the approver

1. **Terraform and cloud-CLI deny entries.** ADR-001 section 5 puts them in the core list; the design section 5 and the brief of
   this row put them in the platform list, and the platform's own file says its preset adds no rule. This row follows the design: the
   core list names no infrastructure tool. The platform's preset PR must carry them in its `deny.platform.txt`, or the design should
   say they are core.
2. **Platform deny entries in an owned file (decided 2026-11-27: option 1, implemented in the TC amendments PR, see `template-dev/docs/pr-contract.md`; the text below is the question as it stood).** Init merges the entries of a preset's
   `deny.platform.txt` into the owned `.claude/settings.json`, because `deny-list` wants them there. (a) **TC-2 refuses it.** An init PR may
   write only seeded paths, and the merge changes an owned file, so the first init PR of a repository with a preset deny list fails
   `pr-contract` with `TC-2: written path is not seeded: .claude/settings.json`. A test (`InitPullRequestTC2` in
   `template-dev/tests/test_instructions.py`) runs TC-2 on the file list of a generated tree: it passes without preset entries, and
   with them the failure is exactly that path (the test was an expected failure until the decision; it is now a passing test). (b) **Template-sync**
   overwrites owned files and would drop the entries (row 8). Options: (1) TC-2 accepts, for `.claude/settings.json` only, a change
   that appends entries to `permissions.deny` that are all listed in the head's `.claude/deny.platform.txt`, and row 8 regenerates
   `settings.json` from the new core list plus the seeded list, so no override entry is needed; (2) the platform entries live only in
   the seeded list, `settings.json` stays byte-identical to the template, and `deny-list` checks the seeded list's syntax but not its
   presence in `settings.json`; the entries then reach Claude Code through a layer outside the repository (the platform's user-level
   settings), which the gate cannot verify; (3) init writes an override entry for `settings.json` and TC-2 accepts that file in an init
   PR; this needs the override format of row 8. **Recommendation: option 1.** It keeps the design's rule that the gate sees both
   lists in `settings.json`, makes the file a pure function of two inputs (so sync and `owned-drift` need no special case), and
   widens TC-2 by one narrow, checkable exemption. Nothing beyond the test is implemented until the approver decides.
3. **The subprocess allowlist and `operator_endpoints`.** A new optional key `workflow.operator_commands` carries the allowlist. ADR-009
   decision 14 describes `operator_endpoints` as read from the base commit and failing closed when missing while `operator_paths` is
   non-empty. The generic check does not judge hosts, because design section 12 does not list endpoint rules among the generic ones,
   so that key is not read. If hosts belong in the generic contract, the key and a literal-endpoint rule are a later addition.
4. **`operator.md` and `operator_paths`.** The rule is owned and identical everywhere, so its `paths:` cannot follow the project's
   `operator_paths`. It is scoped to `**/operator/**`, `**/operators/**`, and `scripts/operator*`; a project that uses another
   directory gets the rule only through `CLAUDE.md`'s Never list. The core deny list cannot name the project's operator path either, so
   "never run by Claude Code" is a rule and an entry for the preset list, not a core deny entry.
5. **Shell operator scripts.** A shell script under `operator_paths` fails `operator-contract`, because the subprocess, token, and dry-run
   rules cannot be checked there. The platform's scripts of this kind would need to be Python.
6. **Which checks belong on the base-against-head list of `pr-contract.yml`.** ADR-009 decision 14 lists the six policy checks
   (`workflow-policy`, `no-claude-in-workflows`, `forbidden-api`, `owned-drift`, `operator-contract`, `dependabot-config`).
   `operator-contract` belongs there: its policy (`operator_paths`, `operator_commands`) comes from the base commit and a pull
   request must not widen it. `claude-md` and `deny-list` do not: they have no policy input to read from the base, and the base's
   copy of the check would still compare the head's `CLAUDE.md` with the head's `state.toml`, and the head's `required-deny.txt`
   with the head's `settings.json`. What guards those files against a pull request that edits both sides is `owned-drift`
   (row 8), which reads the hashes from the base and needs an override entry. Until row 8 lands that gap is open in a generated
   repository; the code-owner review of the approver is the control.
7. **`operator-contract` was not on the list in this PR, on purpose (done in the follow-up, `template-dev/docs/pr-contract.md`, which also adds `cr-layout`).** A checker that is missing on the base commit
   fails `pr-contract` (ADR-009 decision 14 removed the bootstrap skip), so the pull request that adds the checker cannot also add it
   to the list without failing its own `pr-contract`. The follow-up, after this one is merged: in `.github/workflows/pr-contract.yml`
   add `operator-contract` to the id list of the step "Run the policy checks of the base against the head" and update the comment
   above it (the checks that a later row adds are `owned-drift` and `dependabot-config`; `claude-md` and `deny-list` are not on it, with
   the reason of point 6). In `template-dev/tests/pr_contract/test_policy_commands.py`: copy `scripts/pr_contract` into the base layout
   of `setUp` (the check reads its policy with that package), give the base configuration `operator_paths` and `operator_commands`
   (the check fails closed on a configuration without `operator_paths`), add `operator-contract` to the list asserted by
   `test_the_loop_runs_every_check_of_the_list`, and add two tests: a violation under the base `operator_paths` fails, and a head
   that replaces the checker, empties `operator_paths` in its own configuration, and adds a violation still fails.
8. **A tag not made by `git tag`.** The gate forbids `git tag` in any script, so the guard test writes tag refs as files in a throwaway
   repository. That is a test device and creates nothing in a real repository.
