"""Tests for Pydantic v2 models in video_analyzer.models.

Covers VideoDetail, Comment, and the aggregate VideoAnalysisResult.
"""

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
# VideoDetail
# ---------------------------------------------------------------------------


def test_video_detail_model():
    """Create VideoDetail with valid data, verify all fields."""
    vd = VideoDetail(
        aid=1,
        bvid="BV1xx411c7mD",
        cid=100,
        title="测试视频标题",
        desc="一段视频描述",
        duration=3600,
        pubdate=1700000000,
        owner={"mid": 12345, "name": "UP主名称", "face": "https://example.com/face.jpg"},
        stat={
            "view": 100000,
            "danmaku": 500,
            "reply": 300,
            "favorite": 1000,
            "coin": 2000,
            "share": 150,
            "like": 5000,
        },
        tname="科技",
        pic="https://example.com/cover.jpg",
        dynamic="测试动态",
        pub_location="北京",
    )
    assert vd.aid == 1
    assert vd.bvid == "BV1xx411c7mD"
    assert vd.cid == 100
    assert vd.title == "测试视频标题"
    assert vd.desc == "一段视频描述"
    assert vd.duration == 3600
    assert vd.pubdate == 1700000000
    assert vd.owner["mid"] == 12345
    assert vd.owner["name"] == "UP主名称"
    assert vd.owner["face"] == "https://example.com/face.jpg"
    assert vd.stat["view"] == 100000
    assert vd.stat["like"] == 5000
    assert vd.tname == "科技"
    assert vd.pic == "https://example.com/cover.jpg"
    assert vd.dynamic == "测试动态"
    assert vd.pub_location == "北京"


def test_video_detail_defaults():
    """VideoDetail defaults: desc, tname, pic, dynamic, pub_location are empty strings."""
    vd = VideoDetail(
        aid=2,
        bvid="BV1xx411c7mE",
        cid=200,
        title="Minimal",
        duration=30,
        pubdate=0,
        owner={"mid": 1, "name": "u", "face": ""},
        stat={"view": 0, "danmaku": 0, "reply": 0, "favorite": 0, "coin": 0, "share": 0, "like": 0},
    )
    assert vd.desc == ""
    assert vd.tname == ""
    assert vd.pic == ""
    assert vd.dynamic == ""
    assert vd.pub_location == ""


def test_video_detail_extra_ignored():
    """Extra fields in input dict are silently ignored."""
    vd = VideoDetail.model_validate({
        "aid": 3,
        "bvid": "BV1xx411c7mF",
        "cid": 300,
        "title": "Extra",
        "duration": 60,
        "pubdate": 0,
        "owner": {"mid": 1, "name": "u", "face": ""},
        "stat": {"view": 0, "danmaku": 0, "reply": 0, "favorite": 0, "coin": 0, "share": 0, "like": 0},
        "unexpected_field": "should be ignored",
    })
    assert vd.title == "Extra"
    assert not hasattr(vd, "unexpected_field")


# ---------------------------------------------------------------------------
# Comment
# ---------------------------------------------------------------------------


def test_comment_model():
    """Create Comment, verify Optional fields work (default to 0 or "")."""
    c = Comment(
        rpid=1001,
        mid="2002",
        uname="评论用户",
        message="这是一条评论内容",
    )
    assert c.rpid == 1001
    assert c.mid == "2002"
    assert c.uname == "评论用户"
    assert c.message == "这是一条评论内容"
    # Optional fields should have default values
    assert c.avatar == ""
    assert c.like == 0
    assert c.ctime == 0
    assert c.rcount == 0


def test_comment_with_all_fields():
    """Comment with all fields explicitly provided."""
    c = Comment(
        rpid=999,
        mid="42",
        uname="PowerUser",
        avatar="https://example.com/avatar.jpg",
        message="完整评论",
        like=99,
        ctime=1600000000,
        rcount=5,
    )
    assert c.avatar == "https://example.com/avatar.jpg"
    assert c.like == 99
    assert c.ctime == 1600000000
    assert c.rcount == 5


def test_comment_extra_ignored():
    """Unexpected fields in Comment input are ignored."""
    c = Comment.model_validate({
        "rpid": 1,
        "mid": "1",
        "uname": "u",
        "message": "test",
        "reply_to": "should be ignored",
    })
    assert c.message == "test"


# ---------------------------------------------------------------------------
# PBP
# ---------------------------------------------------------------------------


def test_pbp_model():
    """Create PBP with step_sec data."""
    pbp = PBP(step_sec=[0, 5, 10, 3, 0], interval=3)
    assert pbp.step_sec == [0.0, 5.0, 10.0, 3.0, 0.0]
    assert pbp.interval == 3


