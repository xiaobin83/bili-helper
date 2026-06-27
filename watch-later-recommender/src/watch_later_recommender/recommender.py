"""Core recommendation engine for watch-later-recommender.

Provides all pipeline phases except the actual LLM API call
(which is handled by the agent orchestrator via task()).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from watch_later_recommender.api_client import BiliAPIClient
from watch_later_recommender.models import PrefsConfig, RecommendationResult, VideoItem

logger = logging.getLogger(__name__)

MAX_LLM_CANDIDATES = 20
TOVIEW_CAPACITY_LIMIT = 100
TOVIEW_WARN_THRESHOLD = 95

RECOMMENDATION_PROMPT_TEMPLATE = """你是一个B站视频推荐助手。请根据用户的偏好配置，从以下候选视频中精选{count}个推荐给用户。

## 用户偏好
{preferences_text}

## 推荐要求
1. {surprise_text}的视频应为"惊喜内容"（来自用户偏好分区之外的视频）
2. 每个推荐请给出具体理由（结合用户偏好和视频内容）
3. 拒绝广告/推广内容（rcmd_reason含"广告"、"推广"等关键词的不要选）

## 候选视频列表
{candidates_text}

## 输出格式
请严格按以下JSON格式输出，不要包含其他文字：
{{"bvids": ["BV...", "BV...", ...], "reasons": ["理由1", "理由2", ...], "surprise_count": N}}
务必输出{count}个bvid和{count}个理由，数量必须一致。"""


async def fetch_candidates(
    client: BiliAPIClient,
) -> tuple[list[VideoItem], dict[str, int]]:
    """Phases 1-4: Fetch candidates from 3 sources, normalize, dedup, filter.

    Args:
        client: BiliAPIClient (may be authenticated or anonymous).

    Returns:
        Tuple of (candidates list, counts dict with per-step stats).
    """
    counts: dict[str, int] = {}

    popular_videos = await client.fetch_popular(ps=50)
    ranking_videos = await client.fetch_ranking()
    rcmd_videos = await client.fetch_rcmd()

    counts["popular"] = len(popular_videos)
    counts["ranking"] = len(ranking_videos)
    counts["rcmd"] = len(rcmd_videos)

    # Combine + deduplicate by bvid
    all_videos = popular_videos + ranking_videos + rcmd_videos
    total_before = len(all_videos)
    seen_bvids: set[str] = set()
    deduped: list[VideoItem] = []
    for v in all_videos:
        if v.bvid and v.bvid not in seen_bvids:
            seen_bvids.add(v.bvid)
            deduped.append(v)
    counts["before_dedup"] = total_before
    counts["after_dedup"] = len(deduped)

    # Filter ads
    before_ad = len(deduped)
    deduped = [v for v in deduped if not v.is_ad()]
    counts["ads_removed"] = before_ad - len(deduped)

    # Filter existing toview items
    toview_list = await client.fetch_toview_list()
    toview_bvids = {str(item.get("bvid", "")) for item in toview_list}
    before_tv = len(deduped)
    deduped = [v for v in deduped if v.bvid not in toview_bvids]
    counts["toview_removed"] = before_tv - len(deduped)
    counts["toview_existing"] = len(toview_list)
    counts["candidates"] = len(deduped)

    return deduped, counts


def build_llm_prompt(candidates: list[VideoItem], prefs: PrefsConfig | None = None) -> str:
    """Phase 5: Build the LLM recommendation prompt.

    Args:
        candidates: Deduplicated, filtered candidate list (capped internally at MAX_LLM_CANDIDATES).
        prefs: User preference config. When None, uses empty defaults.

    Returns:
        Formatted prompt string ready for LLM consumption.
    """
    prefs = prefs or PrefsConfig()

    # Build preferences text
    pref_lines = []
    if prefs.categories:
        for cat in prefs.categories:
            kw = f", 关键词: {cat.keywords}" if cat.keywords else ""
            pref_lines.append(f"- {cat.name} (分区ID: {cat.tids}{kw})")
    if prefs.exclude_categories:
        for cat in prefs.exclude_categories:
            pref_lines.append(f"- 排除: {cat.name} (分区ID: {cat.tids})")
    if prefs.max_duration:
        pref_lines.append(f"- 最大时长: {prefs.max_duration}秒")

    surprise_pct = max(0, min(50, int(prefs.surprise_ratio * 100)))
    surprise_text = f"最多{surprise_pct}%"
    if not pref_lines:
        pref_lines.append("无特定偏好（从所有类型中精选）")
        surprise_text = "0%"

    candidate_batch = candidates[:MAX_LLM_CANDIDATES]
    candidate_lines = []
    for i, v in enumerate(candidate_batch, 1):
        dur = f"{v.duration // 60}分{v.duration % 60}秒" if v.duration else "未知时长"
        candidate_lines.append(
            f"{i}. [{v.bvid}] {v.title[:60]}"
            f" | 分区: {v.tname}"
            f" | UP主: {v.owner_name}"
            f" | 播放: {v.view} 点赞: {v.like}"
            f" | 时长: {dur}"
        )

    return RECOMMENDATION_PROMPT_TEMPLATE.format(
        count=5,
        preferences_text="\n".join(pref_lines),
        surprise_text=surprise_text,
        candidates_text="\n".join(candidate_lines),
    )


def parse_llm_result(
    llm_text: str,
    candidates: list[VideoItem],
) -> RecommendationResult | None:
    """Phase 5: Parse and validate LLM JSON response.

    Args:
        llm_text: Raw text response from LLM (expected to contain JSON).
        candidates: Full candidate list (used for lookups).

    Returns:
        Validated ``RecommendationResult`` on success, ``None`` on failure.
    """
    # Extract JSON from potentially wrapped response
    text = llm_text.strip()
    # Find JSON boundaries
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        logger.warning("parse_llm_result: no JSON found in response")
        return None

    json_str = text[start : end + 1]
    try:
        data = json.loads(json_str)
        result = RecommendationResult.model_validate(data)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("parse_llm_result: JSON parse failed: %s", e)
        return None

    # Validate that bvids exist in candidate pool
    bvid_pool = {v.bvid for v in candidates}
    valid_bvids = [b for b in result.bvids if b in bvid_pool]
    if len(valid_bvids) < 5:
        logger.warning(
            "parse_llm_result: only %d/%d bvids valid in candidate pool",
            len(valid_bvids), len(result.bvids),
        )
        return None

    return RecommendationResult(
        bvids=valid_bvids[:5],
        reasons=result.reasons[:5],
        surprise_count=result.surprise_count,
    )


def fallback_selection(candidates: list[VideoItem]) -> RecommendationResult:
    """Fallback: select top 5 videos by view count when LLM fails."""
    top = sorted(candidates, key=lambda v: v.view, reverse=True)[:5]
    return RecommendationResult(
        bvids=[v.bvid for v in top],
        reasons=[f"热门备选: {v.title[:40]}" for v in top],
        surprise_count=0,
    )


async def add_recommendations(
    client: BiliAPIClient,
    recommendations: list[dict[str, Any]],
    toview_count: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Phase 6: Pre-check capacity and add recommendations to watch-later.

    Args:
        client: Authenticated BiliAPIClient.
        recommendations: List of dicts with bvid, aid, title, reason.
        toview_count: Current number of items in watch-later.
        dry_run: When True, skip actual API calls.

    Returns:
        Dict with keys: success, added, failed, message.
    """
    result: dict[str, Any] = {
        "success": False,
        "added": 0,
        "failed": [],
        "message": "",
    }

    if dry_run:
        result["success"] = True
        result["message"] = f"干跑模式完成，推荐了 {len(recommendations)} 个视频（未实际添加）"
        return result

    if toview_count >= TOVIEW_WARN_THRESHOLD:
        result["message"] = (
            f"稍后再看列表空间不足（{toview_count}/{TOVIEW_CAPACITY_LIMIT}），"
            f"请先清理后再试"
        )
        return result

    for rec in recommendations:
        resp = await client.add_to_toview(rec["aid"])
        if resp.get("code") == 0:
            rec["status"] = "added"
            result["added"] += 1
        elif resp.get("code") == 90001:
            result["message"] = "稍后再看列表已满，部分视频未能添加"
            break
        else:
            rec["status"] = "failed"
            result["failed"].append(rec["bvid"])

    result["success"] = result["added"] > 0
    if not result["message"]:
        parts = [f"已将 {result['added']} 个视频添加到稍后再看"]
        if result["failed"]:
            parts.append(f"，{len(result['failed'])} 个失败")
        result["message"] = "".join(parts)

    return result
