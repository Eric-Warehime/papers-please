# <img src="assets/papers-please.png" alt="" width="48" align="center"> papers-please

A Claude Code plugin that checks every patch an agent writes before it lets the turn end:
lint, complexity and test checks, then a review by a different model.

## Install

Requires [uv](https://docs.astral.sh/uv/), which runs the hooks with Python 3.11+ and fetches the
Python tools that aren't installed. This repo is its own plugin marketplace:

```
claude plugin marketplace add Eric-Warehime/papers-please   # or the path to a local clone
claude plugin install papers-please@papers-please
```

Installed plugins are copied to the plugin cache, so after editing run
`claude plugin marketplace update papers-please && claude plugin update papers-please@papers-please`,
or test uninstalled changes with `claude --plugin-dir .` from the repo root. Run the tests with
`uv run pytest`.

## How it works

`hooks/gate.py` is the entry point for three hooks. The code is in `hooks/papers_please/`, with one
module per language (`python.py`, `go.py`, `rust.py`), each with two functions. Add a language by
adding a module.

- **`fix`** runs after every `Write`/`Edit` (`PostToolUse`), and at `Stop` on every changed file
  before the checks, which covers files written through Bash. It formats the file and applies safe
  fixes. When it changes a file, Claude is told which file and that line numbers refer to the new
  version: in the edit's feedback, in the gate's feedback if the Stop blocks, or at the next prompt
  if it passes. On a pass, you see the notice too. For Python it also reports remaining ruff
  findings to Claude straight away, except unused-name rules that fire on half-finished edits. An
  existing Python file keeps its style: only changed lines are formatted, and lint autofixes are
  skipped since they can rewrite any line.
- **`check`** runs at `Stop` on the changed files: linters, then the tests for those files.

On `Stop`, these stages run in order when the turn changed the working tree:

1. **Suppressions:** added `nolint`/`noqa`/`#[allow(`/skip markers or removed test functions.
2. **Checks:** `check` for each changed file's language, plus the ast-grep and script
   [custom lints](#custom-lints).
3. **Judged lints:** the model-judged custom lints.
4. **Review:** `hooks/review-prompt.md` plus the patch and the session's recent messages (yours,
   Claude's replies and earlier gate feedback, from the transcript).

Stages 3 and 4 go to Gemini CLI, else Codex CLI, else `claude -p --model $CODER_REVIEW_MODEL`
(default `sonnet`).

A failure blocks the stop and feeds the output to Claude, headed by a fix strategy for each kind of
finding. It also asks Claude to end its next reply with its full summary of the turn, since the
gate's output scrolls the original out of view and no hook can run before it. Findings of every stage
but the checks can be rebutted by stopping again without changes. After 3 blocks per turn the gate
gives up and reports to the user.

Lint findings only count when the block they start on (the whole function, for a complexity finding)
contains a line the patch changed, so existing issues elsewhere in a touched file don't block. A
missing tool skips its checks and is reported to the user at `Stop`, once per session.

### Rules and precedence

Each linter's rules and settings come from three layers, later ones winning:

1. **Our defaults** in [hooks/rules.toml](hooks/rules.toml), below.
2. **The repo's own linter config:** everything it turns on runs, everything it turns off stays off
   (ruff `ignore`, golangci-lint `disable`, clippy `[lints.clippy]`), and its settings apply. Where it
   says nothing, our defaults apply. For golangci-lint, the repo's config plus our additions is written
   to a temporary file; for clippy, the same is done for `clippy.toml`.
3. **The harness config:** `~/.config/papers-please/config.toml` for all your repos, overridden by a
   repo's `.papers-please.toml`. It uses the tables of `rules.toml`, plus `disable`, and wins over the
   repo's config:

```toml
[golangci]
disable = ["dupl"]
settings = { gocognit.min-complexity = 20 }

[clippy]
enable = ["unwrap_used"]

[lints]
disable = ["module-is-shallow"]
```

| | Format | Cyclomatic | Cognitive | Also |
|---|---|---|---|---|
| Go | gofumpt, gci | gocyclo 15 | gocognit 15 | nestif, funlen 80, dupl, revive (early return, ≤5 args, ≤3 results, unused params), unparam, gocritic, errorlint, nilerr. Our revive rules are added to the repo's by name. |
| Python | ruff format | ruff C901 10 | flake8-cognitive-complexity 15 | Pylint size limits (PLR09), bugbear, simplify, return, unused arguments, commented-out code, blind excepts |
| Rust | rustfmt | lizard 15 | clippy 15 | Lines 80, arguments 5, nesting 5 (counting the `impl` block), redundant else, let-else, needless pass by value |

### Custom lints

Each file in [hooks/lints](hooks/lints) is a lint, on for every repo unless the harness config's
`[lints]` disables its id. Findings only count where they overlap changed lines.

- **ast-grep rules:** a YAML file with `id`, `language`, an [ast-grep](https://ast-grep.github.io)
  `rule` and a `message`. Runs with the checks.
- **Model-judged rules:** a YAML file with `ask`, modeled after [jev-lint](https://github.com/mizchi/jev-lint)'s
  rules. `ask` is a statement about a subject, decided with `criteria` (`true` and `false`) and
  `note`; `message` is shown to Claude with a finding, never to the model. `subject: file` judges each
  changed file whole, else each match of the optional ast-grep `rule`. One call to the review model
  judges every subject.
- **Executables:** run with the changed file paths; print `file:line: message` findings and exit 1
  if there are any. Run with the checks.

A YAML lint may only use the keys above, plus ast-grep's `constraints`, `utils` and `transform`;
any other key is reported as a config error.

[module-is-shallow](hooks/lints/module-is-shallow.yml) asks whether a module's public interface is
wider than its job.

## Not built yet

- A warning when a diff grows past a size budget.
- Asking before the agent edits tests while a test failure is outstanding.

## Disclaimer

This is an independently developed coding-agent plugin. It is not affiliated with, endorsed by, or
sponsored by the developer or publisher of the video game Papers, Please.

## License

MIT, see [LICENSE](LICENSE).
