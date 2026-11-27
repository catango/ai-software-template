---
paths:
  - "**/operator/**"
  - "**/operators/**"
  - "scripts/operator*"
---

# Operator scripts

An operator script changes or inspects a machine that Claude Code never reaches. The configured `operator_paths` in
`config/project.yaml` list them. Claude Code writes and reviews them here; the operator runs them.

- **Never run one**, and never reach, probe, or configure the machine they are for. Reading and editing the script text is allowed.
- **Dry run is the default.** The script has an apply mode that needs an explicit `--apply`; without it, it only reports what it would do.
- **Release tags only.** An apply run refuses unless `HEAD` equals a release tag that is contained in the default branch
  (`scripts/lib/operator_guard.py`). Never apply from a branch or an untagged commit. By default the guard is offline and reads the
  tag and the default branch from the local clone, so a tag made on the operator machine would pass; an apply run on a machine that
  can reach the remote passes `--verify-remote <remote>` (or `verify_remote=`), which also requires the tag on the remote.
- **Credentials are prompted for or short-lived**, kept inside the script process, never stored, never read from arguments or from
  the environment, never printed, and never passed to a child process on its command line.
- **Fact collection is read-only, enforced in code**, not by a comment: no write call and no state-changing command on the read path.
- **Output is safe to paste** into an issue or a PR: no secret, no address that identifies a person or a machine, no raw log dump.
- **No infrastructure change.** An operator script prepares or inspects a machine; it does not create, change, or destroy
  cloud or network resources.
- Write the script in Python. No `shell=True`, `os.system`, `eval`, or `exec`. Start a subprocess only with a literal argument list
  and only for a program in `operator_commands` of `config/project.yaml`. The gate check `operator-contract` rejects the rest.
- A PR that changes an operator path fills in the "Operator script review" section (rule PC-8).
