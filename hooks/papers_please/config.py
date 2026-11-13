"""Which rules and settings each tool runs with. Three layers, later ones winning: our defaults
(hooks/rules.toml), the repo's own linter config, then the harness config
($XDG_CONFIG_HOME/papers-please/config.toml, default ~/.config, overridden by the repo's
.papers-please.toml)."""

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_DEFAULTS = Path(__file__).resolve().parent.parent / "rules.toml"
_REPO = ".papers-please.toml"


def read(path):
    """The config file at path as a dict, parsed by its suffix (TOML, JSON, else YAML), or {} if there
    is none. Raises ValueError naming the file if it is malformed."""
    p = Path(path)
    if not p.is_file():
        return {}
    parse = {".toml": tomllib.loads, ".json": json.loads}.get(p.suffix, yaml.safe_load)
    try:
        return parse(p.read_text()) or {}
    except (ValueError, yaml.YAMLError) as e:
        raise ValueError(f"{p}: {e}") from e


def _flat(d, prefix=""):
    """Nested tables as dotted keys: {"a": {"b": 1}} -> {"a.b": 1}."""
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out |= _flat(v, f"{prefix}{k}.")
        else:
            out[f"{prefix}{k}"] = v
    return out


def _has(d, key):
    """Whether nested dict d has dotted `key`."""
    for k in key.split("."):
        if not isinstance(d, dict) or k not in d:
            return False
        d = d[k]
    return True


@dataclass
class Policy:
    """One tool's rules and settings. `defaults` and `default_settings` are ours and give way to the
    repo's config. `enable`, `disable` and `settings` come from the harness config and override both."""

    defaults: set = field(default_factory=set)
    enable: set = field(default_factory=set)
    disable: set = field(default_factory=set)
    default_settings: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)  # Tool-specific tables of rules.toml.

    @property
    def rules(self):
        """Every rule any layer names."""
        return self.defaults | self.enable | self.disable

    def on(self, rule, repo_off=False):
        """Whether `rule` runs, given whether the repo's config turns it off."""
        return rule not in self.disable and (
            rule in self.enable or (rule in self.defaults and not repo_off)
        )

    def overrides(self, *repo):
        """Flat settings to apply on top of the repo's settings (nested tables, or flat ones whose
        keys have no dots): ours for keys none of `repo` sets, then the harness's."""
        ours = self.default_settings.items()
        return {
            k: v for k, v in ours if not any(_has(r, k) for r in repo)
        } | self.settings

    def resolve(self, repo):
        """The settings in effect over the repo's own flat settings."""
        return repo | self.overrides(repo)


def load(root):
    """Policy per tool name. The repo's harness config wins over the user's, rule by rule."""
    home = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
    ours, user = (
        read(_DEFAULTS),
        read(home / "papers-please" / "config.toml"),
    )
    repo = read(Path(root) / _REPO)
    out = {}
    for tool in ours.keys() | user.keys() | repo.keys():
        o, u, r = (c.get(tool, {}) for c in (ours, user, repo))
        u_on, u_off, r_on, r_off = (
            set(c.get(k, [])) for c in (u, r) for k in ("enable", "disable")
        )
        out[tool] = Policy(
            defaults=set(o.get("enable", [])),
            enable=(u_on - r_off) | r_on,
            disable=(u_off - r_on) | r_off,
            default_settings=_flat(o.get("settings", {})),
            settings=_flat(u.get("settings", {})) | _flat(r.get("settings", {})),
            extra=o,
        )
    return out
