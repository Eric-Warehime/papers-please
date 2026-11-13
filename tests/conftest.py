import shlex
import subprocess

import pytest
from papers_please.checks import Ctx


class FakeCtx(Ctx):
    """A Ctx whose tools all exist and whose commands are recorded instead of run. A command
    containing a key of `replies` prints that key's value."""

    def __init__(self, root, replies=()):
        super().__init__(root)
        self.calls, self.replies = [], dict(replies)

    def tool(self, tool):
        return [tool.name]

    def run(self, cmd, cwd=None, env=None, **_):
        self.calls.append((cmd, cwd, env or {}))
        line = shlex.join(map(str, cmd))
        out = next((v for k, v in self.replies.items() if k in line), "")
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def call(self, *words):
        """The first recorded command containing all of `words`, and its environment."""
        return next((c, e) for c, _, e in self.calls if set(words) <= set(map(str, c)))


@pytest.fixture
def fake_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "none"))
    return FakeCtx
