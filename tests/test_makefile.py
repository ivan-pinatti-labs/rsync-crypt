"""Contract checks on the Makefile itself.

These need neither Docker nor a remote, so they stay fast and run first.
"""

from __future__ import annotations

import os
import shlex

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


def test_help_lists_every_phony_target():
    """Catches a target being added without a matching help line."""
    result = run(["make", "help"])
    assert result.returncode == 0

    makefile = (REPO_ROOT / "Makefile").read_text()
    phony = makefile.split(".PHONY:", 1)[1].split("\n\n", 1)[0]
    targets = {t for t in phony.replace("\\", " ").split() if t}

    # Shorthand aliases are intentionally documented next to their long form.
    aliases = {"r", "ro", "rr", "rro", "v", "vr"}
    missing = sorted(t for t in targets - aliases if t not in result.stdout)
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


def _build_recipe(env_file, extra_args=()):
    """The shell text 'make --dry-run build' would run, as one string."""
    result = run(["make", "--dry-run", "build", f"ENV_FILE={env_file}", *extra_args])
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
    """CLAUDE.md forbids '|| true' as a general error suppressor."""
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
    """docs/TODO.md item 10: a blank ${VAR} expansion must arrive as an empty
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
    """The exact scenario docs/TODO.md item 10 and CLAUDE.md's gotcha
    describe: blanking GOCRYPTFS_CIPHER must not shift GOCRYPTFS_SCRYPT_N
    into the cipher slot and push GOCRYPTFS_ENCRYPT_NAMES out of the argument
    list entirely, which previously made backup.sh fall back to its 'true'
    default and silently defeat every rsync filter rule (CLAUDE.md:
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
    arguments down, which is the same class of bug docs/TODO.md item 10
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
    """docs/TODO.md item 11: .env.example quotes every value deliberately, so
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
    convention would spell an empty value. docs/TODO.md item 11 records this
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
