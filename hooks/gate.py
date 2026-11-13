#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Entry point for the hooks in hooks.json (`gate.py session|prompt|edit|stop`, see papers_please/hook.py)
and the doctor skill (`gate.py doctor`, see papers_please/doctor.py)."""

import sys

if sys.argv[1:2] == ["doctor"]:
    from papers_please.doctor import main
else:
    from papers_please.hook import main

main()
