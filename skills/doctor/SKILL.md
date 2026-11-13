---
description: Check that the tools papers-please needs are installed and working (uv, the formatters and linters for this repo's languages, ast-grep, a reviewer CLI), and install the missing ones. Use right after installing the plugin, or when papers-please reports missing tools.
argument-hint: "[--all]"
allowed-tools: Bash(command -v uv), Bash(uv run -q --script ${CLAUDE_PLUGIN_ROOT}/hooks/gate.py doctor), Bash(uv run -q --script ${CLAUDE_PLUGIN_ROOT}/hooks/gate.py doctor --all)
---

Set up papers-please's tools for this repo's languages. Arguments: `$ARGUMENTS` (`--all` checks every
language, e.g. to set up a machine once).

1. **uv.** Run `command -v uv`. If it prints nothing, uv is missing and nothing else can run. Ask the
   user before installing it, then use `brew install uv` if Homebrew is installed, else the official
   installer, `curl -LsSf https://astral.sh/uv/install.sh | sh` (show it to them first). If the new
   `uv` isn't on PATH afterwards, tell them to open a new shell, and stop here.

2. **Check.** Run `uv run -q --script ${CLAUDE_PLUGIN_ROOT}/hooks/gate.py doctor $ARGUMENTS` from the
   repo. It lists each tool as ok or MISSING with how to fix it, and exits 1 while anything is missing.
   It also fetches the Python tools (ruff, flake8, lizard, ast-grep) into uv's cache, which can take a
   minute the first time.

3. **Install.** If any fix reads `--install runs: ...`, show the user those commands and ask before
   running `uv run -q --script ${CLAUDE_PLUGIN_ROOT}/hooks/gate.py doctor --install $ARGUMENTS`. It
   installs with Homebrew or rustup, and only what needs no password.

4. **What the user must do.** For fixes that read `run yourself: sudo ...`, tell the user to run them
   with a `!` prefix in the prompt, since they need a password. For fixes that are a URL or other
   instructions (e.g. installing Rust with rustup), pass them on; offer to help, but don't pipe install
   scripts into a shell without the user's go-ahead. If no reviewer CLI is installed, say that the
   model-judged lints and the review are skipped until one is (`claude` is the usual choice).

5. **Confirm.** Re-run the check and summarize: what was installed, what's still missing and why, and
   which checks stay off until it's fixed.
