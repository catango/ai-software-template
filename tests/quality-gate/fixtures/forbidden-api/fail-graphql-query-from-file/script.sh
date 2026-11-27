#!/usr/bin/env bash
set -euo pipefail
gh api graphql -F query=@docs/merge.graphql
