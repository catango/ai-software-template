#!/usr/bin/env bash
set -euo pipefail
gh api graphql -f query='mutation { submitPullRequestReview(input: {}) { clientMutationId } }'
