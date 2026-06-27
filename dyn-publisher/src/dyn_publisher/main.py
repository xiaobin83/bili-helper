"""CLI entry point for dyn-publisher — Bilibili dynamic publisher."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from bili_core.auth import get_credentials

from dyn_publisher.api import DynPublisherAPI
from dyn_publisher.template import TemplateError, load_template, validate_template

# ── Footer appended to every published dynamic ─────────────────────

_GITHUB_URL = "https://github.com/xiaobin83/bili-helper"
_PUBLISH_FOOTER = f"\nfrom bili-helper: {_GITHUB_URL}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bilibili Dynamic Publisher — publish text and image dynamics"
    )
    parser.add_argument("--version", action="version", version="0.1.0")

    # Shared auth options (top-level, inherited by all subcommands)
    parser.add_argument(
        "--env-prefix",
        default="BILI_",
        help="Environment variable prefix for auth (default: BILI_)",
    )
    parser.add_argument(
        "--auth-file",
        help="Path to .auth.json file",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── publish subcommand ──────────────────────────────────
    p_publish = subparsers.add_parser("publish", help="Publish a dynamic")
    p_publish.add_argument("--text", help="Dynamic text content")
    p_publish.add_argument(
        "--image", help="Path to image file (for image dynamic)"
    )
    p_publish.add_argument(
        "--category",
        default="daily",
        choices=["daily", "draw", "cos"],
        help="Image category (for image dynamic)",
    )
    p_publish.add_argument(
        "--template", help="JSON template file path"
    )
    p_publish.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print template without publishing",
    )

    # ── upload-image subcommand ─────────────────────────────
    p_upload = subparsers.add_parser(
        "upload-image", help="Upload an image for dynamic"
    )
    p_upload.add_argument(
        "--file", required=True, help="Path to image file"
    )
    p_upload.add_argument(
        "--category",
        required=True,
        choices=["daily", "draw", "cos"],
        help="Image category",
    )

    return parser


def _print_json(data: dict[str, Any]) -> None:
    """Print JSON response to stdout."""
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Get credentials (shared auth args at top level)
    try:
        creds = get_credentials(
            env_prefix=args.env_prefix,
            auth_file=Path(args.auth_file) if args.auth_file else None,
        )
    except Exception as e:
        print(f"Auth error: {e}", file=sys.stderr)
        sys.exit(1)

    # Route to subcommand handler
    if args.command == "publish":
        result = _handle_publish(creds, args)
    elif args.command == "upload-image":
        result = asyncio.run(_upload_image(creds, args))
    else:
        print(f"Error: unknown command {args.command}", file=sys.stderr)
        sys.exit(1)

    # Print result and exit with error code if non-zero
    if result.get("code") != 0:
        _print_json(result)
        sys.exit(1)
    _print_json(result)


def _handle_publish(creds, args) -> dict[str, Any]:
    """Handle the publish subcommand."""
    if args.template:
        try:
            template = load_template(args.template)
            validate_template(template)
        except TemplateError as e:
            print(f"Template error: {e}", file=sys.stderr)
            sys.exit(1)

        if args.dry_run:
            return {"code": 0, "message": "Dry run", "template": template}

        return asyncio.run(_publish_from_template(creds, template))

    if args.text:
        return asyncio.run(_publish_text(creds, args))

    print("Error: --text or --template required for publish", file=sys.stderr)
    sys.exit(1)


async def _publish_text(creds, args) -> dict[str, Any]:
    api = DynPublisherAPI(
        sessdata=creds.sessdata,
        bili_jct=creds.bili_jct,
        buvid3=creds.buvid3,
    )
    try:
        text = args.text + _PUBLISH_FOOTER
        if args.image:
            return await api.publish_image(
                text=text,
                image_paths=args.image,
                category=args.category,
            )
        else:
            return await api.publish_text(content=text)
    finally:
        await api.close()


async def _publish_from_template(creds, template: dict) -> dict[str, Any]:
    api = DynPublisherAPI(
        sessdata=creds.sessdata,
        bili_jct=creds.bili_jct,
        buvid3=creds.buvid3,
    )
    try:
        dtype = template.get("type", "text")
        text = template.get("text", "") + _PUBLISH_FOOTER
        if dtype == "image" and template.get("images"):
            imgs = template["images"]
            paths = [img["file"] for img in imgs]
            cats = [img.get("category", "daily") for img in imgs]
            return await api.publish_image(
                text=text,
                image_paths=paths,
                categories=cats,
            )
        else:
            return await api.publish_text(content=text)
    finally:
        await api.close()


async def _upload_image(creds, args) -> dict[str, Any]:
    api = DynPublisherAPI(
        sessdata=creds.sessdata,
        bili_jct=creds.bili_jct,
        buvid3=creds.buvid3,
    )
    try:
        return await api.upload_image(
            file_path=args.file,
            category=args.category,
        )
    finally:
        await api.close()


if __name__ == "__main__":
    main()
