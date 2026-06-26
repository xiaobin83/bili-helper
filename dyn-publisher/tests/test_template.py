"""Tests for dyn-publisher template module."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from dyn_publisher.template import TemplateError, load_template, validate_template


def test_load_valid_text_template():
    """Loading a valid text template should succeed."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"type": "text", "text": "hello"}, f)
        f.flush()
        result = load_template(f.name)
        assert result["type"] == "text"
        assert result["text"] == "hello"
        os.unlink(f.name)


def test_validate_missing_type_raises():
    """Template without 'type' should raise TemplateError."""
    with pytest.raises(TemplateError, match="type"):
        validate_template({"text": "hello"})


def test_validate_image_without_images_raises():
    """Image template without images should raise TemplateError."""
    with pytest.raises(TemplateError, match="images"):
        validate_template({"type": "image", "text": "caption"})


def test_load_nonexistent_file_raises():
    """Loading a nonexistent file should raise TemplateError."""
    with pytest.raises(TemplateError, match="not found"):
        load_template("/nonexistent/path.json")
