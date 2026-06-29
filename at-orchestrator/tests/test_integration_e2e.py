"""End-to-end integration tests for the Processor pipeline.

Tests the full pipeline (pending → classifying → dispatching → replying →
replied) with real SQLite and ALL external dependencies mocked:

- ``classifier.build_classification_prompt`` / ``parse_llm_result``
- ``Dispatcher.dispatch_with_timeout``
- ``replier.reply_comment`` / ``reply_pm`` / ``check_session_detail``

No real B站 API calls, no real subprocesses, no real LLM inference.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from at_orchestrator import db
from at_orchestrator.processor import Processor


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _make_task(**overrides: object) -> dict:
    """Create a complete task dict matching the DB schema columns.

    All 16 columns from ``db._TASK_COLUMNS`` are present so
    ``db.insert_task()`` can insert without ``None`` gaps.
    """
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
        "cursor_id": None,
        "cursor_time": None,
    }
    data.update(overrides)  # type: ignore[arg-type]
    return data  # type: ignore[return-value]


async def _get_task_row(db_path: str, msg_id: int, source: str) -> dict | None:
    """Read a single task row from the real SQLite database.

    Returns a dict of all columns or ``None`` if not found.
    """

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
    """Count tasks with the given status."""

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


def _make_valid_classification(
    skill_name: str = "video-analyzer",
    **params: object,
) -> dict:
    """Return a valid :class:`ClassificationResult` dict."""
    return {
        "skill_name": skill_name,
        "params": params,
        "confidence": 0.95,
        "reason": f"User asked for {skill_name}",
    }


def _make_dispatch_result(
    skill: str = "video-analyzer",
    exit_code: int = 0,
    stdout: str = "分析完成",
    error: str | None = None,
) -> dict:
    """Return a :class:`DispatchResult`-compatible dict."""
    return {
        "skill": skill,
        "exit_code": exit_code,
        "stdout": stdout,
        "output_file": "/tmp/at-orchestrator/1001_reply/output.md",
        "error": error,
    }


# ──────────────────────────────────────────────────────────────────────
# E2E: Full success path
# ──────────────────────────────────────────────────────────────────────


class TestFullSuccessPath:
    """pending → classifying → dispatching → replying → replied."""

    async def test_full_success_path(self, tmp_db_path: Path) -> None:
        # 1. Initialise real SQLite and insert 1 pending task
        await db.init_db(str(tmp_db_path))
        task = _make_task()
        assert await db.insert_task(task) is True

        # 2. Build mock dispatcher returning success
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch_with_timeout = AsyncMock(
            return_value=_make_dispatch_result(stdout="分析完成，视频详情已生成")
        )

        # 3. Create processor with all mocks returning success
        client = MagicMock()
        classification = _make_valid_classification("video-analyzer", bvid="BV1xx")

        with (
            patch(
                "at_orchestrator.processor.classifier.build_classification_prompt",
                return_value="fixed prompt",
            ),
            patch(
                "at_orchestrator.processor.classifier.parse_llm_result",
                return_value=classification,
            ),
            patch(
                "at_orchestrator.processor.replier.reply_comment",
                AsyncMock(return_value=True),
            ),
        ):
            processor = Processor(
                client=client, sender_uid=12345, dispatcher=mock_dispatcher
            )
            results = await processor.process_pending(
                limit=1, llm_result='{"skill_name":"video-analyzer","params":{"bvid":"BV1xx"},"confidence":0.95,"reason":"test"}'
            )

        # 4. Verify result list
        assert len(results) == 1
        assert results[0]["msg_id"] == 1001
        assert results[0]["source"] == "reply"
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "comment"
        assert results[0]["error"] is None

        # 5. Verify DB state — status transitions applied
        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row is not None
        assert row["status"] == "replied"
        assert row["reply_method"] == "comment"
        assert row["reply_error"] is None
        assert row["processed_at"] is not None

        # Only 1 task should be affected
        assert await _count_tasks_by_status(str(tmp_db_path), "replied") == 1


# ──────────────────────────────────────────────────────────────────────
# E2E: Classification failure
# ──────────────────────────────────────────────────────────────────────


class TestClassificationFailure:
    """Invalid LLM output → task status = 'failed' with error."""

    async def test_classification_failure(self, tmp_db_path: Path) -> None:
        await db.init_db(str(tmp_db_path))
        task = _make_task()
        assert await db.insert_task(task) is True

        # Mock parse_llm_result to return None (invalid LLM output)
        client = MagicMock()
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch_with_timeout = AsyncMock()

        with (
            patch(
                "at_orchestrator.processor.classifier.build_classification_prompt",
                return_value="fixed prompt",
            ),
            patch(
                "at_orchestrator.processor.classifier.parse_llm_result",
                return_value=None,  # ← classification failure
            ),
        ):
            processor = Processor(
                client=client, sender_uid=12345, dispatcher=mock_dispatcher
            )
            results = await processor.process_pending(
                limit=1, llm_result="invalid garbage not json"
            )

        # Verify result
        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "classification_failed"

        # Verify DB state
        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row is not None
        assert row["status"] == "failed"
        assert row["reply_error"] == "classification_failed"

        # Dispatcher MUST NOT be called after classification failure
        mock_dispatcher.dispatch_with_timeout.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# E2E: Reply failure
# ──────────────────────────────────────────────────────────────────────


class TestReplyFailure:
    """Mock API error during reply → task status = 'failed'."""

    async def test_reply_failure(self, tmp_db_path: Path) -> None:
        await db.init_db(str(tmp_db_path))
        task = _make_task(subject_id=12345)  # has subject_id → triggers comment reply
        assert await db.insert_task(task) is True

        client = MagicMock()
        classification = _make_valid_classification("dyn-publisher", text="hello")

        # Dispatch succeeds but reply fails
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch_with_timeout = AsyncMock(
            return_value=_make_dispatch_result(
                skill="dyn-publisher", stdout="ok", exit_code=0
            )
        )

        with (
            patch(
                "at_orchestrator.processor.classifier.build_classification_prompt",
                return_value="fixed prompt",
            ),
            patch(
                "at_orchestrator.processor.classifier.parse_llm_result",
                return_value=classification,
            ),
            patch(
                "at_orchestrator.processor.replier.reply_comment",
                AsyncMock(return_value=False),  # ← reply fails
            ),
        ):
            processor = Processor(
                client=client, sender_uid=12345, dispatcher=mock_dispatcher
            )
            results = await processor.process_pending(
                limit=1,
                llm_result='{"skill_name":"dyn-publisher","params":{"text":"hello"},"confidence":0.9,"reason":"test"}',
            )

        # Verify result
        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "reply_failed"

        # Verify DB state
        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row is not None
        assert row["status"] == "failed"
        assert row["reply_error"] == "reply_failed"

    async def test_dispatch_non_zero_exit_code_fails(self, tmp_db_path: Path) -> None:
        """Dispatch returns non-zero exit code → marked failed before reply phase."""
        await db.init_db(str(tmp_db_path))
        task = _make_task()
        assert await db.insert_task(task) is True

        client = MagicMock()
        classification = _make_valid_classification("video-analyzer", bvid="BV1xx")

        # Dispatch fails with exit code 1
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch_with_timeout = AsyncMock(
            return_value=_make_dispatch_result(exit_code=1, error="non-zero exit code 1")
        )

        with (
            patch(
                "at_orchestrator.processor.classifier.build_classification_prompt",
                return_value="fixed prompt",
            ),
            patch(
                "at_orchestrator.processor.classifier.parse_llm_result",
                return_value=classification,
            ),
        ):
            processor = Processor(
                client=client, sender_uid=12345, dispatcher=mock_dispatcher
            )
            results = await processor.process_pending(
                limit=1,
                llm_result='{"skill_name":"video-analyzer","params":{"bvid":"BV1xx"},"confidence":0.95,"reason":"test"}',
            )

        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert "exit code 1" in results[0]["error"]

        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row is not None
        assert row["status"] == "failed"


# ──────────────────────────────────────────────────────────────────────
# E2E: Batch processing
# ──────────────────────────────────────────────────────────────────────


class TestBatchProcessing:
    """limit=3 processes exactly 3 pending tasks."""

    async def test_batch_processing(self, tmp_db_path: Path) -> None:
        await db.init_db(str(tmp_db_path))

        # Insert 3 pending tasks
        t1 = _make_task(msg_id=1, content="分析 BV1xx")
        t2 = _make_task(msg_id=2, content="推荐视频")
        t3 = _make_task(msg_id=3, content="发布动态")
        for t in (t1, t2, t3):
            assert await db.insert_task(t) is True

        client = MagicMock()
        classification = _make_valid_classification("dyn-publisher", text="hello")

        # All mocks return success
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch_with_timeout = AsyncMock(
            return_value=_make_dispatch_result(
                skill="dyn-publisher", stdout="ok", exit_code=0
            )
        )

        with (
            patch(
                "at_orchestrator.processor.classifier.build_classification_prompt",
                return_value="fixed prompt",
            ),
            patch(
                "at_orchestrator.processor.classifier.parse_llm_result",
                return_value=classification,
            ),
            patch(
                "at_orchestrator.processor.replier.reply_comment",
                AsyncMock(return_value=True),
            ),
        ):
            processor = Processor(
                client=client, sender_uid=12345, dispatcher=mock_dispatcher
            )
            results = await processor.process_pending(
                limit=3,
                llm_result='{"skill_name":"dyn-publisher","params":{"text":"hello"},"confidence":0.9,"reason":"test"}',
            )

        # All 3 tasks processed
        assert len(results) == 3
        for i, r in enumerate(results, start=1):
            assert r["msg_id"] == i
            assert r["status"] == "replied"

        # DB: 3 tasks in "replied" status
        assert await _count_tasks_by_status(str(tmp_db_path), "replied") == 3
        assert await _count_tasks_by_status(str(tmp_db_path), "pending") == 0

        # Each task row should be individually correct
        for msg_id in (1, 2, 3):
            row = await _get_task_row(str(tmp_db_path), msg_id, "reply")
            assert row is not None
            assert row["status"] == "replied"
            assert row["reply_method"] == "comment"
            assert row["processed_at"] is not None

    async def test_batch_mixed_outcomes(self, tmp_db_path: Path) -> None:
        """Verify that when one task fails classification, others still process."""
        await db.init_db(str(tmp_db_path))

        # Insert 3 tasks — second one will get invalid classification
        t1 = _make_task(msg_id=1, content="分析 BV1xx")
        t2 = _make_task(msg_id=2, content="garbage")
        t3 = _make_task(msg_id=3, content="发布动态")
        for t in (t1, t2, t3):
            assert await db.insert_task(t) is True

        client = MagicMock()
        classification = _make_valid_classification("dyn-publisher", text="hello")

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch_with_timeout = AsyncMock(
            return_value=_make_dispatch_result(stdout="ok")
        )

        # Track call count for parse_llm_result so task 2 returns None
        call_count = 0

        def _parse_side_effect(llm_text: str) -> dict | None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return None  # task 2 fails classification
            return classification

        with (
            patch(
                "at_orchestrator.processor.classifier.build_classification_prompt",
                return_value="fixed prompt",
            ),
            patch(
                "at_orchestrator.processor.classifier.parse_llm_result",
                side_effect=_parse_side_effect,
            ),
            patch(
                "at_orchestrator.processor.replier.reply_comment",
                AsyncMock(return_value=True),
            ),
        ):
            processor = Processor(
                client=client, sender_uid=12345, dispatcher=mock_dispatcher
            )
            results = await processor.process_pending(
                limit=3,
                llm_result="valid",
            )

        # 3 results
        assert len(results) == 3

        # Task 1: replied
        assert results[0]["msg_id"] == 1
        assert results[0]["status"] == "replied"

        # Task 2: failed (classification)
        assert results[1]["msg_id"] == 2
        assert results[1]["status"] == "failed"
        assert results[1]["error"] == "classification_failed"

        # Task 3: replied
        assert results[2]["msg_id"] == 3
        assert results[2]["status"] == "replied"

        # DB verification
        row1 = await _get_task_row(str(tmp_db_path), 1, "reply")
        assert row1["status"] == "replied"

        row2 = await _get_task_row(str(tmp_db_path), 2, "reply")
        assert row2["status"] == "failed"
        assert row2["reply_error"] == "classification_failed"

        row3 = await _get_task_row(str(tmp_db_path), 3, "reply")
        assert row3["status"] == "replied"

    async def test_batch_limited_by_limit(self, tmp_db_path: Path) -> None:
        """When limit < pending count, only limit tasks are processed."""
        await db.init_db(str(tmp_db_path))

        # Insert 5 pending tasks
        for i in range(1, 6):
            t = _make_task(msg_id=i, content=f"task {i}")
            assert await db.insert_task(t) is True

        client = MagicMock()
        classification = _make_valid_classification("dyn-publisher", text="hello")

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch_with_timeout = AsyncMock(
            return_value=_make_dispatch_result(stdout="ok")
        )

        with (
            patch(
                "at_orchestrator.processor.classifier.build_classification_prompt",
                return_value="fixed prompt",
            ),
            patch(
                "at_orchestrator.processor.classifier.parse_llm_result",
                return_value=classification,
            ),
            patch(
                "at_orchestrator.processor.replier.reply_comment",
                AsyncMock(return_value=True),
            ),
        ):
            processor = Processor(
                client=client, sender_uid=12345, dispatcher=mock_dispatcher
            )
            results = await processor.process_pending(
                limit=3,
                llm_result="valid",
            )

        # Only 3 processed
        assert len(results) == 3
        assert await _count_tasks_by_status(str(tmp_db_path), "replied") == 3
        assert await _count_tasks_by_status(str(tmp_db_path), "pending") == 2


# ──────────────────────────────────────────────────────────────────────
# E2E: PM reply path
# ──────────────────────────────────────────────────────────────────────


class TestPMReplyPath:
    """Long stdout triggers private message reply."""

    async def test_pm_reply_success(self, tmp_db_path: Path) -> None:
        await db.init_db(str(tmp_db_path))
        task = _make_task(subject_id=12345, user_mid=99999)
        assert await db.insert_task(task) is True

        client = MagicMock()
        classification = _make_valid_classification("video-analyzer", bvid="BV1xx")

        # Dispatch returns long output → PM path
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch_with_timeout = AsyncMock(
            return_value=_make_dispatch_result(
                skill="video-analyzer",
                stdout="x" * 500,  # >= 200 chars → PM
            )
        )

        with (
            patch(
                "at_orchestrator.processor.classifier.build_classification_prompt",
                return_value="fixed prompt",
            ),
            patch(
                "at_orchestrator.processor.classifier.parse_llm_result",
                return_value=classification,
            ),
            patch(
                "at_orchestrator.processor.replier.check_session_detail",
                AsyncMock(return_value=True),  # session exists
            ),
            patch(
                "at_orchestrator.processor.replier.reply_pm",
                AsyncMock(return_value=True),
            ),
        ):
            processor = Processor(
                client=client, sender_uid=12345, dispatcher=mock_dispatcher
            )
            results = await processor.process_pending(
                limit=1,
                llm_result="valid",
            )

        assert len(results) == 1
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "pm"

        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row["status"] == "replied"
        assert row["reply_method"] == "pm"


# ──────────────────────────────────────────────────────────────────────
# E2E: Unknown skill shortcut
# ──────────────────────────────────────────────────────────────────────


class TestUnknownSkill:
    """Skill 'unknown' skips dispatch entirely, marks replied immediately."""

    async def test_unknown_skill_skips_dispatch(self, tmp_db_path: Path) -> None:
        await db.init_db(str(tmp_db_path))
        task = _make_task()
        assert await db.insert_task(task) is True

        client = MagicMock()
        classification = _make_valid_classification("unknown")

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch_with_timeout = AsyncMock()

        with (
            patch(
                "at_orchestrator.processor.classifier.build_classification_prompt",
                return_value="fixed prompt",
            ),
            patch(
                "at_orchestrator.processor.classifier.parse_llm_result",
                return_value=classification,
            ),
        ):
            processor = Processor(
                client=client, sender_uid=12345, dispatcher=mock_dispatcher
            )
            results = await processor.process_pending(
                limit=1,
                llm_result='{"skill_name":"unknown","params":{},"confidence":0.95,"reason":"not actionable"}',
            )

        assert len(results) == 1
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "none"

        # Dispatch NOT called
        mock_dispatcher.dispatch_with_timeout.assert_not_called()

        row = await _get_task_row(str(tmp_db_path), 1001, "reply")
        assert row["status"] == "replied"
        assert row["reply_method"] == "none"
