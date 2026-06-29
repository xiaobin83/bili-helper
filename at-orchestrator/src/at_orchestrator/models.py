"""Pydantic v2 data models for at-orchestrator.

All models use ``model_config = ConfigDict(extra="ignore")`` for resilience
against Bilibili API field additions / shape changes.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ──────────────────────────────────────────────────────────────────────
# Shared constants
# ──────────────────────────────────────────────────────────────────────

VALID_SKILLS: frozenset[str] = frozenset({
    "video-analyzer",
    "watch-later-recommender",
    "dyn-publisher",
    "fav-organizer",
    "unknown",
})


# ──────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────


class TaskStatus(str, Enum):
    """Lifecycle of an AT/reply task."""

    pending = "pending"
    classifying = "classifying"
    dispatching = "dispatching"
    replying = "replying"
    replied = "replied"
    failed = "failed"


# ──────────────────────────────────────────────────────────────────────
# Domain models
# ──────────────────────────────────────────────────────────────────────


class Task(BaseModel):
    """An inbound AT (@) or reply notification from Bilibili."""

    model_config = ConfigDict(extra="ignore")

    msg_id: int
    source: str
    user_mid: int
    user_nickname: str
    content: str
    business_id: int
    subject_id: int
    root_id: int | None = None
    source_id: int | None = None
    status: TaskStatus = TaskStatus.pending
    created_at: float = Field(default_factory=time.time)
    processed_at: float | None = None
    reply_method: str | None = None
    reply_error: str | None = None
    cursor_id: int | None = None
    cursor_time: float | None = None


class ClassificationResult(BaseModel):
    """Result of classifying a task to a skill."""

    model_config = ConfigDict(extra="ignore")

    skill_name: str
    params: dict[str, Any] = {}
    confidence: float
    reason: str

    @field_validator("skill_name")
    @classmethod
    def _validate_skill_name(cls, v: str) -> str:
        if v not in VALID_SKILLS:
            msg = f"skill_name must be one of {sorted(VALID_SKILLS)}, got '{v}'"
            raise ValueError(msg)
        return v

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError(f"confidence must be between 0 and 1, got {v}")
        return v


class DispatchResult(BaseModel):
    """Result of dispatching a task to a skill."""

    model_config = ConfigDict(extra="ignore")

    skill: str
    exit_code: int
    stdout: str
    output_file: str | None = None
    error: str | None = None


class ReplyRequest(BaseModel):
    """Request payload for posting a reply (comment or PM)."""

    model_config = ConfigDict(extra="ignore")

    task: Any
    reply_content: str
    method: str

    @field_validator("method")
    @classmethod
    def _validate_method(cls, v: str) -> str:
        if v not in ("comment", "pm"):
            raise ValueError(f"method must be 'comment' or 'pm', got '{v}'")
        return v
