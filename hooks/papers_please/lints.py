"""Custom lints in hooks/lints, each on unless the harness config's [lints] table disables its id:

- An ast-grep rule: a YAML file with `rule` and no `ask`, reported with its `message`.
- A model-judged rule: a YAML file with `ask`, modeled after jev-lint's. `ask` is a statement about a
  subject, decided with `criteria` (`true` and `false`) and `note`. `message` is shown to Claude with
  a finding and never to the model. `subject: file` judges each changed file whole, else each match of
  the optional ast-grep `rule`.
- An executable, run with the changed file paths, that prints `file:line: message` findings and exits 1
  if it found any.

A YAML lint with a key outside _KEYS is an error. Findings only count where they overlap changed lines."""

import json
import os
import re
from pathlib import Path

import yaml

from . import config, languages, model
from .checks import joined

_LINTS = Path(__file__).resolve().parent.parent / "lints"
_MAX_SUBJECT = 20_000
_AST_GREP_KEYS = ("rule", "constraints", "utils", "transform", "message")
_KEYS = {"id", "language", "subject", "ask", "criteria", "note", *_AST_GREP_KEYS}
_JUDGE_PROMPT = """You are checking code for a quality gate. Each rule below is a statement; decide for each
subject under it whether the statement is true of that subject, using the rule's criteria. You may
read repository files for context, but do not modify anything. Answer with one line per subject, in
order: `S<n>: TRUE` or `S<n>: FALSE`, then ` - ` and one sentence of reason."""
_VERDICT = re.compile(r"^\W*(S\d+):\s*(TRUE|FALSE)\b\W*(.*)$", re.MULTILINE)


def _load(ctx):
    """(id, path, spec) of each enabled lint; spec is None for an executable."""
    off, out = ctx.policy("lints").disable, []
    for p in sorted(_LINTS.iterdir()) if _LINTS.is_dir() else []:
        if p.suffix in (".yml", ".yaml"):
            spec = config.read(p)
            if unknown := spec.keys() - _KEYS:
                raise ValueError(f"{p.name}: unknown keys {sorted(unknown)}")
            lid = spec["id"]
        elif p.is_file() and os.access(p, os.X_OK):
            spec, lid = None, p.stem
        else:
            continue
        if lid not in off:
            out.append((lid, p, spec))
    return out


def _matches(ctx, spec, files):
    """(file, first line, last line) of each match of spec's ast-grep `rule` in the files of its
    languages, or with no `rule`, of each such file whole; and ast-grep's error output, if any."""
    langs = (
        [spec["language"]] if isinstance(spec["language"], str) else spec["language"]
    )
    files = [f for f in files if Path(f).suffix in languages.suffixes(langs)]
    if not files or "rule" not in spec:
        return [(f, 1, len(ctx.changes.source(f))) for f in files], ""
    rules = "---\n".join(
        yaml.safe_dump(
            {"id": spec["id"], "language": l}
            | {k: spec[k] for k in _AST_GREP_KEYS if k in spec}
        )
        for l in langs
    )
    if not (sg := ctx.tool("ast-grep", pypi="ast-grep-cli")):
        return [], ""
    p = ctx.run([*sg, "scan", "--inline-rules", rules, "--json=stream", *files])
    if p.returncode and p.stderr.strip():
        return [], f"ast-grep failed on lint {spec['id']}:\n{p.stderr.strip()}"
    found = [json.loads(l) for l in p.stdout.splitlines()]
    return [
        (m["file"], m["range"]["start"]["line"] + 1, m["range"]["end"]["line"] + 1)
        for m in found
    ], ""


def _touched(ctx, found):
    return [(f, s, e) for f, s, e in found if ctx.changes.overlaps(f, range(s, e + 1))]


def check(ctx, files):
    """Findings of the ast-grep and executable lints."""
    out = []
    for lid, path, spec in _load(ctx):
        if spec is None:
            out.append(ctx.lint([str(path), *files]))
        elif "ask" not in spec:
            found, err = _matches(ctx, spec, files)
            out += [
                err,
                *(
                    f"{f}:{s}: {lid}: {spec.get('message', '')}"
                    for f, s, _ in _touched(ctx, found)
                ),
            ]
    return joined(out)


def _subjects(ctx, files, notices):
    """(id, spec, file, first line, last line) of each subject of a model-judged lint that the patch
    touches."""
    out = []
    for lid, _, spec in _load(ctx):
        if spec is None or "ask" not in spec:
            continue
        found, err = _matches(ctx, spec, files)
        notices += [err] if err else []
        if spec.get("subject") == "file":
            found = [
                (f, 1, len(ctx.changes.source(f)))
                for f in dict.fromkeys(f for f, _, _ in found)
            ]
        out += [(lid, spec, *m) for m in _touched(ctx, found)]
    return out


def _text(x):
    return x if isinstance(x, str) else yaml.safe_dump(x)


def _prompt(ctx, subs):
    parts = [_JUDGE_PROMPT]
    for lid, spec in {lid: spec for lid, spec, *_ in subs}.items():
        c = spec.get("criteria", {})
        true, false = c.get("true", c.get(True, "")), c.get("false", c.get(False, ""))
        parts.append(
            f"## Rule {lid}\n{spec['ask']}\n\nTrue when: {_text(true)}\n\nFalse when: {_text(false)}\n\n{spec.get('note', '')}"
        )
    for i, (lid, _, f, s, e) in enumerate(subs, 1):
        src = "\n".join(ctx.changes.source(f)[s - 1 : e])
        if len(src) > _MAX_SUBJECT:
            src = src[:_MAX_SUBJECT] + "\n[truncated; read the file for the rest]"
        parts.append(f"## S{i}: rule {lid}, {f} lines {s}-{e}\n```\n{src}\n```")
    return "\n\n".join(parts)


def judge(ctx, files, notices):
    """Asks the review model each model-judged lint's statement about the subjects the patch touches.
    Returns the findings for Claude, or ""."""
    subs = _subjects(ctx, files, notices)
    if not subs:
        return ""
    ids = {f"S{i}" for i in range(1, len(subs) + 1)}
    name, out = model.ask(
        ctx.root,
        _prompt(ctx, subs),
        lambda a: ids <= {m[0] for m in _VERDICT.findall(a)},
        notices,
    )
    verdicts = {m[0]: m[1:] for m in _VERDICT.findall(out)}
    hits = [
        f"{f}:{s}: {lid}: {spec['message'].strip()}\n  ({name}: {verdicts[f'S{i}'][1]})"
        for i, (lid, spec, f, s, _) in enumerate(subs, 1)
        if verdicts.get(f"S{i}", ("FALSE",))[0] == "TRUE"
    ]
    if not hits:
        return ""
    return (
        f"Lint findings (judged by {name}). Address them, or if you disagree, leave the code unchanged and "
        "explain why in your reply:\n" + "\n".join(hits)
    )
