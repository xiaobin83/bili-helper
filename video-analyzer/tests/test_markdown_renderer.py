"""Tests for the markdown report formatter (markdown_renderer).

Covers:
- Full output with all 6 sections present.
- Empty/fallback output for optional data sources.
- Skip flags hide optional sections.
- Long text truncation (via _truncate).
- Duration formatting helper (via _format_duration).
"""

from video_analyzer.markdown_renderer import (
    _format_duration,
    render_markdown,
)
from video_analyzer.models import (
    AISummary,
    Comment,
    PBP,
    PlayUrl,
    Screenshot,
    VideoAnalysisResult,
    VideoDetail,
)


# ---------------------------------------------------------------------------
# Helpers — build common VideoAnalysisResult fixtures
# ---------------------------------------------------------------------------


def _full_result() -> VideoAnalysisResult:
    """Return a VideoAnalysisResult with all 6 data sources populated."""
    return VideoAnalysisResult(
        bvid="BV1xx411c7mD",
        video_detail=VideoDetail(
            aid=1,
            bvid="BV1xx411c7mD",
            cid=100,
            title="Awesome Video",
            duration=120,
            pubdate=1000000000,
            owner={"mid": 1, "name": "Creator", "face": ""},
            stat={
                "view": 1000,
                "danmaku": 50,
                "reply": 30,
                "favorite": 100,
                "coin": 200,
                "share": 20,
                "like": 500,
            },
            desc="A great video",
            tname="科技",
        ),
        hot_comments=[
            Comment(
                rpid=1,
                mid="1",
                uname="User1",
                message="Great video!",
                like=100,
                ctime=1000000000,
            ),
        ],
        pbp=PBP(step_sec=[0, 5, 10, 15, 20], interval=5),
        ai_summary=AISummary(summary="Summary text", outline=[{"title": "Point 1"}, {"title": "Point 2"}]),
        play_url=PlayUrl(
            url="https://example.com/play",
            quality=80,
            quality_desc="1080P",
            backup_urls=["https://backup.com/play"],
        ),
        screenshot=Screenshot(
            image_urls=["https://example.com/shot1.jpg", "https://example.com/shot2.jpg"],
        ),
    )


def _minimal_result(
    *,
    with_comments: bool = False,
    include_pbp: bool = True,
    include_summary: bool = True,
    include_playurl: bool = True,
    include_screenshot: bool = True,
) -> VideoAnalysisResult:
    """Return a minimal VideoAnalysisResult (only video_detail populated by default)."""
    vd = VideoDetail(
        aid=1,
        bvid="BVmin",
        cid=1,
        title="Minimal",
        duration=60,
        pubdate=0,
        owner={"mid": 1, "name": "u", "face": ""},
        stat={"view": 0, "danmaku": 0, "reply": 0, "favorite": 0, "coin": 0, "share": 0, "like": 0},
    )
    result = VideoAnalysisResult(bvid="BVmin", video_detail=vd)

    if with_comments:
        result.hot_comments = [
            Comment(rpid=1, mid="1", uname="U", message="comment"),
        ]
    if include_pbp:
        result.pbp = PBP(step_sec=[1], interval=1)
    if include_summary:
        result.ai_summary = AISummary(summary="summary")
    if include_playurl:
        result.play_url = PlayUrl(url="https://example.com/play")
    if include_screenshot:
        result.screenshot = Screenshot(image_urls=["https://example.com/shot.jpg"])

    return result


# ---------------------------------------------------------------------------
# Section coverage
# ---------------------------------------------------------------------------


def test_render_all_sections():
    """Full VideoAnalysisResult with all data, verify 6 ``##`` sections."""
    result = _full_result()
    md = render_markdown(result, set())

    assert md.count("## ") == 6
    assert "## 🎬 视频详情" in md
    assert "## 💬 前10热评" in md
    assert "## 📊 高能进度条" in md
    assert "## 🤖 AI总结" in md
    assert "## 🔗 播放地址" in md
    assert "## 📸 视频截图" in md


def test_render_all_sections_content():
    """Each section contains expected data from the populated result."""
    result = _full_result()
    md = render_markdown(result, set())

    # Video detail section
    assert "Awesome Video" in md
    assert "Creator" in md
    assert "科技" in md
    assert "1,000" in md  # stat view formatted

    # Hot comments section
    assert "User1" in md
    assert "Great video!" in md
    assert "👍100" in md

    # PBP section
    assert "高能时刻 TOP 5" in md

    # AI summary section
    assert "Summary text" in md
    assert "Point 1" in md
    assert "Point 2" in md

    # Play URL section
    assert "1080P" in md
    assert "https://example.com/play" in md

    # Screenshot section
    assert "https://example.com/shot1.jpg" in md
    assert "https://example.com/shot2.jpg" in md


