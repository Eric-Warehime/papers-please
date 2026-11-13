from papers_please import config, python


def ruff_args(fake_ctx, root, pol):
    """The arguments `check` gives ruff for a.py beyond its own."""
    ctx = fake_ctx(root)
    ctx.policy = lambda _tool: pol
    python.check(ctx, ["a.py"])
    cmd, _ = ctx.call("ruff", "check")
    return cmd[cmd.index("concise") + 1 : -1]


def test_ruff_args(tmp_path, fake_ctx):
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
    args = ruff_args(fake_ctx, tmp_path, pol)
    assert args == [
        "--extend-select",
        "ARG,RET,SIM108",
        "--extend-ignore",
        "ARG,ERA,RET505",
        "--config",
        "lint.pylint.max-args = 5",
    ]


def test_no_ruff_config(tmp_path, fake_ctx):
    pol = config.Policy(
        defaults={"RET"}, default_settings={"lint.mccabe.max-complexity": 10}
    )
    assert ruff_args(fake_ctx, tmp_path, pol) == [
        "--extend-select",
        "RET",
        "--config",
        "lint.mccabe.max-complexity = 10",
    ]
