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

`hooks/gate.py` runs on `Stop` whenever the turn changed the working tree, in order:

1. **Suppressions:** added `nolint`/`noqa`/`#[allow(`/skip markers or removed test functions.
2. **Checks:** `hooks/checks/<ext>.sh` for each changed file extension (Go, Python, Rust so far).
   Add a language by dropping in a script that takes the changed files as arguments.
3. **Review:** `hooks/review-prompt.md` plus the patch and recent requests, sent to Gemini CLI,
   else Codex CLI, else `claude -p --model $CODER_REVIEW_MODEL` (default `sonnet`).

A failure blocks the stop and feeds the output to Claude. Suppression and review findings can be
rebutted by stopping again without changes; check failures cannot. After 3 blocks per turn the gate
gives up and reports to the user.

## Not built yet

- Formatting after each edit (`PostToolUse`).
- A warning when a diff grows past a size budget.
- Asking before the agent edits tests while a test failure is outstanding.

## License

MIT, see [LICENSE](LICENSE).
