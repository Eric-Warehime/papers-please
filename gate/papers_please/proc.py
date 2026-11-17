"""Every subprocess the hooks start goes through run."""

import os
import subprocess

TIMED_OUT, NOT_FOUND = 124, 127
_TIMEOUT = 600


def run(cmd, cwd, env=None, stdin=None):
    """Runs cmd with its output captured, and never raises: a timeout exits TIMED_OUT and a missing
    program NOT_FOUND, with the reason on stderr. `env` entries override the environment; a None value
    unsets one."""
    env = {k: v for k, v in (os.environ | (env or {})).items() if v is not None}
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            cmd, TIMED_OUT, "", f"timed out after {_TIMEOUT}s"
        )
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(cmd, NOT_FOUND, "", str(e))


def git(root, *args):
    """git's stdout, or "" if it fails."""
    p = run(["git", *args], root)
    return "" if p.returncode else p.stdout


def git_paths(root, cmd, *args):
    """The paths a git subcommand lists, read NUL-separated so any file name survives."""
    return [p for p in git(root, cmd, "-z", *args).split("\0") if p]
