"""Markdown report formatter for video analysis results.

Transforms a ``VideoAnalysisResult`` into a structured Markdown string
with up to 6 sections.  Individual sections can be hidden via *skip_flags*.

This module is pure formatting — no I/O, no network calls, no Bili-core.
"""

from __future__ import annotations

import time

from video_analyzer.models import (
    AISummary,
    Comment,
    PBP,
    PlayUrl,
    Screenshot,
    VideoAnalysisResult,
)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_duration(seconds: int) -> str:
    """Convert *seconds* to ``m:ss`` or ``h:mm:ss``."""
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_timestamp(ts: int) -> str:
    """Convert unix timestamp *ts* to ``YYYY-MM-DD HH:mm``."""
    if ts <= 0:
        return "未知"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate *text* to *max_len* characters, appending ``...`` if needed."""
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


# ---------------------------------------------------------------------------
# Section renderers (each returns ``list[str]``)
# ---------------------------------------------------------------------------


def _render_video_detail(result: VideoAnalysisResult) -> list[str]:
    """Section 1 — video metadata (always included)."""
    vd = result.video_detail
    if vd is None:
        return ["## 🎬 视频详情\n", "视频详情数据不可用\n"]

    lines: list[str] = []
    lines.append(f"# {result.bvid} 视频分析报告\n")
    lines.append("## 🎬 视频详情\n")

    lines.append(f"**标题**: {vd.title}")
    owner_name = vd.owner.get("name", "未知")
    owner_mid = vd.owner.get("mid", "")
    lines.append(f"**BVID**: `{vd.bvid}`")
    if owner_mid:
        lines.append(f"**UP主**: [{owner_name}](https://space.bilibili.com/{owner_mid})")
    else:
        lines.append(f"**UP主**: {owner_name}")
    lines.append(f"**分区**: {vd.tname or '未知'}")
    lines.append(f"**时长**: {_format_duration(vd.duration)}")
    lines.append(f"**发布时间**: {_format_timestamp(vd.pubdate)}")
    lines.append("")

    stat = vd.stat
    lines.append(
        f"**播放**: {stat.get('view', 0):,} "
        f"**弹幕**: {stat.get('danmaku', 0):,} "
        f"**评论**: {stat.get('reply', 0):,} "
        f"**点赞**: {stat.get('like', 0):,} "
        f"**投币**: {stat.get('coin', 0):,} "
        f"**收藏**: {stat.get('favorite', 0):,} "
        f"**分享**: {stat.get('share', 0):,}"
    )
    lines.append("")

    if vd.pic:
        lines.append(f"![cover]({vd.pic})")
        lines.append("")

    if vd.desc:
        lines.append(f"{_truncate(vd.desc)}\n")

    return lines


def _render_hot_comments(result: VideoAnalysisResult) -> list[str]:
    """Section 2 — top 10 hot comments."""
    lines: list[str] = []
    lines.append("## 💬 前10热评\n")

    if not result.hot_comments:
        lines.append("暂无热评数据\n")
        return lines

    for i, c in enumerate(result.hot_comments[:10], 1):
        msg = _truncate(c.message)
        lines.append(f"{i}. @{c.uname}: {msg} (👍{c.like})")
    lines.append("")
    return lines


def _render_pbp(result: VideoAnalysisResult) -> list[str]:
    """Section 3 — high-energy progress bar."""
    lines: list[str] = []
    lines.append("## 📊 高能进度条\n")

    pbp: PBP | None = result.pbp
    if pbp is None or not pbp.step_sec:
        lines.append("该视频暂无高能进度条数据\n")
        return lines

    # Show peak density areas (top 5 seconds by danmaku count)
    indexed = [(i, v) for i, v in enumerate(pbp.step_sec) if v > 0]
    indexed.sort(key=lambda x: x[1], reverse=True)
    peaks = indexed[:5]

    total_danmaku = sum(pbp.step_sec)
    lines.append(f"总弹幕密度采样点: {len(pbp.step_sec)} 总弹幕数: {total_danmaku}\n")
    lines.append("**高能时刻 TOP 5:**\n")
    for idx, count in peaks:
        t_str = _format_duration(idx)
        bar = "█" * min(count // 10 + 1, 40)
        lines.append(f"- `{t_str}` {bar} ({count} 条)")
    lines.append("")
    return lines


def _render_ai_summary(result: VideoAnalysisResult) -> list[str]:
    """Section 4 — AI summary."""
    lines: list[str] = []
    lines.append("## 🤖 AI总结\n")

    ai: AISummary | None = result.ai_summary
    if ai is None:
        lines.append("该视频暂无AI总结\n")
        return lines

    if ai.summary:
        lines.append(f"{_truncate(ai.summary)}\n")

    if ai.outline:
        lines.append("**大纲:**\n")
        for point in ai.outline:
            lines.append(f"- {_truncate(point)}")
        lines.append("")

    return lines


def _render_play_url(result: VideoAnalysisResult) -> list[str]:
    """Section 5 — play URL."""
    lines: list[str] = []
    lines.append("## 🔗 播放地址\n")

    pu: PlayUrl | None = result.play_url
    if pu is None or not pu.url:
        lines.append("暂无播放地址数据\n")
        return lines

    quality = pu.quality_desc or f"{pu.quality}P"
    lines.append(f"- [{quality}] {pu.url}")

    if pu.backup_urls:
        for i, bu in enumerate(pu.backup_urls, 1):
            lines.append(f"- [备用 {i}] {bu}")

    lines.append("")
    return lines


def _render_screenshot(result: VideoAnalysisResult) -> list[str]:
    """Section 6 — screenshot images."""
    lines: list[str] = []
    lines.append("## 📸 视频截图\n")

    ss: Screenshot | None = result.screenshot
    if ss is None or not ss.image_urls:
        lines.append("暂无截图数据\n")
        return lines

    for url in ss.image_urls:
        lines.append(f"![screenshot]({url})")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_SECTION_RENDERERS: list[tuple[str, str]] = [
    ("video_detail", "_video_detail"),  # special: always rendered, no skip flag
    ("comments", "_hot_comments"),
    ("pbp", "_pbp"),
    ("summary", "_ai_summary"),
    ("playurl", "_play_url"),
    ("screenshot", "_screenshot"),
]


def render_markdown(result: VideoAnalysisResult, skip_flags: set[str]) -> str:
    """Render *result* as a structured Markdown report string.

    Parameters
    ----------
    result:
        Aggregated analysis result containing up to 6 data sources.
    skip_flags:
        Set of section keys to skip.  Valid keys:
        ``comments``, ``pbp``, ``summary``, ``playurl``, ``screenshot``.

    Returns
    -------
    str
        Complete Markdown document.
    """
    lines: list[str] = []

    # Section 1 — video detail (always rendered)
    lines.extend(_render_video_detail(result))

    # Sections 2–6 — conditionally rendered
    if "comments" not in skip_flags:
        lines.extend(_render_hot_comments(result))
    if "pbp" not in skip_flags:
        lines.extend(_render_pbp(result))
    if "summary" not in skip_flags:
        lines.extend(_render_ai_summary(result))
    if "playurl" not in skip_flags:
        lines.extend(_render_play_url(result))
    if "screenshot" not in skip_flags:
        lines.extend(_render_screenshot(result))

    return "\n".join(lines)
