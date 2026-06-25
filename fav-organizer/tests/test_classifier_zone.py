"""Tests for zone classifier — classify_by_zone().

Verifies:
- Known tids map to correct 20 main zone names.
- Non-video content (type != 2) → "其他".
- Unknown tids → "未分类".
- BVIDs are cached — get_video_info called at most once per BVID.
- API errors are caught and result in "未分类".
- Edge cases: empty BVID, missing tid, empty item list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from src.classifier_zone import ZONE_MAP, classify_by_zone
from src.models import ClassificationResult, FavoritedItem


# ---------------------------------------------------------------------------
# Fake video API — returns pre-configured tid maps
# ---------------------------------------------------------------------------


@dataclass
class FakeVideoAPI:
    """Drop-in fake for VideoInfoProvider that returns hard-coded tid data.

    Tracks every ``get_video_info`` call so tests can assert on caching.
    """

    tid_map: dict[str, int] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def get_video_info(self, bvid: str) -> dict:
        self.calls.append(bvid)
        tid = self.tid_map.get(bvid, 0)
        return {"bvid": bvid, "tid": tid, "tname": ZONE_MAP.get(tid, "")}


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_api() -> FakeVideoAPI:
    return FakeVideoAPI()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_video_item(bvid: str = "BV1xx411c7mD") -> FavoritedItem:
    """Return a minimal video FavoritedItem (type=2)."""
    return FavoritedItem(
        id=1,
        type=2,
        title="测试视频",
        bvid=bvid,
        upper_name="UP主",
        upper_mid=100,
        attr=0,
        fav_time=1234567890,
    )


def _make_non_video_item(content_type: int = 12) -> FavoritedItem:
    """Return a non-video FavoritedItem (e.g. audio type=12)."""
    return FavoritedItem(
        id=2,
        type=content_type,
        title="音频内容",
        bvid="",
        upper_name="UP主",
        upper_mid=100,
        attr=0,
        fav_time=1234567890,
    )


# ===================================================================
# Zone mapping — known tids
# ===================================================================


@pytest.mark.asyncio
async def test_classify_maps_tid_4_to_music(fake_api: FakeVideoAPI):
    """tid=4 → "音乐"."""
    fake_api.tid_map = {"BV1xx411c7mD": 4}
    item = _make_video_item("BV1xx411c7mD")

    results = await classify_by_zone([item], fake_api)

    assert len(results) == 1
    assert results[0].category == "音乐"
    assert results[0].target_folder_title == "音乐区"


@pytest.mark.asyncio
async def test_classify_maps_tid_8_to_tech(fake_api: FakeVideoAPI):
    """tid=8 → "科技"."""
    fake_api.tid_map = {"BV1xx411c7mD": 8}
    item = _make_video_item("BV1xx411c7mD")

    results = await classify_by_zone([item], fake_api)

    assert results[0].category == "科技"
    assert results[0].target_folder_title == "科技区"


@pytest.mark.asyncio
async def test_classify_maps_tid_17_to_movie_tv(fake_api: FakeVideoAPI):
    """tid=17 → "影视"."""
    fake_api.tid_map = {"BV1xx411c7mD": 17}
    item = _make_video_item("BV1xx411c7mD")

    results = await classify_by_zone([item], fake_api)

    assert results[0].category == "影视"


@pytest.mark.asyncio
async def test_classify_maps_tid_23_to_movie(fake_api: FakeVideoAPI):
    """tid=23 → "电影"."""
    fake_api.tid_map = {"BV1xx411c7mD": 23}
    item = _make_video_item("BV1xx411c7mD")

    results = await classify_by_zone([item], fake_api)

    assert results[0].category == "电影"


@pytest.mark.asyncio
async def test_classify_all_20_zones(fake_api: FakeVideoAPI):
    """Every tid in ZONE_MAP maps to the expected name."""
    bvids = [f"BV1xx411c7m{i:02d}" for i in range(len(ZONE_MAP) + 20)]
    items: list[FavoritedItem] = []
    expected: list[str] = []

    for idx, (tid, name) in enumerate(sorted(ZONE_MAP.items())):
        bvid = bvids[idx]
        items.append(_make_video_item(bvid))
        expected.append(name)
        fake_api.tid_map[bvid] = tid

    results = await classify_by_zone(items, fake_api)

    assert len(results) == 20
    for r, exp in zip(results, expected):
        assert r.category == exp, f"tid mapping failed: expected {exp}, got {r.category}"
        assert r.target_folder_title == f"{exp}区"


# ===================================================================
# Non-video content → "其他"
# ===================================================================


@pytest.mark.asyncio
async def test_audio_type_12_classified_as_other(fake_api: FakeVideoAPI):
    """Content with type=12 (audio) → "其他"."""
    item = _make_non_video_item(12)

    results = await classify_by_zone([item], fake_api)

    assert results[0].category == "其他"
    assert results[0].target_folder_title == "其他区"


@pytest.mark.asyncio
async def test_collection_type_21_classified_as_other(fake_api: FakeVideoAPI):
    """Content with type=21 (collection) → "其他"."""
    item = _make_non_video_item(21)

    results = await classify_by_zone([item], fake_api)

    assert results[0].category == "其他"


@pytest.mark.asyncio
async def test_video_without_bvid_classified_as_other(fake_api: FakeVideoAPI):
    """A type=2 item with an empty bvid → "其他" (no API call)."""
    item = FavoritedItem(
        id=3,
        type=2,
        title="失效视频",
        bvid="",  # empty
        upper_name="UP",
        upper_mid=100,
        attr=0,
        fav_time=0,
    )

    results = await classify_by_zone([item], fake_api)

    assert results[0].category == "其他"
    # Should NOT have called the API for an empty bvid
    assert len(fake_api.calls) == 0


@pytest.mark.asyncio
async def test_mixed_video_and_non_video(fake_api: FakeVideoAPI):
    """Half video, half non-video — each classified correctly."""
    fake_api.tid_map = {"BV_video": 8}
    video = _make_video_item("BV_video")
    audio = _make_non_video_item(12)
    collection = _make_non_video_item(21)

    results = await classify_by_zone([video, audio, collection], fake_api)

    assert results[0].category == "科技"
    assert results[1].category == "其他"
    assert results[2].category == "其他"


# ===================================================================
# Unknown tid → "未分类"
# ===================================================================


@pytest.mark.asyncio
async def test_unknown_tid_maps_to_uncategorized(fake_api: FakeVideoAPI):
    """A tid not in ZONE_MAP → "未分类"."""
    fake_api.tid_map = {"BV1xx411c7mD": 999}  # unknown
    item = _make_video_item("BV1xx411c7mD")

    results = await classify_by_zone([item], fake_api)

    assert results[0].category == "未分类"
    assert results[0].target_folder_title == "未分类区"


@pytest.mark.asyncio
async def test_missing_tid_field_maps_to_uncategorized(fake_api: FakeVideoAPI):
    """API returns no 'tid' key → defaults to 0 → "未分类"."""
    fake_api.tid_map = {}  # get_video_info returns tid=0 via default

    async def _no_tid(bvid: str) -> dict:
        fake_api.calls.append(bvid)
        return {"bvid": bvid}  # no "tid" key

    fake_api.get_video_info = _no_tid  # type: ignore[assignment]
    item = _make_video_item("BV1xx411c7mD")

    results = await classify_by_zone([item], fake_api)

    assert results[0].category == "未分类"


# ===================================================================
# BVID caching — only one API call per BVID
# ===================================================================


@pytest.mark.asyncio
async def test_same_bvid_called_only_once(fake_api: FakeVideoAPI):
    """Two items with the same BVID → only one get_video_info call."""
    fake_api.tid_map = {"BVdup": 4}
    item1 = _make_video_item("BVdup")
    item2 = _make_video_item("BVdup")

    results = await classify_by_zone([item1, item2], fake_api)

    # Both should get the same category
    assert results[0].category == "音乐"
    assert results[1].category == "音乐"
    # But the fake API should only have been consulted once
    assert fake_api.calls == ["BVdup"]


@pytest.mark.asyncio
async def test_distinct_bvids_each_called_once(fake_api: FakeVideoAPI):
    """Distinct BVIDs → one call each."""
    fake_api.tid_map = {"BV_alpha": 6, "BV_beta": 8}
    item1 = _make_video_item("BV_alpha")
    item2 = _make_video_item("BV_beta")

    results = await classify_by_zone([item1, item2], fake_api)

    assert results[0].category == "游戏"
    assert results[1].category == "科技"
    assert set(fake_api.calls) == {"BV_alpha", "BV_beta"}
    assert len(fake_api.calls) == 2


# ===================================================================
# API error handling
# ===================================================================


@pytest.mark.asyncio
async def test_api_exception_yields_uncategorized(fake_api: FakeVideoAPI):
    """If get_video_info raises → "未分类" (no crash)."""

    async def _raise(_bvid: str) -> dict:
        fake_api.calls.append("failed")
        raise RuntimeError("network error")

    fake_api.get_video_info = _raise  # type: ignore[assignment]
    item = _make_video_item("BV_broken")

    results = await classify_by_zone([item], fake_api)

    assert results[0].category == "未分类"
    assert len(fake_api.calls) == 1


@pytest.mark.asyncio
async def test_api_exception_does_not_affect_other_items(fake_api: FakeVideoAPI):
    """A failing BVID is isolated — other items classify normally."""

    call_order: list[str] = []

    async def _erratic(bvid: str) -> dict:
        call_order.append(bvid)
        if bvid == "BV_bad":
            raise RuntimeError("fail")
        return {"bvid": bvid, "tid": fake_api.tid_map.get(bvid, 0)}

    fake_api.get_video_info = _erratic  # type: ignore[assignment]
    fake_api.tid_map = {"BV_good": 8}
    bad = _make_video_item("BV_bad")
    good = _make_video_item("BV_good")

    results = await classify_by_zone([bad, good], fake_api)

    assert results[0].category == "未分类"
    assert results[1].category == "科技"


# ===================================================================
# Edge cases
# ===================================================================


@pytest.mark.asyncio
async def test_empty_item_list(fake_api: FakeVideoAPI):
    """Zero items → empty result list, no API calls."""
    results = await classify_by_zone([], fake_api)

    assert results == []
    assert len(fake_api.calls) == 0


@pytest.mark.asyncio
async def test_result_type_is_classification_result(fake_api: FakeVideoAPI):
    """Every result is a fully-formed ClassificationResult."""
    fake_api.tid_map = {"BV1xx411c7mD": 4}
    item = _make_video_item("BV1xx411c7mD")

    results = await classify_by_zone([item], fake_api)

    assert isinstance(results[0], ClassificationResult)
    assert results[0].item is item
    assert results[0].target_folder_exists is False  # default
