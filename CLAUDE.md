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
of silently installing a different major version. Re-resolve every pin against
the new base image before merging such a bump. Alpine 3.24 moved `gocryptfs`
2.5 to 2.6, `less` 685 to 702, and `openssh` 10.2 to 10.3.

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
- No auto-commit or auto-push step in any workflow. Checkouts keep
  `persist-credentials: false`; jobs hold `contents: read`.
- A fixable finding fails the PR. The author fixes it locally and pushes.

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

Also: a **resolved** thread does not mean a fix was verified. CodeRabbit
auto-resolves threads whose lines a later commit changed, which means the code
moved, not that it was re-read.

### Dependency-bot pull requests are not reviewed automatically

CodeRabbit does not auto-review pull requests authored by a bot, and posts no
check on them at all. That is fine while the pull request is only the bot's
one-line version bump. It stops being fine the moment work is added on top:
Renovate's Alpine 3.24 bump grew three re-resolved apk pins and a format
compatibility investigation, and none of it would have been reviewed. Ask for
it explicitly with an `@coderabbitai review` comment.

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
