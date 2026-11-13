"""Rust: rustfmt, clippy, lizard for cyclomatic complexity (which clippy lacks), and each changed
crate's tests."""

import json
from pathlib import Path

from . import config
from .checks import joined

NAME, SUFFIX = "Rust", ".rs"


def _manifest(d):
    return config.read(d / "Cargo.toml")


def _workspace(ctx, cargo, crate):
    p = ctx.run(
        [
            *cargo,
            "locate-project",
            "--workspace",
            "--message-format",
            "plain",
            "--manifest-path",
            str(crate / "Cargo.toml"),
        ]
    )
    return Path(p.stdout.strip()).parent if p.returncode == 0 else crate


def _get(d, keys):
    for k in keys:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d


def _inherited(crate, ws, *keys):
    """The value at `keys` in `crate`'s manifest, or in its workspace's [workspace] table when the crate
    inherits it (`workspace = true`)."""
    v = _get(_manifest(crate), keys)
    return (
        _get(_manifest(ws), ("workspace", *keys))
        if _get(v, ["workspace"]) is True
        else v
    )


def _toml_value(v):
    """v as a TOML value."""
    if isinstance(v, dict):
        return (
            "{ "
            + ", ".join(f"{json.dumps(k)} = {_toml_value(x)}" for k, x in v.items())
            + " }"
        )
    if isinstance(v, list):
        return "[" + ", ".join(map(_toml_value, v)) + "]"
    return str(v).lower() if isinstance(v, bool) else json.dumps(v)


def _clippy_conf(ctx, crate):
    """A directory holding the crate's clippy.toml (the nearest up to the repo root) with our thresholds
    where it sets none, and the harness's on top."""
    found = ctx.find(crate / "Cargo.toml", "clippy.toml", ".clippy.toml")
    conf = ctx.policy("clippy").resolve(config.read(found) if found else {})
    text = "".join(f"{k} = {_toml_value(v)}\n" for k, v in conf.items())
    return ctx.generated("clippy.toml", crate.resolve(), text).parent


def _clippy_flags(ctx, crate, ws):
    """`-W` for our lints the crate's [lints.clippy] (or its workspace's, when inherited) doesn't set,
    and the harness's enabled and disabled lints, which win over the crate's."""
    theirs = {
        k.replace("-", "_") for k in _get(_inherited(crate, ws, "lints"), ["clippy"])
    }
    pol = ctx.policy("clippy")
    on = sorted(l for l in pol.rules if pol.on(l, l in theirs))
    return [f for l in on for f in ("-W", f"clippy::{l}")] + [
        f for l in sorted(pol.disable) for f in ("-A", f"clippy::{l}")
    ]


def clippy(ctx, crate, ws):
    """The lint flags and environment for running clippy on `crate` in workspace `ws`."""
    return _clippy_flags(ctx, crate, ws), {
        "CLIPPY_CONF_DIR": str(_clippy_conf(ctx, crate))
    }


def fix(ctx, f):
    """Formats f alone, through stdin so rustfmt doesn't also reformat the modules f declares."""
    crate = ctx.project(f, "Cargo.toml")
    rustfmt, cargo = ctx.tool("rustfmt"), ctx.tool("cargo")
    if not (crate and rustfmt and cargo):
        return ""
    edition = _inherited(crate, _workspace(ctx, cargo, crate), "package", "edition")
    src = (ctx.root / f).read_text()
    p = ctx.run(
        [*rustfmt, "--edition", edition or "2021", "--emit", "stdout"],
        cwd=crate,
        stdin=src,
    )
    if p.returncode == 0 and p.stdout != src:
        (ctx.root / f).write_text(p.stdout)
    return ""


def _lizard(ctx, files):
    pol = ctx.policy("lizard")
    lizard = pol.on("CCN") and ctx.tool("lizard", pypi="lizard")
    if not lizard:
        return ""
    return ctx.lint([*lizard, "-w", "-C", str(pol.resolve({})["CCN"]), *files])


def check(ctx, files):
    crates = sorted({c for f in files if (c := ctx.project(f, "Cargo.toml"))})
    if not crates or not (cargo := ctx.tool("cargo")):
        return ""
    out = []
    for crate in crates:
        ws = _workspace(ctx, cargo, crate)
        flags, env = clippy(ctx, crate, ws)
        manifest = ["--manifest-path", str(crate / "Cargo.toml")]
        clippy_cmd = [*cargo, "clippy", "-q", "--message-format", "short", *manifest]
        # Diagnostic paths are relative to the workspace root.
        out.append(
            ctx.lint([*clippy_cmd, "--all-targets", "--", *flags], cwd=ws, env=env)
        )
        out.append(ctx.quiet([*cargo, "test", "-q", *manifest]))
    return joined([*out, _lizard(ctx, files)])
