#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ivan Pinatti
"""Decide the `Review Verified` status from a pull request's own state.

Read a small JSON object on stdin (assembled by
`.github/workflows/coderabbit-gate.yml` from the GitHub API, never from an
event payload) and print `state=<success|pending|failure>` and
`description=<text>` on stdout, in the shape `>> "$GITHUB_OUTPUT"` expects.

Ported ahead of need from docker-torrent-box-with-vpn, which built this as the
fix for its own #114: `CodeRabbit` reports `success` on three outcomes that
are not the same thing. `Review completed` means a review happened. `Review
rate limited` means the quota was exhausted and nothing read the diff. A
skipped draft means CodeRabbit never looked because it was told not to.
Branch protection cannot tell these apart, because the legacy commit status
API it reads has no fifth state for "green but not for the reason you think",
and three of that repository's pull requests merged with no review ever
having happened as a direct result. `Review Verified` exists to be the
context that can tell them apart, so it is required instead of `CodeRabbit`
and grades every one of those outcomes as `failure` rather than inheriting
their `success`. Nothing here is specific to that repository's incident;
the same ambiguity in CodeRabbit's status applies to any repository using it,
which is why this repository carries the fix before ever needing it.

Three lanes, decided in this order:

1. A draft is `pending`, not `failure`.
CodeRabbit has not reviewed it because it was told not to (`drafts: false` in
.coderabbit.yaml), which is a deliberate wait rather than a decline, and a
required context that reads red for a pull request's entire draft phase
teaches nothing. Pending blocks the merge exactly as hard as failure does, so
nothing merges early either way.

2. A dependency bot pull request (`renovate[bot]` or `dependabot[bot]`, from
an account with write access, so never a fork) is graded on
`scripts/assert-pin-only-diff.py`'s verdict instead of CodeRabbit's, because
CodeRabbit never reviews a bot's pull request at all (confirmed upstream: bot
authors are hardcoded to be ignored). Requiring its review there would block
every bot pull request forever. A pin-only diff has nothing in it a review
would catch beyond what the assertion already read line by line, so it passes
unattended, same as it does today. A diff that fails the assertion is not
pin-only, which is the one shape a bot pull request can take that a human
would normally have opened instead, so from there it is graded exactly like a
human pull request: it already gets no automatic approval either way
(`bot-auto-merge.yml` withholds one), so a person is already looking, and
asking for a real review alongside that cannot stall the happy path.

3. Everything else, human authored or a bot pull request whose diff already
failed the pin-only assertion, is `success` only when the latest `CodeRabbit`
status description is exactly `Review completed`. Absent, `Review queued`, or
`Review in progress` is `pending`: a review that has been asked for and not
yet returned, or is actively running, has not declined anything, and reading
it as a failure would turn every ordinary review's opening minutes into a red
required check for no reason. This is not hypothetical: docker-torrent-box-with-vpn
found and fixed the identical bug in its own copy of this script, and this
copy carried it forward unfixed since it was ported before that fix landed.
Anything else present, `Review rate limited`, any `Review skipped: ...`
description reaching this lane (a non-bot pull request is never a draft by
the time this runs, since lane 1 already caught that), an error state, or a
description this has never seen before, is `failure`. No exceptions there:
this lane is where the sibling repository's unreviewed merges happened, and
a status this script does not recognize is exactly the shape a future change
to CodeRabbit's wording would take, so only the two known in-flight strings
above move to `pending`; nothing else gets the benefit of the doubt.

Fails closed throughout. An unset or unrecognized `pin_only_state` for a bot
pull request is treated as "not yet known" rather than "assume clean", which
reads `pending` rather than `success`; an unrecognized `is_fork` is treated as
a fork, which is the reading that cannot grant the unattended bot lane to
something that should not have it.
"""

import json
import sys

# Exact login match only. `renovate[bot]-x` is a login somebody may register,
# and this script never sees `head.repo.fork` compared for it: the workflow
# already withholds `is_fork == false` from anything but the two bots, but the
# check is repeated here anyway, because trusting an upstream filter to have
# been applied correctly is how the ambiguity this script fixes went
# unnoticed for as long as it did.
BOTS = frozenset({"renovate[bot]", "dependabot[bot]"})

# CodeRabbit's own in-flight states, observed live on real pull requests.
# Neither is a decline: a review that is queued or actively running has not
# read the diff and returned an answer yet, which is exactly what `pending`
# is for. Treating either as `failure` was the bug found on this
# repository's #32, the first pull request this gate ever ran against:
# `Review Verified` read red for the several minutes CodeRabbit was still
# working, with no decline behind it at all.
IN_FLIGHT_DESCRIPTIONS = frozenset({"Review queued", "Review in progress"})


def decide(data: dict) -> tuple[str, str]:
    """Return (state, description) for `Review Verified`."""
    if data.get("is_draft"):
        return "pending", "waiting for ready for review"

    author = data.get("author", "")
    # Fail closed: an unset or malformed is_fork reads as "is a fork", which
    # is the direction that cannot mistakenly grant the unattended bot lane.
    # `bool(...)` does not do that: `None`, `0`, `""` and `[]` all coerce to
    # `False`, "not a fork", for exactly the values this is meant to treat as
    # unrecognized. Only the literal `False` may open the bot lane, so the
    # comparison is against that value rather than through a cast.
    is_fork = data.get("is_fork", True) is not False
    pin_only_state = data.get("pin_only_state", "")

    if author in BOTS and not is_fork:
        if pin_only_state == "success":
            return "success", "pin-only diff, nothing to review"
        if pin_only_state != "failure":
            # Not yet published, or a value this has never seen. Either way,
            # the bot lane cannot be graded yet, and "cannot be graded" is
            # pending, not a pass.
            return "pending", "waiting for the pin-only verdict"
        # Falls through: a bot pull request whose diff is not pin-only is
        # graded exactly like a human one, below.

    description = data.get("coderabbit_description", "")
    # `in IN_FLIGHT_DESCRIPTIONS` raises TypeError for an unhashable value (a
    # list or an object survives JSON decoding as one), which main() does not
    # catch, so a malformed payload would crash this script instead of
    # reaching its own fail-closed return below. Every string comparison
    # above this point is safe against any type on its own, but checking the
    # type once here, before either of the checks below, is what keeps a
    # malformed `coderabbit_description` failing closed instead of crashing.
    if not isinstance(description, str):
        return "failure", "coderabbit_description was not a string"
    if description == "":
        return "pending", "waiting for a CodeRabbit review"
    if description == "Review completed":
        return "success", 'CodeRabbit reports "Review completed"'
    if description in IN_FLIGHT_DESCRIPTIONS:
        return "pending", f'CodeRabbit reports "{description}"'
    return "failure", f'CodeRabbit reports "{description}", which is not a review'


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"REFUSED: stdin was not valid JSON: {exc}", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        print("REFUSED: stdin JSON was not an object.", file=sys.stderr)
        return 1

    state, description = decide(data)
    # Both values are written to GITHUB_OUTPUT as `key=value` lines by the
    # caller, where a newline would let the rest of the value be parsed as a
    # further output: an unsanitised description could set `state=success` on
    # the very check meant to withhold it. The description embeds CodeRabbit's
    # own text, so it is flattened here rather than trusted to be one line.
    description = " ".join(description.split())
    print(f"state={state}")
    print(f"description={description}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
