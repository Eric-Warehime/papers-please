import json

import pytest
from papers_please import model


@pytest.fixture
def asked(monkeypatch):
    """The prompts review sends to the reviewer, which passes everything."""
    prompts = []

    def ask(_root, prompt, _valid, _notices):
        prompts.append(prompt)
        return "claude", "VERDICT: PASS"

    monkeypatch.setattr(model, "ask", ask)
    return prompts


def _context(prompt):
    return prompt.split("## Conversation, most recent last\n")[1].split("\n\n## Patch")[
        0
    ]


def test_review_from_opencode_messages(tmp_path, asked):
    messages = [
        {"type": "user", "text": "Add a flag"},
        {"type": "assistant", "content": [{"type": "text", "text": "Done."}]},
        {"type": "synthetic", "text": "another plugin's notice"},
        {
            "type": "synthetic",
            "text": "Code review requested changes",
            "metadata": {"source": "papers-please"},
        },
        {"type": "shell", "text": "make"},
    ]
    assert model.review(tmp_path, "diff", {"messages": messages}, [], []) == ""
    assert _context(asked[0]) == (
        "### User\nAdd a flag\n\n### Agent\nDone.\n\n### Quality gate\nCode review requested changes"
    )


def test_review_from_transcript_or_prompts(tmp_path, asked):
    lines = [
        {
            "type": "user",
            "message": {"content": "Add a flag <system-reminder>x</system-reminder>"},
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Done."}]},
        },
        {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(map(json.dumps, lines)))
    model.review(tmp_path, "diff", {"transcript_path": str(path)}, ["x"], [])
    model.review(tmp_path, "diff", {}, ["first", "second"], [])
    assert [_context(p) for p in asked] == [
        "### User\nAdd a flag\n\n### Agent\nDone.",
        "first\n---\nsecond",
    ]
