---
paths:
  - "**/*.sh"
  - "scripts/**"
---

# Shell scripts

- Start with `#!/usr/bin/env bash` and `set -euo pipefail`. Quote every expansion. The script passes ShellCheck with no suppression
  that lacks a reason.
- Anything that deletes or overwrites has `--dry-run`, and the default for destructive work is to show what it would do.
- Never build a command from untrusted text and run it through a shell: no `eval`, no `bash -c "$value"`. Pass arguments as an array.
- Read credentials from nowhere but a prompt or a short-lived source inside the process; never from arguments, never print them.
- Print at most a compact result and end with one summary line that starts with the script name. Exit non-zero on failure.
- Every script has `--fixture` or `--offline` where it calls a network service, and a test that runs without network.
- A script does not call what only the approver may do: merge, review submission, auto-merge, rulesets, collaborators, repository
  settings, tags. The gate check `forbidden-api` rejects it; endpoints and GraphQL documents are string literals.
- Do not write bytecode or other generated files into the repository.
