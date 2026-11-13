from papers_please import config, python
from papers_please.checks import Ctx


def ctx(root, pol):
    c = Ctx(root)
    c.policy = lambda _tool: pol
    return c


def test_ruff_args(tmp_path):
    (tmp_path / "base.toml").write_text(
        '[lint]\nignore = ["SIM108"]\nmccabe.max-complexity = 20\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff]\nextend = "base.toml"\n[tool.ruff.lint]\nignore = ["RET505", "ARG"]\n'
    )
    pol = config.Policy(
        defaults={"RET", "ARG", "ERA"},
        enable={"SIM108"},
        disable={"ERA"},
        default_settings={"lint.mccabe.max-complexity": 10, "lint.pylint.max-args": 5},
    )
    args = python.ruff_args(ctx(tmp_path, pol), ["a.py"])
    assert args == [
        "--extend-select",
        "ARG,RET,SIM108",
        "--extend-ignore",
        "ARG,ERA,RET505",
        "--config",
        "lint.pylint.max-args = 5",
    ]


def test_no_ruff_config(tmp_path):
    pol = config.Policy(
        defaults={"RET"}, default_settings={"lint.mccabe.max-complexity": 10}
    )
    assert python.ruff_args(ctx(tmp_path, pol), ["a.py"]) == [
        "--extend-select",
        "RET",
        "--config",
        "lint.mccabe.max-complexity = 10",
    ]
