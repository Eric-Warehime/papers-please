from papers_please import proc


def test_run_never_raises(tmp_path):
    assert proc.run(["no-such-program"], tmp_path).returncode == proc.NOT_FOUND
    assert (
        proc.run(["sh", "-c", 'echo "$HOME"'], tmp_path, env={"HOME": None}).stdout
        == "\n"
    )
    assert proc.git(tmp_path, "rev-parse", "HEAD") == ""
