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

`ARG GOCRYPTFS_VERSION=2.6` resolves to `2.6.1-r5` in the Alpine 3.24
community repo, verified 2026-08-20 with `apk policy gocryptfs` in
`alpine:3.24`.
The `-bs` (block size) flag is NOT supported by this build. Do not add it back.

An `ALPINE_VERSION` bump can invalidate this and the `~=` pins in the
Dockerfile, which is what those pins are for: the build fails loudly instead
of silently installing a different major version.

### apk pins re-resolve themselves when ALPINE_VERSION bumps

`GOCRYPTFS_VERSION`, `BASH_VERSION`, `LESS_VERSION`, `OPENSSH_VERSION`,
`RSYNC_VERSION`, `SSHFS_VERSION` and `VIM_VERSION` are apk `~=` version
constraints, not Docker tags, so no Renovate datasource can track them: an
independently proposed bump could easily name a version the pinned Alpine
release's repo does not carry and fail the build. This used to mean
re-resolving all seven by hand (`apk policy <pkg>` inside the new
`alpine:${ALPINE_VERSION}`) every time a Renovate `ALPINE_VERSION` pull
request landed, which is how the 2.5 to 2.6, 685 to 702 and 10.2 to 10.3
moves above were originally found.

All eight pins, `ALPINE_VERSION` included, are `ARG` defaults in the
`Dockerfile`. They lived in `.env.example` until then, which meant a plain
`docker build .` produced `alpine:` and `bash~=` and only `make build` (or a
workflow restating all eight as `--build-arg`) could build the image at all.
Nothing about the mechanism below changed with the move, only which file it
reads and rewrites, and that `ALPINE_VERSION` now carries Renovate's
annotation in Renovate's own documented shape for a Dockerfile ARG.

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

The commit is pushed with `GITHUB_TOKEN` (the job grants itself `contents:
write` for exactly this). GitHub does not start new workflow runs from a push
authenticated with the default `GITHUB_TOKEN`, specifically to prevent
workflows retriggering each other in a loop, so that push alone would leave
none of the pull request's required checks re-run against it: not
`pull-request-validation.yml`'s `Tests` job (`make build` plus the
backup/restore roundtrip), and not `coderabbit-gate.yml`'s `Pin Only` and
`Review Verified` either, since that workflow's own regrading triggers never
fire from a GITHUB_TOKEN push any more than `pull-request-validation.yml`'s
do. The anti-recursion rule has one documented exception: an explicit
`workflow_dispatch` call made through the API, even with `GITHUB_TOKEN`.
Right after the push, `resolve-apk-pins.yml` dispatches both workflows
directly: `pull-request-validation.yml` carries a `workflow_dispatch` trigger
added for this, invoked with `gh workflow run pull-request-validation.yml
--ref <branch>`; `coderabbit-gate.yml` already had one, `pr_number`, its own
manual recovery path for a pull request stuck with a stale verdict, invoked
with `gh workflow run coderabbit-gate.yml --field pr_number=<number>`
instead (both need `actions: write`). Finishing that second run also fires
`bot-auto-merge.yml`'s own `workflow_run` trigger, which is what actually
supplies the approval once every required check reads green. Branch
protection matches a required check by name and by the SHA it reports
against, not by which event produced the run, so a check run started this
way satisfies the pull request's requirement the same as one from the
ordinary trigger would. No stored credential (no PAT like
`CODERABBIT_NUDGE_TOKEN` in `coderabbit-review-queue.yml`) is needed for
this, since the retrigger is an explicit API call rather than a push that
needs to look human-authored.

Safe to re-run, including when Renovate's own `rebaseWhen` rebases or
recreates its branch later and drops this workflow's commit the way any
rebase drops a commit absent from the new base: the rebase changes the head
SHA, which is a `synchronize` event, which re-triggers this workflow, which
recomputes from scratch against whatever `Dockerfile` the rebased branch
actually carries. `scripts/resolve-apk-pins.py` only ever writes a value
that differs from what is already there, so a rebase that dropped the fix
gets it re-applied and a rebase that happened to keep it produces no commit
at all. Nothing has to detect that a rebase happened; recomputing and
comparing is what makes it not matter.

