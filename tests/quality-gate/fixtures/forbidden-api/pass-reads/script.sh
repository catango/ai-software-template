#!/usr/bin/env bash
set -euo pipefail
gh api "repos/$OWNER/$REPO/pulls/$N/reviews"
gh api "repos/$OWNER/$REPO/pulls/$N/reviews/$ID/comments"
gh api "repos/$OWNER/$REPO/commits/$SHA/check-runs"
gh api "repos/$OWNER/$REPO/check-runs/$ID/annotations"
gh api "repos/$OWNER/$REPO/commits/$SHA/status"
gh api -X GET "repos/$OWNER/$REPO/rulesets"
gh api "repos/$OWNER/$REPO/collaborators" --paginate
gh api "repos/$OWNER/$REPO/git/ref/tags/v1.0.0"
gh api "repos/$OWNER/$REPO/pulls/$N/merge"
gh api "repos/$OWNER/$REPO"
gh api graphql -f query='query { viewer { login } }'
curl -sS -H "Accept: application/vnd.github+json" "https://api.github.com/repos/$OWNER/$REPO/releases/latest"
git fetch origin
git push origin HEAD
git for-each-ref --format='%(refname)' refs/tags

