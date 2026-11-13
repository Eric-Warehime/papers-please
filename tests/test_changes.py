import subprocess

from papers_please.changes import Changes


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=False,
        capture_output=True,
    )


def _merge(root, *args):
    """Commits a.txt and b.txt on main, changes both on a branch and a.txt on main, then merges."""
    _git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("base\n")
    (root / "b.txt").write_text("base\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "other")
    (root / "a.txt").write_text("other\n")
    (root / "b.txt").write_text("other\n")
    _git(root, "commit", "-qam", "other")
    _git(root, "checkout", "-q", "main")
    (root / "a.txt").write_text("main\n")
    _git(root, "commit", "-qam", "main")
    _git(root, "merge", *args, "other")


def test_conflicted_merge_patch_is_the_resolution(tmp_path):
    _merge(tmp_path)
    (tmp_path / "a.txt").write_text("resolved\n")
    diff, files = Changes(tmp_path).patch()
    assert files == ["a.txt"]
    assert "+resolved" in diff


def test_pending_merge_without_conflicts_has_no_base(tmp_path):
    _merge(tmp_path, "-s", "ours", "--no-commit")
    assert Changes(tmp_path).base is None
    c = Changes(tmp_path)
    assert c.patch() == ("", [])
    assert c.hunks("a.txt") == []
    assert not c.touched("a.txt:1: finding", tmp_path)
