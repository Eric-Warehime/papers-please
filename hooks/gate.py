#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Entry point for the hooks in hooks.json: `gate.py prompt|edit|stop`. See papers_please/hook.py."""

from papers_please.hook import main

main()
