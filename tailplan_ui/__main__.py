"""Command line: python3 -m tailplan_ui bundle INPUT [-o OUT]."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bundler import TARGETS, bundle
from .color import solve as S


def _bundle(args) -> int:
    html = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
    out, meta = bundle(html, target=args.target)
    to_file = bool(args.output) and args.output != "-"
    if to_file:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)
    if args.metadata:
        # Metadata goes to stdout with -o FILE, else to stderr.
        stream = sys.stdout if to_file else sys.stderr
        json.dump(meta, stream, indent=2)
        print(file=stream)
    for warning in meta["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _solve(args) -> int:
    seed = float(args.seed) if args.seed.replace(".", "", 1).isdigit() else args.seed
    result = S.solve_accent(seed, args.light_bg, args.dark_bg, contrast=args.contrast)
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tailplan_ui")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("bundle", help="stamp the Tailplan UI style block into an HTML page")
    b.add_argument("input", help="HTML file, or - for stdin")
    b.add_argument("-o", "--output", help="output file (default: stdout)")
    b.add_argument("--target", choices=TARGETS, default="standalone")
    b.add_argument("--metadata", action="store_true", help="print the metadata JSON (stdout with -o, else stderr)")
    b.set_defaults(func=_bundle)
    s = sub.add_parser("solve", help="solve accent tokens for a seed hex or hue")
    s.add_argument("seed")
    s.add_argument("--light-bg", default="#faf6ee")
    s.add_argument("--dark-bg", default="#1c1b18")
    s.add_argument("--contrast", choices=("normal", "more"), default="normal")
    s.set_defaults(func=_solve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
