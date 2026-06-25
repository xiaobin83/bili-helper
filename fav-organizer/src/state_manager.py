"""State file manager for fav-organizer pipeline.

Manages the ``.fav-organizer/`` directory and its JSON files:

- ``state.json`` — produced by ``classify``, consumed by ``plan``
- ``classification_result.json`` — filled by agent, consumed by ``plan``
- ``plan.json`` — produced by ``plan``, consumed by ``execute``
- ``video_cache.json`` — video info disk cache (30-day TTL)

Usage::

    from src.state_manager import StateManager

    mgr = StateManager()
    mgr.save_state(state_data)
    loaded = mgr.load_state()
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .models import (
    ClassificationResultList,
    PlanFile,
    StateData,
)


class StateManager:
    """Read/write pipeline state files under ``.fav-organizer/``."""

    # Directory name relative to skill root (parent of src/)
    DIR_NAME = ".fav-organizer"

    FILE_STATE = "state.json"
    FILE_CLASSIFICATION = "classification_result.json"
    FILE_PLAN = "plan.json"
    FILE_VIDEO_CACHE = "video_cache.json"

    # ------------------------------------------------------------------
    # Directory
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self._root = self._resolve_root()

    @staticmethod
    def _resolve_root() -> Path:
        """Resolve the skill root directory (parent of src/)."""
        src_dir = Path(__file__).resolve().parent  # .../fav-organizer/src/
        return src_dir.parent  # .../fav-organizer/

    @property
    def state_dir(self) -> Path:
        """Return the ``.fav-organizer/`` directory path (creates if missing)."""
        d = self._root / self.DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------
    # Generic read/write helpers
    # ------------------------------------------------------------------

    def _path(self, filename: str) -> Path:
        return self.state_dir / filename

    def _write_json(self, filename: str, data: dict) -> None:
        path = self._path(filename)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_json(self, filename: str) -> dict:
        path = self._path(filename)
        if not path.exists():
            raise FileNotFoundError(f"状态文件不存在: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _exists(self, filename: str) -> bool:
        return self._path(filename).exists()

    # ------------------------------------------------------------------
    # State (classify → plan)
    # ------------------------------------------------------------------

    def save_state(self, state: StateData) -> Path:
        """Serialize *state* to ``state.json``.  Returns the file path."""
        self._write_json(self.FILE_STATE, state.model_dump())
        return self._path(self.FILE_STATE)

    def load_state(self) -> StateData:
        """Load and validate ``state.json``."""
        raw = self._read_json(self.FILE_STATE)
        return StateData.model_validate(raw)

    def has_state(self) -> bool:
        """Return True if ``state.json`` exists."""
        return self._exists(self.FILE_STATE)

    # ------------------------------------------------------------------
    # Classification result (agent → plan)
    # ------------------------------------------------------------------

    def save_classification(self, result: ClassificationResultList) -> Path:
        """Serialize classification to ``classification_result.json``."""
        self._write_json(self.FILE_CLASSIFICATION, result.model_dump())
        return self._path(self.FILE_CLASSIFICATION)

    def load_classification(self) -> ClassificationResultList:
        """Load and validate ``classification_result.json``."""
        raw = self._read_json(self.FILE_CLASSIFICATION)
        return ClassificationResultList.model_validate(raw)

    def has_classification(self) -> bool:
        """Return True if ``classification_result.json`` exists."""
        return self._exists(self.FILE_CLASSIFICATION)

    # ------------------------------------------------------------------
    # Plan (plan → execute)
    # ------------------------------------------------------------------

    def save_plan(self, plan: PlanFile) -> Path:
        """Serialize *plan* to ``plan.json``."""
        self._write_json(self.FILE_PLAN, plan.model_dump())
        return self._path(self.FILE_PLAN)

    def load_plan(self) -> PlanFile:
        """Load and validate ``plan.json``."""
        raw = self._read_json(self.FILE_PLAN)
        return PlanFile.model_validate(raw)

    def has_plan(self) -> bool:
        """Return True if ``plan.json`` exists."""
        return self._exists(self.FILE_PLAN)

    # ------------------------------------------------------------------
    # Video cache
    # ------------------------------------------------------------------

    CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 days

    def load_video_cache(self) -> dict[str, dict]:
        """Load the video cache (bvid → {data, ts}).  Returns {} if missing."""
        try:
            raw = self._read_json(self.FILE_VIDEO_CACHE)
            if not isinstance(raw, dict):
                return {}
            return raw
        except FileNotFoundError:
            return {}

    def save_video_cache(self, cache: dict[str, dict]) -> None:
        """Persist the video cache to disk."""
        self._write_json(self.FILE_VIDEO_CACHE, cache)

    def clear_video_cache(self) -> None:
        """Delete the video cache file."""
        path = self._path(self.FILE_VIDEO_CACHE)
        if path.exists():
            path.unlink()
            print(f"🗑️  已清除视频缓存: {path}")

    def _is_cache_fresh(self, entry: dict) -> bool:
        """Return True if cache entry is within TTL."""
        ts = entry.get("ts", 0)
        return (time.time() - ts) < self.CACHE_TTL_SECONDS

    def get_cached_video(self, bvid: str) -> dict | None:
        """Return cached video data for *bvid*, or None if expired/missing."""
        cache = self.load_video_cache()
        entry = cache.get(bvid)
        if entry and self._is_cache_fresh(entry):
            return entry.get("data")
        return None

    def set_cached_video(self, bvid: str, data: dict) -> None:
        """Store video data in the disk cache with current timestamp."""
        cache = self.load_video_cache()
        cache[bvid] = {"data": data, "ts": int(time.time())}
        self.save_video_cache(cache)
