"""Tests for dyn-publisher API client."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dyn_publisher.api import DynPublisherAPI


@pytest.fixture
def api():
    return DynPublisherAPI("test_sess", "test_jct", buvid3="test_buv", min_interval=0.1)


@pytest.mark.asyncio
async def test_publish_text_calls_post(api):
    """publish_text should call post with correct endpoint and params."""
    api._client.post = AsyncMock(return_value={"code": 0, "data": {"dynamic_id_str": "123"}})
    result = await api.publish_text("Hello World")
    assert result["code"] == 0
    api._client.post.assert_awaited_once()
    args, kwargs = api._client.post.await_args
    assert "dynamic_svr" in args[0]
    assert kwargs["data"]["type"] == 4
    assert kwargs["data"]["content"] == "Hello World"


@pytest.mark.asyncio
async def test_upload_image_calls_upload_file(api, tmp_path):
    """upload_image should call upload_file with correct args."""
    api._client.upload_file = AsyncMock(
        return_value={
            "code": 0,
            "data": {
                "image_url": "http://example.com/img.png",
                "image_width": 800,
                "image_height": 600,
            },
        }
    )
    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"fake_image_data")

    result = await api.upload_image(str(img_file), category="daily")
    assert result["code"] == 0
    api._client.upload_file.assert_awaited_once()
    args, kwargs = api._client.upload_file.await_args
    assert "upload_bfs" in args[0]


@pytest.mark.asyncio
async def test_close_called(api):
    """close should delegate to client.close."""
    api._client.close = AsyncMock()
    await api.close()
    api._client.close.assert_awaited_once()
