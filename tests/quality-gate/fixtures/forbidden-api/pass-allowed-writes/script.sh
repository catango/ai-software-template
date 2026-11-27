#!/usr/bin/env bash
set -euo pipefail
gh pr create --draft --title "$TITLE" --body-file "$BODY"
gh api "repos/$OWNER/$REPO/issues/$N/comments" -f body="$TEXT"
gh api "repos/$OWNER/$REPO/issues" -f title="$TITLE" -f body="$TEXT"
gh api "repos/$OWNER/$REPO/pulls" -f title="$TITLE" -f head="$HEAD" -f base=main
gh api -X PATCH "repos/$OWNER/$REPO/pulls/$N" -F body=@body.md
gh api "repos/$OWNER/$REPO/pulls/$N/comments/$ID/replies" -f body="$TEXT"
gh api graphql -f query='mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) { clientMutationId } }' -f id="$PR_ID"
gh api graphql -f query='mutation($t: ID!, $b: String!) { addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $t, body: $b}) { clientMutationId } }' -f t="$THREAD" -f b="$TEXT"
gh api graphql -f query='mutation { addComment(input: {subjectId: "x", body: "y"}) { clientMutationId } }'
gh pr view "$N" --json state
gh pr ready "$N"

