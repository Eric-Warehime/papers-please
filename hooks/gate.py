#!/usr/bin/env python3
"""Quality gate for agent patches.

`gate.py prompt` (UserPromptSubmit) records the request and the tree state at turn start.
`gate.py stop` (Stop) checks the uncommitted patch in three stages: lint suppressions and test
removals, per-language checks (hooks/checks/<ext>.sh), then a review by a different model.
A failing stage exits 2 so Claude keeps working. Suppression and review findings can be rebutted
by stopping again without changing the tree; check failures cannot. After MAX_ROUNDS blocks in
one turn the gate lets the turn end and reports what is unresolved to the user.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MAX_ROUNDS = 3
CHECK_TIMEOUT = 600
REVIEW_TIMEOUT = 600
MAX_OUTPUT = 8000  # Claude Code moves hook output over 10k chars to a file.
MAX_REVIEW_DIFF = 150_000

SUPPRESSION = re.compile(r"//\s*nolint|#\s*noqa|#\s*type:\s*ignore|#!?\[allow\(|\bt\.Skip(Now)?\(|pytest\.mark\.skip|unittest\.skip|#\[ignore\]")
TEST_DEF = re.compile(r"^\s*func Test\w*\(|^\s*def test_\w*\(|^\s*#\[test\]")

REVIEWERS = [
    ("gemini", ["gemini", "-p", "Follow the review instructions given on stdin."]),
    ("codex", ["codex", "exec", "--sandbox", "read-only", "-"]),
    ("claude", ["claude", "-p", "--model", os.environ.get("CODER_REVIEW_MODEL", "sonnet"),
                "--tools", "Read,Grep,Glob", "--settings", '{"disableAllHooks": true}']),
]


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True).stdout


def snapshot(root, base):
    """Returns the patch against base (untracked files as additions) and the paths it touches."""
    untracked = [f for f in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if f]
    parts = [git(root, "diff", base, "--no-color")]
    for f in untracked:
        try:
            text = (root / f).read_text() if (root / f).stat().st_size < 1_000_000 else ""
        except (OSError, UnicodeDecodeError):
            continue
        parts.append(f"diff --git a/{f} b/{f}\n--- /dev/null\n+++ b/{f}\n" + "".join("+" + l for l in text.splitlines(True)))
    files = [f for f in git(root, "diff", base, "--name-only", "--diff-filter=d", "-z").split("\0") if f]
    return "\n".join(parts), files + untracked


def suppressions(diff):
    lines = [l for l in diff.splitlines() if not l.startswith(("+++", "---"))]
    added = [l[1:].strip() for l in lines if l.startswith("+")]
    removed = [l[1:].strip() for l in lines if l.startswith("-")]
    hits = [f"added: {l}" for l in added if SUPPRESSION.search(l) and l not in removed]
    lost = sum(map(bool, map(TEST_DEF.search, removed))) - sum(map(bool, map(TEST_DEF.search, added)))
    if lost > 0:
        hits.append(f"{lost} test function(s) removed")
    if not hits:
        return ""
    return ("The patch silences checks or removes tests:\n" + "\n".join(hits) +
            "\nRemove these, or if one is genuinely necessary, leave the code unchanged and justify it in your reply.")


def run_checks(root, files, base):
    by_ext = {}
    for f in files:
        by_ext.setdefault(Path(f).suffix.lstrip("."), []).append(f)
    out = []
    for ext, paths in sorted(by_ext.items()):
        script = HERE / "checks" / f"{ext}.sh"
        if not ext or not script.exists():
            continue
        try:
            p = subprocess.run(["sh", str(script), *paths], cwd=root, capture_output=True, text=True,
                               env={**os.environ, "CODER_BASE": base}, timeout=CHECK_TIMEOUT)
            if p.returncode:
                out.append(f"$ checks/{ext}.sh\n{p.stdout}{p.stderr}")
        except subprocess.TimeoutExpired:
            out.append(f"$ checks/{ext}.sh timed out after {CHECK_TIMEOUT}s")
    if not out:
        return ""
    return "Quality checks failed. Fix these before finishing:\n" + "\n".join(out)


def review(root, diff, prompts, notices):
    if len(diff) > MAX_REVIEW_DIFF:
        diff = diff[:MAX_REVIEW_DIFF] + "\n[diff truncated; read the files for the rest]"
    request = "\n---\n".join(prompts) or "(not recorded)"
    prompt = f"{(HERE / 'review-prompt.md').read_text()}\n\n## User requests, most recent last\n{request}\n\n## Patch\n{diff}"
    # Claude Code refuses to start inside another Claude Code session unless this is unset.
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    for name, cmd in REVIEWERS:
        if not shutil.which(cmd[0]):
            continue
        try:
            p = subprocess.run(cmd, input=prompt, cwd=root, capture_output=True, text=True, env=env, timeout=REVIEW_TIMEOUT)
        except subprocess.TimeoutExpired:
            notices.append(f"{name} review timed out")
            continue
        verdicts = re.findall(r"^\W*VERDICT:\s*(PASS|BLOCK)", p.stdout, re.M)
        if p.returncode or not verdicts:
            notices.append(f"{name} review failed (exit {p.returncode}); trying the next reviewer")
            continue
        if verdicts[-1] == "PASS":
            return ""
        return (f"Code review ({name}) requested changes. Address them, or if you disagree, leave the code "
                f"unchanged and explain why in your reply:\n{p.stdout.strip()}")
    notices.append("No reviewer succeeded; review skipped.")
    return ""


def on_prompt(event, root, base, st):
    diff, _ = snapshot(root, base)
    st.update(turn_start=digest(diff), rounds=0, prompts=(st.get("prompts", []) + [event.get("prompt", "")])[-3:])
    return 0, ""


def on_stop(event, root, base, st):
    diff, files = snapshot(root, base)
    h = digest(diff)
    if not diff.strip() or h in (st.get("turn_start"), st.get("passed")):
        return 0, ""
    waived = st.get("waived", {}).get(h, [])
    if h == st.get("blocked") and st.get("blocked_stage") != "checks":
        waived = waived + [st["blocked_stage"]]
    st["waived"] = {h: waived}
    notices = []
    stages = (("suppressions", lambda: suppressions(diff)),
              ("checks", lambda: run_checks(root, files, base)),
              ("review", lambda: review(root, diff, st.get("prompts", []), notices)))
    for stage, run in stages:
        failure = "" if stage in waived else run()
        if failure:
            break
    else:
        st["passed"] = h
        if waived:
            notices.append(f"Claude declined the {' and '.join(waived)} findings; see its reply.")
        return 0, " ".join(notices)
    st["rounds"] = st.get("rounds", 0) + 1
    if st["rounds"] > MAX_ROUNDS:
        return 0, f"Quality gate gave up after {MAX_ROUNDS} rounds. Unresolved:\n{failure}"
    st.update(blocked=h, blocked_stage=stage)
    return 2, failure


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def main():
    event = json.load(sys.stdin)
    cwd = event.get("cwd") or os.getcwd()
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True)
    if top.returncode:
        return
    root = Path(top.stdout.strip())
    base = "HEAD" if git(root, "rev-parse", "--verify", "-q", "HEAD") else EMPTY_TREE
    state_dir = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or Path(tempfile.gettempdir()) / "papers-please")
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / f"{event.get('session_id', 'default')}.json"
    st = json.loads(state_file.read_text()) if state_file.exists() else {}
    code, message = (on_prompt if sys.argv[1] == "prompt" else on_stop)(event, root, base, st)
    state_file.write_text(json.dumps(st))
    if len(message) > MAX_OUTPUT:
        message = message[:MAX_OUTPUT] + "\n[output truncated]"
    if code == 2:
        sys.stderr.write(message)
    elif message:
        print(json.dumps({"systemMessage": message}))
    sys.exit(code)


if __name__ == "__main__":
    main()
