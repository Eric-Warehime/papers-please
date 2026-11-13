"""Running formatters, linters and tests through the language adapters in this package."""

import hashlib
import shlex
import shutil
import tempfile
from pathlib import Path

from . import config, proc
from .changes import Changes

SCRATCH = Path(tempfile.gettempdir()) / "papers-please"


def _titled(cmd, out):
    """Output under a `$ cmd` header, or "" if there is none."""
    return f"$ {shlex.join(map(str, cmd))}\n{out.strip()}" if out.strip() else ""


def joined(parts):
    """The non-empty outputs in parts, one after another."""
    return "\n".join(filter(None, parts))


class Ctx:
    """One hook run's view of the repo: its tool policies, the changed lines, and the tools found
    missing (reported to the user rather than to Claude)."""

    def __init__(self, root):
        self.root = Path(root)
        self.missing = []
        self._policies = None
        self._changes = None

    @property
    def changes(self):
        if self._changes is None:
            self._changes = Changes(self.root)
        return self._changes

    def policy(self, tool):
        # Loaded on first use, inside the hook's error handling for malformed config files.
        if self._policies is None:
            self._policies = config.load(self.root)
        return self._policies.get(tool) or config.Policy()

    def tool(self, name, pypi=None, plugins=()):
        """The command that runs CLI `name`: from PATH, else through uvx when it is on PyPI as `pypi`.
        None if neither works, and the tool is reported missing. `plugins` (PyPI packages) force uvx,
        since a copy on PATH may lack them."""
        if not plugins and shutil.which(name):
            return [name]
        if pypi and shutil.which("uvx"):
            src = ["--from", pypi] if pypi != name else []
            return ["uvx", *src, *(a for p in plugins for a in ("--with", p)), name]
        self.missing.append(f"{name} is not installed, so its checks were skipped")
        return None

    def find(self, path, *names, where=Path.is_file):
        """The nearest file named one of `names` that satisfies `where`, searching from `path`'s
        directory up to the repo root, or None. `path` is repo-relative or absolute."""
        start = (self.root / path).parent
        dirs = [start, *start.parents]
        dirs = dirs[: dirs.index(self.root) + 1] if self.root in dirs else []
        return next((d / n for d in dirs for n in names if where(d / n)), None)

    def project(self, path, marker):
        """The directory of the project (e.g. marker go.mod) holding `path`, or None."""
        found = self.find(path, marker)
        return found and found.parent

    def generated(self, name, key, text):
        """Writes text to file `name` in a scratch directory kept per `key` (e.g. a module path) and
        returns the file's path."""
        d = SCRATCH / hashlib.sha256(str(key).encode()).hexdigest()[:12]
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text)
        return d / name

    def run(self, cmd, cwd=None, env=None, stdin=None):
        return proc.run(cmd, cwd or self.root, env, stdin)

    def lint(self, cmd, cwd=None, env=None):
        """Runs a linter and returns its findings touched by the patch, so existing issues elsewhere in
        touched files don't count. A linter that fails abnormally (exit above 1) has its whole output
        returned instead."""
        p = self.run(cmd, cwd, env)
        out = p.stdout + p.stderr
        if p.returncode <= 1:
            # Read afresh: at Stop, formatting earlier files in this run changes the diff.
            changes = Changes(self.root)
            out = "\n".join(
                l for l in out.splitlines() if changes.touched(l, cwd or self.root)
            )
        return _titled(cmd, out)

    def quiet(self, cmd, cwd=None, ok=(0,)):
        """Runs a command, returning its output only if its exit code is not in `ok`."""
        p = self.run(cmd, cwd)
        return "" if p.returncode in ok else _titled(cmd, p.stdout + p.stderr)
