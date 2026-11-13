"""Quality gate for agent patches.

`session` (SessionStart) tells the user and Claude which tools the checks lack, pointing to the doctor.
`prompt` (UserPromptSubmit) records the request and the tree state at turn start, and tells Claude about
files the last Stop reformatted.
`edit` (PostToolUse) formats the edited file, tells Claude if that changed it, and feeds what lint
can't fix back to Claude.
`stop` (Stop) formats every changed file, then checks the patch in four stages: lint suppressions and
test removals, linters and tests (including custom lints), model-judged lints, then a review by a
different model. A failing stage exits 2 so Claude keeps working. Findings of every stage but linters
and tests can be rebutted by stopping again without changing the tree. After _MAX_ROUNDS blocks in one
turn the gate lets the turn end and reports what is unresolved to the user.
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from . import doctor, languages, lints, model
from .changes import Changes
from .checks import SCRATCH, Ctx, joined
from .proc import git

_MAX_ROUNDS = 3
_MAX_OUTPUT = 8000  # Claude Code moves hook output over 10k chars to a file.
# Appended to Stop feedback: the gate's output scrolls Claude's summary out of view, and no hook runs
# before the final message, so the reply after a block has to carry the summary again.
_RESTATE = (
    "Once you have addressed the findings below, end your reply with your complete summary of the whole turn, "
    "updated for these changes. The user won't scroll back to the earlier one."
)

_SUPPRESSION = re.compile(
    r"//\s*nolint|#\s*noqa|#\s*type:\s*ignore|#!?\[allow\(|\bt\.Skip(Now)?\(|pytest\.mark\.skip|unittest\.skip|#\[ignore\]"
)
_TEST_DEF = re.compile(r"^\s*func Test\w*\(|^\s*def test_\w*\(|^\s*#\[test\]")


# Fix strategies shown above linter output, for findings whose output matches the pattern.
_HINTS = [
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
        "Nesting: invert the condition and return or continue early instead of wrapping code in another block.",
    ),
    (
        r"too long|too many (lines|statements)",
        "Length: split the function into steps named after what they do.",
    ),
    (
        r"argument-limit|too many arguments",
        "Arguments: group parameters that travel together into a struct or dataclass, or split the function.",
    ),
    (
        r"\(dupl\)",
        "Duplication: extract the repeated code into one helper that both sites call.",
    ),
    (
        r"BLE001|S110|nilerr|errorlint",
        "Errors: catch or compare the specific error, then handle or propagate it; never discard it.",
    ),
    (
        r"unused-parameter|unparam|ARG00\d",
        "Unused parameter: remove it and update the callers.",
    ),
    (
        r"early-return|indent-error-flow|superfluous-else|redundant else|RET50\d",
        "Else after return: handle the exceptional case first and return, then continue unindented.",
    ),
    (r"ERA001", "Commented-out code: delete it; git keeps the history."),
]


def _hinted(output):
    """Prefixes linter output with the fix strategy for each kind of finding in it."""
    # Command headers name the rules enabled, not the ones found.
    found = "\n".join(l for l in output.splitlines() if not l.startswith("$ "))
    hints = [
        f"- {h}\n" for pattern, h in _HINTS if re.search(pattern, found, re.IGNORECASE)
    ]
    return ("How to fix:\n" + "".join(hints) + "\n" if hints else "") + output


def _suppressions(diff):
    lines = [l for l in diff.splitlines() if not l.startswith(("+++", "---"))]
    added = [l[1:].strip() for l in lines if l.startswith("+")]
    removed = [l[1:].strip() for l in lines if l.startswith("-")]
    hits = [f"added: {l}" for l in added if _SUPPRESSION.search(l) and l not in removed]
    lost = sum(map(bool, map(_TEST_DEF.search, removed))) - sum(
        map(bool, map(_TEST_DEF.search, added))
    )
    if lost > 0:
        hits.append(f"{lost} test function(s) removed")
    if not hits:
        return ""
    return (
        "The patch silences checks or removes tests:\n"
        + "\n".join(hits)
        + "\nRemove these, or if one is genuinely necessary, leave the code unchanged and justify it in your reply."
    )


def _guarded(fn, *args):
    """fn(*args), or the error as output if a config file it reads is malformed."""
    try:
        return fn(*args)
    except ValueError as e:
        return f"papers-please could not read a config file: {e}"


def _run_checks(ctx, files):
    by_lang = {}
    for f in files:
        if lang := languages.of(f):
            by_lang.setdefault(lang, []).append(f)
    out = joined(
        [_guarded(lang.check, ctx, fs) for lang, fs in by_lang.items()]
        + [_guarded(lints.check, ctx, files)]
    )
    return (
        "Quality checks failed. Fix these before finishing:\n" + _hinted(out)
        if out
        else ""
    )


def _fix(ctx, files):
    """Formats each file. Returns the files that changed and the lint findings left."""
    changed, out = [], []
    for f in files:
        lang = languages.of(f)
        if not lang or not (ctx.root / f).is_file():  # e.g. a submodule
            continue
        before = (ctx.root / f).read_bytes()
        out.append(_guarded(lang.fix, ctx, f))
        if (ctx.root / f).read_bytes() != before:
            changed.append(f)
    return changed, joined(out)


def _reformatted(files, when):
    """The notice telling Claude that papers-please changed files it wrote, or "" if there are none."""
    if not files:
        return ""
    return f"papers-please reformatted {', '.join(files)} {when}; line numbers refer to the reformatted files."


def _on_session(_event, root, _st):
    if not (lacking := doctor.missing(root)):
        return 0, "", ""
    note = (
        f"papers-please is missing {', '.join(lacking)}, so those checks are skipped. "
        "Run /papers-please:doctor to install what's missing."
    )
    return 0, note, note


def _on_prompt(event, root, st):
    diff, _ = Changes(root).patch()
    st.update(
        turn_start=_digest(diff),
        rounds=0,
        prompts=(st.get("prompts", []) + [event.get("prompt", "")])[-3:],
    )
    return 0, "", _reformatted(st.pop("fixed", []), "after your last turn")


def _on_edit(event, root, _st):
    try:
        f = (
            Path(event["tool_input"]["file_path"])
            .resolve()
            .relative_to(root.resolve())
            .as_posix()
        )
    except (KeyError, ValueError):
        return 0, "", ""
    fixed, out = _fix(Ctx(root), [f])
    note = _reformatted(fixed, "after your edit")
    if out:
        return (
            2,
            joined([note, f"Lint issues remain in {f}:\n{_hinted(out)}"]),
            "",
        )
    return 0, "", note


def _on_stop(event, root, st):
    changes = Changes(root)
    if changes.base is None:
        skipped = "papers-please skipped its checks: a merge, cherry-pick or revert awaits its commit."
        return 0, _report(st, [], [skipped]), ""
    diff, files = changes.patch()
    if not diff.strip() or _digest(diff) in (st.get("turn_start"), st.get("passed")):
        return 0, "", ""
    # Fix first, so the checks, the reviewer and the digests below all see the files as they will stay.
    fixed, _ = _fix(Ctx(root), files)
    diff, files = Changes(root).patch()
    ctx = Ctx(root)
    note = _reformatted(fixed, "before checking")
    h = _digest(diff)
    waived = st.get("waived", {}).get(h, [])
    if h == st.get("blocked") and st.get("blocked_stage") != "checks":
        waived = waived + [st["blocked_stage"]]
    st["waived"] = {h: waived}
    notices = list(filter(None, [note]))
    context = _conversation_or_prompts(event, st)
    stages = (
        ("suppressions", lambda: _suppressions(diff)),
        ("checks", lambda: _run_checks(ctx, files)),
        ("lints", lambda: _guarded(lints.judge, ctx, files, notices)),
        ("review", lambda: model.review(root, diff, context, notices)),
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
            notices.append(
                f"Claude declined the {' and '.join(waived)} findings; see its reply."
            )
        return 0, _report(st, notices, ctx.missing), ""
    st["rounds"] = st.get("rounds", 0) + 1
    if st["rounds"] > _MAX_ROUNDS:
        st["fixed"] = fixed
        notices.append(
            f"Quality gate gave up after {_MAX_ROUNDS} rounds. Unresolved:\n{failure}"
        )
        return 0, _report(st, notices, ctx.missing), ""
    st.update(blocked=h, blocked_stage=stage)
    return 2, joined([note, _RESTATE, failure]), ""


def _conversation_or_prompts(event, st):
    return model.conversation(event.get("transcript_path")) or "\n---\n".join(
        st.get("prompts", [])
    )


def _report(st, notices, missing):
    """Joins notices for the user, adding the missing-tool notices not yet shown this session."""
    fresh = [m for m in dict.fromkeys(missing) if m not in st.get("reported", [])]
    st["reported"] = st.get("reported", []) + fresh
    return "\n".join(notices + fresh)


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def main():
    event = json.load(sys.stdin)
    cwd = event.get("cwd") or os.getcwd()
    top = git(cwd, "rev-parse", "--show-toplevel").strip()
    if not top:
        return
    root = Path(top)
    state_dir = Path(os.environ.get("CLAUDE_PLUGIN_DATA") or SCRATCH)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / f"{event.get('session_id', 'default')}.json"
    st = json.loads(state_file.read_text()) if state_file.exists() else {}
    handler = {
        "session": _on_session,
        "prompt": _on_prompt,
        "edit": _on_edit,
        "stop": _on_stop,
    }[sys.argv[1]]
    code, message, context = handler(event, root, st)
    state_file.write_text(json.dumps(st))
    if len(message) > _MAX_OUTPUT:
        message = message[:_MAX_OUTPUT] + "\n[output truncated]"
    if code == 2:
        sys.stderr.write(message)
    elif message or context:
        out = {"systemMessage": message} if message else {}
        if (
            context
        ):  # Shown to Claude on SessionStart, UserPromptSubmit and PostToolUse.
            out["hookSpecificOutput"] = {
                "hookEventName": event["hook_event_name"],
                "additionalContext": context,
            }
        print(json.dumps(out))
    sys.exit(code)
