# rsync-crypt (Claude Project Memory)

## What This Project Is

Docker-based encrypted backup tool using gocryptfs reverse mode + rsync over SSH.
Makefile-driven. Alpine image. Key binaries: gocryptfs, rsync, sshfs, openssh (sshd).

## Key Files

| File                            | Purpose                                                                    |
| ------------------------------- | -------------------------------------------------------------------------- |
| `Makefile`                      | All targets; reads env file via `ENV_FILE ?= .env` + `include $(ENV_FILE)` |
| `.env`                          | User config (not committed); `.env.example` is the template                |
| `scripts/backup.sh`             | Main backup script, called inside Docker                                   |
| `scripts/restore.sh`            | Restore script                                                             |
| `scripts/view.sh`               | SFTP view mode via sshd inside container                                   |
| `conf/backup-filter-rules.txt`  | rsync filter rules (+ include, - exclude)                                  |
| `conf/restore-exclude-list.txt` | Restore exclusions                                                         |
| `conf/restore-paths.txt`        | Selective restore paths (empty = restore all)                              |

## Architecture

1. `gocryptfs -reverse` mounts a read-only encrypted virtual view of `BACKUP_SOURCE_FOLDER`
2. `rsync` pushes the encrypted view to the remote server over SSH
3. View mode: `sshfs` mounts the remote encrypted dir, `gocryptfs` decrypts it, `sshd` serves it via
   SFTP on `127.0.0.1:2222`

## Known Gotchas

### GOCRYPTFS_ENCRYPT_NAMES must be false for filter rules to work

When `true`, rsync sees scrambled filenames and no filter pattern can match them.
Default is `false`. File contents are still fully encrypted either way.
gocryptfs has `-exclude-wildcard` with gitignore negation, but the include-first
catch-all-exclude pattern in the filter file cannot be expressed with excludes alone.
Wiring gocryptfs `-exclude-from` instead of rsync filters is a planned future improvement.
Upstream: <https://github.com/rfjakob/gocryptfs/issues/1000> proposes a `-filter-from` flag with
rsync-style first-match-wins semantics.

### Alpine gocryptfs version

`GOCRYPTFS_VERSION="2.6"` resolves to `2.6.1-r5` in the Alpine 3.24 community
repo, verified 2026-08-20 with `apk policy gocryptfs` in `alpine:3.24`.
The `-bs` (block size) flag is NOT supported by this build. Do not add it back.

An `ALPINE_VERSION` bump can invalidate this and the `~=` pins in the
Dockerfile, which is what those pins are for: the build fails loudly instead
of silently installing a different major version.

### apk pins re-resolve themselves when ALPINE_VERSION bumps

`GOCRYPTFS_VERSION`, `BASH_VERSION`, `LESS_VERSION`, `OPENSSH_VERSION`,
`RSYNC_VERSION`, `SSHFS_VERSION` and `VIM_VERSION` in `.env.example` are apk
`~=` version constraints, not Docker tags, so no Renovate datasource can
track them: an independently proposed bump could easily name a version the
pinned Alpine release's repo does not carry and fail the build. This used to
mean re-resolving all seven by hand (`apk policy <pkg>` inside the new
`alpine:${ALPINE_VERSION}`) every time a Renovate `ALPINE_VERSION` pull
request landed, which is how the 2.5 to 2.6, 685 to 702 and 10.2 to 10.3
moves above were originally found.

That step is automated now, by `.github/workflows/resolve-apk-pins.yml` and
`scripts/resolve-apk-pins.py`. When a `renovate[bot]` pull request changes
`ALPINE_VERSION`, the workflow runs `apk update && apk policy <pkg>` inside
the proposed `alpine:<version>` for all seven packages and, if anything
resolves to a different value, pushes a second commit onto the same pull
request with the update. Each of the seven carries a
`# apk-pin: resolved-from=ALPINE_VERSION` comment directly above it, a
marker distinct from Renovate's own `# renovate:` on purpose: it is what
`scripts/assert-pin-only-diff.py`'s `Pin Only` check reads to accept a bump
to one of these seven as a pin bump rather than refusing the pull request for
"a dependency bot reaching outside its lane" (see `APK_PIN_ANNOTATION` in
that script). It is deliberately not `# renovate:` with a different
datasource attached: that shape is exactly what Renovate's own regex manager
matches, which would put these seven right back under Renovate's independent
tracking, the failure mode they are excluded from Renovate to avoid in the
first place.

