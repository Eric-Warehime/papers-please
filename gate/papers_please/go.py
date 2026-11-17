"""Go: golangci-lint (linters and formatters) and the tests of each changed package, module by module.
golangci-lint runs with the module's own config plus our linters and settings where it says nothing
about them, written to a temporary file."""

import yaml

from . import config
from .checks import joined
from .tools import Tool

NAME, SUFFIX = "Go", ".go"
_GO = Tool(
    "go",
    probe=("version",),
    install={"brew": "go", "apt-get": "golang-go", "dnf": "golang"},
    manual="https://go.dev/dl/",
)
_GOLANGCI_LINT = Tool(
    "golangci-lint",
    probe=("version",),
    version=r"version v?2\.",  # The configs we generate are in v2's format.
    install={"brew": "golangci-lint"},
    manual="https://golangci-lint.run/welcome/install/",
)
TOOLS = (_GO, _GOLANGCI_LINT)
_FORMATTERS = {"gci", "gofmt", "gofumpt", "goimports", "golines", "swaggo"}
_RUN_FLAGS = [
    "--path-mode=abs",
    "--uniq-by-line=false",
    "--max-issues-per-linter=0",
    "--max-same-issues=0",
]


def _put(d, key, value):
    *parents, leaf = key.split(".")
    for k in parents:
        d = d.setdefault(k, {})
    d[leaf] = value


def _repo_config(ctx, lint, mod):
    """The golangci config that applies in module directory `mod` as a dict, and its directory."""
    p = ctx.run([*lint, "config", "path"], cwd=mod)
    out = (p.stdout + p.stderr).strip()  # It prints the path to stderr.
    if p.returncode or not out:
        return {}, None
    path = (mod / out.splitlines()[-1]).resolve()
    return config.read(path), path.parent


def _enable(pol, section, names):
    """Turns on `names` in a linters or formatters section unless the section disables them; the
    harness config wins over both. Returns the names this added."""
    off, theirs = set(section.get("disable", [])), set(section.get("enable", []))
    on = {n for n in names if pol.on(n, n in off)}
    section["enable"] = sorted((theirs - pol.disable) | on)
    if off or pol.disable & names:
        section["disable"] = sorted((off - on) | (pol.disable & names))
    return on - theirs


def _configure(pol, section, formatters):
    """Adds our settings for the section's linters or formatters where it has none, then the
    harness's. revive's rule list is merged separately."""
    settings = section.setdefault("settings", {})
    for k, v in pol.overrides(settings).items():
        if k != "revive.rules" and (k.split(".")[0] in _FORMATTERS) == formatters:
            _put(settings, k, v)


def _revive(pol, settings, repo_runs_it):
    """Adds our revive rules to the repo's, by name. If the repo runs revive without listing rules,
    revive's default rules are kept alongside ours. Rules in the harness config replace both."""
    rules = pol.settings.get("revive.rules")
    if rules is None:
        theirs = settings.get("revive", {}).get("rules")
        if theirs is None and repo_runs_it:
            _put(settings, "revive.enable-default-rules", True)
        names = {r["name"] for r in theirs or []}
        rules = (theirs or []) + [
            r
            for r in pol.default_settings.get("revive.rules", [])
            if r["name"] not in names
        ]
    if rules:
        _put(settings, "revive.rules", rules)


def _merged(pol, repo):
    """The repo's golangci config (changed in place) combined with our defaults and the harness
    config."""
    cfg = repo or {"version": "2"}
    linters, formatters = (
        cfg.setdefault("linters", {}),
        cfg.setdefault("formatters", {}),
    )
    runs_revive = (
        "revive" in linters.get("enable", []) or linters.get("default") == "all"
    )
    added = _enable(pol, linters, pol.rules - _FORMATTERS)
    _enable(pol, formatters, pol.rules & _FORMATTERS)
    _configure(pol, linters, formatters=False)
    _configure(pol, formatters, formatters=True)
    _revive(pol, linters["settings"], runs_revive)
    # Our exclusions only cover linters we added, so the repo's own findings aren't hidden.
    for rule in pol.extra.get("exclusions", []):
        if names := [n for n in rule["linters"] if n in added]:
            linters.setdefault("exclusions", {}).setdefault("rules", []).append(
                {**rule, "linters": names}
            )
    return cfg


def _golangci(ctx, lint, mod, *args):
    """golangci-lint `args` in module directory `mod`, with the combined config."""
    mod = mod.resolve()
    repo, cfg_dir = _repo_config(ctx, lint, mod)
    cfg = _merged(ctx.policy("golangci"), repo)
    # Paths in the repo's config are relative to its directory, and the combined file lives elsewhere.
    mode = {mod: "gomod", ctx.root.resolve(): "gitroot"}.get(cfg_dir)
    if mode and "relative-path-mode" not in cfg.get("run", {}):
        _put(cfg, "run.relative-path-mode", mode)
    # YAML, not JSON: golangci-lint reads JSON numbers as floats, which revive's rule arguments reject.
    path = ctx.generated(".golangci.yml", mod, yaml.safe_dump(cfg))
    return [*lint, args[0], "-c", str(path), *args[1:]]


def fix(ctx, f):
    mod = ctx.project(f, "go.mod")
    if mod and (lint := ctx.tool(_GOLANGCI_LINT)):
        rel = str((ctx.root / f).relative_to(mod))
        ctx.run(_golangci(ctx, lint, mod, "fmt", rel), cwd=mod)
    return ""


def check(ctx, files):
    mods = {}
    for f in files:
        if mod := ctx.project(f, "go.mod"):
            mods.setdefault(mod, set()).add(
                "./" + (ctx.root / f).parent.relative_to(mod).as_posix()
            )
    if not mods or not (go := ctx.tool(_GO)):
        return ""
    lint, out = ctx.tool(_GOLANGCI_LINT), []
    for mod, pkgs in sorted(mods.items()):
        pkgs = sorted(pkgs)
        if lint:
            out.append(
                ctx.lint(_golangci(ctx, lint, mod, "run", *_RUN_FLAGS, *pkgs), cwd=mod)
            )
        out.append(ctx.quiet([*go, "test", *pkgs], cwd=mod))
    return joined(out)
