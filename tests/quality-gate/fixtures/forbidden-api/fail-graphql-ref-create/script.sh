#!/usr/bin/env bash
set -euo pipefail
gh api graphql -f query='mutation { createRef(input: {}) { clientMutationId } }'