# ---------------------------------------------------------------------------
# Empty / fallback
# ---------------------------------------------------------------------------


def test_render_empty_data():
    """Empty result (no comments, None for optional fields), verify fallback messages."""
    result = VideoAnalysisResult(
        bvid="BVempty",
        video_detail=VideoDetail(
            aid=1,
            bvid="BVempty",
            cid=1,
            title="Empty Test",
            duration=60,
            pubdate=0,
            owner={"mid": 1, "name": "u", "face": ""},
            stat={"view": 0, "danmaku": 0, "reply": 0, "favorite": 0, "coin": 0, "share": 0, "like": 0},
        ),
        hot_comments=[],
        pbp=None,
        ai_summary=None,
        play_url=None,
        screenshot=None,
    )
    md = render_markdown(result, set())

    # Fallback messages for each optional section
    assert "暂无热评数据" in md
    assert "该视频暂无高能进度条数据" in md
    assert "该视频暂无AI总结" in md
    assert "暂无播放地址数据" in md
    assert "暂无截图数据" in md


# ---------------------------------------------------------------------------
# Skip flags
# ---------------------------------------------------------------------------


def test_render_skip_flags():
    """Skip all 5 optional sections, verify only 1 ``##`` header."""
    result = _minimal_result()
    md = render_markdown(
        result,
        {"comments", "pbp", "summary", "playurl", "screenshot"},
    )
    # Only the video detail section remains
    assert md.count("## ") == 1
    assert "## 🎬 视频详情" in md
    assert "## 💬 前10热评" not in md
    assert "## 📊 高能进度条" not in md
    assert "## 🤖 AI总结" not in md
    assert "## 🔗 播放地址" not in md
    assert "## 📸 视频截图" not in md


def test_render_skip_single_flag():
    """Skip one section at a time, verify it's omitted but others present."""
    result = _full_result()

    # Skip only comments
    md = render_markdown(result, {"comments"})
    assert "## 💬 前10热评" not in md
    assert "## 📊 高能进度条" in md
    assert "## 🤖 AI总结" in md
    assert "## 🔗 播放地址" in md
    assert "## 📸 视频截图" in md

    # Verify comments are still excluded and the rest are present
    assert md.count("## ") == 5


def test_render_skip_all_video_detail_present():
    """Skipping all optional sections still shows video detail with correct title."""
    result = _full_result()
    md = render_markdown(result, {"comments", "pbp", "summary", "playurl", "screenshot"})

    # Title line should still be present with the BVID
    assert "# BV1xx411c7mD 视频分析报告" in md
    assert "Awesome Video" in md
    assert "Creator" in md


# ---------------------------------------------------------------------------
# Long text truncation
# ---------------------------------------------------------------------------


def test_render_long_text_truncation():
    """Very long comment message, verify ``...`` truncation."""
    long_msg = "x" * 300
    result = VideoAnalysisResult(
        bvid="BVtrunc",
        video_detail=VideoDetail(
            aid=1,
            bvid="BVtrunc",
            cid=1,
            title="Truncation Test",
            duration=60,
            pubdate=0,
            owner={"mid": 1, "name": "u", "face": ""},
            stat={"view": 0, "danmaku": 0, "reply": 0, "favorite": 0, "coin": 0, "share": 0, "like": 0},
        ),
        hot_comments=[
            Comment(rpid=1, mid="1", uname="User", message=long_msg, like=5),
        ],
    )
    md = render_markdown(result, set())

    # The message should be truncated to 200 chars + "..."
    assert "..." in md
    # The original 300-char message should NOT appear in full
    assert long_msg not in md


def test_render_short_text_no_truncation():
    """Short comment message (<200 chars), no ``...`` truncation."""
    short_msg = "Hello, this is a short comment!"
    result = VideoAnalysisResult(
        bvid="BVshort",
        video_detail=VideoDetail(
            aid=1, bvid="BVshort", cid=1, title="Short",
            duration=60, pubdate=0,
            owner={"mid": 1, "name": "u", "face": ""},
            stat={"view": 0, "danmaku": 0, "reply": 0, "favorite": 0, "coin": 0, "share": 0, "like": 0},
        ),
        hot_comments=[
            Comment(rpid=1, mid="1", uname="User", message=short_msg, like=5),
        ],
    )
    md = render_markdown(result, set())

    assert short_msg in md
    # No truncation needed
    assert "..." not in md.split("short comment!")[0]


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------


def test_render_duration_formatting():
    """Test _format_duration with various inputs: 0, <1min, >=1min, >=1h."""
    assert _format_duration(0) == "0:00"
    assert _format_duration(59) == "0:59"
    assert _format_duration(60) == "1:00"
    assert _format_duration(3599) == "59:59"
    assert _format_duration(3600) == "1:00:00"
    assert _format_duration(3661) == "1:01:01"
    assert _format_duration(86399) == "23:59:59"
