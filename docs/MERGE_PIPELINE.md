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

Once every required check reads green, and once branch protection requires a
merge queue (not yet: see "Not yet enabled" below), GitHub would add the pull
request to the queue on its own and merge it once the queue's own run of the
same check set passes on the commit the queue actually builds. Today, with no
branch protection at all, a green set of checks is a maintainer's own signal
to merge by hand.

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
   branch protection will require, once it exists. A diff that is not
   pin-only gets no approval and waits for a person, same as a major bump
   does. See that workflow's own header comment for why it does nothing
   observable yet.
3. **GitHub merges it** once every required check, including `Review
   Verified`, is green. This step, too, needs branch protection to require an
   approval before it does anything.

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
   `Review completed`. Absent is `pending`. Anything else, including a
   description this script has never seen before, is `failure`. No
   exceptions.

`coderabbit-review-queue.yml`'s hourly nudge exists for the same reason as in
the sibling repository: CodeRabbit never reviews a bot's pull request on its
own, so once `Pin Only` fails and a pull request falls into lane 3, nothing
but an explicit `@coderabbitai review` comment will ever put a status there
for `Review Verified` to read.

## Not yet enabled

Branch protection is not turned on for this repository, and no ruleset
exists yet. That is a deliberate, separate step the owner performs by hand,
not an oversight in this pull request. Until it is:

- `bot-auto-merge.yml`'s approval has nothing to satisfy and is a no-op in
  effect, even though it runs.
- There is no merge queue, so `merge_group` triggers on
  `pull-request-validation.yml` and `coderabbit-gate.yml` never fire; they
  exist now so that enabling the queue later does not also require writing
  new workflow code at the same time, which is the entire point of
  rehearsing this pipeline here before touching the more important
  repository.
- Nothing stops a direct push to `main` today except the pre-commit hook
  `checklist-git-protected-branches` on a contributor's own machine, which is
  not a substitute for a server side rule and was never meant to be one.

This repository is intentionally being used to test the whole shape of the
pipeline while personal-owned, so that the eventual transfer to an
organization tests only the transfer itself.

---

See also: [README.md](../README.md), [CLAUDE.md](../CLAUDE.md)
