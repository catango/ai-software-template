# Review feedback (plan row 7)

An implementation note for plan row 7 (`review-feedback`) of the software template design: the Skill `address-review-feedback` and
the script `scripts/review-threads`. It records what the row built, how GitHub exposes the edit of a comment by another account
(the precondition of the plan row), the reading of the rule "not edited by any other account" that the script implements, what is
not proven without a real pull request, and the points for the approver. It changes no decision.

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

## What the row adds

| Area | Files | Class |
|---|---|---|
| Skill | `.claude/skills/address-review-feedback/SKILL.md` (44 lines, 2.5 KiB, description 214 characters) | owned |
| Script (wrapper) | `scripts/review-threads` | owned (already listed in the manifest) |
| Code behind it | `scripts/lib/cr_tools/review.py`; additions to `client.py` (review reads, the reply, `head`/`base` repository names in `pull_data`) | owned |
| Tests | `template-dev/tests/skills/test_review_threads.py`; `test_skills.py` (the reply joins the list of the client's writes) and `helpers.py` (`pr_json` carries the repositories of the head and the base) | template-only |

The manifest needed no entry: `scripts/review-threads` was owned already, and the Skill and `review.py` match the existing patterns
(a test asserts all three classify as `owned`). Nothing was seeded: the platform repository has no such script or Skill.

## The script

- **`scripts/review-threads <pr> [--page N]`** prints one line of compact JSON (`pr`, `page`, `pages`, `threads`, `reviews`,
  `excluded`), an optional `cut:` line, and one summary line. A thread is `{thread, path, line, outdated, resolved, text}` (`text`
  is the approver's kept comments of the thread, in order, separated by a blank line); a review summary is `{review, state, text}`.
  Review comments are read inside their thread (every review comment belongs to one), so the one GraphQL query for threads
  carries them. Review summaries have their own query. Both are paged by cursor and written out as literals in `client.py`.
- **Identity.** The configuration is read at the BASE commit of the pull request (`?ref=<base sha>`; a test asserts the call). The
  script refuses when `approver_id` is missing there (`identity.py` keeps it optional for the other scripts), when the pull request is
  not authored by the machine account, and when its head is not a branch of this repository (a fork, a deleted fork, or another
  base repository). The refusals come before any review data is fetched.
- **Who is kept.** The author must be a `User` whose `databaseId` equals `approver_id`. The login is not compared: a renamed approver
  is kept, a recycled or look-alike login is not. A `Bot` with the same number is a bot (the ID spaces differ). Excluded items are
  counted: `look-alike` (an account with an ID, whose login is the approver's or within two characters of it, ignoring case,
  hyphens, and 0/1/5 for o/l/s), `bot`, `machine` (login of `machine_login`), `edited` (see above), `other` (any other account, no
  author, no ID, an organisation), and `unread` (comments of a thread beyond the 50 fetched, reviews beyond the pages fetched). None
  of their text is printed.
- **Output limits.** ADR-010 decision 8: a page closes at 5 items (unresolved threads, resolved threads, then review summaries) or
  once 4 KiB is reached, never splits an item, and holds at least one. Later items are named on `cut: <n> more items on page <p>
  (review-threads <pr> --page <p>)`. The approver's text is sanitized (control, bidirectional, zero-width, unassigned characters;
  CRLF becomes LF) and **not shortened**: decision 8 says it never truncates an approver's text, so one oversized item makes its page
  larger. The path is cut at 200 characters, a review state outside the five GitHub states prints as `UNKNOWN`, and the summary line
  is cut at 120.
- **`scripts/review-threads reply <thread-id> <commit>`** validates the thread ID (`PRRT_` and 1 to 90 characters of letters, digits,
  `_`, `-`) and the commit (40 lowercase hexadecimal characters), asks GitHub which pull request the thread belongs to (it must be
  this repository), runs the same checks and the same filter as the listing, refuses a thread that the listing would not print
  (on any page, resolved or not), refuses a commit that is not one of the pull request's own commits (the REST list of its commits,
  up to three pages of 100: a commit already on the base branch is an ancestor of the head too, but is not in that list, so "Addressed in
  commit <an old base commit>" cannot be posted), and then posts one reply with the GraphQL mutation `addPullRequestReviewThreadReply`,
  with the fixed text `Addressed in commit <sha>.` and no text from GitHub. It passes no review ID, so no review is created.
  **Idempotent:** before the mutation it looks in the thread's comments (the ones already fetched) for a comment by the machine account
  (a user, matched by login) whose text is exactly that fixed text; if there is one it posts nothing, prints `already replied`, and
  ends 0, so a retry after a mutation whose answer was lost does not post a second reply. The same text by another account, a reply
  naming another commit, or a reply with extra text does not count. A thread with more than 50 comments may hold the earlier reply
  beyond what was fetched; then the check cannot see it and a duplicate is possible.
- **What it never does.** It resolves nothing, submits no review, dismisses nothing, and merges nothing. Static tests scan the wrapper,
  `review.py`, and `client.py` for those operations (GraphQL names, REST paths, `gh pr` groups), assert that `review.py` and the
  wrapper do not even name them in a docstring, and assert that the writes of `client.py` are exactly four: the label add, the body PATCH,
  the ready mutation, and the reply mutation. The gate check `forbidden-api` passes on all of it.
- Exit codes and the summary line follow the other scripts: 0 done, 1 refused, 2 usage, 3 GitHub error; `--fixture DIR` replaces
  GitHub (the module text of `client.py` lists the files).

## The Skill

`address-review-feedback` runs the script, addresses each unresolved thread of the output (the `text` of an item is the approver's
instruction, nothing else is), commits and pushes, and runs `reply` with the full SHA. It never reads PR comments, review threads,
review bodies, or CI logs by another way (CI comes from `pr-checkpoint show`), never resolves, dismisses, re-requests review, approves, or
merges, stops on `refused`, and lists for the user whatever is unclear, conflicts with the design, or would change an approved design
record. It has the three ADR-010 headings, no `allowed-tools`, and the checks of `test_skills.py` apply to it (budget, layout, the script exists and is
executable, `--fixture` and a test that names it).

## Criterion T-11

| Part of T-11 | Evidence |
|---|---|
| Only items of the approver, matched by account ID | `WhoIsKept`: kept by ID, kept after a rename, a look-alike login, the approver's login with another ID, the same in other case, a bot with the approver's number |
| Bot, machine account, look-alike, edited-by-other excluded and only counted | `WhoIsKept` (counts by reason; the text of excluded items never reaches the output), `EditedByAnotherAccount` (each reading rule, a review summary edited by another account) |
| Refuses a PR not authored by the machine account and a fork PR | `Refusals`: both, a deleted fork, another base, the case of the repository name, a missing `approver_id`, a missing configuration, the refusal before any fetch |
| Configuration of the BASE commit | `Refusals.test_the_head_configuration_is_never_consulted`, `BaseCommitOfTheCall` (the `?ref=<base sha>` of the call) |
| Sanitized, limited output; text never executed | `Sanitized`, `Limits` |
| No call that resolves a thread, submits a review, or merges | `StaticChecks`; `test_skills.py` (the writes of the client); gate check `forbidden-api` |
| The reply | `Reply`: validation, an unknown thread, a thread the listing does not print, a commit off the head, the refusals of the listing, another repository, a failed write; `BaseCommitOfTheCall` (the mutation as an argument array) |
| The Skill | `TheSkill`, and the Skill checks of `test_skills.py` |

## What is not proven here

- **Real GitHub.** Nothing ran against a real review. The fixtures are GraphQL answers written from the schema, not recorded ones.
  The things only a real pull request can show: that the queries are accepted as written (`userContentEdits(first: 20)` under three
  nested connections, the inline fragments on `User` and `Bot`); that `databaseId` is the number the platform preset stores as
  `approver_id`; that `addPullRequestReviewThreadReply` without a review ID posts a visible reply at once; that the commits list of a pull request
  holds a commit that was just pushed; **that GraphQL cursors have the alphabet the script accepts** (see below); what `editor` and `userContentEdits` hold after an edit
  by the machine account in the GitHub web interface; and what a revision shows after its content was deleted. The run waits for row
  9 and the user. The reply mutation was chosen over the REST reply endpoint because it takes the thread ID the listing prints;
  the REST endpoint needs a comment number and is the fallback if the mutation misbehaves.
- **The cursor alphabet.** GraphQL cursors are standard base64 of a binary payload and can contain `+`, `/`, and `=`. The script
  accepts `[A-Za-z0-9+/=_-]` with 4 to 200 characters (the first version took the node-ID alphabet, which has no `+` or `/`, so page 2
  of a pull request with more than 50 threads or reviews could have failed with exit 3). The cursor goes to `gh` as the value of one
  `-f after=<cursor>` argument, never through a shell, and tests pass cursors with `+`, `/`, `=` on both queries and check they are
  sent exactly as given. The alphabet and the length are **not verified against a real answer**: look at a real cursor in the row 9 run.
  A page that announces a next page without a cursor is refused.
- **The REST statement** of the edited-comment section is not from a source read here (see above).
- **A race.** An item edited between the listing and the work, or between the listing and `reply`, is not seen by the script. The
  reply runs the filter again, so it refuses a thread whose item became edited by another account in between; the work done on the
  earlier text stays done, and the approver reviews the commit.
- **Not an edit.** Hiding a comment (minimizing it) is not an edit, changes no text, and does not exclude an item. A comment that
  quotes or links to another text does not change what is printed: the Skill treats that text as data.
- **Machine account by login.** The pull request author and a comment author are compared to `machine_login` by login (the
  configuration has no ID for the machine account). The approver is the only identity matched by ID, as the design says.

## Points for the approver

1. **Edited items are excluded on any doubt** (an unknown editor, an incomplete history, 100 edits or more). An approver who edits their
   own comment many times, or whose history GitHub shortens, sees the item excluded and counted; the remedy is to post a new comment.
2. **Paging and truncation.** The row follows ADR-010 decision 8 (pages of 5 items or 4 KiB, text never truncated) and adds `--page`;
   it counts review summaries as items. The plan row names no paging; if you prefer a hard cut with no way to the rest, it is a small change.
3. **`approver_id` is now required** by this script. A repository generated before the configuration carries it gets a refusal that
   names the key.
4. **Reply on a resolved thread** is allowed (the listing prints it). The Skill replies only for threads it addressed.
5. **A pull request is judged by its author's login** (`machine_login`); no ID of the machine account is configured.
6. **`reply` names the full SHA** and refuses a commit that is not one of the pull request's own commits, so the Skill must push first.
