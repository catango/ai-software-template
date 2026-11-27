# Public repository rules

This repository can be public, and a private one can become public. Treat every file, commit message, PR text, and log as published.

- Never put these in a file, commit, PR text, or log: cloud tenancy or resource identifiers, IP addresses, usernames,
  email addresses, keys, fingerprints, tokens, or passwords. Use variables and placeholders.
- A secret that was committed is compromised even after it is removed: stop, tell the user, and do not try to rewrite history.
- Pin every version, tool, and Action to an exact version or a full commit SHA, and verify a checksum or signature. Never use `curl | sh`.
- Examples and fixtures use documentation values: the `example.org` domain, the documentation address blocks, and made-up names.
  Build a value that looks like an identifier from fragments at run time instead of committing it.
- Findings and logs name the rule and the location, never the matched text.
- The gate check `public-repo-hygiene` runs in every repository, public or private. Do not weaken or skip it.
- The deny rules cover only Claude Code's own commands. They do not cover what a script runs, a command typed by a person,
  or a push from another machine: keep branch protection procedural where the platform cannot enforce it.
