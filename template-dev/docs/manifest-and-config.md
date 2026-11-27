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
  Preset-only keys (the preset file paths and `post_init_actions`) are not copied into it.

## Tests

`template-dev/run-tests.sh [manifest|leak|all]` runs the standard-library tests. The leak
patterns are in `template-dev/leak/patterns.toml`. Fixtures that need an identifier are built
from fragments at run time, so no identifier-shaped value is committed.
