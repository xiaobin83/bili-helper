"""Core recommendation engine for watch-later-recommender.

Provides all pipeline phases except the actual LLM API call
(which is handled by the agent orchestrator via task()).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from watch_later_recommender.api_client import BiliAPIClient
from watch_later_recommender.models import Folder, PrefsConfig, RecommendationResult, VideoItem

logger = logging.getLogger(__name__)

MAX_LLM_CANDIDATES = 20
TOVIEW_CAPACITY_LIMIT = 100
TOVIEW_WARN_THRESHOLD = 95
MAX_FOLDERS_IN_PROMPT = 30

RECOMMENDATION_PROMPT_TEMPLATE = """你是一个B站视频推荐助手。请根据用户的偏好配置，从以下候选视频中精选{count}个推荐给用户。

## 用户偏好
{preferences_text}

{folders_section}
{topic_section}## 推荐要求
1. {surprise_text}的视频应为"惊喜内容"（来自用户偏好分区之外的视频）
2. 每个推荐请给出具体理由（结合用户偏好和视频内容）
3. 拒绝广告/推广内容（rcmd_reason含"广告"、"推广"等关键词的不要选）

## 候选视频列表
{candidates_text}

## 输出格式
请严格按以下JSON格式输出，不要包含其他文字：
{output_format}
务必输出{count}个bvid和{count}个理由，数量必须一致。"""

TOVIEW_OUTPUT_FORMAT = """{{"bvids": ["BV...", "BV...", ...], "reasons": ["理由1", "理由2", ...], "surprise_count": N}}"""

FAV_OUTPUT_FORMAT = """{{"bvids": ["BV...", "BV...", ...], "reasons": ["理由1", "理由2", ...], "surprise_count": N, "target_action": "add_to_existing"|"create_new", "target_folder": "收藏夹名称", "folder_description": "新建收藏夹的简介（仅create_new时需要）"}}"""


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


async def search_candidates(
    client: BiliAPIClient,
    topic: str,
    max_pages: int = 2,
) -> tuple[list[VideoItem], dict[str, int]]:
    """Phases 1-4 (search variant): Search candidates by topic keyword.

    Replaces popular/ranking/rcmd when --topic is provided.
    Fetches multiple pages of search results, deduplicates, filters.

    Args:
        client: Authenticated BiliAPIClient.
        topic: Search keyword / topic.
        max_pages: Number of search result pages to fetch (default 2, ~40 videos).

    Returns:
        Tuple of (deduped_candidates, counts_dict).
        counts_dict keys: search_total, before_dedup, after_dedup, ads_removed, candidates.
    """
    counts: dict[str, int] = {"search_total": 0, "before_dedup": 0, "after_dedup": 0, "ads_removed": 0, "candidates": 0}
    combined: list[VideoItem] = []

    for page in range(1, max_pages + 1):
        items = await client.search_videos(topic, page=page)
        if page == 1:
            counts["search_total"] = len(items)
        combined.extend(items)

    counts["before_dedup"] = len(combined)

    # Dedup by bvid
    seen: set[str] = set()
    deduped: list[VideoItem] = []
    for v in combined:
        if v.bvid not in seen:
            seen.add(v.bvid)
            deduped.append(v)
    counts["after_dedup"] = len(deduped)

    # Filter ads
    filtered = [v for v in deduped if not v.is_ad()]
    ads_removed = len(deduped) - len(filtered)
    counts["ads_removed"] = ads_removed

    # Cap at MAX_LLM_CANDIDATES (sort by view descending first)
    filtered.sort(key=lambda v: v.view, reverse=True)
    candidates = filtered[:MAX_LLM_CANDIDATES]
    counts["candidates"] = len(candidates)

    return candidates, counts


async def fetch_folders(client: BiliAPIClient, up_mid: int) -> list[Folder]:
    """Fetch user's favorites folders sorted by media_count descending."""
    folders = await client.list_fav_folders(up_mid)
    folders.sort(key=lambda f: f.media_count, reverse=True)
    return folders[:MAX_FOLDERS_IN_PROMPT]


