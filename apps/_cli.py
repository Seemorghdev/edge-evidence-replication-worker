"""Shared command-line helpers for SPEC-001 entry points."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from packages import __version__


def foundation_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def parse_help_only(
    parser: argparse.ArgumentParser, argv: Sequence[str] | None = None
) -> int:
    parser.parse_args(argv)
    parser.print_help()
    return 0
