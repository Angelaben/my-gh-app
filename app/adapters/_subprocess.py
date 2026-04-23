"""Shared subprocess helpers used by all adapters."""
import os
import subprocess


class SubprocessError(Exception):
    """A subprocess returned a non-zero exit code."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def clean_env() -> dict[str, str]:
    """Return a copy of the environment without GitHub tokens.

    Removes GITHUB_TOKEN and GH_TOKEN so gh CLI uses its keyring auth,
    and opencode sub-processes don't inherit the caller's token.
    """
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    return env


def run_subprocess(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 60,
    input: str | None = None,  # noqa: A002
    env: dict[str, str] | None = None,
) -> str:
    """Run a subprocess and return stdout. Raises SubprocessError on failure."""
    if not args:
        raise ValueError("run_subprocess: args must not be empty")
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env if env is not None else clean_env(),
        cwd=cwd,
        input=input,
    )
    if result.returncode != 0:
        raise SubprocessError(
            f"{args[0]!r} exit code {result.returncode}: {result.stderr.strip()}",
            stderr=result.stderr.strip(),
        )
    return result.stdout.strip()


def run_git(args: list[str], *, cwd: str, timeout: int = 120) -> str:
    """Run a git command in a specific directory."""
    try:
        return run_subprocess(["git", *args], cwd=cwd, timeout=timeout)
    except SubprocessError as e:
        raise SubprocessError(f"git {args[0]}: {e}", stderr=e.stderr) from e
