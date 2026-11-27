#!/usr/bin/env bash
# Shared helpers for scripts/checks/*.sh. Sourced, never run directly.
# Check contract : file list on stdin, findings as path:line: message on
# stdout, exit 0 (clean), 1 (findings), 77 (nothing applicable), 78 (required tool missing).
set -euo pipefail

# gate_select REGEX: keep the paths on stdin that match REGEX.
gate_select() { grep -E -- "$1" || true; }

# gate_require_tool NAME: end the check with 78 when NAME is not on PATH.
gate_require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then exit 78; fi
}

# gate_shebang FILE: print the first line when it starts with "#!", else nothing.
gate_shebang() {
  local two='' first=''
  IFS= read -r -n 2 two <"$1" 2>/dev/null || true
  if [[ $two == '#!' ]]; then
    IFS= read -r first <"$1" 2>/dev/null || true
    printf '%s' "$first"
  fi
}

# gate_is_shell FILE: *.sh, *.bash, or a shell shebang.
gate_is_shell() {
  case $1 in *.sh | *.bash) return 0 ;; esac
  [[ $(gate_shebang "$1") =~ ^#!.*[/\ ](bash|sh|dash|ksh|zsh)([[:space:]]|$) ]]
}

# gate_is_text FILE: true for a non-empty file without NUL bytes.
gate_is_text() { grep -Iq . -- "$1" 2>/dev/null; }

# gate_workflow_files: workflow files among the paths on stdin.
gate_workflow_files() { gate_select '^\.github/workflows/[^/]+\.ya?ml$'; }

# gate_all_workflow_files: workflows plus local action definitions.
gate_all_workflow_files() { gate_select '^\.github/(workflows/[^/]+|actions/.+)\.ya?ml$'; }

# gate_linter_line: normalise "path:line:col: text" to "path:line: text" (stdin to stdout).
gate_linter_line() { sed -E 's/^([^:]+):([0-9]+):[0-9]+: /\1:\2: /'; }

# gate_relative: strip the working directory from the start of each line (some linters print absolute paths).
gate_relative() { awk -v root="$(pwd -P)/" 'index($0, root) == 1 { $0 = substr($0, length(root) + 1) } { print }'; }
