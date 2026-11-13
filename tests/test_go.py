from pathlib import Path

import yaml
from papers_please import config, go

POL = config.Policy(
    defaults={"gocognit", "dupl", "revive", "gofumpt"},
    default_settings={
        "gocognit.min-complexity": 15,
        "revive.rules": [{"name": "early-return"}],
    },
    extra={"exclusions": [{"path": "_test", "linters": ["dupl", "gocognit"]}]},
)


def merged(tmp_path, fake_ctx, pol, repo):
    """The golangci config `check` runs with, in a module whose own config is `repo`."""
    (tmp_path / "go.mod").write_text("module m\n")
    (tmp_path / ".golangci.yml").write_text(yaml.safe_dump(repo))
    ctx = fake_ctx(tmp_path, {"config path": ".golangci.yml"} if repo else {})
    ctx.policy = lambda _tool: pol
    go.check(ctx, ["x.go"])
    cmd, _ = ctx.call("run", "-c")
    return yaml.safe_load(Path(cmd[cmd.index("-c") + 1]).read_text())


def test_no_repo_config(tmp_path, fake_ctx):
    cfg = merged(tmp_path, fake_ctx, POL, {})
    assert cfg["linters"]["enable"] == ["dupl", "gocognit", "revive"]
    assert cfg["formatters"]["enable"] == ["gofumpt"]
    assert cfg["linters"]["settings"]["gocognit"] == {"min-complexity": 15}
    assert cfg["linters"]["settings"]["revive"] == {"rules": [{"name": "early-return"}]}
    assert cfg["linters"]["exclusions"]["rules"] == [
        {"path": "_test", "linters": ["dupl", "gocognit"]}
    ]


def test_repo_wins(tmp_path, fake_ctx):
    repo = {
        "version": "2",
        "linters": {
            "enable": ["revive", "gocognit"],
            "disable": ["dupl"],
            "settings": {
                "gocognit": {"min-complexity": 30},
                "revive": {"rules": [{"name": "var-naming"}]},
            },
        },
    }
    cfg = merged(tmp_path, fake_ctx, POL, repo)
    linters = cfg["linters"]
    assert linters["enable"] == ["gocognit", "revive"] and linters["disable"] == [
        "dupl"
    ]
    assert linters["settings"]["gocognit"] == {"min-complexity": 30}
    assert [r["name"] for r in linters["settings"]["revive"]["rules"]] == [
        "var-naming",
        "early-return",
    ]
    assert "exclusions" not in linters  # Only linters we added get our exclusions.


def test_repo_runs_revive_without_rules(tmp_path, fake_ctx):
    cfg = merged(tmp_path, fake_ctx, POL, {"linters": {"enable": ["revive"]}})
    assert cfg["linters"]["settings"]["revive"]["enable-default-rules"] is True


def test_harness_wins(tmp_path, fake_ctx):
    pol = config.Policy(
        **{
            **POL.__dict__,
            "enable": {"dupl"},
            "disable": {"gocognit"},
            "settings": {"dupl.threshold": 50},
        }
    )
    cfg = merged(
        tmp_path,
        fake_ctx,
        pol,
        {"linters": {"disable": ["dupl"], "settings": {"dupl": {"threshold": 200}}}},
    )
    assert (
        "dupl" in cfg["linters"]["enable"]
        and "gocognit" not in cfg["linters"]["enable"]
    )
    assert cfg["linters"]["disable"] == ["gocognit"]
    assert cfg["linters"]["settings"]["dupl"] == {"threshold": 50}
