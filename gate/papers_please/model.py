"""Asking a model that isn't the coding agent: the review stage and model-judged lints."""

import json
import os
import re
import shutil
from pathlib import Path

from . import proc

_PROMPTS = Path(__file__).resolve().parent.parent
_MAX_REVIEW_DIFF = 150_000
_MAX_CONVERSATION = 40_000
_MAX_MESSAGE = 6_000

_REVIEWERS = [
    ("gemini", ["gemini", "-p", "Follow the instructions given on stdin."]),
    ("codex", ["codex", "exec", "--sandbox", "read-only", "-"]),
    (
        "claude",
        [
            "claude",
            "-p",
            "--model",
            os.environ.get("CODER_REVIEW_MODEL", "sonnet"),
            "--tools",
            "Read,Grep,Glob",
            "--settings",
            '{"disableAllHooks": true}',
        ],
    ),
]


def reviewer():
    """The name of the first reviewer CLI installed, which ask tries first, or None."""
    return next((name for name, cmd in _REVIEWERS if shutil.which(cmd[0])), None)


def ask(root, prompt, valid, notices):
    """Sends prompt to the first reviewer CLI installed, moving to the next if one fails or its answer
    doesn't satisfy valid(answer). Returns the reviewer's name and answer, or (None, "")."""
    for name, cmd in _REVIEWERS:
        if not shutil.which(cmd[0]):
            continue
        # Claude Code refuses to start inside another Claude Code session unless this is unset.
        p = proc.run(cmd, root, env={"CLAUDECODE": None}, stdin=prompt)
        if p.returncode == 0 and valid(p.stdout):
            return name, p.stdout
        why = (
            "timed out"
            if p.returncode == proc.TIMED_OUT
            else f"failed (exit {p.returncode}) or answered out of format"
        )
        notices.append(f"{name} {why}; trying the next model")
    notices.append("No model answered; model checks skipped.")
    return None, ""


def _message(line):
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
    return ("Agent" if entry["type"] == "assistant" else "User"), text


def _opencode_message(entry):
    """Returns the speaker and text of an OpenCode session message, or None for shell runs, other plugins'
    notices and everything else. An assistant message's text parts are kept; tool calls are dropped."""
    kind, text = entry.get("type"), entry.get("text", "")
    if kind == "user":
        return "User", text
    if kind == "assistant":
        parts = [
            c.get("text", "")
            for c in entry.get("content", [])
            if c.get("type") == "text"
        ]
        return "Agent", "\n".join(parts).strip()
    if (
        kind == "synthetic"
        and (entry.get("metadata") or {}).get("source") == "papers-please"
    ):
        return "Quality gate", text
    return None


def _conversation(event):
    """The session's messages, most recent last, keeping as many recent ones as fit in _MAX_CONVERSATION
    characters. They come from the OpenCode messages in event["messages"] if present, else from the Claude Code
    transcript (JSONL) at event["transcript_path"]; "" if neither is available."""
    if "messages" in event:
        parsed = map(_opencode_message, event["messages"])
    else:
        try:
            parsed = map(
                _message, Path(event.get("transcript_path")).read_text().splitlines()
            )
        except (OSError, TypeError):
            return ""
    kept, size = [], 0
    for who, text in reversed([m for m in parsed if m and m[1]]):
        if len(text) > _MAX_MESSAGE:
            text = text[:_MAX_MESSAGE] + "\n[message truncated]"
        size += len(text)
        if size > _MAX_CONVERSATION:
            break
        kept.append(f"### {who}\n{text}")
    return "\n\n".join(reversed(kept))


def review(root, diff, event, prompts, notices):
    """Has a reviewer model judge the patch against what the session asked for: the hook event's messages, or
    the recorded user `prompts` when it has none. Returns the findings to act on, or "" on a pass."""
    context = _conversation(event) or "\n---\n".join(prompts)
    if len(diff) > _MAX_REVIEW_DIFF:
        diff = (
            diff[:_MAX_REVIEW_DIFF] + "\n[diff truncated; read the files for the rest]"
        )
    prompt = (
        f"{(_PROMPTS / 'review-prompt.md').read_text()}\n\n## Conversation, most recent last\n"
        f"{context or '(not recorded)'}\n\n## Patch\n{diff}"
    )
    verdict = re.compile(r"^\W*VERDICT:\s*(PASS|BLOCK)", re.MULTILINE)
    name, out = ask(root, prompt, verdict.search, notices)
    if not name or verdict.findall(out)[-1] == "PASS":
        return ""
    return (
        f"Code review ({name}) requested changes. Address them, or if you disagree, leave the code "
        f"unchanged and explain why in your reply:\n{out.strip()}"
    )
