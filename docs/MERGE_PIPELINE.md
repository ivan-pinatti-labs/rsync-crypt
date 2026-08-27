# The Merge Pipeline

<!-- cspell:words coderabbit coderabbitai dockerhub renovate -->

What happens between opening a pull request against this repository and it
landing on `main`. This is a thinner version of the document of the same
name in `docker-torrent-box-with-vpn`, which this pipeline was ported from
ahead of a planned migration of that repository to an organization (GitHub
refuses a merge queue on a personal account). This repository was the
rehearsal for that migration: it transferred first, proved the queue works
under an organization, and the fixes found along the way are meant to sweep
back into the sibling repository afterward. It has four required contexts,
not six, and no integration suite, so the shape here is simpler; where the
reasoning is identical it is only summarized, not restated.

## A human pull request

Open it as a **draft** first. `Pre-commit` runs the full hook set over every
file, and CodeRabbit does not review a draft at all: `.coderabbit.yaml` sets
`drafts: false` on purpose, so a review is not spent on a diff the mechanical
linters have not finished cleaning up yet.

**Mark it ready for review** once `Pre-commit` and `Tests` are green. That is
what starts CodeRabbit. Address what it raises, pushing fixes as needed; each
push re-runs both jobs and gets a fresh review.

Once every required check reads green and a maintainer has approved it,
GitHub adds the pull request to the merge queue on its own (see "The merge
queue is live" below), and it merges once the queue's own run of the same
check set passes on the commit the queue actually builds.

## The repository owner's own pull request

The owner is the only account with write access, and GitHub refuses to let
an account approve its own pull request, which used to mean a genuine
deadlock once the queue went live: `gh pr merge --admin` skips the queue
entirely, and arming auto-merge on an unapproved pull request leaves it
enqueued forever with no `merge_group` run, because `enforce_admins: false`
exempts the owner from performing a merge without approval, not from the
approval the queue itself requires to accept the pull request at all. Both
failure modes were confirmed empirically, not assumed, on real pull requests
against this repository.

`bot-auto-merge.yml`'s `approve-owner` job is the fix: once `Pre-commit`,
`Tests`, `Pin Only` and `Review Verified` are all green, it supplies the
approval that makes the pull request queue eligible. It does not arm
auto-merge, deliberately: the owner still decides when to enqueue, which is
the "check everything is fine, then merge" step the rest of this pipeline
takes away from nobody else. This approval is not evidence a human read the
diff; it is issued the moment the four contexts settle, with no review of
their content, which is exactly why it waits for `Review Verified` rather
than for `Pin Only` alone. `Review Verified` is what actually carries "a
review happened," and nothing else in this pipeline does. A contributor or a
fork gets no approval from this job and still needs a genuine human review,
same as always.

## A dependency bot pull request

Dependabot and Renovate open pull requests unattended. For the ones that are
pin only:

1. **`Pin Only` is graded.** `scripts/assert-pin-only-diff.py` checks that
   every changed line differs from its counterpart in nothing but a version,
   in a pin position, across four allowed pin surfaces, and
   `.github/workflows/coderabbit-gate.yml` publishes its verdict as the
   `Pin Only` status. A number that is not a pin does not count as one.
2. **The approval is supplied, conditionally.** `bot-auto-merge.yml` waits
   for `Pin Only` to read `success` and then supplies the approving review
   branch protection requires. A diff that is not pin-only gets no approval
   and waits for a person, same as a major bump does.
3. **GitHub enqueues and merges it** once every required check, including
   `Review Verified`, is green and the approval is in place, the same as any
   other pull request; see "The merge queue is live" below.

## Every required status context

| Context | What it actually proves | Who publishes it |
| --- | --- | --- |
| `Pre-commit` | The full pre-commit hook set passed over every file | `pull-request-validation.yml`, as a job |
| `Tests` | `pytest tests` passed, which includes building the image through `make build` | `pull-request-validation.yml`, as a job |
| `Pin Only` | A dependency bot's diff changes nothing but a version in a pin position; `success` with a "not a dependency bot pull request" description on everything else | `coderabbit-gate.yml`, published directly onto the head SHA |
| `Review Verified` | CodeRabbit's actual review outcome, not merely that it reported something | `coderabbit-gate.yml`, published directly onto the head SHA |

`Pre-commit` and `Tests` are ordinary workflow jobs: GitHub reports a job's
own pass or fail as the check. The other two are commit statuses, written
directly by a workflow step rather than read off a job's outcome, for the
same reason as in the sibling repository: a status a workflow chooses whether
to write, and what to write, does not read as passed merely because it was
skipped.

`Docker Build`, also a job in `pull-request-validation.yml`, is deliberately
not in this table. It builds the Dockerfile standalone and, once
`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` exist, logs into Docker Hub before
doing it, which is the probe for whether those secrets survive a future
repository transfer. It is not required, and nothing in this pipeline waits
on it.

## `Review Verified`, and the bug it exists to fix

Ported unchanged in reasoning from `docker-torrent-box-with-vpn`, ahead of
this repository ever hitting the bug itself: a green `CodeRabbit` check does
not mean a review happened, because CodeRabbit posts through the legacy
commit status API, which offers only `error`, `failure`, `pending` and
`success`, with no fifth state for "green, but not for the reason you think."
An exhausted review quota, a skipped draft, and an actual completed review
all read `success`. Three pull requests in that repository merged with no
review having actually happened on 2026-08-19 as a direct result (its #114).

`scripts/coderabbit-review-verdict.py`, published as `Review Verified` by
`coderabbit-gate.yml`, is the fix: it reads the actual description behind the
`CodeRabbit` status rather than its color, and grades in three lanes.

1. **A draft is `pending`**, not `failure`, since a required context reading
   red for a pull request's entire draft phase teaches nothing.
2. **A dependency bot pull request is graded on `Pin Only` first.** A clean
   verdict returns `success` with no CodeRabbit review at all, because
   CodeRabbit never reviews a bot's pull request in the first place
   (hardcoded upstream, confirmed by CodeRabbit support). A diff that is not
   pin-only falls through to lane 3 and is graded exactly like a human pull
   request from there.
3. **Everything else** is `success` only for the literal description
   `Review completed`. Absent, `Review queued`, or `Review in progress` is
   `pending`: a review that is queued or actively running has not declined
   anything, and reading it as `failure` turns every ordinary review's
   opening minutes into a spurious red required check, which is exactly what
   happened on this repository's own #32, the first pull request this gate
   ever ran against, before that bug was found and fixed here. Anything else,
   including a description this script has never seen before, is `failure`.
   No exceptions.

`coderabbit-review-queue.yml`'s hourly nudge exists for the same reason as in
the sibling repository: CodeRabbit never reviews a bot's pull request on its
own, so once `Pin Only` fails and a pull request falls into lane 3, nothing
but an explicit `@coderabbitai review` comment will ever put a status there
for `Review Verified` to read.

## Recovering a stuck `Review Verified`, honestly

`coderabbit-gate.yml`'s hourly schedule (`47 * * * *`) is described in its own
header comment as "the self-healing path," and that claim needs a caveat: it
is a real mitigation, not a guarantee. GitHub's own documentation says
scheduled workflows on public repositories are deprioritized under load and
can be skipped outright rather than merely delayed, and this repository has
already seen it happen twice in a row: both the `18:47` and `19:47` slots on
2026-08-26 passed with no run recorded against either, confirmed against the
Actions API rather than assumed, and PR #31 sat ungraded through both. An
hourly tick that cannot be relied on, repeatedly, is not something a required
check should be staked on being self-healing.

`workflow_dispatch` on `coderabbit-gate.yml` is the manual recovery path for
a bot pull request that remains blocked, run by anyone with write access,
either against a single `pr_number` or, left blank, against every open pull
request at once. It does not depend on GitHub's scheduler, but it is not a
guarantee either: it still needs a person to notice the stuck pull request
and start it, and that run still has to succeed. What makes it a workable
recovery path rather than merely a different kind of hope is that a stuck
`Review Verified` blocks the merge branch protection requires, so someone is
already looking at the pull request by the time it matters, unlike the
schedule's up-to-an-hour wait for a tick that might not come at all. Treat
the hourly schedule as a convenience that clears most missed runs without
anyone having to notice, and `workflow_dispatch` as what an actual person
reaches for when it does not.

## The merge queue is live

This repository transferred from a personal account to the
`ivan-pinatti-labs` organization specifically so a `merge_queue` ruleset
rule could exist at all: GitHub refuses that rule under personal ownership
and accepts it under an organization, free plan included. Ruleset `21672903`
is `enforcement: active`, with no bypass actor, so `merge_group` triggers on
`pull-request-validation.yml` and `coderabbit-gate.yml`, both added ahead of
need while the queue could not exist yet, are live rather than inert: every
required context runs a second time against the queue's own temporary
commit before anything actually merges, exactly as `docker-torrent-box-with-vpn`'s
own queue does.

Branch protection on `main` requires `Pre-commit`, `Tests`, `Pin Only` and
`Review Verified`, one approval, dismissal of stale reviews, approval of the
last push, conversation resolution and a linear history. `enforce_admins` is
`false`, which matters for exactly one account: it lets the owner merge
without being blocked by rules an admin can bypass, but it does not exempt
the owner's own pull request from the approval the queue itself requires to
accept it, which is what made "The repository owner's own pull request"
above necessary. `allow_auto_merge` is enabled, without which the queue
cannot accept anything at all.

---

See also: [README.md](../README.md), [CLAUDE.md](../CLAUDE.md)
