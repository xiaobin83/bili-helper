"""Tests for scan_invalid — the invalid-content scanner for Bilibili favorites.

Verifies that:
- attr=0 items are skipped (valid)
- attr=1 items are detected (deleted, other reason)
- attr=9 items are detected (deleted by UP主)
- "稍后再看" folder is always skipped
- Empty results are handled correctly
- Multiple folders with mixed content work as expected
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.fav_organizer.scanner import scan_invalid
from src.fav_organizer.models import Folder, FavoritedItem


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _folder(id: int, title: str = "默认收藏夹") -> Folder:
    return Folder(id=id, fid=id, mid=1, attr=0, title=title, media_count=0)


def _item(id: int, attr: int = 0, bvid: str = "BV1xx411c7mD") -> FavoritedItem:
    return FavoritedItem(
        id=id,
        type=2,
        title=f"video-{id}",
        bvid=bvid,
        upper_name="uploader",
        upper_mid=1,
        attr=attr,
        fav_time=1000,
    )


# ---------------------------------------------------------------------------
# scan_invalid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_invalid_items_with_attr_1() -> None:
    """items with attr=1 (deleted, other reason) must be returned."""
    folder = _folder(id=100, title="游戏收藏")
    items = [
        _item(id=1, attr=0, bvid="BV1"),
        _item(id=2, attr=1, bvid="BV2"),
        _item(id=3, attr=0, bvid="BV3"),
    ]

    api = AsyncMock()
    api.get_all_contents.return_value = items

    result = await scan_invalid(folders=[folder], fav_api=api)

    assert len(result) == 1
    assert result[0][0].id == 2
    assert result[0][0].attr == 1
    assert result[0][0].bvid == "BV2"
    assert result[0][1] is folder


@pytest.mark.asyncio
async def test_detects_attr_9_as_invalid() -> None:
    """items with attr=9 (deleted by UP主) must be returned."""
    folder = _folder(id=200, title="收藏夹")
    items = [
        _item(id=10, attr=9, bvid="BV_deleted"),
        _item(id=11, attr=0, bvid="BV_valid"),
    ]

    api = AsyncMock()
    api.get_all_contents.return_value = items

    result = await scan_invalid(folders=[folder], fav_api=api)

    assert len(result) == 1
    assert result[0][0].id == 10
    assert result[0][0].attr == 9


@pytest.mark.asyncio
async def test_skips_attr_0_valid_items() -> None:
    """items with attr=0 must NOT appear in the result."""
    folder = _folder(id=300, title="音乐")
    valid_items = [_item(id=i, attr=0) for i in range(10)]

    api = AsyncMock()
    api.get_all_contents.return_value = valid_items

    result = await scan_invalid(folders=[folder], fav_api=api)

    assert result == []


@pytest.mark.asyncio
async def test_skips_watch_later_folder() -> None:
    """The folder titled '稍后再看' must be completely skipped."""
    watch_later = _folder(id=400, title="稍后再看")

    api = AsyncMock()
    # get_all_contents should NOT be called on this folder
    api.get_all_contents.return_value = [_item(id=99, attr=1)]

    result = await scan_invalid(folders=[watch_later], fav_api=api)

    assert result == []
    api.get_all_contents.assert_not_called()


@pytest.mark.asyncio
async def test_returns_empty_with_empty_folders_list() -> None:
    """An empty folder list must yield an empty result."""
    api = AsyncMock()

    result = await scan_invalid(folders=[], fav_api=api)

    assert result == []
    api.get_all_contents.assert_not_called()


@pytest.mark.asyncio
async def test_returns_empty_when_no_invalid_items() -> None:
    """When every folder has only attr=0 items, result must be empty."""
    folders = [_folder(id=1, title="A"), _folder(id=2, title="B")]

    api = AsyncMock()
    api.get_all_contents.return_value = [_item(id=1, attr=0), _item(id=2, attr=0)]

    result = await scan_invalid(folders=folders, fav_api=api)

    assert result == []


@pytest.mark.asyncio
async def test_multiple_folders_with_mixed_content() -> None:
    """Scanning two folders that both have invalid items returns them all."""
    folder_a = _folder(id=500, title="A")
    folder_b = _folder(id=501, title="B")

    folder_a_invalid = _item(id=2, attr=1)
    folder_b_invalid_1 = _item(id=4, attr=9)
    folder_b_invalid_2 = _item(id=6, attr=1)

    api = AsyncMock()
    # Side effect: first call → folder A items, second call → folder B items
    api.get_all_contents.side_effect = [
        [_item(id=1, attr=0), folder_a_invalid, _item(id=3, attr=0)],
        [folder_b_invalid_1, _item(id=5, attr=0), folder_b_invalid_2],
    ]

    result = await scan_invalid(folders=[folder_a, folder_b], fav_api=api)

    assert len(result) == 3
    # From folder A
    assert result[0] == (folder_a_invalid, folder_a)
    # From folder B
    assert result[1] == (folder_b_invalid_1, folder_b)
    assert result[2] == (folder_b_invalid_2, folder_b)


@pytest.mark.asyncio
async def test_folder_with_no_items() -> None:
    """A folder that returns an empty item list should not cause errors."""
    folder = _folder(id=600, title="空收藏夹")

    api = AsyncMock()
    api.get_all_contents.return_value = []

    result = await scan_invalid(folders=[folder], fav_api=api)

    assert result == []
    api.get_all_contents.assert_awaited_once_with(media_id=600)


@pytest.mark.asyncio
async def test_skips_watch_later_but_scans_other_folders() -> None:
    """稍后再看 is skipped; other folders are still scanned normally."""
    watch_later = _folder(id=700, title="稍后再看")
    normal = _folder(id=701, title="正常收藏夹")

    api = AsyncMock()
    api.get_all_contents.side_effect = [
        [_item(id=10, attr=1)],  # folder B only (folder A never called)
    ]

    result = await scan_invalid(folders=[watch_later, normal], fav_api=api)

    assert len(result) == 1
    assert result[0][0].id == 10
    assert result[0][1] is normal
    # Verify get_all_contents was called only for the normal folder
    api.get_all_contents.assert_awaited_once_with(media_id=701)
