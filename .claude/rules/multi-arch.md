---
paths:
  - "toolchain/**"
  - "**/Dockerfile*"
  - "**/requirements*.txt"
  - "**/*.lock"
  - "**/package.json"
  - "**/pyproject.toml"
  - "scripts/**"
---

# More than one CPU architecture

The architectures of this repository are listed under `architectures` in `config/project.yaml`. Do not assume one.

- A download, image, or wheel that you add or change needs an artifact **and** a checksum for every configured architecture.
  The CI manifest `toolchain/ci-manifest.toml` fails the gate when one is missing or when it declares one that is not configured.
- Do not write an architecture name, a runner label, or an architecture-specific path in a script or workflow: read it from
  the configuration or from `uname -m` through the install engine.
- A script that works on one architecture only says so and fails early on the others, with a message that names the architecture.
- Where no runner exists for an architecture, CI reports `not run: <name>`. The change PR then carries the host-reported evidence line
  from `scripts/gate-evidence`, run on that host at the PR's head commit. Do not write the line by hand.
