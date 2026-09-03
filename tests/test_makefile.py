"""Contract checks on the Makefile itself.

These need neither Docker nor a remote, so they stay fast and run first.
"""

from __future__ import annotations

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


@pytest.mark.parametrize(
    "var",
    [
        "ALPINE_VERSION",
        "GOCRYPTFS_VERSION",
        "BASH_VERSION",
        "LESS_VERSION",
        "OPENSSH_VERSION",
        "RSYNC_VERSION",
        "SSHFS_VERSION",
        "VIM_VERSION",
    ],
)
def test_build_refuses_an_empty_apk_version_pin(build_env_file, tmp_path, var):
    """An empty *_VERSION must fail loudly before it reaches an empty --build-arg.

    Without this guard, a blank pin silently becomes `bash~=` inside the
    Dockerfile's apk add, which fails deep inside the build with no hint that
    the real problem is the env file, not the Dockerfile.
    """
    text = build_env_file.read_text()
    original = next(line for line in text.splitlines() if line.startswith(f'{var}="'))
    blanked = text.replace(original, f'{var}=""')
    assert blanked != text

    env_file = tmp_path / "env.blank"
    env_file.write_text(blanked)

    result = run(["make", "build", f"ENV_FILE={env_file}"])
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert var in combined
    assert "missing or empty" in combined
    # Never got as far as invoking docker.
    assert "docker build" not in combined


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

    referenced = set()
    for chunk in makefile.split("${")[1:]:
        name = chunk.split("}", 1)[0]
        if name.isupper() and name.replace("_", "").isalnum():
            referenced.add(name)

    missing = sorted(referenced - documented - internal)
    assert not missing, (
        f"variables used by the Makefile but absent from .env.example: {missing}"
    )


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
