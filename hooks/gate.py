#!/usr/bin/env python3
"""Quality gate for agent patches.

`gate.py prompt` (UserPromptSubmit) records the request and the tree state at turn start, and tells
Claude about files the last Stop reformatted.
`gate.py edit` (PostToolUse) runs `hooks/checks/<ext>.sh fix` on the edited file, tells Claude if it
changed the file, and feeds what it cannot fix back to Claude.
`gate.py stop` (Stop) runs the fixers on every changed file, then checks the patch in three stages: lint suppressions and test removals, `hooks/checks/<ext>.sh check`, then a review by
a different model. A failing stage exits 2 so Claude keeps working. Suppression and review findings
can be rebutted by stopping again without changing the tree; check failures cannot. After
MAX_ROUNDS blocks in one turn the gate lets the turn end and reports what is unresolved to the user.
`gate.py newonly CMD...` is used by the check scripts; see newonly().
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
MAX_CONVERSATION = 40_000
MAX_MESSAGE = 6_000

SUPPRESSION = re.compile(r"//\s*nolint|#\s*noqa|#\s*type:\s*ignore|#!?\[allow\(|\bt\.Skip(Now)?\(|pytest\.mark\.skip|unittest\.skip|#\[ignore\]")
TEST_DEF = re.compile(r"^\s*func Test\w*\(|^\s*def test_\w*\(|^\s*#\[test\]")
LOCATION = re.compile(r"^(.+?):(\d+):")

# Fix strategies shown above linter output, for findings whose output matches the pattern.
HINTS = [
    (
        r"cognitive complexity|CCR001",
        (
            "Cognitive complexity: flatten nesting with guard clauses and early returns, and move nested loop "
            "bodies or branches into named helpers."
        ),
    ),
    (
        r"cyclomatic complexity|is too complex|too many (branches|return)|\d+ CCN",
        (
            "Cyclomatic complexity: too many paths. Split the function by responsibility, or replace branch "
            "chains with a lookup table."
        ),
    ),
    (
        r"nested blocks|too nested",
        (
            "Nesting: invert the condition and return or continue early instead of wrapping code in another block."
        ),
    ),
    (
        r"too long|too many (lines|statements)",
        "Length: split the function into steps named after what they do.",
    ),
    (
        r"argument-limit|too many arguments",
        (
            "Arguments: group parameters that travel together into a struct or dataclass, or split the function."
        ),
    ),
    (
        r"\(dupl\)",
        "Duplication: extract the repeated code into one helper that both sites call.",
    ),
    (
        r"BLE001|S110|nilerr|errorlint",
        (
            "Errors: catch or compare the specific error, then handle or propagate it; never discard it."
        ),
    ),
    (
        r"unused-parameter|unparam|ARG00\d",
        "Unused parameter: remove it and update the callers.",
    ),
    (
        r"early-return|indent-error-flow|superfluous-else|redundant else|RET50\d",
        (
            "Else after return: handle the exceptional case first and return, then continue unindented."
        ),
    ),
    (r"ERA001", "Commented-out code: delete it; git keeps the history."),
]

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


def run_scripts(root, files, base, mode, notices):
    """Runs `checks/<ext>.sh <mode>` on the files of each extension and returns the failing runs' output.
    Lines the scripts print starting with "papers-please: " go to notices instead."""
    by_ext = {}
    for f in files:
        by_ext.setdefault(Path(f).suffix.lstrip("."), []).append(f)
    out = []
    for ext, paths in sorted(by_ext.items()):
        script = HERE / "checks" / f"{ext}.sh"
        if not ext or not script.exists():
            continue
        try:
            p = subprocess.run(
                ["sh", str(script), mode, *paths],
                cwd=root,
                capture_output=True,
                text=True,
                env={**os.environ, "CODER_BASE": base},
                timeout=CHECK_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            out.append(f"$ checks/{ext}.sh {mode} timed out after {CHECK_TIMEOUT}s")
            continue
        text = p.stdout + p.stderr
        notices += re.findall(r"^papers-please: (.*)", text, re.MULTILINE)
        if p.returncode:
            out.append(
                f"$ checks/{ext}.sh {mode}\n"
                + re.sub(r"^papers-please: .*\n?", "", text, flags=re.MULTILINE)
            )
    return "\n".join(out)


def hinted(output):
    """Prefixes linter output with the fix strategy for each kind of finding in it."""
    hints = [
        f"- {h}\n" for pattern, h in HINTS if re.search(pattern, output, re.IGNORECASE)
    ]
    return ("How to fix:\n" + "".join(hints) + "\n" if hints else "") + output


def run_checks(root, files, base, missing):
    out = run_scripts(root, files, base, "check", missing)
    return (
        "Quality checks failed. Fix these before finishing:\n" + hinted(out)
        if out
        else ""
    )


def changed_lines(root, base):
    """Maps each path the patch touches to its changed line numbers; a deletion marks the line before
    it. Untracked files map to None (all lines)."""
    lines, path = {}, None
    for l in git(root, "diff", "-U0", "--no-color", "--no-ext-diff", base).splitlines():
        if l.startswith("+++ "):
            path = l[6:]
        elif m := re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))?", l):
            start, n = int(m[1]), int(m[2] or 1)
            lines.setdefault(path, set()).update(
                range(start, start + n) if n else [start]
            )
    for f in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if f:
            lines[f] = None
    return lines


def block(lines, i, python):
    """Returns the 1-based line numbers of the construct starting at line i: through its closing bracket,
    or for a Python header, through the lines indented under it. Relies on formatted code; brackets in
    strings or comments can throw it off."""
    depth, indent = 0, None
    for j in range(i - 1, len(lines)):
        l = lines[j]
        if indent is not None:
            if l.strip() and len(l) - len(l.lstrip()) <= indent:
                return range(i, j + 1)
            continue
        depth += sum(map(l.count, "([{")) - sum(map(l.count, ")]}"))
        if depth > 0:
            continue
        if not (python and l.split("#")[0].rstrip().endswith(":")):
            return range(i, j + 2)
        indent = len(lines[i - 1]) - len(lines[i - 1].lstrip())
    return range(i, len(lines) + 1)


def touched(finding, top, changed, sources):
    """Whether the construct a `file:line:` finding starts (e.g. a whole function, for a complexity
    finding) contains a non-blank changed line. Paths are relative to the working directory."""
    m = LOCATION.match(finding)
    try:
        rel = m and (Path.cwd() / m[1]).resolve().relative_to(top).as_posix()
    except ValueError:
        return False
    if rel not in changed or changed[rel] is None:
        return rel in changed
    if rel not in sources:
        sources[rel] = (top / rel).read_text(errors="replace").splitlines()
    src = sources[rel]
    return any(
        n <= len(src) and n in changed[rel] and src[n - 1].strip()
        for n in block(src, int(m[2]), rel.endswith(".py"))
    )


def newonly(cmd):
    """Runs a linter and prints only its findings touched by changes since $CODER_BASE, so existing
    issues elsewhere in touched files don't block. Returns 1 if any remain. A linter that fails
    abnormally (exit above 1) has its whole output passed through instead."""
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    out = p.stdout + p.stderr
    if p.returncode > 1:
        print(out)
        return 1
    top = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip()).resolve()
    changed, sources = changed_lines(top, os.environ.get("CODER_BASE", "HEAD")), {}
    kept = [l for l in out.splitlines() if touched(l, top, changed, sources)]
    if kept:
        print("\n".join(kept))
    return 1 if kept else 0


def message(line):
    """Returns the speaker and text of a transcript line, or None for tool calls, tool results, metadata
    and unparseable lines. System reminders injected into user turns are dropped."""
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    content = (entry.get("message") or {}).get("content")
    if entry.get("type") not in ("user", "assistant") or not content:
        return None
    blocks = (
        [content]
        if isinstance(content, str)
        else [b.get("text", "") for b in content if b.get("type") == "text"]
    )
    text = re.sub(
        r"<system-reminder>.*?</system-reminder>",
        "",
        "\n".join(blocks),
        flags=re.DOTALL,
    ).strip()
    if text.startswith("Stop hook feedback"):
        return "Quality gate", text
    if not text or entry.get("isMeta"):
        return None
    return ("Claude" if entry["type"] == "assistant" else "User"), text


def conversation(path):
    """Returns the session's messages from the Claude Code transcript (JSONL) at path, most recent last,
    keeping as many recent ones as fit in MAX_CONVERSATION characters."""
    try:
        lines = Path(path).read_text().splitlines()
    except (OSError, TypeError):
        return ""
    entries = [m for m in map(message, lines) if m]
    kept, size = [], 0
    for who, text in reversed(entries):
        if len(text) > MAX_MESSAGE:
            text = text[:MAX_MESSAGE] + "\n[message truncated]"
        size += len(text)
        if size > MAX_CONVERSATION:
            break
        kept.append(f"### {who}\n{text}")
    return "\n\n".join(reversed(kept))


def review(root, diff, context, notices):
    if len(diff) > MAX_REVIEW_DIFF:
        diff = diff[:MAX_REVIEW_DIFF] + "\n[diff truncated; read the files for the rest]"
    prompt = (
        f"{(HERE / 'review-prompt.md').read_text()}\n\n## Conversation, most recent last\n"
        f"{context or '(not recorded)'}\n\n## Patch\n{diff}"
    )
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


def fix(root, files, base):
    """Runs the fixer on each file. Returns the files it changed and what it could not fix."""
    changed, out = [], []
    for f in files:
        if not (root / f).is_file():  # e.g. a submodule
            continue
        before = (root / f).read_bytes()
        out.append(run_scripts(root, [f], base, "fix", []))
        if (root / f).read_bytes() != before:
            changed.append(f)
    return changed, "\n".join(filter(None, out))


def reformatted(files, when):
    """The notice telling Claude that papers-please changed files it wrote, or "" if there are none."""
    if not files:
        return ""
    return f"papers-please reformatted {', '.join(files)} {when}; line numbers refer to the reformatted files."


def on_prompt(event, root, base, st):
    diff, _ = snapshot(root, base)
    st.update(turn_start=digest(diff), rounds=0, prompts=(st.get("prompts", []) + [event.get("prompt", "")])[-3:])
    return 0, "", reformatted(st.pop("fixed", []), "after your last turn")


def on_edit(event, root, base, _st):
    try:
        f = (
            Path(event["tool_input"]["file_path"])
            .resolve()
            .relative_to(root.resolve())
            .as_posix()
        )
    except (KeyError, ValueError):
        return 0, "", ""
    fixed, out = fix(root, [f], base)
    note = reformatted(fixed, "after your edit")
    if out:
        return (
            2,
            "\n".join(
                filter(None, [note, f"Lint issues remain in {f}:\n{hinted(out)}"])
            ),
            "",
        )
    return 0, "", note


def on_stop(event, root, base, st):
    diff, files = snapshot(root, base)
    if not diff.strip() or digest(diff) in (st.get("turn_start"), st.get("passed")):
        return 0, "", ""
    # Fix first, so the checks, the reviewer and the digests below all see the files as they will stay.
    fixed, _ = fix(root, files, base)
    diff, files = snapshot(root, base)
    note = reformatted(fixed, "before checking")
    h = digest(diff)
    waived = st.get("waived", {}).get(h, [])
    if h == st.get("blocked") and st.get("blocked_stage") != "checks":
        waived = waived + [st["blocked_stage"]]
    st["waived"] = {h: waived}
    notices, missing = list(filter(None, [note])), []
    stages = (
        ("suppressions", lambda: suppressions(diff)),
        ("checks", lambda: run_checks(root, files, base, missing)),
        (
            "review",
            lambda: review(
                root,
                diff,
                conversation(event.get("transcript_path"))
                or "\n---\n".join(st.get("prompts", [])),
                notices,
            ),
        ),
    )
    for stage, run in stages:
        failure = "" if stage in waived else run()
        if failure:
            break
    else:
        st.update(
            passed=h, fixed=fixed
        )  # Claude hears of the fixes at its next prompt.
        if waived:
            notices.append(f"Claude declined the {' and '.join(waived)} findings; see its reply.")
        return 0, report(st, notices, missing), ""
    st["rounds"] = st.get("rounds", 0) + 1
    if st["rounds"] > MAX_ROUNDS:
        st["fixed"] = fixed
        notices.append(
            f"Quality gate gave up after {MAX_ROUNDS} rounds. Unresolved:\n{failure}"
        )
        return 0, report(st, notices, missing), ""
    st.update(blocked=h, blocked_stage=stage)
    return 2, "\n".join(filter(None, [note, failure])), ""


def report(st, notices, missing):
    """Joins notices for the user, adding the missing-tool notices not yet shown this session."""
    fresh = [m for m in dict.fromkeys(missing) if m not in st.get("reported", [])]
    st["reported"] = st.get("reported", []) + fresh
    return "\n".join(notices + fresh)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def main():
    if sys.argv[1] == "newonly":
        sys.exit(newonly(sys.argv[2:]))
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
    code, message, context = {"prompt": on_prompt, "edit": on_edit, "stop": on_stop}[
        sys.argv[1]
    ](event, root, base, st)
    state_file.write_text(json.dumps(st))
    if len(message) > MAX_OUTPUT:
        message = message[:MAX_OUTPUT] + "\n[output truncated]"
    if code == 2:
        sys.stderr.write(message)
    elif message or context:
        out = {"systemMessage": message} if message else {}
        if context:  # Shown to Claude; Claude Code supports this on UserPromptSubmit and PostToolUse.
            out["hookSpecificOutput"] = {
                "hookEventName": event["hook_event_name"],
                "additionalContext": context,
            }
        print(json.dumps(out))
    sys.exit(code)


if __name__ == "__main__":
    main()