The commit is pushed with a personal access token (`APK_PIN_PUSH_TOKEN`
secret, fine-grained, `Contents: write` on this repository only), not
`GITHUB_TOKEN`, the same reasoning as `CODERABBIT_NUDGE_TOKEN` in
`coderabbit-review-queue.yml`: GitHub does not start new workflow runs from a
push authenticated with the default `GITHUB_TOKEN`, specifically to prevent
workflows retriggering each other in a loop, and this repository needs the
opposite here. The whole point of pushing this commit is that
`pull-request-validation.yml`'s real `Tests` job (`make build` plus the
backup/restore roundtrip) grades it the normal way, and a `GITHUB_TOKEN`
push would leave that job never re-run against it.

Safe to re-run, including when Renovate's own `rebaseWhen` rebases or
recreates its branch later and drops this workflow's commit the way any
rebase drops a commit absent from the new base: the rebase changes the head
SHA, which is a `synchronize` event, which re-triggers this workflow, which
recomputes from scratch against whatever `.env.example` the rebased branch
actually carries. `scripts/resolve-apk-pins.py` only ever writes a value
that differs from what is already there, so a rebase that dropped the fix
gets it re-applied and a rebase that happened to keep it produces no commit
at all. Nothing has to detect that a rebase happened; recomputing and
comparing is what makes it not matter.

`bot-auto-merge.yml` needed no change for this: `Pin Only` grades the pull
request's cumulative diff (`gh pr diff`), not any one commit, so a second,
workflow-authored commit on top of Renovate's own is graded the same as if
it had all been one commit, and the approval job re-triggers correctly on
the resolver's own `synchronize` event the same way it already does on any
other push to the pull request.

Only these seven packages resolve automatically. A package newly added to
the Dockerfile's `apk add` line still needs a person to decide its variable
name, add it to `.env.example` with the `# apk-pin:` marker, and add it to
`scripts/resolve-apk-pins.py`'s `PACKAGE_TO_VAR` before this mechanism picks
it up.

### rsync exit codes 23 and 24

Exit 23 = partial transfer (some files skipped/unreadable), exit 24 = vanished files.
Both are treated as success-with-warning (break loop), not as retriable failures.
With `RSYNC_LOOP=true` these used to cause infinite retry loops.

### ENODATA (errno 61) from gocryptfs

Happens when gocryptfs reverse mode cannot read locked files (SQLite WAL, LevelDB LOCK).
Fixed by excluding `*.lock`, `*.db-wal`, `*.db-shm`, `*.sqlite-wal`, `*.sqlite-shm`, `**/LOCK` in
filter rules.

### check-passkey requires a real TTY

The `read -r -p` prompt for passphrase creation needs an interactive terminal.
Running `make bb` from a non-TTY context will fail at `check-passkey`.
The `chmod 600` is guarded: exits with an error if the passkey file does not exist after the prompt.

### gocryptfs params are init-time only

Cipher, scryptn, and encrypt_names are stored in `.gocryptfs.reverse.conf` on first init.
Changing them after init requires deleting the conf and re-encrypting the full backup.

### CI never autofixes

Settled decision, do not revisit. Formatting hooks auto-fix locally (ruff
--fix, ruff-format, shfmt --write, prettier --write, end-of-file-fixer,
trailing-whitespace). In CI they run identically, rewrite files inside the
runner's checkout, and pre-commit exits non-zero so the job fails. Nothing is
ever committed or pushed back to a branch by CI.

markdownlint is deliberately not in that list. `fix` is a markdownlint-cli2
runner option and is inert in `.markdownlint.yaml`, which is rule
configuration; there is no `.markdownlint-cli2.yaml` here, so nothing enables
fixing. Markdown findings are reported and fixed by hand. The file's own
comments say so.

