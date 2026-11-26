"""Run the Tailplan UI command line: python3 -m tailplan_ui COMMAND."""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(prog="python3 -m tailplan_ui"))
