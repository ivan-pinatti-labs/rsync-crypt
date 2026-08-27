# The Merge Pipeline

<!-- cspell:words coderabbit coderabbitai dockerhub renovate -->

What happens between opening a pull request against this repository and it
landing on `main`. This is a thinner version of the document of the same
name in `docker-torrent-box-with-vpn`, which this pipeline was ported from
ahead of a planned migration of that repository to an organization (GitHub
refuses a merge queue on a personal account). This repository has four
required contexts, not six, and no integration suite, so the shape here is
simpler; where the reasoning is identical it is only summarized, not
restated.

## A human pull request

Open it as a **draft** first. `Pre-commit` runs the full hook set over every
file, and CodeRabbit does not review a draft at all: `.coderabbit.yaml` sets
`drafts: false` on purpose, so a review is not spent on a diff the mechanical
linters have not finished cleaning up yet.

**Mark it ready for review** once `Pre-commit` and `Tests` are green. That is
what starts CodeRabbit. Address what it raises, pushing fixes as needed; each
push re-runs both jobs and gets a fresh review.

Once every required check reads green and a maintainer has approved it,
branch protection allows the merge. There is no merge queue on this
repository (see "Branch protection: what is live and what is not" below), so
`strict: true` does that job instead: GitHub requires the pull request to be
up to date with `main` first, which a stale pull request clears by merging
`main` into it rather than by anything this pipeline runs.

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
3. **GitHub merges it** once every required check, including `Review
   Verified`, is green, the approval is in place, and the pull request is up
   to date with `main` (`strict: true`; see below).

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

## Branch protection: what is live and what is not

Classic branch protection on `main` requires `Pre-commit`, `Tests`,
`Pin Only` and `Review Verified`, with `strict: true` (a pull request must be
up to date with `main` before it merges), one approval, dismissal of stale
reviews, approval of the last push, conversation resolution and a linear
history. That means `bot-auto-merge.yml`'s approval now does something real:
without it, a pin-only bot pull request has no approving review and cannot
merge, which is the gap that workflow existed to close ahead of need.

Not live: a merge queue. This repository is personal-owned, and GitHub
refuses a `merge_queue` ruleset rule on a personal account, which is the
entire reason `docker-torrent-box-with-vpn` is transferring to an
organization in the first place. `strict: true` here is doing the job a
queue would otherwise do, at the older cost that made the sibling repository
move away from it: a pull request has to be rebased or merged up to date with
`main` itself, and one merge can invalidate every other open pull request's
checks. `merge_group` triggers on `pull-request-validation.yml` and
`coderabbit-gate.yml` exist anyway, and stay inert, so that enabling a queue
after a future transfer does not also require writing new workflow code at
the same time, which is the entire point of rehearsing this pipeline here
first.

---

See also: [README.md](../README.md), [CLAUDE.md](../CLAUDE.md)
