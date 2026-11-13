import pytest
from papers_please import config


def test_layers(tmp_path, monkeypatch):
    user = tmp_path / "papers-please" / "config.toml"
    user.parent.mkdir()
    user.write_text(
        '[ruff]\ndisable = ["ERA", "SIM"]\nenable = ["D"]\nsettings = { a = 1, b = 1 }\n'
    )
    (tmp_path / ".papers-please.toml").write_text(
        '[ruff]\nenable = ["SIM"]\nsettings = { b = 2 }\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    pol = config.load(tmp_path)["ruff"]
    assert "C90" in pol.defaults
    assert pol.enable == {"D", "SIM"}
    assert pol.disable == {"ERA"}
    assert pol.settings == {"a": 1, "b": 2}


def test_policy():
    pol = config.Policy(
        defaults={"x", "y"},
        enable={"z"},
        disable={"y"},
        default_settings={"k": 1, "j": 1},
        settings={"j": 3},
    )
    assert pol.on("x") and not pol.on("x", repo_off=True)
    assert not pol.on("y") and pol.on("z", repo_off=True)
    assert pol.resolve({"k": 2}) == {"k": 2, "j": 3}
    assert pol.resolve({}) == {"k": 1, "j": 3}


def test_read_names_bad_file(tmp_path):
    (tmp_path / "bad.toml").write_text("x = [")
    with pytest.raises(ValueError, match="bad.toml"):
        config.read(tmp_path / "bad.toml")
    assert config.read(tmp_path / "missing.yml") == {}
