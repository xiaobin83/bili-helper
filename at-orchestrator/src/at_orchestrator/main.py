"""AT Orchestrator CLI - Bilibili up主 AT（@）互动编排工具."""

from __future__ import annotations

import argparse
import sys

from at_orchestrator import __version__


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="at-orchestrator",
        description="Bilibili up主 AT（@）互动编排工具",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"at-orchestrator {__version__}",
    )
    parser.add_argument(
        "--db-path",
        default=".at-orchestrator/tasks.db",
        help="SQLite database path (default: .at-orchestrator/tasks.db)",
    )
    parser.add_argument(
        "--auth-file",
        default=None,
        help="Path to Bilibili credentials JSON file",
    )
    parser.add_argument(
        "--env-prefix",
        default="BILI_",
        help="Environment variable prefix for credentials (default: BILI_)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch new @ messages from Bilibili")
    p_fetch.set_defaults(_handler="fetch")

    # process
    p_process = sub.add_parser("process", help="Process pending AT tasks")
    p_process.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of tasks to process (default: 1)",
    )
    p_process.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without executing",
    )
    p_process.add_argument(
        "--apply-llm-result",
        type=str,
        default=None,
        metavar="FILE",
        help="Apply LLM classification result from file",
    )
    p_process.set_defaults(_handler="process")

    # status
    p_status = sub.add_parser("status", help="Show task status counts")
    p_status.set_defaults(_handler="status")

    # reset
    p_reset = sub.add_parser("reset", help="Reset database (drop and recreate tables)")
    p_reset.add_argument(
        "--force",
        action="store_true",
        required=True,
        help="Confirm database reset (required)",
    )
    p_reset.set_defaults(_handler="reset")

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "_handler", None)

    if handler == "fetch":
        print(f"fetch: not yet implemented")
    elif handler == "process":
        print(
            f"process: limit={args.limit}, "
            f"dry_run={args.dry_run}, "
            f"apply_llm={args.apply_llm_result}"
        )
    elif handler == "status":
        print(f"status: not yet implemented")
    elif handler == "reset":
        print(f"reset: not yet implemented")


if __name__ == "__main__":
    main()
