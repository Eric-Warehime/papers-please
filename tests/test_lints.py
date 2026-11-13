import subprocess

import pytest
from papers_please import lints, model
from papers_please.checks import Ctx


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("def f():\n    print(1)\n")
    (tmp_path / "b.go").write_text("package b\n")
    rules = tmp_path / "lints"
    rules.mkdir()
    (rules / "no-print.yml").write_text(
        "id: no-print\nlanguage: Python\nrule: {pattern: print($$$)}\nmessage: Use logging.\n"
    )
    (rules / "shallow.yml").write_text(
        'id: shallow\nlanguage: [Python, Go]\nsubject: file\nask: It is shallow.\ncriteria: {"true": leaks, "false": hides}\nmessage: Deepen it.\n'
    )
    script = rules / "todo"
    script.write_text('#!/bin/sh\ngrep -Hn TODO "$@" && exit 1; exit 0\n')
    script.chmod(0o755)
    monkeypatch.setattr(lints, "_LINTS", rules)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "none"))
    return tmp_path


def test_check(repo):
    (repo / "a.py").write_text("def f():\n    print(1)  # TODO\n")
    out = lints.check(Ctx(repo), ["a.py", "b.go"])
    assert "a.py:2: no-print: Use logging." in out
    assert "a.py:2:" in out.split("todo")[-1]


def test_disabled(repo):
    (repo / ".papers-please.toml").write_text(
        '[lints]\ndisable = ["no-print", "todo"]\n'
    )
    assert lints.check(Ctx(repo), ["a.py"]) == ""


def test_judge(repo, monkeypatch):
    prompts = []

    def ask(_root, prompt, valid, _notices):
        prompts.append(prompt)
        answer = "S1: TRUE - helpers leak.\nS2: FALSE - fine."
        return ("stub", answer) if valid(answer) else (None, "")

    monkeypatch.setattr(model, "ask", ask)
    out = lints.judge(Ctx(repo), ["a.py", "b.go"], [])
    assert (
        "## S1: rule shallow, a.py lines 1-2" in prompts[0]
        and "True when: leaks" in prompts[0]
    )
    assert "Deepen it." not in prompts[0]
    assert "a.py:1: shallow: Deepen it.\n  (stub: helpers leak.)" in out
    assert "b.go" not in out


def test_unknown_key(repo):
    (repo / "lints" / "shallow.yml").write_text("id: shallow\nask: x\nthreshold: 0.5\n")
    with pytest.raises(ValueError, match="threshold"):
        lints.check(Ctx(repo), ["a.py"])