def build_llm_prompt(
    candidates: list[VideoItem],
    prefs: PrefsConfig | None = None,
    count: int = 5,
    target: str = "toview",
    folders: list[Folder] | None = None,
    topic: str = "",
) -> str:
    """Build the LLM recommendation prompt.

    Args:
        candidates: Deduplicated, filtered candidate list.
        prefs: User preference config.
        count: Number of videos to recommend (default 5, max 10).
        target: "toview" or "fav".
        folders: User's favorites folders (for target="fav").
        topic: Optional topic keyword to amplify.

    Returns:
        Formatted prompt string ready for LLM consumption.
    """
    prefs = prefs or PrefsConfig()

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

    # Folders section (for fav target)
    folders_section = ""
    if target == "fav" and folders:
        folder_lines = [f"- {f.title} ({f.media_count} 个视频)" for f in folders]
        folders_section = (
            "## 用户收藏夹\n"
            "用户有以下收藏夹可供选择（名称 | 现有视频数）：\n"
            + "\n".join(folder_lines)
            + "\n\n"
        )

    # Topic section
    topic_section = ""
    if topic:
        topic_section = f"## 本次推荐主题\n用户本次特别关注: \"{topic}\"\n请优先从候选视频中筛选与\"{topic}\"相关的内容。\n\n"

    # Candidate list
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

    output_format = FAV_OUTPUT_FORMAT if target == "fav" else TOVIEW_OUTPUT_FORMAT

    return RECOMMENDATION_PROMPT_TEMPLATE.format(
        count=count,
        preferences_text="\n".join(pref_lines),
        folders_section=folders_section,
        topic_section=topic_section,
        surprise_text=surprise_text,
        candidates_text="\n".join(candidate_lines),
        output_format=output_format,
    )


def parse_llm_result(
    llm_text: str,
    candidates: list[VideoItem],
    count: int = 5,
) -> RecommendationResult | None:
    """Phase 5: Parse and validate LLM JSON response.

    Args:
        llm_text: Raw text response from LLM (expected to contain JSON).
        candidates: Full candidate list (used for lookups).
        count: Expected number of recommendations (default 5, max 10).

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
    if len(valid_bvids) < min(count, 5):
        logger.warning(
            "parse_llm_result: only %d/%d bvids valid in candidate pool",
            len(valid_bvids), len(result.bvids),
        )
        return None

    return RecommendationResult(
        bvids=valid_bvids[:count],
        reasons=result.reasons[:count],
        surprise_count=result.surprise_count,
    )


def fallback_selection(candidates: list[VideoItem], count: int = 5) -> RecommendationResult:
    """Fallback: select top N videos by view count when LLM fails.

    Args:
        candidates: List of VideoItem candidates.
        count: Number of videos to select (default 5, max 10).
    """
    top = sorted(candidates, key=lambda v: v.view, reverse=True)[:count]
    return RecommendationResult(
        bvids=[v.bvid for v in top],
        reasons=[f"热门备选: {v.title[:40]}" for v in top],
        surprise_count=0,
    )


def determine_fav_target(
    selected_bvids: list[str],
    candidates: list[VideoItem],
    folders: list[Folder],
    prefs: PrefsConfig,
) -> tuple[str, str, str]:
    """Fallback: determine target folder from selected videos.

    Stats partition distribution, matches against pref categories,
    then looks for an existing folder whose name contains the
    matching category name. Falls back to creating a new folder.

    Returns:
        Tuple of (action, folder_name, folder_description).
        action is "add_to_existing" or "create_new".
    """
    lookup = {v.bvid: v for v in candidates}
    selected = [lookup[b] for b in selected_bvids if b in lookup]

    tid_counts: dict[int, int] = {}
    for v in selected:
        tid_counts[v.tid] = tid_counts.get(v.tid, 0) + 1

    if not tid_counts:
        return "add_to_existing", "默认收藏夹", ""

    dominant_tid = max(tid_counts, key=tid_counts.get)  # type: ignore[arg-type]

    match_name = ""
    for cat in prefs.categories:
        if dominant_tid in cat.tids:
            match_name = cat.name
            break

    if not match_name:
        match_name = selected[0].tname if selected else "默认收藏夹"

    for f in folders:
        if match_name in f.title or f.title in match_name:
            return "add_to_existing", f.title, ""

    new_name = f"{match_name}精选"
    new_desc = f"由智能推荐自动创建的{match_name}精选收藏夹"
    return "create_new", new_name, new_desc


async def add_recommendations(
    client: BiliAPIClient,
    recommendations: list[dict[str, Any]],
    target: str = "toview",
    toview_count: int = 0,
    target_folder: str = "",
    target_action: str = "add_to_existing",
    folders: list[Folder] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Phase 6: Add recommendations to target (toview or favorites folder).

    Args:
        client: Authenticated BiliAPIClient.
        recommendations: List of dicts with bvid, aid, title, reason.
        target: "toview" or "fav".
        toview_count: Current items in watch-later.
        target_folder: Name of target folder (for target="fav").
        target_action: "add_to_existing" or "create_new".
        folders: Full folder list (for media_id lookup by name).
        dry_run: When True, skip actual API calls.

    Returns:
        Dict with keys: success, added, failed, message.
    """
    result: dict[str, Any] = {
        "success": False, "added": 0, "failed": [], "message": "",
    }

    if dry_run:
        result["success"] = True
        msg = f"干跑模式完成，推荐了 {len(recommendations)} 个视频"
        if target == "fav":
            msg += f"，目标收藏夹: {target_folder}"
        result["message"] = msg
        return result

    if target == "toview":
        return await _add_to_toview(client, recommendations, toview_count)
    else:
        return await _add_to_fav(client, recommendations, target_action, target_folder, folders)


