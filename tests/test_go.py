from papers_please import config, go

POL = config.Policy(
    defaults={"gocognit", "dupl", "revive", "gofumpt"},
    default_settings={
        "gocognit.min-complexity": 15,
        "revive.rules": [{"name": "early-return"}],
    },
    extra={"exclusions": [{"path": "_test", "linters": ["dupl", "gocognit"]}]},
)


def test_no_repo_config():
    cfg = go.merged(POL, {})
    assert cfg["linters"]["enable"] == ["dupl", "gocognit", "revive"]
    assert cfg["formatters"]["enable"] == ["gofumpt"]
    assert cfg["linters"]["settings"]["gocognit"] == {"min-complexity": 15}
    assert cfg["linters"]["settings"]["revive"] == {"rules": [{"name": "early-return"}]}
    assert cfg["linters"]["exclusions"]["rules"] == [
        {"path": "_test", "linters": ["dupl", "gocognit"]}
    ]


def test_repo_wins():
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
    cfg = go.merged(POL, repo)
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
    assert repo["linters"]["enable"] == ["revive", "gocognit"]  # Not mutated.


def test_repo_runs_revive_without_rules():
    cfg = go.merged(POL, {"linters": {"enable": ["revive"]}})
    assert cfg["linters"]["settings"]["revive"]["enable-default-rules"] is True


def test_harness_wins():
    pol = config.Policy(
        **{
            **POL.__dict__,
            "enable": {"dupl"},
            "disable": {"gocognit"},
            "settings": {"dupl.threshold": 50},
        }
    )
    cfg = go.merged(
        pol,
        {"linters": {"disable": ["dupl"], "settings": {"dupl": {"threshold": 200}}}},
    )
    assert (
        "dupl" in cfg["linters"]["enable"]
        and "gocognit" not in cfg["linters"]["enable"]
    )
    assert cfg["linters"]["disable"] == ["gocognit"]
    assert cfg["linters"]["settings"]["dupl"] == {"threshold": 50}
