"""JSON template support for dyn-publisher.

Supports two template types:
  - text:  {"type": "text", "text": "content"}
  - image: {"type": "image", "text": "caption", "images": [{"file": "path.png", "category": "daily"}]}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TemplateError(Exception):
    """Raised when a template is invalid."""

    pass


def load_template(path: str) -> dict[str, Any]:
    """Load and parse a JSON template file.

    Args:
        path: Path to JSON template file.

    Returns:
        Parsed template dict.

    Raises:
        TemplateError: If file not found or invalid JSON.
    """
    filepath = Path(path)
    if not filepath.exists():
        raise TemplateError(f"Template file not found: {path}")
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise TemplateError(f"Invalid JSON in template: {e}")


def validate_template(data: dict[str, Any]) -> None:
    """Validate a template dict.

    Checks:
    - Must have ``type`` field (``"text"`` or ``"image"``)
    - Must have ``text`` field (non-empty)
    - For ``"image"`` type, must have non-empty ``images`` list
    - Each image entry must have a ``file`` field

    Raises:
        TemplateError: If validation fails.
    """
    if "type" not in data:
        raise TemplateError("Template missing required field: 'type'")

    dtype = data["type"]
    if dtype not in ("text", "image"):
        raise TemplateError(f"Invalid type '{dtype}': must be 'text' or 'image'")

    if "text" not in data or not data["text"]:
        raise TemplateError("Template missing required field: 'text' (non-empty)")

    if dtype == "image":
        images = data.get("images", [])
        if not images:
            raise TemplateError("Image template requires non-empty 'images' list")
        for i, img in enumerate(images):
            if "file" not in img:
                raise TemplateError(f"Image {i} missing required field: 'file'")
