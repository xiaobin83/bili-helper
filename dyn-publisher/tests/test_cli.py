"""Tests for dyn-publisher CLI argument parsing."""

from __future__ import annotations

from dyn_publisher.main import build_parser


def test_parser_publish_subcommand():
    """Parser should have publish subcommand with --text and --image."""
    parser = build_parser()
    args = parser.parse_args(["publish", "--text", "test"])
    assert args.command == "publish"
    assert args.text == "test"
    assert args.image is None


def test_parser_upload_image_subcommand():
    """Parser should have upload-image subcommand with --file and --category."""
    parser = build_parser()
    args = parser.parse_args(["upload-image", "--file", "img.png", "--category", "draw"])
    assert args.command == "upload-image"
    assert args.file == "img.png"
    assert args.category == "draw"


def test_parser_publish_with_template():
    """Parser should accept --template flag."""
    parser = build_parser()
    args = parser.parse_args(["publish", "--template", "template.json"])
    assert args.command == "publish"
    assert args.template == "template.json"
