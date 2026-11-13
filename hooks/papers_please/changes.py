"""The patch under review: the diff against the turn's base commit and which lines it changed."""

import re
from functools import cached_property
from pathlib import Path

from .proc import git, git_paths

_LOCATION = re.compile(r"^(.+?):(\d+):")
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
# Refs present while an operation awaits its commit; the diff from HEAD then holds everything it brought
# in, not just the agent's changes.
_PENDING = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")


def _has(root, ref):
    return bool(git(root, "rev-parse", "--verify", "-q", ref))


def _base(root):
    """What the patch is measured from: HEAD (or the empty tree before the first commit), or AUTO_MERGE
    while an operation has stopped on conflicts. AUTO_MERGE is the merged tree with conflict markers, so
    the patch is the agent's resolutions and later edits. None if an operation is pending without one."""
    pending = any(_has(root, r) for r in _PENDING)
    if (pending or _has(root, "REBASE_HEAD")) and _has(root, "AUTO_MERGE"):
        return "AUTO_MERGE"
    if pending:
        return None
    return "HEAD" if _has(root, "HEAD") else _EMPTY_TREE


def _untracked(root):
    return git_paths(root, "ls-files", "--others", "--exclude-standard")


def _hunks(root, base, *paths):
    """(path, first line, line count) of each hunk of the tracked diff against base. A count of 0 is a
    pure deletion after line `first`."""
    out, path = [], None
    for l in git(
        root, "diff", "-U0", "--no-color", "--no-ext-diff", base, "--", *paths
    ).splitlines():
        if l.startswith("+++ "):
            path = l[6:]
        elif m := re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))?", l):
            out.append((path, int(m[1]), int(m[2] or 1)))
    return out


def _changed_lines(root, base):
    """Maps each path the patch touches to its changed line numbers; a deletion marks the line before
    it. Untracked files map to None (all lines)."""
    lines = {}
    for path, start, n in _hunks(root, base):
        lines.setdefault(path, set()).update(range(start, start + n) if n else [start])
    return lines | dict.fromkeys(_untracked(root))


def _block(lines, i, python):
    """Returns the 1-based line numbers of the construct starting at line i: through its closing bracket,
    or for a Python header, through the lines indented under it. Relies on formatted code; brackets in
    strings or comments can throw it off."""
    depth, indent = 0, None
    for j in range(i - 1, len(lines)):
        l = lines[j]
        if indent is not None:
            if l.strip() and len(l) - len(l.lstrip()) <= indent:
                return range(i, j + 1)
            continue
        depth += sum(map(l.count, "([{")) - sum(map(l.count, ")]}"))
        if depth > 0:
            continue
        if not (python and l.split("#")[0].rstrip().endswith(":")):
            return range(i, j + 2)
        indent = len(lines[i - 1]) - len(lines[i - 1].lstrip())
    return range(i, len(lines) + 1)


class Changes:
    """The uncommitted changes in the repo at `root`, measured from _base. With no base, nothing counts
    as changed. Changed lines are read once, on first use."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.base = _base(self.root)
        self.sources = {}

    @cached_property
    def lines(self):
        return _changed_lines(self.root, self.base) if self.base else {}

    def patch(self):
        """The patch now (untracked files as additions) and the paths it touches."""
        if self.base is None:
            return "", []
        new = _untracked(self.root)
        parts = [git(self.root, "diff", self.base, "--no-color")]
        for f in new:
            p = self.root / f
            try:
                text = p.read_text() if p.stat().st_size < 1_000_000 else ""
            except (OSError, UnicodeDecodeError):
                continue
            parts.append(
                f"diff --git a/{f} b/{f}\n--- /dev/null\n+++ b/{f}\n"
                + "".join("+" + l for l in text.splitlines(True))
            )
        files = git_paths(
            self.root, "diff", self.base, "--name-only", "--diff-filter=d"
        )
        return "\n".join(parts), files + new

    def hunks(self, rel):
        """(first line, line count) of each hunk of path `rel`'s diff now. A count of 0 is a pure
        deletion after line `first`."""
        if not self.base:
            return []
        return [(s, n) for _, s, n in _hunks(self.root, self.base, rel)]

    def is_new(self, rel):
        """Whether repo-relative path `rel` is untracked."""
        return rel in self.lines and self.lines[rel] is None

    def source(self, rel):
        if rel not in self.sources:
            self.sources[rel] = (
                (self.root / rel).read_text(errors="replace").splitlines()
            )
        return self.sources[rel]

    def overlaps(self, rel, lines):
        """Whether any of `lines` (1-based) of repo-relative path `rel` is a non-blank changed line."""
        if rel not in self.lines or self.is_new(rel):
            return rel in self.lines
        src = self.source(rel)
        return any(
            n <= len(src) and n in self.lines[rel] and src[n - 1].strip() for n in lines
        )

    def touched(self, finding, cwd):
        """Whether the construct a `file:line:` finding starts (e.g. a whole function, for a complexity
        finding) contains a changed line. The finding's path is relative to cwd."""
        m = _LOCATION.match(finding)
        try:
            rel = m and (Path(cwd) / m[1]).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return False
        if not rel or rel not in self.lines:
            return False
        return self.overlaps(
            rel, _block(self.source(rel), int(m[2]), rel.endswith(".py"))
        )