def test_pbp_defaults():
    """PBP defaults to empty list and zero interval."""
    pbp = PBP()
    assert pbp.step_sec == []
    assert pbp.interval == 0


# ---------------------------------------------------------------------------
# AISummary
# ---------------------------------------------------------------------------


def test_ai_summary_model():
    """Create AISummary with summary and outline."""
    ai = AISummary(
        summary="这是一个AI总结",
        outline=[
            {"title": "要点一", "part_outline": [{"content": "详情一"}]},
            {"title": "要点二"},
            {"title": "要点三"},
        ],
    )
    assert ai.summary == "这是一个AI总结"
    assert len(ai.outline) == 3
    assert ai.outline[0].title == "要点一"
    assert ai.outline[0].part_outline[0].content == "详情一"


def test_ai_summary_defaults():
    """AISummary defaults to empty strings and empty list."""
    ai = AISummary()
    assert ai.summary == ""
    assert ai.outline == []


# ---------------------------------------------------------------------------
# PlayUrl
# ---------------------------------------------------------------------------


def test_play_url_model():
    """Create PlayUrl with url, quality, and backup URLs."""
    pu = PlayUrl(
        url="https://example.com/play.m3u8",
        backup_urls=["https://backup1.com/play.m3u8", "https://backup2.com/play.m3u8"],
        quality=80,
        quality_desc="1080P",
    )
    assert pu.url == "https://example.com/play.m3u8"
    assert len(pu.backup_urls) == 2
    assert pu.quality == 80
    assert pu.quality_desc == "1080P"


def test_play_url_defaults():
    """PlayUrl defaults to empty strings and zero."""
    pu = PlayUrl()
    assert pu.url == ""
    assert pu.backup_urls == []
    assert pu.quality == 0
    assert pu.quality_desc == ""


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


def test_screenshot_model():
    """Create Screenshot with image URLs."""
    ss = Screenshot(image_urls=["https://example.com/shot1.jpg", "https://example.com/shot2.jpg"])
    assert len(ss.image_urls) == 2
    assert ss.image_urls[0] == "https://example.com/shot1.jpg"


def test_screenshot_defaults():
    """Screenshot defaults to empty list."""
    ss = Screenshot()
    assert ss.image_urls == []


# ---------------------------------------------------------------------------
# VideoAnalysisResult (aggregate)
# ---------------------------------------------------------------------------


def test_video_analysis_result_aggregate():
    """Create VideoAnalysisResult with all sub-models, verify access to each."""
    result = VideoAnalysisResult(
        bvid="BV1xx411c7mD",
        video_detail=VideoDetail(
            aid=1,
            bvid="BV1xx411c7mD",
            cid=100,
            title="聚合测试",
            duration=120,
            pubdate=1700000000,
            owner={"mid": 1, "name": "测试UP", "face": ""},
            stat={"view": 5000, "danmaku": 100, "reply": 50, "favorite": 200, "coin": 300, "share": 30, "like": 1000},
        ),
        hot_comments=[
            Comment(rpid=1, mid="10", uname="用户甲", message="评论一"),
            Comment(rpid=2, mid="20", uname="用户乙", message="评论二"),
        ],
        pbp=PBP(step_sec=[0, 3, 7, 2], interval=3),
        ai_summary=AISummary(summary="视频总结", outline=[{"title": "介绍"}, {"title": "正文"}, {"title": "结尾"}]),
        play_url=PlayUrl(url="https://play.url", quality=64, quality_desc="720P"),
        screenshot=Screenshot(image_urls=["https://img.url/shot.png"]),
    )
    # Verify bvid
    assert result.bvid == "BV1xx411c7mD"

    # Verify video_detail
    assert result.video_detail.title == "聚合测试"
    assert result.video_detail.duration == 120

    # Verify hot_comments
    assert len(result.hot_comments) == 2
    assert result.hot_comments[0].uname == "用户甲"
    assert result.hot_comments[1].message == "评论二"

    # Verify pbp
    assert result.pbp is not None
    assert result.pbp.interval == 3
    assert len(result.pbp.step_sec) == 4

    # Verify ai_summary
    assert result.ai_summary is not None
    assert result.ai_summary.summary == "视频总结"
    assert len(result.ai_summary.outline) == 3

    # Verify play_url
    assert result.play_url is not None
    assert result.play_url.quality_desc == "720P"

    # Verify screenshot
    assert result.screenshot is not None
    assert len(result.screenshot.image_urls) == 1


def test_video_analysis_result_defaults():
    """VideoAnalysisResult defaults: video_detail=None, hot_comments=[], others=None."""
    result = VideoAnalysisResult(bvid="BVtest")
    assert result.video_detail is None
    assert result.hot_comments == []
    assert result.pbp is None
    assert result.ai_summary is None
    assert result.play_url is None
    assert result.screenshot is None
