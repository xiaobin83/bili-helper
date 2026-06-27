"""Pydantic v2 models for watch-later-recommender.

Defines unified video representation, LLM recommendation output,
and user preference config schema. All models use extra="ignore"
to survive B站 API field changes.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class VideoItem(BaseModel):
    """Unified video representation normalized from all source APIs."""

    model_config = ConfigDict(extra="ignore")

    aid: int
    bvid: str
    title: str
    tid: int               # B站 partition ID
    tname: str             # partition name
    desc: str = ""
    duration: int = 0      # seconds
    owner_name: str = ""
    owner_mid: int = 0
    view: int = 0
    like: int = 0
    pubdate: int = 0       # unix timestamp
    pic: str = ""          # cover URL
    rcmd_reason: Optional[str] = None  # recommendation reason from API
    is_commercial: bool = False

    def is_ad(self) -> bool:
        """Check if this item is an advertisement or promotion."""
        if self.is_commercial:
            return True
        if self.rcmd_reason:
            ad_keywords = ["推广", "广告", "推荐"]
            for kw in ad_keywords:
                if kw in self.rcmd_reason:
                    return True
        return False


class RecommendationResult(BaseModel):
    """LLM recommendation output — exactly 5 videos with reasons."""

    model_config = ConfigDict(extra="ignore")

    bvids: list[str] = Field(..., min_length=5, max_length=5)
    reasons: list[str] = Field(..., min_length=5, max_length=5)
    surprise_count: int = 0

    def __init__(self, **data):
        super().__init__(**data)
        # Additional runtime validation after Pydantic init
        if len(self.bvids) != len(self.reasons):
            raise ValueError("bvids and reasons must have same length")


class CategoryPref(BaseModel):
    """Single category preference entry."""

    model_config = ConfigDict(extra="ignore")

    name: str                     # e.g. "技术"
    tids: list[int]               # B站 partition IDs, e.g. [36, 188]
    keywords: list[str] = []      # optional keywords for this category


class PrefsConfig(BaseModel):
    """User content preference configuration."""

    model_config = ConfigDict(extra="ignore")

    categories: list[CategoryPref] = []
    exclude_categories: list[CategoryPref] = []
    surprise_ratio: float = 0.2   # 0.0-0.5, fraction of surprise content
    max_duration: Optional[int] = None  # max video duration in seconds
