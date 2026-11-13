import tomllib
from pathlib import Path

from papers_please import rust


def test_clippy(tmp_path, fake_ctx):
    (tmp_path / ".papers-please.toml").write_text(
        '[clippy]\nenable = ["unwrap_used"]\ndisable = ["redundant_else"]\nsettings = { too-many-lines-threshold = 60 }\n'
    )
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["c"]\n[workspace.lints.clippy]\nneedless-pass-by-value = "allow"\n'
    )
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "Cargo.toml").write_text(
        '[package]\nname = "c"\n[lints]\nworkspace = true\n'
    )
    (tmp_path / "clippy.toml").write_text(
        'cognitive-complexity-threshold = 30\ndisallowed-methods = [{ path = "std::process::exit", reason = "no" }]\n'
    )
    ctx = fake_ctx(tmp_path, {"locate-project": str(tmp_path / "Cargo.toml")})
    rust.check(ctx, ["c/src/lib.rs"])
    cmd, env = ctx.call("clippy", "--all-targets")
    flags = cmd[cmd.index("--") + 1 :]
    assert "clippy::needless_pass_by_value" not in flags
    assert flags[flags.index("clippy::unwrap_used") - 1] == "-W"
    assert flags[flags.index("clippy::redundant_else") - 1] == "-A"
    conf = tomllib.loads((Path(env["CLIPPY_CONF_DIR"]) / "clippy.toml").read_text())
    assert conf["cognitive-complexity-threshold"] == 30
    assert conf["too-many-lines-threshold"] == 60
    assert conf["excessive-nesting-threshold"] == 5
    assert conf["disallowed-methods"] == [
        {"path": "std::process::exit", "reason": "no"}
    ]
