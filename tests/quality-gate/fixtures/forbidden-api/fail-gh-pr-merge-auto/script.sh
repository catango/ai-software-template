#!/usr/bin/env bash
set -euo pipefail
gh pr merge "$N" --auto --squash
