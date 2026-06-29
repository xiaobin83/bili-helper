"""Tests for at_orchestrator.processor — the pipeline orchestrator.

TDD: these tests are written BEFORE the implementation.
Run once to see them fail (ImportError), then implement processor.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task(**overrides: object) -> dict:
    data: dict = {
        "msg_id": 1001,
        "source": "reply",
        "user_mid": 12345678,
        "user_nickname": "测试用户",
        "content": "你好 @UP主",
        "business_id": 1,
        "subject_id": 20220101,
        "root_id": None,
        "source_id": None,
        "status": "pending",
        "created_at": 1750000000.0,
    }
    data.update(overrides)
    return data


def _make_llm_result_json(skill_name: str = "video-analyzer", **params: object) -> str:
    import json
    return json.dumps({
        "skill_name": skill_name,
        "params": params,
        "confidence": 0.95,
        "reason": f"Classified as {skill_name}",
    }, ensure_ascii=False)


def _make_dispatch_result(
    skill: str = "video-analyzer",
    exit_code: int = 0,
    stdout: str = "分析完成",
    error: str | None = None,
) -> dict:
    return {
        "skill": skill,
        "exit_code": exit_code,
        "stdout": stdout,
        "output_file": "/tmp/at-orchestrator/1001_reply/output.md",
        "error": error,
    }


class TestDecideReplyMethod:

    def _call(self, dispatch_result: dict, task: dict) -> str:
        from at_orchestrator.processor import _decide_reply_method
        return _decide_reply_method(dispatch_result, task)

    def test_short_stdout_with_subject_id_returns_comment(self) -> None:
        result = _make_dispatch_result(stdout="short output")
        task = _make_task(subject_id=12345)
        assert self._call(result, task) == "comment"

    def test_long_stdout_returns_pm(self) -> None:
        result = _make_dispatch_result(stdout="x" * 300)
        task = _make_task(subject_id=12345)
        assert self._call(result, task) == "pm"

    def test_exactly_200_chars_returns_pm(self) -> None:
        result = _make_dispatch_result(stdout="x" * 200)
        task = _make_task(subject_id=12345)
        assert self._call(result, task) == "pm"

    def test_199_chars_with_subject_id_returns_comment(self) -> None:
        result = _make_dispatch_result(stdout="x" * 199)
        task = _make_task(subject_id=12345)
        assert self._call(result, task) == "comment"

    def test_short_stdout_no_subject_id_returns_pm(self) -> None:
        result = _make_dispatch_result(stdout="short")
        task = _make_task(subject_id=0)
        assert self._call(result, task) == "pm"

    def test_empty_stdout_with_subject_id_returns_comment(self) -> None:
        result = _make_dispatch_result(stdout="")
        task = _make_task(subject_id=12345)
        assert self._call(result, task) == "comment"

    def test_stdout_field_missing_returns_pm(self) -> None:
        result = {"skill": "video-analyzer", "exit_code": 0}
        task = _make_task(subject_id=12345)
        assert self._call(result, task) == "pm"


class TestProcessPendingEmptyQueue:

    @pytest.mark.asyncio
    async def test_no_pending_tasks_returns_empty(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        with patch("at_orchestrator.processor.db") as mock_db:
            mock_db.get_pending_tasks = AsyncMock(return_value=[])
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1)
        assert results == []

    @pytest.mark.asyncio
    async def test_respects_limit_parameter(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        with patch("at_orchestrator.processor.db") as mock_db:
            mock_db.get_pending_tasks = AsyncMock(return_value=[])
            processor = Processor(client=client, sender_uid=12345)
            await processor.process_pending(limit=5)
            mock_db.get_pending_tasks.assert_called_once_with(5)


class TestProcessPendingDryRun:

    @pytest.mark.asyncio
    async def test_dry_run_prints_prompt_and_skips(self, capsys: pytest.CaptureFixture[str]) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task()
        llm_json = _make_llm_result_json("video-analyzer", bvid="BV1xx")

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "PROMPT_CONTENT"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.95, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock()
            mock_disp_cls.return_value = mock_disp
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1, dry_run=True, llm_result=llm_json)

        captured = capsys.readouterr()
        assert "PROMPT_CONTENT" in captured.out
        mock_disp.dispatch_with_timeout.assert_not_called()
        mock_db.update_task_status.assert_not_called()
        assert len(results) == 1
        assert results[0]["status"] == "classifying"


class TestProcessPendingClassificationFailed:

    @pytest.mark.asyncio
    async def test_classification_failed_mark_as_failed(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task()

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = None
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock()
            mock_disp_cls.return_value = mock_disp
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1, llm_result="bad json")

        assert mock_db.update_task_status.call_count >= 1
        mock_db.update_task_status.assert_any_call(1001, "reply", "failed", error="classification_failed")
        mock_disp.dispatch_with_timeout.assert_not_called()
        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "classification_failed"


class TestProcessPendingDispatchFailed:

    @pytest.mark.asyncio
    async def test_dispatch_failed_mark_as_failed(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task()
        dispatch_result = _make_dispatch_result(exit_code=1, error="non-zero exit code 1")

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.9, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(return_value=dispatch_result)
            mock_disp_cls.return_value = mock_disp
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        mock_db.update_task_status.assert_any_call(1001, "reply", "dispatching")
        mock_db.update_task_status.assert_any_call(1001, "reply", "failed", error="non-zero exit code 1")
        assert results[0]["status"] == "failed"
        assert "exit code 1" in results[0]["error"]


class TestProcessPendingUnknownSkill:

    @pytest.mark.asyncio
    async def test_unknown_skill_skips_dispatch(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task()

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "unknown", "params": {},
                "confidence": 0.9, "reason": "not actionable",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock()
            mock_disp_cls.return_value = mock_disp
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("unknown"))

        mock_disp.dispatch_with_timeout.assert_not_called()
        mock_db.update_task_status.assert_any_call(1001, "reply", "classifying")
        mock_db.update_task_status.assert_any_call(1001, "reply", "replied")
        mock_db.update_task_reply.assert_called_with(1001, "reply", "none")
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "none"


class TestProcessPendingReplyCommentSuccess:

    @pytest.mark.asyncio
    async def test_comment_reply_success(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(subject_id=12345, root_id=50, source_id=60)
        dispatch_result = _make_dispatch_result(skill="video-analyzer", stdout="分析完成")

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls, \
             patch("at_orchestrator.processor.replier") as mock_replier:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.9, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(return_value=dispatch_result)
            mock_disp_cls.return_value = mock_disp
            mock_replier.reply_comment = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        mock_replier.reply_comment.assert_called_once()
        args = mock_replier.reply_comment.call_args
        assert args[0][0] is client
        assert args[0][1] == task
        assert args[0][2] == "分析完成"
        mock_db.update_task_status.assert_any_call(1001, "reply", "replied")
        mock_db.update_task_reply.assert_called_with(1001, "reply", "comment")
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "comment"


class TestProcessPendingReplyPmLongResult:

    @pytest.mark.asyncio
    async def test_pm_reply_success(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(user_mid=99999, subject_id=12345)
        dispatch_result = _make_dispatch_result(skill="video-analyzer", stdout="x" * 500)

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls, \
             patch("at_orchestrator.processor.replier") as mock_replier:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.95, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(return_value=dispatch_result)
            mock_disp_cls.return_value = mock_disp
            mock_replier.check_session_detail = AsyncMock(return_value=True)
            mock_replier.reply_pm = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        mock_replier.check_session_detail.assert_called_once_with(client, 12345, 99999)
        mock_replier.reply_pm.assert_called_once_with(client, 12345, 99999, "x" * 500)
        mock_replier.reply_comment.assert_not_called()
        mock_db.update_task_status.assert_any_call(1001, "reply", "replied")
        mock_db.update_task_reply.assert_called_with(1001, "reply", "pm")
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "pm"


class TestProcessPendingPmFallbackToComment:

    @pytest.mark.asyncio
    async def test_pm_fallback_to_truncated_comment(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(subject_id=12345, user_mid=99999)
        long_stdout = "x" * 500
        dispatch_result = _make_dispatch_result(skill="video-analyzer", stdout=long_stdout)

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls, \
             patch("at_orchestrator.processor.replier") as mock_replier:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.9, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(return_value=dispatch_result)
            mock_disp_cls.return_value = mock_disp
            mock_replier.check_session_detail = AsyncMock(return_value=False)
            mock_replier.reply_comment = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        mock_replier.reply_pm.assert_not_called()
        mock_replier.reply_comment.assert_called_once()
        reply_content = mock_replier.reply_comment.call_args[0][2]
        assert len(reply_content) <= 1001
        mock_db.update_task_status.assert_any_call(1001, "reply", "replied")
        mock_db.update_task_reply.assert_called_with(1001, "reply", "comment")
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "comment"


class TestProcessPendingReplyFails:

    @pytest.mark.asyncio
    async def test_comment_reply_fails(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(subject_id=12345)
        dispatch_result = _make_dispatch_result(stdout="short")

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls, \
             patch("at_orchestrator.processor.replier") as mock_replier:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.9, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(return_value=dispatch_result)
            mock_disp_cls.return_value = mock_disp
            mock_replier.reply_comment = AsyncMock(return_value=False)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        calls = [c[0] for c in mock_db.update_task_status.call_args_list]
        assert (1001, "reply", "replying") in calls
        assert (1001, "reply", "failed") in calls
        assert results[0]["status"] == "failed"
        assert "reply" in results[0]["error"].lower()

    @pytest.mark.asyncio
    async def test_pm_and_fallback_both_fail(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(subject_id=12345, user_mid=99999)
        dispatch_result = _make_dispatch_result(stdout="x" * 500)

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls, \
             patch("at_orchestrator.processor.replier") as mock_replier:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.9, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(return_value=dispatch_result)
            mock_disp_cls.return_value = mock_disp
            mock_replier.check_session_detail = AsyncMock(return_value=False)
            mock_replier.reply_comment = AsyncMock(return_value=False)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        mock_db.update_task_status.assert_any_call(1001, "reply", "failed", error="reply_failed")
        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "reply_failed"


class TestProcessPendingMultipleTasks:

    @pytest.mark.asyncio
    async def test_processes_multiple_tasks(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task1 = _make_task(msg_id=1, subject_id=100)
        task2 = _make_task(msg_id=2, subject_id=200)
        dispatch_result = _make_dispatch_result(stdout="short")

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls, \
             patch("at_orchestrator.processor.replier") as mock_replier:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task1, task2])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [
                {"msg_id": 1, "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.9, "reason": "test"},
                {"msg_id": 2, "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.9, "reason": "test"},
            ]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(return_value=dispatch_result)
            mock_disp_cls.return_value = mock_disp
            mock_replier.reply_comment = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=10,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        assert len(results) == 2
        assert results[0]["msg_id"] == 1
        assert results[1]["msg_id"] == 2
        assert results[0]["status"] == "replied"
        assert results[1]["status"] == "replied"
        assert mock_replier.reply_comment.call_count == 2


class TestProcessPendingExceptionHandling:

    @pytest.mark.asyncio
    async def test_build_prompt_exception_caught(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task()

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher"):
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier.build_batch_classification_prompt.side_effect = RuntimeError("boom")
            processor = Processor(client=client, sender_uid=12345)
            with pytest.raises(RuntimeError, match="boom"):
                await processor.process_pending(limit=1)

    @pytest.mark.asyncio
    async def test_dispatch_exception_caught(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task()

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.9, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(side_effect=RuntimeError("subprocess crash"))
            mock_disp_cls.return_value = mock_disp
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        mock_db.update_task_status.assert_any_call(1001, "reply", "failed", error="dispatch_error: subprocess crash")
        assert results[0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_reply_exception_caught(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(subject_id=12345)
        dispatch_result = _make_dispatch_result(stdout="short")

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls, \
             patch("at_orchestrator.processor.replier") as mock_replier:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.9, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(return_value=dispatch_result)
            mock_disp_cls.return_value = mock_disp
            mock_replier.reply_comment = AsyncMock(side_effect=ConnectionError("network down"))
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        mock_db.update_task_status.assert_any_call(1001, "reply", "failed", error="reply_exception: network down")
        assert results[0]["status"] == "failed"


class TestProcessPendingResultStructure:

    @pytest.mark.asyncio
    async def test_result_has_all_required_keys(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(subject_id=12345)
        dispatch_result = _make_dispatch_result(stdout="short")

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls, \
             patch("at_orchestrator.processor.replier") as mock_replier:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.9, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(return_value=dispatch_result)
            mock_disp_cls.return_value = mock_disp
            mock_replier.reply_comment = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        r = results[0]
        assert r["msg_id"] == 1001
        assert r["source"] == "reply"
        assert r["status"] == "replied"
        assert r["reply_method"] == "comment"
        assert r["error"] is None


class TestProcessPendingNoLLMResult:

    @pytest.mark.asyncio
    async def test_no_llm_result_not_dry_run_prints_prompt_and_skips(self,
            capsys: pytest.CaptureFixture[str]) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task()

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "PROMPT_NO_LLM"
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock()
            mock_disp_cls.return_value = mock_disp
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.process_pending(limit=1, llm_result=None)

        captured = capsys.readouterr()
        assert "PROMPT_NO_LLM" in captured.out
        mock_disp.dispatch_with_timeout.assert_not_called()
        assert results[0]["status"] == "classifying"


class TestProcessPendingCallsUpdateTaskReply:

    @pytest.mark.asyncio
    async def test_update_task_reply_called_with_pm(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(user_mid=99999, subject_id=12345)
        dispatch_result = _make_dispatch_result(stdout="x" * 500)

        with patch("at_orchestrator.processor.db") as mock_db, \
             patch("at_orchestrator.processor.classifier") as mock_classifier, \
             patch("at_orchestrator.processor.Dispatcher") as mock_disp_cls, \
             patch("at_orchestrator.processor.replier") as mock_replier:
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001,
                "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"},
                "confidence": 0.95, "reason": "test",
            }]
            mock_disp = MagicMock()
            mock_disp.dispatch_with_timeout = AsyncMock(return_value=dispatch_result)
            mock_disp_cls.return_value = mock_disp
            mock_replier.check_session_detail = AsyncMock(return_value=True)
            mock_replier.reply_pm = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            await processor.process_pending(limit=1,
                llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx"))

        mock_db.update_task_reply.assert_called_with(1001, "reply", "pm")


class TestProcessorConstructor:

    def test_accepts_client_and_sender_uid(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        processor = Processor(client=client, sender_uid=12345)
        assert processor._client is client
        assert processor._sender_uid == 12345

    def test_accepts_custom_dispatcher(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        dispatcher = MagicMock()
        processor = Processor(client=client, sender_uid=12345, dispatcher=dispatcher)
        assert processor._dispatcher is dispatcher

    def test_default_dispatcher_created(self) -> None:
        from at_orchestrator.processor import Processor
        from at_orchestrator.dispatcher import Dispatcher
        client = MagicMock()
        processor = Processor(client=client, sender_uid=12345)
        assert isinstance(processor._dispatcher, Dispatcher)
