import shutil
import subprocess

from papers_please import doctor, model
from papers_please.checks import Ctx
from papers_please.tools import Tool


def _bin(tmp_path, monkeypatch, **scripts):
    """A PATH holding git and shell scripts named after the keyword arguments, printing their values."""
    d = tmp_path / "bin"
    d.mkdir()
    (d / "git").symlink_to(shutil.which("git"))
    for name, out in scripts.items():
        (d / name.replace("_", "-")).write_text(f"#!/bin/sh\necho '{out}'\n")
        (d / name.replace("_", "-")).chmod(0o755)
    monkeypatch.setenv("PATH", str(d))


def test_tool(tmp_path, monkeypatch):
    _bin(tmp_path, monkeypatch, lint="lint has version 1.2", brew="", apt_get="")
    t = Tool("lint", version=r"version 2\.", install={"apt-get": "x", "brew": "y"})
    assert t.problem() == "wrong version: lint has version 1.2"
    assert t.installer() == ["brew", "install", "y"]  # Preferred over apt-get.
    assert Tool("gone", install={"apt-get": "g"}).installer()[0] == "sudo"
    assert Tool("gone").problem() == "not installed"
    assert Tool("gone", pypi="gone").command() is None  # No uvx on PATH.


def test_missing_by_language(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "main.go").write_text("package main\n")  # Untracked still counts.
    monkeypatch.setattr(model, "reviewer", lambda: "claude")
    _bin(tmp_path, monkeypatch, go="go version go1.23")
    assert doctor.missing(tmp_path) == ["golangci-lint", "ast-grep"]


def test_missing_hint(tmp_path, monkeypatch):
    _bin(tmp_path, monkeypatch)
    ctx = Ctx(tmp_path)
    ctx.tool(Tool("pytest", manual="install it with the project's dev dependencies"))
    ctx.tool(Tool("lint", install={"brew": "lint"}))
    assert ctx.missing[0].endswith(
        "skipped; install it with the project's dev dependencies."
    )
    assert ctx.missing[1].endswith("skipped; run /papers-please:doctor to install it.")
