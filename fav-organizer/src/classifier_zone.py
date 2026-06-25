"""Zone-based classification for Bilibili favorites organiser.

Maps a video's ``tid`` (type-id / partition id) to one of the 20 main Bilibili
zone names.  Non-video content is classified as ``"其他"`` and unknown tids as
``"未分类"``.

Usage::

    results = await classify_by_zone(items, video_api)
    for r in results:
        print(f"{r.item.title} → {r.category}")
"""

from __future__ import annotations

from typing import Protocol

from .models import ClassificationResult, FavoritedItem

# ---------------------------------------------------------------------------
# 20 main Bilibili partition names
# ---------------------------------------------------------------------------

ZONE_MAP: dict[int, str] = {
    1: "动画",
    2: "番剧",
    3: "国创",
    4: "音乐",
    5: "舞蹈",
    6: "游戏",
    7: "知识",
    8: "科技",
    9: "运动",
    10: "汽车",
    11: "生活",
    12: "美食",
    13: "动物圈",
    14: "鬼畜",
    15: "时尚",
    16: "娱乐",
    17: "影视",
    18: "纪录片",
    23: "电影",
    24: "电视剧",
}


# ---------------------------------------------------------------------------
# Video info provider protocol (structural typing — easy to mock in tests)
# ---------------------------------------------------------------------------


class VideoInfoProvider(Protocol):
    """Structural interface that any video-info service must satisfy.

    Only ``get_video_info(bvid) -> dict`` is required.  Implementations
    may fetch from the network (``VideoInfoAPI``) or return hard-coded
    data in tests.
    """

    async def get_video_info(self, bvid: str) -> dict: ...


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def classify_by_zone(
    items: list[FavoritedItem],
    video_api: VideoInfoProvider,
) -> list[ClassificationResult]:
    """Classify every item into a Bilibili main zone.

    Parameters
    ----------
    items:
        The favorited items to classify.
    video_api:
        Any object exposing ``get_video_info(bvid) -> dict``.  For video
        items (``type == 2``) this is called to obtain the ``tid`` field
        from the API response.

    Returns
    -------
    A ``ClassificationResult`` per item.  Each result includes:

    * ``category`` — one of the 20 zone names, ``"其他"``, or ``"未分类"``.
    * ``target_folder_title`` — ``"{category}区"``, e.g. ``"科技区"``.
    """
    results: list[ClassificationResult] = []
    total = len(items)
    seen_bvids: dict[str, str] = {}  # bvid → zone name (local cache per batch)

    for idx, item in enumerate(items):
        if item.type == 2 and item.bvid:
            if item.bvid in seen_bvids:
                category = seen_bvids[item.bvid]
            else:
                try:
                    info = await video_api.get_video_info(item.bvid)
                    tid: int = info.get("tid", 0)
                    category = ZONE_MAP.get(tid, "未分类")
                except Exception:
                    category = "未分类"
                seen_bvids[item.bvid] = category
        else:
            category = "其他"

        results.append(
            ClassificationResult(
                item=item,
                category=category,
                target_folder_title=f"{category}区",
            )
        )

        print(f"正在获取视频分区信息... {idx + 1}/{total}")

    return results
