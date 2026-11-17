"""`gate.py doctor [--install] [--all]`: whether the tools the checks need work, and installing the
missing ones. It checks the languages of the git repo in the working directory, or every language with
--all or outside a repo. --install runs the package-manager commands that need no password; the rest
are printed for the user. Exits 1 while anything is missing."""

import shlex
import sys
from pathlib import Path

from . import languages, lints, model
from .proc import git, git_paths, run

_REVIEWER = "a reviewer CLI (claude, codex or gemini)"


def _tools(langs):
    """The tools the checks of `langs` and the custom lints run, without repeats."""
    tools = [t for m in langs for t in m.TOOLS] + list(lints.TOOLS)
    return list({t.name: t for t in tools}.values())


def _langs(root):
    return languages.used(
        git_paths(root, "ls-files", "--cached", "--others", "--exclude-standard")
    )


def missing(root):
    """What the checks for the repo at `root` lack, from PATH lookups alone, so it is fast enough for
    session start."""
    out = [t.name for t in _tools(_langs(root)) if t.command() is None]
    return out + ([] if model.reviewer() else [_REVIEWER])


def _fix(tool):
    """How to fix `tool`, and the command --install runs for it (None if the user must act)."""
    cmd = tool.installer()
    if cmd and cmd[0] == "sudo":
        return f"run yourself: {shlex.join(cmd)}", None
    if cmd:
        return f"--install runs: {shlex.join(cmd)}", cmd
    if tool.manual:
        return tool.manual, None
    return "uvx could not run it; see the error above", None


def _report(scope, tools):
    """Prints each tool's state. Returns the install commands --install can run, and whether
    everything works."""
    print(f"papers-please doctor: {scope}")
    print(f"  ok       {run(['uv', '--version'], None).stdout.strip()}")
    todo, ok = [], True
    for t in tools:
        if not (problem := t.problem()):
            print(f"  ok       {t.name}")
            continue
        hint, cmd = _fix(t)
        print(f"  MISSING  {t.name}: {problem}\n           fix: {hint}")
        todo += [cmd] if cmd else []
        ok = False
    reviewer = model.reviewer()
    print(f"  ok       reviewer: {reviewer}" if reviewer else f"  MISSING  {_REVIEWER}")
    return todo, ok and bool(reviewer)


def main():
    args = sys.argv[2:]
    root = git(Path.cwd(), "rev-parse", "--show-toplevel").strip()
    if "--all" in args or not root:
        langs, scope = languages.used(), "all languages"
    else:
        langs = _langs(root)
        names = ", ".join(m.NAME for m in langs) or "no supported languages"
        scope = f"{root} ({names})"
    tools = _tools(langs)
    todo, ok = _report(scope, tools)
    if todo and "--install" in args:
        for cmd in todo:
            print(f"\n$ {shlex.join(cmd)}")
            p = run(cmd, None)
            if p.returncode:
                print((p.stdout + p.stderr).strip()[-2000:])
        print()
        _, ok = _report(scope, tools)
    elif todo:
        print(f"\n{len(todo)} can be installed with `doctor --install`.")
    sys.exit(0 if ok else 1)
