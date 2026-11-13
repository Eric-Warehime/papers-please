"""The supported languages. Each module has NAME (as ast-grep spells it), SUFFIX, and
`fix(ctx, file)` and `check(ctx, files)`, which return findings for Claude."""

from . import go, python, rust

_ALL = (go, python, rust)


def of(path):
    """The language module for `path`, or None."""
    return next((m for m in _ALL if str(path).endswith(m.SUFFIX)), None)


def suffixes(names):
    """The file suffixes of the languages named (in any case)."""
    names = {n.lower() for n in names}
    return {m.SUFFIX for m in _ALL if m.NAME.lower() in names}
