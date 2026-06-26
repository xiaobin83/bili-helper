"""CLI entry point for dyn-publisher."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Bilibili Dynamic Publisher")
    parser.add_argument("--version", action="version", version="0.1.0")
    # Subcommands will be added in Task 12
    args = parser.parse_args()
    print("dyn-publisher CLI scaffolded")


if __name__ == "__main__":
    main()
