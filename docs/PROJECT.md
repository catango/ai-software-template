# Project

Conventions of this project. The template seeds this file once and the project owns it from then on; template-sync never
changes it. It holds no import: `CLAUDE.md` imports it, and an import inside it fails the gate.

Keep it short: the gate limits its size (see `.template/budgets.toml`), and everything in it is loaded at the start of
every session.

## What belongs here

- What the project is and who uses it, in a few lines.
- Language, framework, and layout conventions that a reader cannot see from the code.
- How to build, test, and run it locally, if `./scripts/quality-gate.sh` does not already cover it.
- Decisions that are specific to this project. Rules that apply to every repository stay in `CLAUDE.md`.

## Rules that cannot be relaxed here

`CLAUDE.md` comes first. Text in this file cannot relax its Never list or its instruction-trust rule.