`bot-auto-merge.yml` needed no change for this: `Pin Only` grades the pull
request's cumulative diff (`gh pr diff`), not any one commit, so a second,
workflow-authored commit on top of Renovate's own is graded the same as if
it had all been one commit. The approval job does not react to a
`synchronize` from the resolver's own push, since that push is GITHUB_TOKEN
and produces no such event; it reacts to `coderabbit-gate.yml`'s
`workflow_run` finishing instead, the same `workflow_dispatch` re-trigger
described above, and that is what actually re-derives `Pin Only` and `Review
Verified` for the new commit and, once both read `success`, supplies the
approval.

Only these seven packages resolve automatically. A package newly added to
the Dockerfile's `apk add` line still needs a person to decide its ARG name,
declare it in the `Dockerfile` with the `# apk-pin:` marker above it, and add
it to `scripts/resolve-apk-pins.py`'s `PACKAGE_TO_VAR` before this mechanism
picks it up.

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
keeps `persist-credentials: false` too; the push instead uses `GITHUB_TOKEN`
with a job-scoped `contents: write`, plus an explicit `workflow_dispatch`
re-trigger of `pull-request-validation.yml`, for the reasons in that section
above.

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

| `bucket`  | `description`                                                    | Reviewed?         |
| --------- | ---------------------------------------------------------------- | ----------------- |
| `pending` | `Review in progress`                                             | no, still running |
| `pass`    | `Review skipped: draft pull request`                             | no, never started |
| `pass`    | `Review rate limited`                                            | no, declined      |
| `pass`    | `Review skipped: manual review required for this OSS repository` | no, must be asked |
| `pass`    | `Review completed`                                               | yes               |

The last two rows were added after #62, where both appeared and both read
`bucket: pass`. `Review Verified` rejected each correctly, so the gate holds;
the point of the table is that reading `bucket` alone would have called them
reviewed.

`Review rate limited` is the free OSS review quota, not a punishment for
asking too often, though asking repeatedly does spend it. On a public
repository CodeRabbit grants roughly one review per short window and answers
every further command with a decline that costs a request and buys nothing;
five were burned that way on #62. The walkthrough comment carries the actual wait
("Next included review available in N minutes"), so read that rather than
retrying blind, and note the observed gap between *successful* reviews there
ran nearer an hour than the advertised twenty minutes.

`Review skipped: manual review required for this OSS repository` means
automatic review is off for the repository, so a push alone will never be
reviewed and a human has to post `@coderabbitai review` for each new head. It
looks identical to a healthy pass in the checks list.

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

`.env.example` quotes its values deliberately, which is one `QuoteCharacter`
finding per quoted value (14 today, and it moves with the file).
`checklist-dev-dotenv` cannot be passed `--ignore-checks`: every
`checklist-*` id routes through `run-checklist.sh` with the checklist name as
its first argument, so an `args:` entry replaces that name instead of reaching
the tool. The library documents the local-hook copy as the supported way out.

### `.trivyignore.yaml` entries need a live scan, not a copied list

