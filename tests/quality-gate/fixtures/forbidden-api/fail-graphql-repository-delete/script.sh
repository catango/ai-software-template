#!/usr/bin/env bash
set -euo pipefail
gh api graphql -f query='mutation { deleteRepository(input: {}) { clientMutationId } }'