async def _add_to_toview(
    client: BiliAPIClient,
    recommendations: list[dict[str, Any]],
    toview_count: int,
) -> dict[str, Any]:
    """Add recommendations to watch-later list."""
    result: dict[str, Any] = {
        "success": False, "added": 0, "failed": [], "message": "",
    }

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


async def _add_to_fav(
    client: BiliAPIClient,
    recommendations: list[dict[str, Any]],
    target_action: str,
    target_folder: str,
    folders: list[Folder] | None,
) -> dict[str, Any]:
    """Add recommendations to a favorites folder (create new if needed)."""
    result: dict[str, Any] = {
        "success": False, "added": 0, "failed": [], "message": "",
    }

    media_id: int | None = None
    if target_action == "create_new":
        resp = await client.create_fav_folder(
            name=target_folder,
            intro=f"由智能推荐自动创建的收藏夹: {target_folder}",
        )
        if resp.get("code") != 0:
            result["message"] = f"创建收藏夹失败: {resp.get('message', '')}"
            return result
        data = resp.get("data") or {}
        media_id = data.get("media_id")
        if not media_id:
            result["message"] = "创建收藏夹后未获取到 media_id"
            return result
        result["message"] = f"已创建新收藏夹「{target_folder}」"
    else:
        if folders:
            for f in folders:
                if f.title == target_folder:
                    media_id = f.id
                    break
        if not media_id:
            result["message"] = f"未找到名为「{target_folder}」的收藏夹"
            return result

    for rec in recommendations:
        resp = await client.add_to_fav_folder(rec["aid"], [media_id])
        if resp.get("code") == 0:
            rec["status"] = "added"
            result["added"] += 1
        else:
            rec["status"] = "failed"
            result["failed"].append(rec["bvid"])

    result["success"] = result["added"] > 0
    suffix = f"到收藏夹「{target_folder}」"
    parts = [f"已将 {result['added']} 个视频添加{suffix}"]
    if result["failed"]:
        parts.append(f"，{len(result['failed'])} 个失败")
    result["message"] = "".join(parts)
    return result