Every CVE ID in `.trivyignore.yaml` traces to gocryptfs's vendored
`golang.org/x/crypto`, which stays at v0.33.0 until gocryptfs itself ships a
release bumping its `go.mod`; an Alpine package rebuild cannot move it, and the
section below spells out why that distinction matters (see also
docs/SECURITY.md's "Accepted-risk CVEs"). None of them are reachable in the
shipped binary either way. Confirm a new entry against a live `trivy rootfs` run over the binary
extracted from the image, or a current Docker Scout scan of that image, before
adding it, never against an advisory feed or a prior list on faith. Not
`trivy image`: as the next paragraph explains, it cannot produce these
findings at all, so it can never confirm that a CVE belongs in this file (it
stays the right tool for a base-layer CVE, which is exactly what does *not*
belong there). The list this file shipped with was
built that way and turned up both extra and missing CVE IDs compared to an
initial guess based on Docker Scout's UI alone. A Go-stdlib section this
file used to carry was removed the same way, on the same rigor: every one of
those CVE IDs came back `state: fixed` in this repository's own code
scanning history, meaning the shipped gocryptfs binary is no longer built
with the flagged Go version, so the entries were suppressing nothing that
still reproduces.

The same live check surfaced a real gap worth knowing before debugging one
like it again: a plain `trivy image` scan of this image does not detect
these CVEs at all. Trivy tracks gocryptfs purely as the Alpine `apk` package
once it is installed and does not separately run its `gobinary` analyzer
against a binary a distro package manager already owns, so the vendored
dependency versions baked into the binary itself never surface through
`trivy image`. `trivy rootfs` against the binary extracted from the image
does run that analyzer and is what actually confirms a given CVE ID belongs
in this file. Docker Scout's SBOM-based scan surfaces them regardless of
this gap, which is how they were found in the first place.

That gap nearly shipped a self-defeating ignore list, and the shape of the
mistake is worth remembering. The `expired_at` migration was written and
documented as "an expiry makes the gate fail, which forces a re-decision"
while both gates still ran `trivy image` only. Since `trivy image` never
produces these findings, expiring an entry would have changed nothing: the
gate would keep passing, and the documentation would have been promising an
enforcement that could not fire. A CodeRabbit review caught it before merge.
Both gates now run a blocking `trivy rootfs` scan against the extracted
binary as well, which is what makes the expiry real. The general lesson: when
adding a suppression with an expiry, first confirm that some gate actually
produces the finding being suppressed, because an expiry on an inert entry is
worse than no expiry, it reads like a control while enforcing nothing.

For the same reason `.github/workflows/security-ignore-audit.yml` judges
"does this still reproduce?" from its own unfiltered `trivy rootfs` run, never
from code scanning alert state. Both CI workflows apply `.trivyignore.yaml`
before uploading their SARIF, so an ignored CVE is missing from the alert
list *because* it is ignored; reading that absence as "fixed upstream" is
circular and would have the audit recommend deleting live suppressions.

A CVE in a transitive base-layer package, the way util-linux's
`libblkid`/`libmount` CVEs were, should generally **not** go in
`.trivyignore.yaml`. Those are fixed by an Alpine security update already
published upstream; the Dockerfile's `apk update && apk upgrade` picks the
fix up on the next rebuild, so the scan failing until that rebuild happens is
the mechanism working, not a false positive to silence.

Every CVE currently in that file is in `golang.org/x/crypto/ssh`, `ssh/agent`
or `ssh/knownhosts`, and **none of those packages are linked into the shipped
binary**. They are reported because Trivy resolves a Go binary at *module*
granularity from its embedded build info, so one `golang.org/x/crypto v0.33.0`
dependency pulls in every CVE against that module regardless of what the linker
kept. Do not read the list as twelve reachable holes in the image. The expiry
exists to force re-verification of the unreachability claim, which is the part
that could change (a future gocryptfs could start linking `ssh`), not to time a
fix.

**How to verify that claim, and how not to.** `govulncheck ./...` in source
mode against the gocryptfs tag is the authoritative check: at v2.6.1 it reports
"0 vulnerabilities ... 22 vulnerabilities in modules you require, but your code
doesn't appear to call these". `go list -deps ./...` corroborates it, showing
`chacha20`, `chacha20poly1305`, `hkdf`, `pbkdf2`, `scrypt` and no `ssh`
anywhere in the build graph. The maintainer said the same on
rfjakob/gocryptfs#973 in November 2025.

**Alpine builds from the maintainer's tarball, not from the git tag.**
`community/gocryptfs`'s APKBUILD fetches `gocryptfs_v${pkgver}_src-deps.tar.gz`,
which the gocryptfs maintainer packages and signs on his own machine with no CI
provenance; `package-release-tarballs.bash` deliberately stops at a printed
`gpg --detach-sig` hint so his key never touches a build machine. Alpine then
compiles that source itself and signs the `apk`, so the binary is Alpine's, but
the source is not verifiably the tag. It was checked once by hand for `v2.6.1`
(206 `.go` files byte-identical, `go.mod`/`go.sum` match, `go mod verify`
clean, `vendor/` reproduces from a fresh `go mod vendor`) and it held. Do not
restate "Alpine builds, tests and signs it" as though that covered the source
provenance; see docs/SECURITY.md's "Why not build gocryptfs from source".
Raised upstream as rfjakob/gocryptfs#1035 and with Alpine as aports#18435.

Do **not** reach for `strings` or `go tool nm` over the shipped binary, which
is the mistake this repo made first: Alpine strips it, so both return zero for
every package including the ones gocryptfs certainly uses, and a zero there
measures the strip rather than absence. `govulncheck -mode=binary` fails worse,
in the confident direction: on the stripped binary it prints 21 vulnerabilities
under `=== Symbol Results ===` and lists `ssh.Dial` five times as though found,
which source mode flatly contradicts. A repeated identical symbol in that
output is the tell that it has degraded to module-level guessing.

**Two classes of CVE reach this binary, and only one of them Alpine can fix.**
Conflating them is easy and wrong, so keep them apart:

- **Go stdlib CVEs** come from the toolchain gocryptfs was compiled with.
  Alpine bumping the package revision and rebuilding changes that, which is why
  the Go-stdlib block this file used to carry cleared on its own: 3.24's
  `2.6.1-r6` is built with go1.26.8.
- **Vendored dependency CVEs** (every entry in the list today, all
  `golang.org/x/crypto`) come from gocryptfs's own `go.mod`. Alpine builds the
  release as published and does not patch dependency versions, so **no number
  of Alpine rebuilds will move `x/crypto` off v0.33.0.** Only a gocryptfs
  release that bumps its `go.mod` can, and `master` already carries v0.52.0
  with no release cut off it since v2.6.1 (2025-08-10), gaps historically
  running one to nineteen months.

Measured 2026-09-08, which is what settles it: 3.24 `2.6.1-r6` is go1.26.8 with
`x/crypto v0.33.0`, and edge `2.6.1-r7` is go1.26.5 with `x/crypto v0.33.0`.
The dependency does not budge across either.

So do not go looking for a newer Alpine to fix this. **edge is currently worse
than the pinned branch**, 20 CRITICAL/HIGH against the extracted binary versus
3.24's 12, because its `r7` was built with an *older* Go toolchain and so
reintroduces stdlib findings that 3.24 has already shed. 3.23 is worse again,
still on gocryptfs 2.5.4. An `ALPINE_VERSION` bump is a decision about the base
image, never a remediation for these entries.

Every entry carries an `expired_at` date (the file is YAML specifically for
this field) and a `statement` saying what would resolve it. Windows follow
severity, per ordinary risk-acceptance practice: 30 days for a `CRITICAL`, 60
for a `HIGH`, never past 90. This is not
optional formatting: an ignore-list entry with no expiry is an
indefinitely-suppressed CVE, which is its own security problem regardless of
how well-reasoned the original acceptance was.
`.github/workflows/security-ignore-audit.yml` re-checks every entry on a
schedule and opens or updates a tracking issue when one is stale or
approaching expiry, but the workflow is a warning system, not the
enforcement: `expired_at` failing the CRITICAL/HIGH gate is what actually
forces a re-decision if the workflow itself is ever broken or its issue
ignored. See docs/SECURITY.md's "Dismissal guidelines" for what qualifies a
CVE for dismissal at all (a sub-gate finding or a no-upgrade-path one, the
worked examples there, generally does not) and for why a GitHub dismissal
and an ignore-list entry have to move together.

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

### `GOCRYPTFS_CIPHER` offers two spellings of one cipher, and one that cannot work

Reverse mode needs deterministic encryption, so gocryptfs enables the `AESSIV`
feature flag unconditionally. `aes-gcm` (no flag) and `aes-siv` (`-aessiv`)
therefore produce identical feature flags, verified by reading `.FeatureFlags`
out of `.gocryptfs.reverse.conf` after initialising each way. `xchacha` cannot
work at all: `gocryptfs -reverse -init -xchacha` exits 24 with "can't have both
XChaCha20Poly1305 and AESSIV feature flags", on 2.5.4 and 2.6.1 alike, so it is
long-standing rather than a regression. `backup.sh` now accepts the two
equivalent spellings, refuses `xchacha` with an explanation, and aborts on
anything else rather than falling back silently.
`tests/test_gocryptfs_cipher.py` executes the real `case` block.

### Makefile expansions are wrapped in `$(subst ",,${VAR})` on purpose

Do not "simplify" those back to `"${VAR}"`. `.env.example` quotes every value,
and `include $(ENV_FILE)` hands the quote characters to make as part of the
value, so `"${VAR}"` produces `""/mnt/my backups""`. Those two pairs do not
nest: the shell concatenates empty string, bare word, empty string, then splits
on the space anyway.

Unquoted was worse and is what this replaced. A blank value did not arrive as an
empty argument, it vanished, shifting every later positional argument down one
slot: a blank `GOCRYPTFS_CIPHER` put `GOCRYPTFS_SCRYPT_N` in the cipher slot and
left `__gocryptfs_encrypt_names` on its `true` default, silently turning on
filename encryption and defeating every filter rule (see the
`GOCRYPTFS_ENCRYPT_NAMES` gotcha above for why that breaks filtering).

`$(subst ",,${VAR})` strips the env file's quotes before make adds its own.
`$(patsubst "%",%,...)` does **not** work here, because `patsubst` operates on
whitespace-separated words and a value with a space arrives as two.
`tests/test_makefile.py` drives `make --dry-run` with blank and
quoted-with-space values and asserts both the argument count and each slot.

Only the seven variables that reach positional arguments are wrapped.
`--volume`, `--tag` and `--build-arg` sites are left alone deliberately: there
the env file's own quotes are the only quoting the shell sees, so a value with a
space already parses as one word, and stripping them without adding real quotes
would regress that.

### `pre-commit run --all-files` only sees tracked files

A new file passes locally and then fails in CI until it is `git add`ed. This has
bitten more than once, most recently on a new `ruff.toml` that cspell only
flagged once staged. `git add` first, then run the hooks.

### `.coderabbit.yaml` sets `auto_pause_after_reviewed_commits: 0` deliberately

The default is 5, which silently pauses automatic reviews once five commits on a
branch have been reviewed. The check stays green, so a pull request looks
reviewed when nothing has read its head; that cost twelve hours on
pre-commit-checklists#12. Do not restore the default.

The other half is not config-fixable: the plan's included-review allowance
**drops** a review rather than queueing it, so a push arriving while the quota
is exhausted is declined and never retried. Recovery is a manual
`@coderabbitai review`, or `full review` when the incremental logic has already
marked the commits as seen. `schema.v2.json` has no retry, backoff, queue or
poll setting anywhere in it.

### The deliberate lint suppressions, and why each exists

There are five, and "nothing is ignored" would be the wrong claim:

| Suppression | Where | Why |
| --- | --- | --- |
| `superfluous-actions` | `.github/zizmor.yml` | zizmor wants `gh release create` instead of a SHA-pinned action carrying `allowUpdates`, which has no equivalent. Tracked in [#71](https://github.com/ivan-pinatti-labs/rsync-crypt/issues/71). |
| `MD001 MD013 MD033 MD041`, and `MD013 MD033` | two blocks in `README.md` | The centred badge header and the crypto QR table are necessarily raw HTML. Scoped `disable`/`enable` pairs naming specific rules, never a file-wide disable. |
| `--ignore-checks QuoteCharacter` | the local dotenv hook | `.env.example` quotes its values deliberately. See the dotenv-linter section above. |
| `ignoreWords` | `.cspell.json` | Identifiers and third-party names, kept separate from the real dictionary `words`. |
| `unset` properties | `.editorconfig` | Each marks something a tool reports that cannot be fixed. See that file's comments. |

`.secrets.baseline` is not on the list: it allowlists zero findings, so it
suppresses nothing. Do not attach counts to any of this; earlier versions said
"38 `ignoreWords`" and similar and every number was wrong within a few commits.

## User Preferences

- No em-dashes (`—` or `--`) in prose; use commas or parentheses instead
- No `|| true` in Makefile; use `docker inspect` conditionals instead
- `make clean` uses `docker inspect` pre-checks before `rm` to avoid false failures
- Do not add `|| true` as a general error suppressor; fail explicitly with a clear message
- Never add `Co-Authored-By: Claude`, "Generated with Claude Code", or any
  other AI attribution to a commit message, a pull request body, or a
  changelog entry. This is a hard prohibition, not a preference: it applies
  to every commit and every pull request, with no exception.
