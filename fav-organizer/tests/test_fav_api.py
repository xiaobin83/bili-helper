"""Tests for FavAPI — Bilibili favorites API client.

Verifies that:
- GET requests are routed through Wbi signing (sign_params).
- POST requests automatically include csrf=bili_jct.
- Pagination via has_more works correctly (2 pages → 40 items).
- All methods parse API responses into the expected types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.fav_api import FavAPI
from src.models import Folder, FavoritedItem


# ---------------------------------------------------------------------------
# Fake BiliHTTPClient — records every call so we can inspect params
# ---------------------------------------------------------------------------


@dataclass
class FakeHTTPClient:
    """Drop-in fake for BiliHTTPClient that returns pre-configured responses.

    Tracks every ``get`` / ``post`` call so tests can assert on parameters.
    """

    get_response: dict[Any, Any] = field(default_factory=dict)
    post_response: dict[Any, Any] = field(default_factory=dict)
    get_calls: list[dict] = field(default_factory=list)
    post_calls: list[dict] = field(default_factory=list)

    async def get(self, url: str, params: dict | None = None) -> dict:
        self.get_calls.append({"url": url, "params": params or {}})
        return self.get_response

    async def post(self, url: str, data: dict | None = None) -> dict:
        self.post_calls.append({"url": url, "data": data or {}})
        return self.post_response


# ---------------------------------------------------------------------------
# Fake signing — returns params unchanged but appends a sentinel
# ---------------------------------------------------------------------------


def fake_sign(params: dict) -> dict:
    """Toy Wbi-sign function that adds a marker so tests can detect it."""
    return {**params, "_signed": True}


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def http_client() -> FakeHTTPClient:
    return FakeHTTPClient()


@pytest.fixture
def api(http_client: FakeHTTPClient) -> FavAPI:
    return FavAPI(http_client=http_client, bili_jct="test_jct_123", signing=fake_sign)


# ---------------------------------------------------------------------------
# Sample response builders
# ---------------------------------------------------------------------------


def _make_folder_dict(idx: int) -> dict:
    """Return a minimal folder dict for testing."""
    return {
        "id": 1000 + idx,
        "fid": idx,
        "mid": 5000,
        "attr": idx % 10,
        "title": f"folder-{idx}",
        "media_count": idx * 5,
        "fav_state": 0,
    }


def _make_media_dict(idx: int) -> dict:
    """Return a minimal media (FavoritedItem) dict for testing."""
    return {
        "id": 600000 + idx,
        "type": 2,
        "title": f"video-{idx}",
        "cover": "",
        "intro": "",
        "page": 1,
        "duration": 100,
        "upper": {"mid": 7000, "name": f"up-{idx}", "face": ""},
        "attr": 0,
        "cnt_info": {"collect": 0, "play": 0, "danmaku": 0},
        "link": "",
        "ctime": 0,
        "pubtime": 0,
        "fav_time": idx * 100,
        "bv_id": f"BVxxx{idx:03d}",
        "bvid": f"BVxxx{idx:03d}",
        "season": None,
    }


# ===================================================================
# list_all_folders
# ===================================================================


@pytest.mark.asyncio
async def test_list_all_folders_returns_empty_when_data_is_none(api: FavAPI, http_client: FakeHTTPClient):
    http_client.get_response = {"code": 0, "data": None}

    result = await api.list_all_folders(up_mid=123)

    assert result == []


@pytest.mark.asyncio
async def test_list_all_folders_parses_folder_list(api: FavAPI, http_client: FakeHTTPClient):
    folders = [_make_folder_dict(i) for i in range(3)]
    http_client.get_response = {"code": 0, "data": {"count": 3, "list": folders, "season": None}}

    result = await api.list_all_folders(up_mid=123)

    assert len(result) == 3
    for i, folder in enumerate(result):
        assert isinstance(folder, Folder)
        assert folder.title == f"folder-{i}"
        assert folder.id == 1000 + i

    # Verify the GET call had the correct params
    assert len(http_client.get_calls) == 1
    assert http_client.get_calls[0]["params"]["up_mid"] == 123


# ===================================================================
# get_folder_contents
# ===================================================================


@pytest.mark.asyncio
async def test_get_folder_contents_returns_empty_when_no_data(api: FavAPI, http_client: FakeHTTPClient):
    http_client.get_response = {"code": 0, "data": None}

    items, has_more = await api.get_folder_contents(media_id=1001)

    assert items == []
    assert has_more is False


@pytest.mark.asyncio
async def test_get_folder_contents_parses_items_and_has_more(api: FavAPI, http_client: FakeHTTPClient):
    medias = [_make_media_dict(i) for i in range(20)]
    http_client.get_response = {
        "code": 0,
        "data": {
            "info": {},
            "medias": medias,
            "has_more": True,
        },
    }

    items, has_more = await api.get_folder_contents(media_id=1001, page=1, page_size=20)

    assert len(items) == 20
    assert has_more is True
    for i, item in enumerate(items):
        assert isinstance(item, FavoritedItem)
        assert item.title == f"video-{i}"
        assert item.bvid == f"BVxxx{i:03d}"
        assert item.upper_name == f"up-{i}"
        assert item.upper_mid == 7000

    # Verify correct params sent
    call = http_client.get_calls[0]
    assert call["params"]["media_id"] == 1001
    assert call["params"]["pn"] == 1
    assert call["params"]["ps"] == 20


@pytest.mark.asyncio
async def test_get_folder_contents_defaults(api: FavAPI, http_client: FakeHTTPClient):
    """When page/page_size are omitted, defaults should be page=1, ps=20."""
    http_client.get_response = {"code": 0, "data": {"medias": [], "has_more": False}}

    await api.get_folder_contents(media_id=99)

    assert http_client.get_calls[0]["params"]["pn"] == 1
    assert http_client.get_calls[0]["params"]["ps"] == 20


# ===================================================================
# get_all_contents — pagination
# ===================================================================


@pytest.mark.asyncio
async def test_get_all_contents_single_page(api: FavAPI, http_client: FakeHTTPClient):
    """One page: should return exactly those items and stop."""
    medias = [_make_media_dict(i) for i in range(15)]
    http_client.get_response = {
        "code": 0,
        "data": {"medias": medias, "has_more": False},
    }

    result = await api.get_all_contents(media_id=42)

    assert len(result) == 15
    assert len(http_client.get_calls) == 1


@pytest.mark.asyncio
async def test_get_all_contents_two_pages_40_items(api: FavAPI, http_client: FakeHTTPClient):
    """Two pages of 20 items each → 40 items total.  Verifies pagination loop."""
    page1 = [_make_media_dict(i) for i in range(20)]
    page2 = [_make_media_dict(i + 20) for i in range(20)]

    call_count = 0

    async def _staggered_get(_url: str, params: dict | None = None) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"code": 0, "data": {"medias": page1, "has_more": True}}
        return {"code": 0, "data": {"medias": page2, "has_more": False}}

    http_client.get = _staggered_get  # type: ignore[assignment]

    result = await api.get_all_contents(media_id=42)

    assert len(result) == 40
    # Titles span 0-39
    assert result[0].title == "video-0"
    assert result[20].title == "video-20"
    assert result[-1].title == "video-39"


# ===================================================================
# get_all_folder_ids
# ===================================================================


@pytest.mark.asyncio
async def test_get_all_folder_ids_returns_ids(api: FavAPI, http_client: FakeHTTPClient):
    data = [
        {"id": 100, "type": 2, "bv_id": "BV1", "bvid": "BV1"},
        {"id": 200, "type": 12, "bv_id": "", "bvid": ""},
    ]
    http_client.get_response = {"code": 0, "data": data}

    result = await api.get_all_folder_ids(media_id=1001)

    assert result == data
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_all_folder_ids_returns_empty_on_null(api: FavAPI, http_client: FakeHTTPClient):
    http_client.get_response = {"code": 0, "data": None}

    result = await api.get_all_folder_ids(media_id=1001)

    assert result == []


# ===================================================================
# batch_get_info
# ===================================================================


@pytest.mark.asyncio
async def test_batch_get_info_joins_resources(api: FavAPI, http_client: FakeHTTPClient):
    http_client.get_response = {"code": 0, "data": [{"id": 1, "type": 2}]}

    result = await api.batch_get_info(["583785685:2", "523:21", "15664:12"])

    assert len(result) == 1
    # Verify resources are comma-joined in the params
    call = http_client.get_calls[0]
    assert "583785685:2,523:21,15664:12" in call["params"]["resources"]


@pytest.mark.asyncio
async def test_batch_get_info_empty_list(api: FavAPI, http_client: FakeHTTPClient):
    http_client.get_response = {"code": 0, "data": []}

    result = await api.batch_get_info([])

    assert result == []
    assert http_client.get_calls[0]["params"]["resources"] == ""


# ===================================================================
# Wbi signing — GET requests
# ===================================================================


@pytest.mark.asyncio
async def test_wbi_signing_is_applied_to_get_requests(api: FavAPI, http_client: FakeHTTPClient):
    """Every GET request must pass through fake_sign, adding ``_signed``."""
    http_client.get_response = {"code": 0, "data": None}

    await api.list_all_folders(up_mid=1)

    params_sent = http_client.get_calls[0]["params"]
    assert params_sent.get("_signed") is True
    assert params_sent.get("up_mid") == 1  # original param still present


@pytest.mark.asyncio
async def test_wbi_signing_not_applied_to_post_requests(api: FavAPI, http_client: FakeHTTPClient):
    """POST requests must NOT be Wbi-signed; they only get csrf injected."""
    http_client.post_response = {"code": 0}

    await api.create_folder(title="test")

    data_sent = http_client.post_calls[0]["data"]
    assert "_signed" not in data_sent
    assert data_sent["csrf"] == "test_jct_123"


# ===================================================================
# CSRF injection — POST requests
# ===================================================================


@pytest.mark.asyncio
async def test_create_folder_injects_csrf(api: FavAPI, http_client: FakeHTTPClient):
    http_client.post_response = {"code": 0, "data": {"id": 999}}

    await api.create_folder(title="my folder", intro="desc", privacy=1)

    call = http_client.post_calls[0]
    assert call["data"]["csrf"] == "test_jct_123"
    assert call["data"]["title"] == "my folder"
    assert call["data"]["intro"] == "desc"
    assert call["data"]["privacy"] == 1


@pytest.mark.asyncio
async def test_create_folder_defaults(api: FavAPI, http_client: FakeHTTPClient):
    """intro and privacy should default to empty string and 0."""
    http_client.post_response = {"code": 0}

    await api.create_folder(title="test")

    data = http_client.post_calls[0]["data"]
    assert data["intro"] == ""
    assert data["privacy"] == 0
    assert data["csrf"] == "test_jct_123"


@pytest.mark.asyncio
async def test_copy_items_injects_csrf(api: FavAPI, http_client: FakeHTTPClient):
    http_client.post_response = {"code": 0}

    await api.copy_items(
        src_media_id=100,
        tar_media_id=200,
        resources=["111:2", "222:2"],
        mid=999,
    )

    data = http_client.post_calls[0]["data"]
    assert data["csrf"] == "test_jct_123"
    assert data["src_media_id"] == 100
    assert data["tar_media_id"] == 200
    assert data["mid"] == 999
    assert data["resources"] == "111:2,222:2"


@pytest.mark.asyncio
async def test_move_items_injects_csrf(api: FavAPI, http_client: FakeHTTPClient):
    http_client.post_response = {"code": 0}

    await api.move_items(
        src_media_id=100,
        tar_media_id=200,
        resources=["333:2"],
        mid=888,
    )

    data = http_client.post_calls[0]["data"]
    assert data["csrf"] == "test_jct_123"
    assert data["resources"] == "333:2"
    assert data["platform"] == "web"


@pytest.mark.asyncio
async def test_batch_delete_injects_csrf(api: FavAPI, http_client: FakeHTTPClient):
    http_client.post_response = {"code": 0}

    await api.batch_delete(media_id=300, resources=["444:2", "555:12"])

    data = http_client.post_calls[0]["data"]
    assert data["csrf"] == "test_jct_123"
    assert data["media_id"] == 300
    assert data["resources"] == "444:2,555:12"


@pytest.mark.asyncio
async def test_clean_invalid_injects_csrf(api: FavAPI, http_client: FakeHTTPClient):
    http_client.post_response = {"code": 0}

    await api.clean_invalid(media_id=500)

    data = http_client.post_calls[0]["data"]
    assert data["csrf"] == "test_jct_123"
    assert data["media_id"] == 500


# ===================================================================
# Edge cases
# ===================================================================


@pytest.mark.asyncio
async def test_get_folder_contents_handles_null_upper(api: FavAPI, http_client: FakeHTTPClient):
    """If upper is None/null, upper_name should default to '', upper_mid to 0."""
    media = _make_media_dict(0)
    media["upper"] = None
    http_client.get_response = {"code": 0, "data": {"medias": [media], "has_more": False}}

    items, has_more = await api.get_folder_contents(media_id=1)

    assert len(items) == 1
    assert items[0].upper_name == ""
    assert items[0].upper_mid == 0


@pytest.mark.asyncio
async def test_get_folder_contents_handles_missing_fields(api: FavAPI, http_client: FakeHTTPClient):
    """Missing bvid/attr/fav_time should default gracefully."""
    http_client.get_response = {
        "code": 0,
        "data": {
            "medias": [
                {"id": 1, "type": 2, "title": "no-bvid", "upper": {"name": "up", "mid": 1}}
            ],
            "has_more": False,
        },
    }

    items, _ = await api.get_folder_contents(media_id=1)

    assert items[0].bvid == ""
    assert items[0].attr == 0
    assert items[0].fav_time == 0


@pytest.mark.asyncio
async def test_list_all_folders_handles_missing_list_key(api: FavAPI, http_client: FakeHTTPClient):
    """If the API response lacks a 'list' key, return empty list."""
    http_client.get_response = {"code": 0, "data": {"count": 0}}

    result = await api.list_all_folders(up_mid=1)

    assert result == []
