"""AT Orchestrator CLI - Bilibili up主 AT（@）互动编排工具."""

from __future__ import annotations

import argparse
import sys

from at_orchestrator import __version__


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="at-orchestrator",
        description="Bilibili up主 AT（@）互动编排工具",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"at-orchestrator {__version__}",
    )
    parser.parse_args(argv)
    print(f"at-orchestrator {__version__}")


if __name__ == "__main__":
    main()
