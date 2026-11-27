# Review feedback (plan row 7)

An implementation note for plan row 7 (`review-feedback`) of the software template design: the Skill `address-review-feedback` and
the script `scripts/review-threads`. It records what the row built, how GitHub exposes the edit of a comment by another account
(the precondition of the plan row), the reading of the rule "not edited by any other account" that the script implements, what is
not proven without a real pull request, and the points for the approver. It changes no decision.

This note is written in steps; the sections below are completed as the row is built.

## Edited comments: what GitHub exposes

Sources (public, read on 2026-11-27): the GraphQL schema reference that GitHub publishes with its documentation
(`docs.github.com/public/fpt/schema.docs.graphql`), and the documentation pages "Tracking changes in a comment" and "Managing
disruptive comments" (`docs.github.com/en/communities/moderating-comments-and-conversations/`). The REST schema description could
not be fetched in this session, so the REST statement below is the only one in this section that rests on knowledge and not on a
source read here.

**Who can edit.** The author of a comment can edit it. Anyone with write access to the repository can edit comments on pull
requests (the page "Managing disruptive comments"). So a comment that carries the approver's name can have had its text changed by
another account that holds write access, for example the machine account itself.

**GraphQL.** The objects `PullRequestReviewComment` (a review comment, inside a thread) and `PullRequestReview` (a review summary)
implement the interface `Comment`, which carries the fields below. The text after each colon is the schema's own description.

| Field | Schema description | Use here |
|---|---|---|
| `author` | The actor who authored the comment | The account ID (`databaseId` of `User` or `Bot`) is compared with `approver_id` |
| `editor` | The actor who edited the comment | The LAST editor only. Null when never edited (or when the editor is unknown) |
| `lastEditedAt` | The moment the editor made the last edit | Null means never edited |
| `includesCreatedEdit` | Check if this comment was edited and includes an edit with the creation data | True when the history holds the creation revision. A flag of "was edited"; not an author or editor |
| `userContentEdits` | A list of edits to this content | The whole history: each node is a `UserContentEdit` |
| `UserContentEdit.editor` | The actor who edited this content | Who made THAT edit |
| `UserContentEdit.editedAt` | When this content was edited | Not used |
| `UserContentEdit.deletedAt`, `deletedBy`, `diff` | When and by whom the content of the revision was deleted; the summary of the changes | Not used (see below) |

**What this means.** `editor` names only the most recent editor. If another account edits the text and the approver then edits it
again (even to fix a typo), `editor` is the approver and `lastEditedAt` is the later time, but the text may still carry the other
account's change. Only the history, `userContentEdits`, names every editor. Reading `editor` alone is therefore not sound.

**Who sees the history.** "Anyone with read access to a repository can view a comment's edit history" (Tracking changes in a
comment), and the edited dropdown "contains a history of edits showing the user and timestamp for each edit" (Managing disruptive
comments). The repository is public, so the token the script runs with can read it.

**Limits of the history** (Tracking changes in a comment).

- Comment authors and anyone with write access can delete the content of a revision from the history. "The person who made the edit
  and when they made the edit is still visible", only the content is gone. So a deleted revision still names its editor, and the
  editor is what the rule needs.
- "A 100-edit maximum applies to ... pull request review comments": beyond it "the oldest intermediate edits are automatically
  removed, while the original content and the most recent 99 edits are always preserved". The editors of removed intermediate edits
  can no longer be established.

**REST.** The REST representation of a review comment or a review has `updated_at` and no editor field (from knowledge, not from a
source read in this session). `updated_at` changes with an edit but is not documented as changing only with an edit, and it names
nobody. It cannot answer "who edited", so the script does not use it.

### The reading the script implements

An item (a review comment or a review summary) is kept only when its author's account ID equals `approver_id` AND one of these holds:

1. It was never edited: `lastEditedAt` and `editor` are null, `includesCreatedEdit` is false, and the history is empty.
2. It was edited, and the editor of EVERY revision in the history is an account with ID `approver_id`, the last `editor` is that
   account too, and the history is complete.

Anything else is excluded and counted as `edited`: a revision by another account (a user, a bot, the machine account), a
revision or last editor that is null or has no ID (it cannot be established), flags that contradict each other (an `editor` without
`lastEditedAt`, a history without an edit mark), a history that is empty although the item was edited, a history that is not
complete (more revisions than were fetched), and a history at the limit of 100 edits (intermediate editors may have been removed).
This is the strictest reading that does not depend on a rule of GitHub that is not documented: when the answer is not fully
established, the item is excluded.

The author of a comment edited by the approver themself stays valid. An edit of an item by the approver is not an edit by "any other
account".
