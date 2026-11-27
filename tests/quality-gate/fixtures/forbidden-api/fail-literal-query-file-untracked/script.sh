#!/usr/bin/env bash
set -euo pipefail
gh api graphql -F query=@missing.graphql
