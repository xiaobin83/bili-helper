"""Pydantic v2 models for video-analyzer API response types.

All models use extra="ignore" to discard unexpected API fields.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class VideoDetail(BaseModel):
    """Video metadata fields needed for markdown display."""

    model_config = ConfigDict(extra="ignore")

    aid: int
    bvid: str
    cid: int
    title: str
    desc: str = ""
    duration: int  # seconds
    pubdate: int  # unix timestamp
    owner: dict  # {"mid": int, "name": str, "face": str}
    stat: dict  # {"view": int, "danmaku": int, "reply": int, "favorite": int, "coin": int, "share": int, "like": int}
    tname: str = ""  # zone/subzone name
    pic: str = ""  # cover URL
    dynamic: str = ""
    pub_location: str = ""  # 发布地点 (optional)


class Comment(BaseModel):
    """User comment field subset (display only)."""

    model_config = ConfigDict(extra="ignore")

    rpid: int
    mid: str
    uname: str
    avatar: str = ""
    message: str  # comment text content
    like: int = 0
    ctime: int = 0  # unix timestamp
    rcount: int = 0  # reply count


class PBP(BaseModel):
    """High-energy progress bar data (danmaku density)."""

    model_config = ConfigDict(extra="ignore")

    step_sec: list[int] = []  # danmaku density per second
    duration: int = 0


class AISummary(BaseModel):
    """AI generated summary + outline."""

    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    outline: list[str] = []


class PlayUrl(BaseModel):
    """Video play URL data."""

    model_config = ConfigDict(extra="ignore")

    url: str = ""  # direct play URL (durl[0].url)
    backup_urls: list[str] = []  # backup URLs
    quality: int = 0  # quality number
    quality_desc: str = ""  # e.g. "1080P", "720P"


class Screenshot(BaseModel):
    """Screenshot image URLs."""

    model_config = ConfigDict(extra="ignore")

    image_urls: list[str] = []  # screenshot image URLs


class VideoAnalysisResult(BaseModel):
    """Aggregate model containing all 6 data sources + metadata."""

    model_config = ConfigDict(extra="ignore")

    video_detail: Optional[VideoDetail] = None
    hot_comments: list[Comment] = []
    pbp: Optional[PBP] = None
    ai_summary: Optional[AISummary] = None
    play_url: Optional[PlayUrl] = None
    screenshot: Optional[Screenshot] = None
    bvid: str = ""
