# papers-please

A Claude Code plugin that checks every patch an agent writes before it lets the turn end:
lint, complexity and test checks, then a review by a different model.

## Install

This repo is its own plugin marketplace:

```
claude plugin marketplace add Eric-Warehime/papers-please   # or the path to a local clone
claude plugin install papers-please@papers-please
```

Installed plugins are copied to the plugin cache, so after editing run
`claude plugin marketplace update papers-please && claude plugin update papers-please@papers-please`,
or test uninstalled changes with `claude --plugin-dir .` from the repo root.

## How it works

Each language has one script, `hooks/checks/<ext>.sh`, with two modes. Add a language by adding a
script.

- **`fix FILE`** runs after every `Write`/`Edit` (`PostToolUse`), and at `Stop` on every changed file
  before the checks, which covers files written through Bash. It formats the file and applies safe
  fixes. When it changes a file, Claude is told which file and that line numbers refer to the new
  version: in the edit's feedback, in the gate's feedback if the Stop blocks, or at the next prompt
  if it passes. On a pass, you see the notice too. For Python it also reports remaining ruff
  findings to Claude straight away, except unused-name rules that fire on half-finished edits. An
  existing Python file keeps its style: only changed lines are formatted, and lint autofixes are
  skipped since they can rewrite any line.
- **`check FILES`** runs at `Stop` on the changed files: linters, then the tests for those files.

On `Stop`, `hooks/gate.py` runs these stages in order when the turn changed the working tree:

1. **Suppressions:** added `nolint`/`noqa`/`#[allow(`/skip markers or removed test functions.
2. **Checks:** `check` for each changed file extension.
3. **Review:** `hooks/review-prompt.md` plus the patch and the session's recent messages (yours,
   Claude's replies and earlier gate feedback, from the transcript), sent to Gemini CLI,
   else Codex CLI, else `claude -p --model $CODER_REVIEW_MODEL` (default `sonnet`).

A failure blocks the stop and feeds the output to Claude, headed by a fix strategy for each kind of
finding. Suppression and review findings can be rebutted by stopping again without changes; check
failures cannot. After 3 blocks per turn the gate gives up and reports to the user.

Lint findings only count when the block they start on (the whole function, for a complexity finding)
contains a line the patch changed, so existing issues elsewhere in a touched file don't block. A
missing tool skips its checks and is reported to the user at `Stop`, once per session; Python tools that are
not installed run through `uvx`.

### Default rules

A repo's own linter config wins over these defaults, except where noted.

| | Format | Cyclomatic | Cognitive | Also |
|---|---|---|---|---|
| Go | gofumpt, gci | gocyclo 15 | gocognit 15 | nestif, funlen 80, dupl, revive (early return, ≤5 args, ≤3 results, unused params), unparam, gocritic, errorlint, nilerr ([golangci.yml](hooks/checks/golangci.yml)). A repo config still gets gocognit, gocyclo and dupl, at their default thresholds. |
| Python | ruff format | ruff C901 10 | flake8-cognitive-complexity 15 | Added to the repo's ruff selection: pylint size limits (PLR09), bugbear, simplify, return, unused arguments, commented-out code, blind excepts |
| Rust | rustfmt | lizard 15 | clippy 15 | Lines 80, arguments 5, nesting 5 (counting the `impl` block), redundant else, let-else, needless pass by value ([clippy.toml](hooks/checks/clippy.toml)). The extra lints run even when the crate has its own `clippy.toml`; only the thresholds come from it. |

## Not built yet

- A warning when a diff grows past a size budget.
- Asking before the agent edits tests while a test failure is outstanding.

## License

MIT, see [LICENSE](LICENSE).
