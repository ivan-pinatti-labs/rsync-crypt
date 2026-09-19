"""Contract checks on the Makefile itself.

These need neither Docker nor a remote, so they stay fast and run first.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT, _read_var, run

# Positional argument specs for the three scripts the Makefile invokes,
# in call order. "literal" entries are fixed container paths the Makefile
# hardcodes; "var" entries are ${VAR} expansions read from the env file.
# Mirrors the __N parameters each script documents in its own "# parameters"
# block (scripts/backup.sh, scripts/restore.sh, scripts/view.sh).
_BACKUP_ARGS = (
    ("literal", "/backup/src"),
    ("literal", "/backup/enc"),
    ("var", "REMOTE_SERVER_BACKUP_FOLDER"),
    ("literal", "/backup/passfile"),
    ("var", "REMOTE_SERVER"),
    ("literal", "/backup/brave-filter-rules.txt"),
    ("var", "RSYNC_RATE_LIMIT"),
    ("var", "RSYNC_LOOP"),
    ("var", "GOCRYPTFS_CIPHER"),
    ("var", "GOCRYPTFS_SCRYPT_N"),
    ("var", "GOCRYPTFS_ENCRYPT_NAMES"),
)

_RESTORE_ARGS = (
    ("var", "REMOTE_SERVER"),
    ("var", "REMOTE_SERVER_BACKUP_FOLDER"),
    ("literal", "/restore/enc"),
    ("literal", "/restore/dec"),
    ("literal", "/restore/passfile"),
    ("literal", "/restore/restore-exclude-list.txt"),
    ("var", "RSYNC_RATE_LIMIT"),
    ("var", "RSYNC_LOOP"),
    ("literal", "/restore/origin"),
    ("literal", "/restore/restore-paths.txt"),
)

_VIEW_ARGS = (
    ("var", "REMOTE_SERVER"),
    ("var", "REMOTE_SERVER_BACKUP_FOLDER"),
    ("literal", "/gocrypt-view/passfile"),
    ("literal", "/gocrypt-view/encrypted"),
    ("literal", "/gocrypt-view/decrypted"),
)

# The eight versions baked into the image. They are ARG defaults in the
# Dockerfile, not env file settings, so the 'build' target only ever passes
# one as --build-arg when a caller sets it explicitly on the command line.
_BUILD_ARG_VARS = (
    "ALPINE_VERSION",
    "GOCRYPTFS_VERSION",
    "BASH_VERSION",
    "LESS_VERSION",
    "OPENSSH_VERSION",
    "RSYNC_VERSION",
    "SSHFS_VERSION",
    "VIM_VERSION",
)


def _env_file_with_overrides(tmp_path, overrides):
    """A full env file derived from .env.example with some values replaced.

    Starting from .env.example rather than a minimal hand-built file means
    every variable the Makefile reads has a realistic value, so only the
    variable under test differs from an ordinary run. Override values are
    written exactly as given, unquoted, which is itself a valid way to set a
    path-like value in an env file and keeps a space in the override real
    rather than absorbed by a pair of literal quote characters.
    """
    example = (REPO_ROOT / ".env.example").read_text()
    lines = []
    remaining = dict(overrides)
    for line in example.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0]
        if key in remaining:
            lines.append(f"{key}={remaining.pop(key)}")
        else:
            lines.append(line)
    assert not remaining, f"override keys not found in .env.example: {remaining}"
    path = tmp_path / "env.override"
    path.write_text("\n".join(lines) + "\n")
    return path


def _expected_value(spec_item, overrides, example_text):
    """The value a spec entry should resolve to, given the overrides in play."""
    kind, name = spec_item
    if kind == "literal":
        return name
    if name in overrides:
        return overrides[name].strip('"')
    return _read_var(example_text, name)


def _dry_run_positional_args(target, env_file, script_name):
    """The shell-parsed positional arguments 'make --dry-run' would pass.

    'make --dry-run' echoes the recipe's shell text after ${VAR} expansion,
    embedded newlines and all, since the backslash line continuations make
    the whole recipe one logical shell command. Joining the lines from the
    script invocation onward, stopping at the first line that does not end
    in a continuing backslash, and handing the result to shlex reproduces
    exactly what the shell would see, quote characters included. This is
    the same target 'make --dry-run' the maintainer used to confirm the fix
    by hand before writing this helper.
    """
    result = run(["make", "--dry-run", target, f"ENV_FILE={env_file}"])
    assert result.returncode == 0, result.stdout + result.stderr

    lines = result.stdout.splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.strip().startswith(f"/app/{script_name}")
    )
    logical = []
    for line in lines[start:]:
        stripped = line.strip()
        continued = stripped.endswith("\\")
        logical.append(stripped[:-1].strip() if continued else stripped)
        if not continued:
            break
    tokens = shlex.split(" ".join(logical))
    return tokens[1:]  # drop the script path itself


def test_help_is_the_default_goal():
    """Running bare 'make' must not require an env file."""
    result = run(["make"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Usage:" in result.stdout
    assert "ENV_FILE=.env.myconfig" in result.stdout


def _documented_help_targets(help_text):
    """Target names and shorthand aliases, parsed out of make help's Targets block.

    Each entry reads "  <name>[ (<alias>)]  <description>", so the name is the
    first field and the alias, where there is one, is the second in parentheses.

    Parsing the names rather than searching the whole help text is the point.
    A substring check over result.stdout passes for a target that is not
    documented at all as soon as its name appears inside some other target's
    description: "backup" sits inside "user-backup" in all's description, so
    deleting backup's own help line did not fail this test. Parsing also
    removes the need to hardcode the alias list, which means an alias losing
    its help line is now caught too.
    """
    names = set()
    aliases = set()
    in_targets = False
    for line in help_text.splitlines():
        if line.startswith("Targets:"):
            in_targets = True
            continue
        if not in_targets:
            continue
        # The block ends at the first line that is not an indented entry,
        # which is the blank line before the env file footer.
        if not line.startswith("  "):
            break
        fields = line.split()
        names.add(fields[0])
        if len(fields) > 1 and fields[1].startswith("(") and fields[1].endswith(")"):
            aliases.add(fields[1][1:-1])
    return names, aliases


def test_help_lists_every_phony_target():
    """Catches a target being added without a matching help line."""
    result = run(["make", "help"])
    assert result.returncode == 0

    # Every .PHONY line, not just the first. The declaration is deliberately
    # split one per line rather than written as a backslash continuation,
    # because checkmake reads only the first physical line of a .PHONY and
    # reports everything after it as undeclared; see the comment above the
    # declaration in the Makefile. Splitting on the first ".PHONY:" and
    # reading to the next blank line, which is what this did while the
    # declaration was a single continuation block, swept the literal
    # ".PHONY:" of every later line into the target set.
    makefile = (REPO_ROOT / "Makefile").read_text()
    targets = {
        t
        for line in makefile.splitlines()
        if line.startswith(".PHONY:")
        for t in line[len(".PHONY:") :].replace("\\", " ").split()
    }
    assert targets, "no .PHONY targets parsed out of the Makefile"

    documented, aliases = _documented_help_targets(result.stdout)
    assert documented, "no targets parsed out of 'make help'"

    missing = sorted(targets - documented - aliases)
    assert not missing, f"targets absent from 'make help': {missing}"


def test_missing_env_file_fails_with_guidance():
    result = run(["make", "build", "ENV_FILE=.env.does-not-exist"])
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Missing ENV_FILE '.env.does-not-exist'" in combined
    assert "cp .env.example" in combined


def test_help_works_even_when_the_env_file_is_missing():
    """help is filtered out of the env file guard, so it must still run."""
    result = run(["make", "help", "ENV_FILE=.env.does-not-exist"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Usage:" in result.stdout


def test_env_file_override_is_honoured(build_env_file):
    """A non-default ENV_FILE should be picked up rather than ignored."""
    result = run(["make", "build", f"ENV_FILE={build_env_file}", "--dry-run"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "local/gocryptfs-test" in result.stdout


def _build_recipe(env_file, extra_args=(), env=None):
    """The shell text 'make --dry-run build' would run, as one string.

    'env', when given, is a full os.environ-shaped mapping for the child
    process: a shell-environment-style override ('VAR=x make build') has to
    reach make as a real process environment variable, which a command-line
    argument in 'extra_args' does not exercise, that being a distinct Make
    override mechanism ('make build VAR=x') with a different $(origin).
    """
    kwargs = {} if env is None else {"env": env}
    result = run(
        ["make", "--dry-run", "build", f"ENV_FILE={env_file}", *extra_args], **kwargs
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("var", _BUILD_ARG_VARS)
def test_build_passes_no_build_arg_by_default(build_env_file, var):
    """A plain 'make build' must let the Dockerfile's own ARG defaults apply.

    The eight pins moved out of the env file and into the Dockerfile, so there
    is nothing left for the target to read; passing '--build-arg VAR=' with an
    empty value instead would build 'alpine:' and 'bash~=', which is the exact
    failure the old (now removed) emptiness guard existed to catch. $(if ...)
    dropping the flag entirely is what makes that unreachable rather than
    merely refused.
    """
    assert f"--build-arg {var}" not in _build_recipe(build_env_file)


@pytest.mark.parametrize("var", _BUILD_ARG_VARS)
def test_build_passes_an_explicit_override_through(build_env_file, var):
    """'VAR=x make build' (or 'make build VAR=x') must still reach docker.

    Overriding one pin for a single build, without editing the Dockerfile, is
    the escape hatch that makes baking the defaults in acceptable in the first
    place, so it is checked for each of the eight rather than assumed to
    generalize from ALPINE_VERSION.
    """
    recipe = _build_recipe(build_env_file, extra_args=[f"{var}=9.9.9"])
    assert f"--build-arg {var}=9.9.9" in recipe


def _legacy_env_file(build_env_file, tmp_path, var, value):
    """build_env_file plus one leftover pin, as if from before this split.

    Anyone who copied .env.example before the eight pins moved into the
    Dockerfile still has a line like this sitting in their real .env; the
    migration cannot reach into an existing file and remove it for them.
    """
    path = tmp_path / "env.legacy"
    path.write_text(build_env_file.read_text() + f"{var}={value}\n")
    return path


@pytest.mark.parametrize("var", _BUILD_ARG_VARS)
def test_build_ignores_a_legacy_pin_left_in_the_env_file(build_env_file, tmp_path, var):
    """A pin surviving from before the Dockerfile-ARG split must not forward.

    'make build' passes '--build-arg VAR' as a plain $(if ${VAR},...) test, so
    to plain Make there is no difference between "the caller set this on the
    command line" and "this line is still sitting in an old .env file": both
    ways for ${VAR} to be non-empty. Left unguarded, a caller who has not
    edited their env file since it carried these eight pins would have every
    build silently overridden by whatever version their file happened to
    freeze, defeating the entire point of moving the defaults into the
    Dockerfile: that a plain 'docker build .' and 'make build' agree. The
    Makefile's 'pin_override' $(origin)-based check is what closes this: a
    value read from ENV_FILE reports origin "file", not "command line" or
    "environment", and only those last two forward.
    """
    legacy = _legacy_env_file(build_env_file, tmp_path, var, "9.9.9")
    assert f"--build-arg {var}" not in _build_recipe(legacy)


@pytest.mark.parametrize("var", _BUILD_ARG_VARS)
def test_build_command_line_override_wins_over_a_legacy_env_pin(
    build_env_file, tmp_path, var
):
    """An explicit override must still work even with a legacy pin present.

    Ignoring ENV_FILE-origin values (the test above) must not also swallow a
    genuine one-off override typed on the command line in the same run: Make
    origin reports "command line" for that regardless of what ENV_FILE
    separately set the same variable to.
    """
    legacy = _legacy_env_file(build_env_file, tmp_path, var, "1.1.1")
    recipe = _build_recipe(legacy, extra_args=[f"{var}=9.9.9"])
    assert f"--build-arg {var}=9.9.9" in recipe
    assert f"--build-arg {var}=1.1.1" not in recipe


@pytest.mark.parametrize("var", _BUILD_ARG_VARS)
def test_build_shell_environment_override_wins_over_a_legacy_env_pin(
    build_env_file, tmp_path, var
):
    """'VAR=x make build' must win even when ENV_FILE also sets VAR.

    A plain '=' assignment in an included file unconditionally overrides a
    same-named value already set in the calling shell's environment, unlike
    a command-line assignment, which no makefile assignment can ever
    override. Without the pre-include snapshot in the Makefile
    (_pin_snapshot_<VAR>), 'ALPINE_VERSION=9.9.9 make build' against a
    legacy env file would silently lose both the caller's 9.9.9 and its
    "environment" origin the moment ENV_FILE is included, forwarding
    neither the caller's value nor the file's, just the Dockerfile's own
    default with no indication anything was overridden at all.
    """
    legacy = _legacy_env_file(build_env_file, tmp_path, var, "1.1.1")
    env = os.environ.copy()
    env[var] = "9.9.9"
    recipe = _build_recipe(legacy, env=env)
    assert f"--build-arg {var}=9.9.9" in recipe
    assert f"--build-arg {var}=1.1.1" not in recipe


@pytest.mark.parametrize("var", _BUILD_ARG_VARS)
def test_build_drops_an_explicit_but_empty_override(build_env_file, var):
    """'make build VAR=' must not forward '--build-arg VAR=' with no value.

    An empty command-line override still reports $(origin) as "command
    line", a qualifying origin on its own; only checking origin and not the
    value it carries would forward the flag with nothing after the '=',
    which is the exact 'docker build .' would-build-alpine: failure the old
    (now removed) blank-pin guard existed to catch.
    """
    recipe = _build_recipe(build_env_file, extra_args=[f"{var}="])
    assert f"--build-arg {var}" not in recipe


def test_build_invokes_docker_with_no_pins_configured(build_env_file, tmp_path):
    """The default path must actually reach 'docker build', not just look right.

    A stub 'docker' ahead of the real one on PATH, so "docker was invoked" is
    proven by a marker file rather than by captured output, which @-prefixed
    recipe lines never echo. This is the positive counterpart to the guard
    test that used to live here: where that one proved docker was NOT reached
    with an empty pin, this proves it IS reached now that there are no pins in
    the env file at all.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "docker-was-invoked"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {shlex.quote(str(marker))}\nexit 0\n"
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = run(["make", "build", f"ENV_FILE={build_env_file}"], env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists(), "docker was never invoked"
    assert "--build-arg" not in marker.read_text()


def test_example_env_documents_every_variable_the_makefile_reads():
    """Every ${VAR} the Makefile expands should exist in .env.example."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    example = (REPO_ROOT / ".env.example").read_text()

    documented = {
        line.split("=", 1)[0]
        for line in example.splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    # Variables the Makefile defines or receives itself, not env file settings.
    internal = {"ENV_FILE", "RESTORE_PATHS", "MAKECMDGOALS", "SHELL"}

    # The eight build-time pins are Dockerfile ARG defaults, and the 'build'
    # target reads them only as optional command-line overrides ($(if ...) per
    # pin). Documenting them in .env.example again would put the value in two
    # places and invite the two copies to disagree, which is what moving them
    # into the Dockerfile was for. test_build.py's
    # test_every_version_arg_has_a_default_and_its_annotation is what holds
    # them to account instead.
    build_arg_overrides = set(_BUILD_ARG_VARS)

    referenced = set()
    for chunk in makefile.split("${")[1:]:
        name = chunk.split("}", 1)[0]
        if name.isupper() and name.replace("_", "").isalnum():
            referenced.add(name)

    missing = sorted(referenced - documented - internal - build_arg_overrides)
    assert not missing, (
        f"variables used by the Makefile but absent from .env.example: {missing}"
    )


def test_no_build_pin_is_left_in_the_example_env():
    """The other direction: a pin must not creep back into .env.example.

    Both places would then define the same version, the Makefile would pass
    the env file's copy as a --build-arg, and the Dockerfile's default would
    quietly stop being what gets built, which is the split this change closed.
    """
    example = (REPO_ROOT / ".env.example").read_text()
    documented = {
        line.split("=", 1)[0]
        for line in example.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    strays = sorted(documented & set(_BUILD_ARG_VARS))
    assert not strays, f"build-time pins are back in .env.example: {strays}"


def test_no_blanket_error_suppression_in_the_makefile():
    """AGENTS.md forbids '|| true' as a general error suppressor."""
    offenders = [
        f"{n}: {line.strip()}"
        for n, line in enumerate((REPO_ROOT / "Makefile").read_text().splitlines(), 1)
        if "|| true" in line
    ]
    assert not offenders, f"'|| true' found in the Makefile: {offenders}"


@pytest.mark.parametrize(
    ("target", "script_name", "arg_spec"),
    [
        ("backup", "backup.sh", _BACKUP_ARGS),
        ("restore", "restore.sh", _RESTORE_ARGS),
        ("view", "view.sh", _VIEW_ARGS),
    ],
)
def test_blank_env_var_does_not_shift_script_arguments(
    tmp_path, target, script_name, arg_spec
):
    """A blank ${VAR} expansion must arrive as an empty
    positional argument, not vanish and shift every later argument down one
    slot. REMOTE_SERVER is used here because it is passed to all three
    scripts, so the same check covers backup.sh, restore.sh and view.sh.
    """
    example_text = (REPO_ROOT / ".env.example").read_text()
    overrides = {"REMOTE_SERVER": ""}
    env_file = _env_file_with_overrides(tmp_path, overrides)

    args = _dry_run_positional_args(target, env_file, script_name)
    expected = [_expected_value(item, overrides, example_text) for item in arg_spec]
    assert args == expected


def test_blank_gocryptfs_cipher_does_not_flip_encrypt_names_default(tmp_path):
    """The exact scenario AGENTS.md's Makefile-quoting and filter-rule gotchas
    describe: blanking GOCRYPTFS_CIPHER must not shift GOCRYPTFS_SCRYPT_N
    into the cipher slot and push GOCRYPTFS_ENCRYPT_NAMES out of the argument
    list entirely, which previously made backup.sh fall back to its 'true'
    default and silently defeat every rsync filter rule (AGENTS.md:
    GOCRYPTFS_ENCRYPT_NAMES must be false for filter rules to match).
    """
    example_text = (REPO_ROOT / ".env.example").read_text()
    overrides = {"GOCRYPTFS_CIPHER": ""}
    env_file = _env_file_with_overrides(tmp_path, overrides)

    args = _dry_run_positional_args("backup", env_file, "backup.sh")
    expected = [_expected_value(item, overrides, example_text) for item in _BACKUP_ARGS]
    assert args == expected

    assert args[8] == ""  # GOCRYPTFS_CIPHER: quoted-and-empty, not vanished
    assert args[9] == _read_var(example_text, "GOCRYPTFS_SCRYPT_N")
    assert args[10] == _read_var(example_text, "GOCRYPTFS_ENCRYPT_NAMES")


def test_value_with_a_space_does_not_split_into_two_arguments(tmp_path):
    """Same defect, opposite direction: an unquoted expansion whose value
    contains a space splits into two shell words instead of shifting later
    arguments down, which is the same class of bug the Makefile-quoting gotcha
    names for REMOTE_SERVER and REMOTE_SERVER_BACKUP_FOLDER.
    """
    example_text = (REPO_ROOT / ".env.example").read_text()
    overrides = {"REMOTE_SERVER_BACKUP_FOLDER": "/mnt/backups/a user"}
    env_file = _env_file_with_overrides(tmp_path, overrides)

    args = _dry_run_positional_args("view", env_file, "view.sh")
    expected = [_expected_value(item, overrides, example_text) for item in _VIEW_ARGS]
    assert args == expected
    assert args[1] == "/mnt/backups/a user"


@pytest.mark.parametrize(
    ("target", "script_name", "arg_spec"),
    [
        ("backup", "backup.sh", _BACKUP_ARGS),
        ("restore", "restore.sh", _RESTORE_ARGS),
        ("view", "view.sh", _VIEW_ARGS),
    ],
)
def test_quoted_env_value_with_a_space_arrives_as_one_argument(
    tmp_path, target, script_name, arg_spec
):
    """.env.example quotes every value deliberately, so
    'include $(ENV_FILE)' hands the make variable those literal quote
    characters as part of its value, e.g.
    REMOTE_SERVER_BACKUP_FOLDER="/mnt/my backups" sets the variable to
    '"/mnt/my backups"', quotes included. Item 10 wrapped each expansion in a
    second pair of Makefile-level quotes, but the two pairs do not nest: the
    recipe used to emit '"/mnt/my backups"' with the quote characters still
    embedded, and the shell split it into two words on the space exactly as
    before item 10. This is the case that fails today (before the item 11
    fix strips the embedded quotes with $(subst)) and is the one item 10
    left open. REMOTE_SERVER_BACKUP_FOLDER is used because it appears in all
    three scripts' argument lists, so one override covers backup.sh,
    restore.sh and view.sh.
    """
    example_text = (REPO_ROOT / ".env.example").read_text()
    overrides = {"REMOTE_SERVER_BACKUP_FOLDER": '"/mnt/backups/a user"'}
    env_file = _env_file_with_overrides(tmp_path, overrides)

    args = _dry_run_positional_args(target, env_file, script_name)
    expected = [_expected_value(item, overrides, example_text) for item in arg_spec]
    assert args == expected
    assert "/mnt/backups/a user" in args


def test_quoted_blank_gocryptfs_cipher_does_not_flip_encrypt_names_default(tmp_path):
    """The item 10 shifting guarantee re-checked under the quoted convention.

    test_blank_gocryptfs_cipher_does_not_flip_encrypt_names_default already
    covers an unquoted blank (GOCRYPTFS_CIPHER=). This is the same scenario
    with GOCRYPTFS_CIPHER="" instead, which is how .env.example's own
    convention would spell an empty value. AGENTS.md records this
    as one of the three cases $(subst ",,$(VAR)) was verified against: a
    value with a space, a value without one, and an empty value.
    """
    example_text = (REPO_ROOT / ".env.example").read_text()
    overrides = {"GOCRYPTFS_CIPHER": '""'}
    env_file = _env_file_with_overrides(tmp_path, overrides)

    args = _dry_run_positional_args("backup", env_file, "backup.sh")
    expected = [_expected_value(item, overrides, example_text) for item in _BACKUP_ARGS]
    assert args == expected

    assert args[8] == ""  # GOCRYPTFS_CIPHER: quoted-and-empty, not vanished
    assert args[9] == _read_var(example_text, "GOCRYPTFS_SCRYPT_N")
    assert args[10] == _read_var(example_text, "GOCRYPTFS_ENCRYPT_NAMES")


# --------------------------------------------------------------------------
# Config path resolution (issue #111).
#
# BACKUP_FILTER_RULES, RESTORE_EXCLUDE_LIST and RESTORE_PATHS_FILE are the
# three variables .env.example documents with relative defaults, and the
# Makefile hands each straight to 'docker run --volume'. A relative value used
# to be resolved by the container runtime against make's working directory
# rather than against the env file, so an env file kept elsewhere mounted
# whatever happened to sit at that relative path here, silently and with no
# symptom while the two copies agreed.
# --------------------------------------------------------------------------

_CONFIG_PATH_VARS = (
    ("BACKUP_FILTER_RULES", "backup", "/backup/brave-filter-rules.txt"),
    ("RESTORE_EXCLUDE_LIST", "restore", "/restore/restore-exclude-list.txt"),
    ("RESTORE_PATHS_FILE", "restore", "/restore/restore-paths.txt"),
)


def _volume_source(target, env_file, container_path):
    """The host side of the --volume flag mounting container_path.

    Reads the flag out of 'make --dry-run' and shlex-parses just that token,
    so the assertion sees the path the shell would, with the env file's own
    quote characters already resolved.
    """
    result = run(["make", "--dry-run", target, f"ENV_FILE={env_file}"])
    assert result.returncode == 0, result.stdout + result.stderr

    matches = [
        line.strip()
        for line in result.stdout.splitlines()
        if "--volume" in line and line.strip().endswith(f":{container_path} \\")
    ]
    assert len(matches) == 1, f"expected one mount of {container_path}, got {matches}"

    flag = matches[0].rstrip("\\").strip()
    spec = shlex.split(flag)[1]  # drop the literal '--volume'
    return spec[: -len(f":{container_path}")]


@pytest.mark.parametrize(("var", "target", "container_path"), _CONFIG_PATH_VARS)
def test_relative_config_path_resolves_against_the_env_file(
    tmp_path, var, target, container_path
):
    """A relative value follows the env file, not make's working directory.

    The env file lives in tmp_path while make runs in REPO_ROOT, which is the
    combination docs/USAGE.md recommends and the one that used to break.
    """
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "rules.txt").write_text("- **/.cache\n")

    env_file = _env_file_with_overrides(tmp_path, {var: "./conf/rules.txt"})
    source = _volume_source(target, env_file, container_path)

    assert source == str(conf_dir / "rules.txt")
    assert "/./" not in source  # the trailing-slash join is collapsed
    assert not source.startswith(str(REPO_ROOT))


@pytest.mark.parametrize(("var", "target", "container_path"), _CONFIG_PATH_VARS)
def test_absolute_config_path_passes_through_unchanged(
    tmp_path, var, target, container_path
):
    """An absolute value is never rewritten, wherever the env file lives."""
    rules = tmp_path / "somewhere-else.txt"
    rules.write_text("- **/.cache\n")

    env_file = _env_file_with_overrides(tmp_path, {var: str(rules)})
    assert _volume_source(target, env_file, container_path) == str(rules)


def test_default_env_file_at_the_repo_root_is_unchanged(tmp_path):
    """The pre-existing default resolves exactly where it always did.

    .env.example ships relative conf paths and the overwhelming majority of
    users run make from the repository root with an env file beside it, so
    this is the case the change must not disturb.
    """
    source = _volume_source(
        "backup", REPO_ROOT / ".env.example", "/backup/brave-filter-rules.txt"
    )
    assert source == str(REPO_ROOT / "conf" / "backup-filter-rules.example.txt")


def test_relative_config_path_containing_a_space_stays_one_argument(tmp_path):
    """A rewritten path with a space must not split into two arguments.

    The env file's own quotes are the only quoting the shell sees at a
    --volume site, so env_rel has to re-quote the value it rewrites. Without
    that, 'docker run' would receive two arguments and mount neither path.
    """
    conf_dir = tmp_path / "my conf"
    conf_dir.mkdir()
    (conf_dir / "rules.txt").write_text("- **/.cache\n")

    env_file = _env_file_with_overrides(
        tmp_path, {"BACKUP_FILTER_RULES": '"./my conf/rules.txt"'}
    )
    source = _volume_source("backup", env_file, "/backup/brave-filter-rules.txt")
    assert source == str(conf_dir / "rules.txt")


def test_absolute_config_path_containing_a_space_stays_one_argument(tmp_path):
    """A quoted absolute path with a space survives as one argument."""
    conf_dir = tmp_path / "abs space"
    conf_dir.mkdir()
    rules = conf_dir / "rules.txt"
    rules.write_text("- **/.cache\n")

    env_file = _env_file_with_overrides(tmp_path, {"BACKUP_FILTER_RULES": f'"{rules}"'})
    source = _volume_source("backup", env_file, "/backup/brave-filter-rules.txt")
    assert source == str(rules)


def test_unquoted_absolute_path_containing_a_space_stays_one_argument(tmp_path):
    """The absolute branch has to add quotes, not assume the author did.

    An unquoted value is a legal way to write a path in an env file, and
    _env_file_with_overrides writes overrides exactly as given for that
    reason. An earlier env_rel returned an absolute value untouched on the
    theory that the env file's own quotes were the only quoting a --volume
    site sees, which is true only while a value is passed through unchanged.
    Without quotes of its own the path split into two arguments and neither
    half named a real file. Caught by CodeRabbit on #112.
    """
    conf_dir = tmp_path / "abs unquoted"
    conf_dir.mkdir()
    rules = conf_dir / "rules.txt"
    rules.write_text("- **/.cache\n")

    env_file = _env_file_with_overrides(tmp_path, {"BACKUP_FILTER_RULES": str(rules)})
    source = _volume_source("backup", env_file, "/backup/brave-filter-rules.txt")
    assert source == str(rules)


def test_unreadable_config_file_fails_before_docker_runs(tmp_path):
    """The check tests readability, which is what its error message claims.

    A regular file with no read permission passes -f. Under rootless Podman
    or Docker the container's root maps back to the invoking user, so it
    cannot read the file either and the run fails later and less clearly.
    """
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    rules = conf_dir / "rules.txt"
    rules.write_text("- **/.cache\n")
    rules.chmod(0o000)
    try:
        env_file = _env_file_with_overrides(
            tmp_path, {"BACKUP_FILTER_RULES": "./conf/rules.txt"}
        )
        result = run(["make", "backup", f"ENV_FILE={env_file}"])
        assert result.returncode != 0
        assert "BACKUP_FILTER_RULES" in result.stderr
        assert str(rules) in result.stderr
    finally:
        rules.chmod(0o600)


@pytest.mark.parametrize(("var", "target", "container_path"), _CONFIG_PATH_VARS)
def test_missing_config_file_fails_before_docker_runs(
    tmp_path, var, target, container_path
):
    """A path that names no file stops the run with an actionable message.

    Left unchecked, the container runtime creates an empty directory at a
    missing bind mount source and mounts that, so the container receives a
    directory where it expects a file and the real cause never surfaces.

    Every config variable the target reads except the one under test is
    pinned to a real file, because a target checks them in order and would
    otherwise fail on whichever sibling the env file's own relative default
    happens to resolve to first.
    """
    overrides = {
        other: str(REPO_ROOT / "conf" / f"{stem}.example.txt")
        for other, stem in (
            ("BACKUP_FILTER_RULES", "backup-filter-rules"),
            ("RESTORE_EXCLUDE_LIST", "restore-exclude-list"),
            ("RESTORE_PATHS_FILE", "restore-paths"),
        )
        if other != var
    }
    overrides[var] = "./conf/absent.txt"

    env_file = _env_file_with_overrides(tmp_path, overrides)
    result = run(["make", target, f"ENV_FILE={env_file}"])

    assert result.returncode != 0
    assert var in result.stderr
    assert str(tmp_path / "conf" / "absent.txt") in result.stderr
    assert str(env_file) in result.stderr


def test_env_file_outside_the_working_directory_is_announced(tmp_path):
    """The resolution is stated rather than left to be inferred."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "rules.txt").write_text("- **/.cache\n")

    env_file = _env_file_with_overrides(
        tmp_path, {"BACKUP_FILTER_RULES": "./conf/rules.txt"}
    )
    result = run(["make", "--dry-run", "backup", f"ENV_FILE={env_file}"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(env_file) in result.stdout
    assert f"{tmp_path}{os.sep}" in result.stdout


def test_env_file_in_the_working_directory_is_not_announced():
    """No note when there is nothing surprising to report."""
    result = run(
        ["make", "--dry-run", "backup", f"ENV_FILE={REPO_ROOT / '.env.example'}"]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "which is outside" not in result.stdout


def _recipe_bodies():
    """Every target's recipe text, keyed by target name.

    A target line is unindented and ends in a colon; its recipe is the run of
    tab-indented lines that follows. Good enough for this Makefile, and the
    point is to read the recipes rather than trust that a call was pasted
    into the right one.
    """
    bodies = {}
    current = None
    for line in (REPO_ROOT / "Makefile").read_text().splitlines():
        if line.startswith("\t"):
            if current:
                bodies[current] += line + "\n"
            continue
        if line and not line[0].isspace() and ":" in line:
            name = line.split(":", 1)[0].strip()
            if name and not name.startswith(".") and " " not in name:
                current = name
                bodies.setdefault(current, "")
                continue
        current = None
    return bodies


@pytest.mark.parametrize(
    ("var", "resolved"),
    [
        ("BACKUP_FILTER_RULES", "_backup_filter_rules"),
        ("RESTORE_EXCLUDE_LIST", "_restore_exclude_list"),
        ("RESTORE_PATHS_FILE", "_restore_paths_file"),
    ],
)
def test_every_target_that_mounts_a_config_file_also_validates_it(var, resolved):
    """Mounting and validating must be the same set of targets, both ways.

    Two failures this catches, one in each direction, and the first shipped:

    - A target mounts the file without validating it, so a missing source
      reaches Docker and becomes an empty directory bind mount, which is the
      failure _require_config_file exists to prevent. run_container and
      run_container_as_root were in this state.
    - A target validates a file it never mounts, so an unrelated missing file
      blocks a perfectly valid run. view and view_as_root were in this state,
      and view.sh takes no filter rules argument at all.

    Both were found by CodeRabbit on #112 after the checks were attached by
    matching each recipe's passkey path rather than by reading what it mounts.
    """
    bodies = _recipe_bodies()
    mounts = {t for t, b in bodies.items() if f"--volume $({resolved})" in b}
    checks = {t for t, b in bodies.items() if f"_require_config_file,{var}" in b}

    assert mounts, f"no target mounts $({resolved}); the patterns have drifted"
    assert mounts == checks, (
        f"{var}: mounted without validation in {sorted(mounts - checks)}; "
        f"validated without mounting in {sorted(checks - mounts)}"
    )


# --------------------------------------------------------------------------
# new-profile.
# --------------------------------------------------------------------------


def _new_profile(name, cwd):
    return subprocess.run(
        ["make", "new-profile", f"NAME={name}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


@pytest.fixture
def profile_workspace(tmp_path):
    """A throwaway copy of the files new-profile reads and writes.

    Copied rather than used in place so a test run never leaves .env.<name>
    or conf/*.<name>.txt behind in the working tree.
    """
    shutil.copy(REPO_ROOT / "Makefile", tmp_path / "Makefile")
    shutil.copy(REPO_ROOT / ".env.example", tmp_path / ".env.example")
    shutil.copytree(REPO_ROOT / "conf", tmp_path / "conf")
    return tmp_path


def test_new_profile_creates_the_env_file_and_its_conf_copies(profile_workspace):
    result = _new_profile("banana", profile_workspace)
    assert result.returncode == 0, result.stdout + result.stderr

    for created in (
        ".env.banana",
        "conf/backup-filter-rules.banana.txt",
        "conf/restore-exclude-list.banana.txt",
        "conf/restore-paths.banana.txt",
    ):
        assert (profile_workspace / created).is_file(), created

    env_text = (profile_workspace / ".env.banana").read_text()
    assert _read_var(env_text, "BACKUP_FILTER_RULES") == (
        "./conf/backup-filter-rules.banana.txt"
    )
    assert _read_var(env_text, "RESTORE_EXCLUDE_LIST") == (
        "./conf/restore-exclude-list.banana.txt"
    )
    assert _read_var(env_text, "RESTORE_PATHS_FILE") == (
        "./conf/restore-paths.banana.txt"
    )


def test_new_profile_copies_rather_than_links_the_examples(profile_workspace):
    """The copy is independent, so editing it cannot touch the tracked file."""
    assert _new_profile("banana", profile_workspace).returncode == 0

    copy = profile_workspace / "conf" / "backup-filter-rules.banana.txt"
    example = profile_workspace / "conf" / "backup-filter-rules.example.txt"
    assert copy.read_text() == example.read_text()

    copy.write_text("- everything\n")
    assert example.read_text() != copy.read_text()


def test_new_profile_refuses_to_overwrite(profile_workspace):
    assert _new_profile("banana", profile_workspace).returncode == 0

    again = _new_profile("banana", profile_workspace)
    assert again.returncode != 0
    assert "already exists" in again.stderr


@pytest.mark.parametrize("name", ["../evil", "a/b", "with space", ".", ".."])
def test_new_profile_rejects_an_unusable_name(profile_workspace, name):
    result = _new_profile(name, profile_workspace)
    assert result.returncode != 0
    assert "not usable as a file name suffix" in result.stderr


def test_new_profile_without_a_name_prints_usage(profile_workspace):
    result = subprocess.run(
        ["make", "new-profile"],
        cwd=profile_workspace,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode != 0
    assert "Usage: make new-profile NAME=<profile>" in result.stderr


@pytest.mark.parametrize("name", ["banana", "foo.example", "example.txt"])
def test_new_profile_output_is_always_gitignored(profile_workspace, name):
    """No profile name can produce a tracked conf file.

    The shipped templates used to be un-ignored with a '!conf/*.example.txt'
    glob. Because a profile name may contain a dot, NAME=foo.example produced
    conf/backup-filter-rules.foo.example.txt, which that glob matched, so the
    generated copies became tracked files and the convention promised
    something it did not deliver. Caught by CodeRabbit on #112; the three
    templates are named individually now.

    Asked of the real .gitignore via git check-ignore, rather than by
    re-implementing its matching rules here.
    """
    assert _new_profile(name, profile_workspace).returncode == 0

    generated = [
        f"conf/backup-filter-rules.{name}.txt",
        f"conf/restore-exclude-list.{name}.txt",
        f"conf/restore-paths.{name}.txt",
    ]
    not_ignored = [
        path
        for path in generated
        if subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=REPO_ROOT,
            check=False,
        ).returncode
        != 0
    ]
    assert not not_ignored, f"NAME={name} produces tracked files: {not_ignored}"


def test_shipped_conf_templates_are_tracked():
    """The other direction: the templates themselves must not be ignored.

    A .gitignore narrow enough to catch every profile copy could just as
    easily exclude the templates a fresh clone needs.
    """
    for template in (
        "backup-filter-rules",
        "restore-exclude-list",
        "restore-paths",
    ):
        path = f"conf/{template}.example.txt"
        assert (REPO_ROOT / path).is_file(), f"missing template: {path}"
        assert (
            subprocess.run(
                ["git", "check-ignore", "-q", path],
                cwd=REPO_ROOT,
                check=False,
            ).returncode
            != 0
        ), f"shipped template is gitignored: {path}"


@pytest.mark.parametrize(
    "target_name",
    [".env.banana", "conf/backup-filter-rules.banana.txt"],
)
def test_new_profile_refuses_a_dangling_symlink(
    profile_workspace, tmp_path, target_name
):
    """A dangling link must be refused, not written through.

    `test -e` is false for a dangling symbolic link, so the overwrite guard
    used to pass and the write followed the link to wherever it pointed.
    GNU cp declines ("not writing through dangling symlink"), but the shell
    redirection that writes the env file does not: on #112 it created its
    target outside the repository, 179 lines of it, while new-profile printed
    "Created:" and exited 0. Anything the user can write was reachable from a
    link planted in the working tree.

    Parametrised over both write mechanisms, because only one of them was
    protected by coreutils and the guard must not depend on which is which.
    """
    victim = tmp_path / "victim"
    assert not victim.exists()
    link = profile_workspace / target_name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(victim)

    result = _new_profile("banana", profile_workspace)

    assert result.returncode != 0, result.stdout
    assert "already exists" in result.stderr
    assert not victim.exists(), f"wrote through the dangling link to {victim}"


def test_new_profile_refuses_a_symlink_to_an_existing_file(profile_workspace, tmp_path):
    """The non-dangling case, which -e already covered, stays covered."""
    victim = tmp_path / "victim"
    victim.write_text("ORIGINAL\n")
    (profile_workspace / ".env.banana").symlink_to(victim)

    result = _new_profile("banana", profile_workspace)

    assert result.returncode != 0
    assert victim.read_text() == "ORIGINAL\n"


def test_new_profile_needs_no_env_file(profile_workspace):
    """It is the target that creates one, so requiring one first is circular."""
    assert not (profile_workspace / ".env").exists()
    assert _new_profile("banana", profile_workspace).returncode == 0
