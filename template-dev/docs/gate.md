# Quality gate, install engine, and CI (plan row 2)

An implementation note for plan row 2 (`gate`) of the software template design. It records what the row built,
how the seeded code differs from its origin, and the points where the design, the security baseline, and the code
disagree. It changes no decision. The seed commit is in `template-dev/provenance.toml`.

## What the row adds

| Area | Files | Class |
|---|---|---|
| Entry point and shared helpers | `scripts/quality-gate.sh`, `scripts/lib/gate-lib.sh` | owned |
| Generic checks (seeded) | `bash-hygiene`, `shellcheck`, `yaml`, `markdown`, `links`, `actionlint`, `python`, `gate-tests`, `public-repo-hygiene`, `workflow-policy` (with `.awk` and `.py`), `no-claude-in-workflows`, `toolchain` under `scripts/checks/` | owned |
| Template checks (new) | `forbidden-api` (`.sh`, `.py`, `.toml`), `size-budget` (`.sh`, `.py`) under `scripts/checks/`; `.template/budgets.toml` | owned |
| Install engine and CI manifest | `toolchain/install`, `toolchain/toolchain_engine/`, `toolchain/ci-manifest.toml`, `toolchain/python/ci.in`, `ci.lock` | owned |
| CI | `.github/workflows/ci.yml`, `scripts/lib/ci_matrix.py` | owned |
| The template's own CI | `.github/workflows/template-ci.yml` | template-only |
| The template's own configuration | `config/project.yaml` (drives its CI matrix) | seeded |
| Gate fixtures | `tests/quality-gate/` (`run.sh`, `catalogue.tsv`, `fixtures/`) | owned |
| Template tests | `template-dev/tests/test_ci.py`, `template-dev/tests/toolchain/`, `template-dev/run-tests.sh [engine\|ci\|gate]` | template-only |

The manifest gained one pattern, `tests/quality-gate/` (owned): the gate's fixtures travel with the checks so that every
repository runs the same fixtures (ADR-009 decision 11). Nothing else needed a class.

Not seeded, on purpose: `dependabot-config` and `codeowners` (they need the seeded `dependabot.yml` and `CODEOWNERS`, which no
row has written yet), `ansible` and `operator-contract` of the platform (the generic `operator-contract` is a later row). The
catalogue lists them as `planned`.

## How the seeded code was changed

- Comments name no record of the origin repository; messages and rule ids (`WP-1` to `WP-16`) stay.
- `workflow-policy`: the default runner label is gone. Allowed labels are the optional key `runner_labels`, or else every
  `runner` and `runner_private` of `architectures` in `config/project.yaml` (read from the base commit under `--base`), plus the
  hosted base label `ubuntu-latest` (see point 2 below).
- `workflow-policy`: the aggregate rule of WP-16 also applies to `template-ci` in `template-ci.yml` (it was written for
  `quality-gate` only, and `template-ci.yml` has an aggregate with `if: always()` as ADR-009 decision 1 says).
- `workflow-policy`: WP-13 accepts one computed matrix (point 1 below).
- `quality-gate.sh`: a check sees every tracked file except the inputs of tests (`tests/*/fixtures/`), unless its header says
  `# scope: all`; `public-repo-hygiene` does, so a token in any fixture is still found. An unknown scope fails the gate.
- `public-repo-hygiene`: an email is allowed only in the GitHub noreply form or with the local part exactly `noreply` or
  `no-reply`; an IPv6 pattern was added (eight groups, or a compressed form; the documentation prefix is allowed).
- `actionlint`: `-oneline` output instead of a Go template (the generated-tree scan treats `{{...}}` as an unfilled placeholder).
- `public-repo-hygiene`: the finding says "cloud resource identifier".
- `toolchain`: validates `toolchain/ci-manifest.toml`; it needs no configuration file.
- The engine names no architecture and no profile. The CI manifest declares its architectures in a table
  `[architectures]` (name to the `uname -m` values that select it) and every download needs an artifact and a SHA-256 for
  each declared architecture and for no other. A profile is any name that tools list (`gate` here). Apt tools stay in the
  schema and are reported as "not installed by this engine". The engine does not read `config/project.yaml`; `ci_matrix.py`
  checks that every configured architecture is declared.
