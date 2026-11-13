"""Python: ruff (format and lint), flake8-cognitive-complexity, and the pytest files named after the
changed modules."""

import configparser
import json
from pathlib import Path

from . import config
from .checks import joined
from .proc import git_paths
from .tools import Tool

NAME, SUFFIX = "Python", ".py"
_RUFF = Tool("ruff", pypi="ruff")
_FLAKE8 = Tool("flake8", pypi="flake8", plugins=("flake8-cognitive-complexity",))
_PYTEST = Tool("pytest", manual="install it with the project's dev dependencies")
TOOLS = (_RUFF, _FLAKE8)  # pytest is the project's, not ours to install.
_RUFF_CONFIGS = (".ruff.toml", "ruff.toml", "pyproject.toml")
# Fire on half-finished edits, so not reported after each edit, and unused imports are never removed.
_EDIT_IGNORE = "F401,F841,ARG"


def _ruff_section(path):
    """The ruff settings in config file `path`, or None if it has none."""
    if not path.is_file():
        return None
    t = config.read(path)
    return t.get("tool", {}).get("ruff") if path.name == "pyproject.toml" else t


def _ruff_repo(ctx, files):
    """The rules ignored and the ruff settings tables of the config that applies to each file, including
    configs it extends. A union across files: a repo with several configs has every config's ignores
    applied to every file."""
    ignores, sections = set(), []
    for f in files:
        cfg = ctx.find(f, *_RUFF_CONFIGS, where=lambda p: _ruff_section(p) is not None)
        while cfg:
            s = _ruff_section(cfg)
            for t in (s, s.get("lint", {})):
                ignores.update(t.get("ignore", []) + t.get("extend-ignore", []))
            sections.append(s)
            cfg = (
                s.get("extend")
                and (cfg.parent / Path(s["extend"]).expanduser()).resolve()
            )
    return ignores, sections


def _ruff_args(ctx, files):
    """Our rules added to the repo's selection and our settings where it has none. The repo's ignores
    are passed back after ours: ruff lets an ignore win a tie with a select at the same specificity."""
    pol = ctx.policy("ruff")
    ignores, sections = _ruff_repo(ctx, files)
    select = (pol.defaults - pol.disable) | pol.enable
    ignore = (ignores - pol.enable) | pol.disable
    args = ["--extend-select", ",".join(sorted(select))] if select else []
    args += ["--extend-ignore", ",".join(sorted(ignore))] if ignore else []
    # Lint settings may also sit at the top level, ruff's older layout.
    for k, v in pol.overrides(*sections, *({"lint": s} for s in sections)).items():
        args += ["--config", f"{k} = {json.dumps(v)}"]
    return args


def _ruff_check(ctx, ruff, files, *args):
    return ctx.lint(
        [
            *ruff,
            "check",
            "--output-format",
            "concise",
            *_ruff_args(ctx, files),
            *args,
            *files,
        ]
    )


def fix(ctx, f):
    """Formats f. A tracked file keeps its existing style: only changed lines are formatted, bottom-up
    so earlier ranges keep their line numbers, and lint autofixes are skipped since they rewrite any
    line. Returns the lint findings left."""
    if not (ruff := ctx.tool(_RUFF)):
        return ""
    if ctx.changes.is_new(f):
        ctx.run([*ruff, "format", "-q", f])
        ctx.run(
            [
                *ruff,
                "check",
                "-q",
                "--fix",
                "--unfixable",
                "F401",
                *_ruff_args(ctx, [f]),
                f,
            ]
        )
    else:
        for s, n in sorted(ctx.changes.hunks(f), reverse=True):
            if n:
                ctx.run([*ruff, "format", "-q", "--range", f"{s}-{s + n}", f])
    return _ruff_check(ctx, ruff, [f], "--extend-ignore", _EDIT_IGNORE)


def _flake8_repo(root):
    """The repo's [flake8] settings, keys spelled with dashes."""
    p = configparser.ConfigParser(interpolation=None, strict=False)
    p.read([root / n for n in ("setup.cfg", "tox.ini", ".flake8")])
    return (
        {k.replace("_", "-"): v for k, v in p["flake8"].items()}
        if p.has_section("flake8")
        else {}
    )


def _cognitive(ctx, files):
    pol = ctx.policy("flake8")
    flake8 = pol.on("CCR001") and ctx.tool(_FLAKE8)
    if not flake8:
        return ""
    cc = pol.resolve(_flake8_repo(ctx.root))["max-cognitive-complexity"]
    return ctx.lint(
        [
            *flake8,
            "--isolated",
            "--select",
            "CCR001",
            "--max-cognitive-complexity",
            str(cc),
            *files,
        ]
    )


def _tests(ctx, files):
    """pytest's output if the test files of the changed modules fail."""
    found = set()
    for f in files:
        b = Path(f).stem
        if b.startswith("test_") or b.endswith("_test"):
            found.add(f)
        else:
            found.update(
                git_paths(ctx.root, "ls-files", f"*test_{b}.py", f"*{b}_test.py")
            )
    # The project's environment when uv manages it: a global pytest can't import the project.
    pytest = (
        ["uv", "run", "--frozen", "pytest"]
        if (ctx.root / "uv.lock").is_file()
        else ctx.tool(_PYTEST)
    )
    if not found or not pytest:
        return ""
    return ctx.quiet(
        [*pytest, "-q", *sorted(found)], ok=(0, 5)
    )  # 5: no tests collected


def check(ctx, files):
    ruff = ctx.tool(_RUFF)
    return joined(
        [
            ruff and _ruff_check(ctx, ruff, files),
            _cognitive(ctx, files),
            _tests(ctx, files),
        ]
    )