- No `ci:` block in `.pre-commit-config.yaml`. That block configures
  pre-commit.ci, whose `autofix_prs` is the only mechanism that would push.
  The app is not installed here (verified against the repo's check runs).
- No auto-commit or auto-push step in any workflow that grades a pull
  request's content. Checkouts keep `persist-credentials: false`; jobs hold
  `contents: read`.
- A fixable finding fails the PR. The author fixes it locally and pushes.

The one deliberate exception is `.github/workflows/resolve-apk-pins.yml`
(see "apk pins re-resolve themselves when ALPINE_VERSION bumps" above), and
it is not really an exception to this policy so much as a different kind of
automation the policy was never about. `pre-commit` never fixes and pushes
*findings* from linting the content someone wrote; that stays true and
unchanged. `resolve-apk-pins.yml` looks up an external fact (what apk version
a given Alpine release actually carries) that no author, human or bot, can
know without querying it, and records the answer, the same category of
automation Renovate and Dependabot already perform on this repository's
behalf, just for the one datasource neither of them can model. Its checkout
keeps `persist-credentials: false` too; the push instead uses
`APK_PIN_PUSH_TOKEN`, a separate, narrowly-scoped credential, deliberately
not `GITHUB_TOKEN`, for the reasons in that section above.

### Branch, PR, gates, then merge

No direct commits to `main`; `checklist-git-protected-branches` enforces it.
Branch out, commit, push, open a draft PR, mark it ready, wait for the gates
and CodeRabbit, address the comments, and merge once green. Branch names are
lowercase slugs (`fix/flaky-test`); commit messages are Conventional Commits.

Never force-push. Not `--force`, not `--force-with-lease`, not on a branch
nobody else is reading, not to tidy up a history. A force-push destroys commits
on the remote that nobody agreed to lose, and on a dependency bot's branch it
also rewrites work this account did not author.

That rules out rebasing a pushed branch, because a rebase is what makes the
force necessary. To bring a stale branch up to date, merge the base branch into
it and the push stays a fast-forward. If a branch has already been rebased and
diverged from its remote, merge the remote ref back into it so the remote tip
becomes an ancestor again, then push normally. `renovate/alpine-3.x` was
recovered exactly that way in #8. When neither is possible, push a new branch
and supersede the old pull request.

### Knowing whether CodeRabbit has actually reviewed a branch

Three separate signals look like "reviewed" and are not. Each of these cost
real time before being pinned down, so check the combination, not any one:

- **A green CodeRabbit check is not a review.** It is green on a skipped draft
  and on a rate-limited decline. While a review is running the check reads
  `Review in progress`, which is `pending`, not a conclusion.
- **Comment timestamps do not move with the review.** CodeRabbit edits its
  verdict comment in place, so `created_at` stays at the first review forever
  while `updated_at` moves for unrelated edits. Every timestamp comparison
  built on this reported fresh reviews as stale.
- **A SHA appearing in a CodeRabbit comment is not a finished review of that
  SHA.** The walkthrough comment names the head commit as soon as the review
  starts, so matching the head against comment bodies reports completion
  immediately, before anything has been read.

The reliable test is both halves together, and the first half has to name the
conclusion rather than merely require one. `gh pr checks <n> --json
name,bucket,description` reports `bucket: pass` for a completed review *and*
for a skipped draft, so the bucket alone cannot tell them apart. The
`description` is what distinguishes them:

| `bucket`  | `description`                        | Reviewed?         |
| --------- | ------------------------------------ | ----------------- |
| `pending` | `Review in progress`                 | no, still running |
| `pass`    | `Review skipped: draft pull request` | no, never started |
| `pass`    | `Review completed`                   | yes               |

So: `description` is `Review completed`, *and* the head SHA is named in
CodeRabbit's comments, which is what proves that completion refers to the
current head rather than an earlier one. Then count unresolved review threads.

Look for that SHA in both places. A clean review puts the range in the
walkthrough issue comment (`issues/<n>/comments`), but a review that posts
findings puts it in the review body (`pulls/<n>/reviews`) instead, and
searching only the issue comments then finds nothing and reads as "not
reviewed". Both #28 and #29 were misjudged that way before the review bodies
were checked.

Also: a **resolved** thread does not mean a fix was verified. CodeRabbit
auto-resolves threads whose lines a later commit changed, which means the code
moved, not that it was re-read.

### Dependency-bot pull requests are not reviewed automatically

CodeRabbit does not auto-review pull requests authored by a bot, and posts no
check on them at all. That is fine while the pull request is only the bot's
one-line version bump. It stops being fine the moment work is added on top:
Renovate's Alpine 3.24 bump grew three re-resolved apk pins and a format
compatibility investigation, and none of it would have been reviewed. Ask for
it explicitly with an `@coderabbitai review` comment, posted by a human
account. See the next section for why that qualifier is load-bearing.

Two related traps on a long-open bot pull request:

- **CodeRabbit reviews incrementally** and will not re-review a commit it has
  already seen, so a plain `@coderabbitai review` after a push covers only what
  is new. `@coderabbitai full review` re-reads the whole current diff, which is
  what previously reviewed commits or a rewritten history need.
- **GitHub can leave the base pinned where the bot opened it.** #8 sat 18
  commits behind and GitHub compared against that old base, showing 31 files
  instead of 7, so CodeRabbit reviewed code already merged to `main`. Check with
  `gh pr diff <n> --name-only` before trusting a review: if files appear that
  the branch never touched, the base is stale.

  `full review` does **not** fix that. It re-reads the current diff, and a stale
  base is what makes the current diff wrong, so a full review of a bad range is
  still a review of the wrong code. Correct the range first by merging the
  intended base branch into the pull request branch, confirm with
  `gh pr diff <n> --name-only` that only the expected files remain, and only
  then ask for a review. That is the order #8 was recovered in.

That accident was useful once, because reviewing already-merged code surfaced
five real defects in it, including the passkey quoting bug fixed in #23. It is
not a review strategy: nothing guarantees the stale range covers anything.

### CodeRabbit silently ignores `@coderabbitai review` from a bot account

`.github/workflows/coderabbit-review-queue.yml` posts an `@coderabbitai
review` comment through `github-actions[bot]` once an hour when `Review
Verified` is stuck failing. On #37 that comment fired five times across most
of a day and CodeRabbit never once replied to it, not with a review, not with
a decline, not with a rate limit notice: nothing. Every comment posted by the
human account got a reply within seconds every single time, including the
times that reply was itself a decline. CodeRabbit appears to drop a review
command from a bot commenter the same way it drops a pull request authored by
one, and #37 sat blocked for hours on that mechanism before anyone checked
whether it was actually being heard.

The workflow still earns its keep: it is what proves, mechanically, that a
pull request is waiting on a review nobody has asked for yet. What it cannot
do is make that ask land. So when `Review Verified` is still failing after the
nudge has fired, check the pull request's comments for a `coderabbitai[bot]`
reply within roughly ten seconds of the nudge's timestamp before assuming the
request is in flight:

- **A `coderabbitai[bot]` reply exists** (even a decline). The request was
  heard; a rate limit or a plan restriction is the actual blocker, and waiting
  out the quota or trying again later is reasonable.
- **No reply at all.** CodeRabbit never saw it as a command worth answering.
  Waiting longer will not change that; only a human posting the exact same
  `@coderabbitai review` comment will. Say so and ask for it, rather than
  re-dispatching the workflow and letting another hourly window pass on a
  mechanism with no evidence it has ever worked.

### Selectors match the library's templates

Both selectors that once deviated no longer do, as of `rev: v2.2.0`.

`checklist-dev-shell` uses `types: [shell]`, which reaches
`files/bash/.bashrc` and `.bash_aliases` as well as `scripts/*.sh`: all are
typed `shell` by `identify` despite the dotfiles having no extension. Before
v2.1.4 the hook's manifest baked in `files: \.(sh|bash)$`, which pre-commit
ANDs with a consumer's `types:`, silently dropping both dotfiles. This repo
carried a `files:` override until that was fixed upstream.

`checklist-json` uses `types_or: [json, json5]`. The only JSON-family file
here is `.github/renovate.json5`, and `identify` tags that `json5`, so a
plain `types: [json]` would match nothing at all. `check-json` self-filters
and never sees the json5 file, which is correct: it is a strict JSON parser
and that file has comments and unquoted keys. Prettier formats it.

### dotenv-linter is a local hook, not checklist-dev-dotenv

`.env.example` quotes its values deliberately, which is 16 `QuoteCharacter`
findings. `checklist-dev-dotenv` cannot be passed `--ignore-checks`: every
`checklist-*` id routes through `run-checklist.sh` with the checklist name as
its first argument, so an `args:` entry replaces that name instead of reaching
the tool. The library documents the local-hook copy as the supported way out.

### Parallel agents need separate worktrees

More than one agent working in this repository at the same time must each get
their own `git worktree`. They cannot share the checkout.

This was learned the hard way: two agents were dispatched into this
repository's checkout at once to verify two different dependency pull requests.
Each needed its own branch checked out, so they took turns swapping the shared
working tree out from under each other. One of them noticed its branch had
changed mid-task and moved itself into a worktree; the other never noticed,
which is the worse outcome, because a verification run against the wrong
branch still reports a result.

Nothing was lost that time. The failure mode to avoid is a passing gate or a
green test run that was measured against a branch nobody intended, which is
indistinguishable from a real pass in the report that comes back.

When dispatching with the Agent tool, pass `isolation: "worktree"` so each
agent gets an isolated copy. A worktree costs a few hundred milliseconds and
some disk, and is removed automatically if unchanged.

## User Preferences

- No em-dashes (`—` or `--`) in prose; use commas or parentheses instead
- No `|| true` in Makefile; use `docker inspect` conditionals instead
- `make clean` uses `docker inspect` pre-checks before `rm` to avoid false failures
- Do not add `|| true` as a general error suppressor; fail explicitly with a clear message
- Never add `Co-Authored-By: Claude`, "Generated with Claude Code", or any
  other AI attribution to a commit message, a pull request body, or a
  changelog entry. This is a hard prohibition, not a preference: it applies
  to every commit and every pull request, with no exception.