- `forbidden-api` keeps its rules in `forbidden-api.toml`, so the check holds no forbidden literal itself. It reads shell
  (a small tokenizer: quotes, command substitutions, pipelines), Python (the syntax tree), and the text of workflows. A
  Python file may pass a variable URL to an HTTP client only if the data file lists it with a reason (one entry: the engine's
  downloader, GET only, no credentials).
- `size-budget` implements the ADR-010 budget file: classes with `max_lines`, `max_bytes`, `max_chars` (the `description:` of a
  Skill), per-key raises from `[[budget]]` entries of `.template/overrides.toml` (printed on stderr as `raise: ...`), and a
  failure for a whole-file override of `budgets.toml`. Not built: the always-loaded group, `output-budget`, `skill-layout`
  (later rows; they need the files of rows 4 to 6).
- Fixtures: runner labels and architectures are neutral names; identifier-shaped values are built at run time.

## Points for the approver

1. **WP-13 and a matrix computed from the configuration.** ADR-009 decision 2 (as implemented by `workflow-policy`) lets
   `runs-on` be a literal or a matrix value from a *literal* matrix, so a matrix taken from `fromJSON(...)` fails. The design
   (ST-9, ST-10, T-3, T-21) wants one owned `ci.yml` that is identical in every repository, names no architecture or release
   label, and picks the runner by the repository's private flag. A literal matrix cannot do all three: it would name the
   architectures in an owned file, and `runs-on` cannot be chosen by a flag. This row therefore uses a `plan` job that computes
   the matrix (`scripts/lib/ci_matrix.py`) and a narrow exception in WP-13: only in `ci.yml`, only for
   `matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}` with `runs-on: ${{ matrix.runner }}`, where the job needs `plan`, and
   `plan` runs on a base label, has no `if:`, and exports the output of its step `plan`. Fixtures cover each way to break it.
   The alternative is a literal matrix that `initialize-project` renders from the configuration (and `template-sync` would
   have to render again); that changes the "owned and identical" rule. The choice is the approver's.
2. **A base runner label.** The `plan` job, the aggregate, and a leg that has nothing to run need a label before any
   configured label is known. They use the hosted alias `ubuntu-latest`, which names no architecture and no release. It is
   not pinned to an image release; these jobs install nothing and run only standard-library Python.
3. **`actions/checkout`** is pinned to the commit of the tag the origin repository uses; the pin was checked against the
   upstream tag.
4. **The leak scan exempts `toolchain/ci-manifest.toml`** for architecture names, like `config/project.yaml`: it is data that
   declares the architectures it has artifacts for.
5. **`config/project.yaml` of the template** holds only `architectures` for now. The later rows add the identity keys when
   `pr-contract` needs them.
6. **An architecture not declared in the CI manifest** (a configuration with another name) fails the plan job with a clear
   message. A repository that needs one adds artifacts for it to the CI manifest as an override. The fixture configurations of
   `template-dev/` use declared names, and a test plans the CI matrix of a tree generated from each.
7. **Policy from the base commit.** On a pull request the plan reads `config/project.yaml` from the base commit, and the gate
   gets `GATE_BASE_REF` set to it (the policy inputs of `workflow-policy`), so a change cannot blank its own runner or widen
   its own policy. Where the base has no configuration yet (the first one) and on a push, the checkout is read. A change to the
   architectures therefore takes effect in the CI of the next pull request. The planner refuses a self-hosted label in any case
   and, when `runner_labels` is set, a label outside it.
8. **`template-ci.yml` is in this PR** although the plan lists `ci.yml` only: without it the `manifest` and `leak` tests of row 1,
   the engine tests, and the CI tests would never run in CI, and T-14 asks for them. It has no `generate`, `sync`, or `skills`
   job yet; those arrive with their rows. Its aggregate `template-ci` follows the same rules as `quality-gate`.
