"""End-to-end integration tests for the 3-phase Processor pipeline.

Tests the full 3-phase pipeline:
Phase 1: pending → classified (classification)
Phase 2: classified → prompting → pending_reply (skill prompt + result)
Phase 3: pending_reply → replied (reply execution)

All external dependencies mocked — no real B站 API calls or subprocesses.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from at_orchestrator import db
from at_orchestrator.processor import Processor


def _make_task(**overrides: object) -> dict:
    data: dict[str, object] = {
        "msg_id": 1001,
        "source": "reply",
        "user_mid": 12345678,
        "user_nickname": "测试用户",
        "content": "@UP主 帮我分析视频 BV1xx",
        "business_id": 1,
        "subject_id": 20220101,
        "root_id": None,
        "source_id": None,
        "status": "pending",
        "created_at": time.time(),
        "processed_at": None,
        "reply_method": None,
        "reply_error": None,
        "classification_result": None,
        "skill_result": None,
        "cursor_id": None,
        "cursor_time": None,
    }
    data.update(overrides)
    return data


async def _get_task_row(db_path: str, msg_id: int, source: str) -> dict | None:
    def _query() -> dict | None:
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tasks WHERE msg_id = ? AND source = ?",
                (msg_id, source),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    return await asyncio.to_thread(_query)


async def _count_tasks_by_status(db_path: str, status: str) -> int:
    def _count() -> int:
        conn = sqlite3.connect(db_path)
        try:
            (cnt,) = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = ?", (status,)
            ).fetchone()
            return cnt
        finally:
            conn.close()
    return await asyncio.to_thread(_count)


# ══════════════════════════════════════════════════════════════════════
# E2E: Full 3-phase success path
# ══════════════════════════════════════════════════════════════════════


class TestFull3PhaseSuccessPath:
    """pending → classified → prompting → pending_reply → replied."""

    async def test_phase1_classification_success(self, tmp_db_path: Path) -> None:
        """Phase 1: LLM classifies task, writes classification_result, status='classified'."""
        await db.init_db(str(tmp_db_path))
        task = _make_task()
        assert await db.insert_task(task) is True

        client = MagicMock()
        classification = {"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.95, "reason": "analyse video"}

        with (
            patch("at_orchestrator.processor.classifier.build_batch_classification_prompt", return_value="fixed prompt"),
            patch("at_orchestrator.processor.classifier.parse_llm_result", return_value=[{**classification, "msg_id": 1001}]),
        ):
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_classification(
                limit=1, llm_result="valid"
            )

        assert len(results) == 1
        assert results[0]["msg_id"] == 1001
        assert results[0]["status"] == "classified"

        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row is not None
        assert row["status"] == "classified"
        assert row["classification_result"] is not None
        assert "video-analyzer" in row["classification_result"]

    async def test_phase2_skill_prompt_and_apply(self, tmp_db_path: Path) -> None:
        """Phase 2: prompt generation + result application → pending_reply."""
        await db.init_db(str(tmp_db_path))

        # Prepopulate with a classified task
        classification_json = json.dumps({"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.95, "reason": "test"})
        task = _make_task(status="classified", classification_result=classification_json)
        await db.insert_task(task)

        # Phase 2a: build skill prompts
        with (
            patch("at_orchestrator.processor.classifier.build_skill_prompt", return_value="skill prompt text"),
        ):
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.build_skill_prompts(limit=1)

        assert results[0]["status"] == "prompting"
        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row["status"] == "prompting"

        # Phase 2b: apply skill result
        llm_output = json.dumps({"msg_id": 1001, "reply_content": "分析完成！视频详情已生成。", "bvid": "BV1xx"})
        with patch("at_orchestrator.processor.classifier._extract_json_text", return_value=None):
            results2 = await processor.apply_skill_results(limit=1, llm_result=llm_output)

        assert results2[0]["status"] == "pending_reply"
        row2 = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row2["status"] == "pending_reply"
        assert row2["skill_result"] is not None
        assert row2["reply_method"] == "分析完成！视频详情已生成。"

    async def test_phase3_reply_success(self, tmp_db_path: Path) -> None:
        """Phase 3: execute reply → replied."""
        await db.init_db(str(tmp_db_path))

        task = _make_task(
            status="pending_reply",
            subject_id=12345,
            reply_method="分析完成！",
        )
        await db.insert_task(task)

        client = MagicMock()
        with (
            patch("at_orchestrator.processor.replier.reply_comment", AsyncMock(return_value=True)),
        ):
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.execute_replies(limit=1)

        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "comment"
        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row["status"] == "replied"
        assert row["reply_method"] == "comment"


# ══════════════════════════════════════════════════════════════════════
# E2E: Phase 1 classification failure
# ══════════════════════════════════════════════════════════════════════


class TestClassificationFailure:
    """Invalid LLM output → task status = 'failed'."""

    async def test_classification_failure(self, tmp_db_path: Path) -> None:
        await db.init_db(str(tmp_db_path))
        task = _make_task()
        assert await db.insert_task(task) is True

        client = MagicMock()
        with (
            patch("at_orchestrator.processor.classifier.build_batch_classification_prompt", return_value="fixed prompt"),
            patch("at_orchestrator.processor.classifier.parse_llm_result", return_value=None),
        ):
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_classification(limit=1, llm_result="invalid")

        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "classification_failed"
        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row["status"] == "failed"
        assert row["reply_error"] == "classification_failed"


# ══════════════════════════════════════════════════════════════════════
# E2E: Phase 3 reply failure
# ══════════════════════════════════════════════════════════════════════


class TestReplyFailure:
    """Reply API failure → task status = 'failed'."""

    async def test_reply_failure(self, tmp_db_path: Path) -> None:
        await db.init_db(str(tmp_db_path))
        task = _make_task(status="pending_reply", subject_id=12345, reply_method="分析完成")
        assert await db.insert_task(task) is True

        client = MagicMock()
        with (
            patch("at_orchestrator.processor.replier.reply_comment", AsyncMock(return_value=False)),
        ):
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.execute_replies(limit=1)

        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "reply_failed"
        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row["status"] == "failed"
        assert row["reply_error"] == "reply_failed"


# ══════════════════════════════════════════════════════════════════════
# E2E: Phase 1 batch processing
# ══════════════════════════════════════════════════════════════════════


class TestBatchProcessing:
    """limit=3 processes exactly 3 tasks through Phase 1."""

    async def test_batch_classification(self, tmp_db_path: Path) -> None:
        await db.init_db(str(tmp_db_path))
        for i in range(1, 6):
            t = _make_task(msg_id=i, content=f"task {i}")
            assert await db.insert_task(t) is True

        client = MagicMock()
        classification = {"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.95, "reason": "test"}

        with (
            patch("at_orchestrator.processor.classifier.build_batch_classification_prompt", return_value="fixed prompt"),
            patch("at_orchestrator.processor.classifier.parse_llm_result", return_value=[
                {**classification, "msg_id": 1},
                {**classification, "msg_id": 2},
                {**classification, "msg_id": 3},
            ]),
        ):
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_classification(limit=3, llm_result="valid")

        assert len(results) == 3
        assert await _count_tasks_by_status(str(tmp_db_path), "classified") == 3
        assert await _count_tasks_by_status(str(tmp_db_path), "pending") == 2
        for msg_id in (1, 2, 3):
            row = await _get_task_row(str(tmp_db_path), msg_id, "reply")
            assert row["status"] == "classified"
            assert row["classification_result"] is not None
            assert row["processed_at"] is not None

    async def test_batch_mixed_outcomes(self, tmp_db_path: Path) -> None:
        """One task gets no classification in LLM output → failed, others → classified."""
        await db.init_db(str(tmp_db_path))
        for i in range(1, 4):
            t = _make_task(msg_id=i, content=f"task {i}")
            assert await db.insert_task(t) is True

        client = MagicMock()
        classification = {"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.95, "reason": "test"}

        with (
            patch("at_orchestrator.processor.classifier.build_batch_classification_prompt", return_value="fixed prompt"),
            patch("at_orchestrator.processor.classifier.parse_llm_result", return_value=[
                {**classification, "msg_id": 1},
                {**classification, "msg_id": 3},
            ]),
        ):
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_classification(limit=3, llm_result="valid")

        assert len(results) == 3
        assert results[0]["msg_id"] == 1
        assert results[0]["status"] == "classified"
        assert results[1]["msg_id"] == 2
        assert results[1]["status"] == "failed"
        assert results[1]["error"] == "no_classification_in_llm_output"
        assert results[2]["msg_id"] == 3
        assert results[2]["status"] == "classified"

        row2 = await _get_task_row(str(tmp_db_path), 2, "reply")
        assert row2["status"] == "failed"


# ══════════════════════════════════════════════════════════════════════
# E2E: Phase 1 unknown skill shortcut
# ══════════════════════════════════════════════════════════════════════


class TestUnknownSkill:
    """Skill 'unknown' skips dispatch, marks replied immediately in Phase 1."""

    async def test_unknown_skill(self, tmp_db_path: Path) -> None:
        await db.init_db(str(tmp_db_path))
        task = _make_task()
        assert await db.insert_task(task) is True

        client = MagicMock()
        classification = {"skill_name": "unknown", "params": {}, "confidence": 0.95, "reason": "not actionable"}

        with (
            patch("at_orchestrator.processor.classifier.build_batch_classification_prompt", return_value="fixed prompt"),
            patch("at_orchestrator.processor.classifier.parse_llm_result", return_value=[{**classification, "msg_id": 1001}]),
        ):
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_classification(limit=1, llm_result="valid")

        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "none"
        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row["status"] == "replied"
        assert row["reply_method"] == "none"


# ══════════════════════════════════════════════════════════════════════
# E2E: Phase 3 PM reply path
# ══════════════════════════════════════════════════════════════════════


class TestPMReplyPath:
    """Long reply_content triggers private message reply."""

    async def test_pm_reply_success(self, tmp_db_path: Path) -> None:
        await db.init_db(str(tmp_db_path))
        long_content = "x" * 500
        task = _make_task(
            status="pending_reply",
            subject_id=12345,
            user_mid=99999,
            reply_method=long_content,
        )
        assert await db.insert_task(task) is True

        client = MagicMock()
        with (
            patch("at_orchestrator.processor.replier.check_session_detail", AsyncMock(return_value=True)),
            patch("at_orchestrator.processor.replier.reply_pm", AsyncMock(return_value=True)),
        ):
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.execute_replies(limit=1)

        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "pm"
        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row["status"] == "replied"
        assert row["reply_method"] == "pm"
