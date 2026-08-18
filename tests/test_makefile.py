"""Contract checks on the Makefile itself.

These need neither Docker nor a remote, so they stay fast and run first.
"""

from __future__ import annotations

from conftest import REPO_ROOT, run


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
