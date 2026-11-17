"""The external programs the checks run: how to find each one, tell whether it works, and install it."""

import re
import shlex
import shutil
from dataclasses import dataclass, field

from . import proc

# Package managers, in order of preference, and their install command for a package.
_INSTALLERS = {
    "brew": lambda p: ["brew", "install", p],
    "rustup": lambda p: ["rustup", "component", "add", p],
    "apt-get": lambda p: ["sudo", "apt-get", "install", "-y", p],
    "dnf": lambda p: ["sudo", "dnf", "install", "-y", p],
}


@dataclass
class Tool:
    """A CLI the checks run. When it isn't on PATH it runs through uvx from PyPI package `pypi`, if
    given; `plugins` are PyPI packages it needs alongside, which force uvx since a copy on PATH may lack
    them. `probe` makes it print its version, which must match `version` if given. `install` maps a
    package manager to the package that provides it; `manual` says how to install it otherwise."""

    name: str
    pypi: str | None = None
    plugins: tuple = ()
    probe: tuple = ("--version",)
    version: str | None = None
    install: dict = field(default_factory=dict)
    manual: str = ""

    def command(self):
        """The command that runs it, or None if it is neither on PATH nor available through uvx."""
        if not self.plugins and shutil.which(self.name):
            return [self.name]
        if self.pypi and shutil.which("uvx"):
            src = ["--from", self.pypi] if self.pypi != self.name else []
            plugins = (a for p in self.plugins for a in ("--with", p))
            return ["uvx", *src, *plugins, self.name]
        return None

    def problem(self):
        """Why it doesn't work, or "" if it does. Runs it, so a uvx tool is fetched into uv's cache."""
        cmd = self.command()
        if not cmd:
            return "not installed"
        p = proc.run([*cmd, *self.probe], None)
        out = (p.stdout + p.stderr).strip()
        first = out.splitlines()[0] if out else f"exit {p.returncode}"
        if p.returncode:
            return f"`{shlex.join([*cmd, *self.probe])}` failed: {first}"
        if self.version and not re.search(self.version, out):
            return f"wrong version: {first}"
        return ""

    def installer(self):
        """The command that installs it with a package manager found here, or None."""
        found = (m for m in _INSTALLERS if m in self.install and shutil.which(m))
        return next((_INSTALLERS[m](self.install[m]) for m in found), None)
