"""CLI for the bounded replication worker."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from apps._cli import foundation_parser
from packages.replication.contracts.model import ReplicationError
import packages.replication.core.worker as core_worker
import packages.replication.worker as legacy_worker

from .target_config import load_target_config


def _add_target_arguments(command: argparse.ArgumentParser) -> None:
    target = command.add_mutually_exclusive_group(required=True)
    target.add_argument("--target-root", type=Path)
    target.add_argument("--target-config", type=Path)
    command.add_argument("--target-id")


def build_parser() -> argparse.ArgumentParser:
    parser = foundation_parser(
        "replication-worker",
        "Bounded verified replication worker.",
    )
    commands = parser.add_subparsers(dest="command")

    for name in ("run", "reconcile"):
        command = commands.add_parser(name)
        command.add_argument("--database", required=True, type=Path)
        command.add_argument("--spool-root", required=True, type=Path)
        _add_target_arguments(command)

    verify = commands.add_parser("verify")
    verify.add_argument("--database", required=True, type=Path)
    _add_target_arguments(verify)

    return parser


def _emit(payload: dict[str, object], *, error: bool = False) -> None:
    print(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
        file=sys.stderr if error else sys.stdout,
    )


def _validate_target_arguments(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    if args.command not in {"run", "reconcile", "verify"}:
        return
    if args.target_config is not None:
        if args.target_id is not None:
            parser.error("--target-id cannot be used with --target-config")
        return
    if args.target_id is None:
        parser.error("--target-id is required with --target-root")


def _run_config_mode(args: argparse.Namespace):
    selection = load_target_config(args.target_config)
    if args.command == "run":
        return core_worker.run(
            args.database,
            args.spool_root,
            selection.target,
            selection.target_id,
        )
    if args.command == "reconcile":
        return core_worker.reconcile(
            args.database,
            args.spool_root,
            selection.target,
            selection.target_id,
        )
    return core_worker.verify(
        args.database,
        selection.target,
        selection.target_id,
    )


def _run_legacy_mode(args: argparse.Namespace):
    if args.command == "run":
        return legacy_worker.run(
            args.database,
            args.spool_root,
            args.target_root,
            args.target_id,
        )
    if args.command == "reconcile":
        return legacy_worker.reconcile(
            args.database,
            args.spool_root,
            args.target_root,
            args.target_id,
        )
    return legacy_worker.verify(
        args.database,
        args.target_root,
        args.target_id,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_target_arguments(parser, args)

    if args.command not in {"run", "reconcile", "verify"}:
        parser.print_help()
        return 0

    try:
        if args.target_config is not None:
            summary = _run_config_mode(args)
        else:
            summary = _run_legacy_mode(args)
    except ReplicationError as exc:
        _emit(
            {"status": "error", "finding": exc.finding},
            error=True,
        )
        return exc.code

    _emit(summary.public())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
