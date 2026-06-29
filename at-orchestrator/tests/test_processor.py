"""Tests for at_orchestrator.processor — the 3-phase pipeline orchestrator."""

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

    def _call(self, reply_content: str, task: dict) -> str:
        from at_orchestrator.processor import _decide_reply_method
        return _decide_reply_method(reply_content, task)

    def test_short_content_with_subject_id_returns_comment(self) -> None:
        task = _make_task(subject_id=12345)
        assert self._call("short output", task) == "comment"

    def test_long_content_returns_pm(self) -> None:
        task = _make_task(subject_id=12345)
        assert self._call("x" * 300, task) == "pm"

    def test_exactly_200_chars_returns_pm(self) -> None:
        task = _make_task(subject_id=12345)
        assert self._call("x" * 200, task) == "pm"

    def test_199_chars_with_subject_id_returns_comment(self) -> None:
        task = _make_task(subject_id=12345)
        assert self._call("x" * 199, task) == "comment"

    def test_short_content_no_subject_id_returns_pm(self) -> None:
        task = _make_task(subject_id=0)
        assert self._call("short", task) == "pm"

    def test_empty_content_with_subject_id_returns_comment(self) -> None:
        task = _make_task(subject_id=12345)
        assert self._call("", task) == "comment"


# ══════════════════════════════════════════════════════════════════════
# Phase 1: process_classification
# ══════════════════════════════════════════════════════════════════════


class TestProcessClassificationEmpty:
    """process_classification — handles empty queue."""

    @pytest.mark.asyncio
    async def test_no_pending_tasks_returns_empty(self) -> None:
        from at_orchestrator.processor import Processor
        with patch("at_orchestrator.processor.db") as mock_db:
            mock_db.get_pending_tasks = AsyncMock(return_value=[])
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.process_classification(limit=1)
        assert results == []

    @pytest.mark.asyncio
    async def test_respects_limit_parameter(self) -> None:
        from at_orchestrator.processor import Processor
        with patch("at_orchestrator.processor.db") as mock_db:
            mock_db.get_pending_tasks = AsyncMock(return_value=[])
            processor = Processor(client=MagicMock(), sender_uid=12345)
            await processor.process_classification(limit=5)
            mock_db.get_pending_tasks.assert_called_once_with(5)


class TestProcessClassificationDryRun:
    """process_classification — dry run prints prompt, skips DB."""

    @pytest.mark.asyncio
    async def test_dry_run_prints_prompt_and_skips(self, capsys: pytest.CaptureFixture[str]) -> None:
        from at_orchestrator.processor import Processor
        task = _make_task()
        llm_json = _make_llm_result_json("video-analyzer", bvid="BV1xx")

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "PROMPT_CONTENT"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001, "skill_name": "video-analyzer",
                "params": {"bvid": "BV1xx"}, "confidence": 0.95, "reason": "test",
            }]
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.process_classification(
                limit=1, dry_run=True, llm_result=llm_json
            )

        captured = capsys.readouterr()
        assert "PROMPT_CONTENT" in captured.out
        mock_db.update_task_status.assert_not_called()
        assert len(results) == 1
        assert results[0]["status"] == "classifying"


class TestProcessClassificationNoLLMResult:
    """process_classification — no LLM result prints prompt, marks classifying."""

    @pytest.mark.asyncio
    async def test_no_llm_result_prints_prompt(self, capsys: pytest.CaptureFixture[str]) -> None:
        from at_orchestrator.processor import Processor
        task = _make_task()

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "PROMPT_NO_LLM"
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.process_classification(limit=1, llm_result=None)

        captured = capsys.readouterr()
        assert "PROMPT_NO_LLM" in captured.out
        assert results[0]["status"] == "classifying"


class TestProcessClassificationFailed:
    """process_classification — invalid LLM output marks tasks as failed."""

    @pytest.mark.asyncio
    async def test_classification_failed_mark_as_failed(self) -> None:
        from at_orchestrator.processor import Processor
        task = _make_task()

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = None
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.process_classification(limit=1, llm_result="bad json")

        mock_db.update_task_status.assert_any_call(1001, "reply", "failed", error="classification_failed")
        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "classification_failed"


class TestProcessClassificationUnknownSkill:
    """process_classification — unknown skill skips to replied."""

    @pytest.mark.asyncio
    async def test_unknown_skill_mark_replied(self) -> None:
        from at_orchestrator.processor import Processor
        task = _make_task()

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001, "skill_name": "unknown", "params": {},
                "confidence": 0.9, "reason": "not actionable",
            }]
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.process_classification(
                limit=1, llm_result=_make_llm_result_json("unknown")
            )

        mock_db.update_task_status.assert_any_call(1001, "reply", "replied")
        mock_db.update_task_reply.assert_called_with(1001, "reply", "none")
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "none"


class TestProcessClassificationSuccess:
    """process_classification — successful classification writes to DB."""

    @pytest.mark.asyncio
    async def test_classify_to_classified(self) -> None:
        from at_orchestrator.processor import Processor
        import json as _json
        task = _make_task()

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_classification = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001, "skill_name": "video-analyzer",
                "params": {"bvid": "BV1xx"}, "confidence": 0.9, "reason": "test",
            }]
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.process_classification(
                limit=1, llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx")
            )

        mock_db.update_classification.assert_called_once()
        assert results[0]["status"] == "classified"

    @pytest.mark.asyncio
    async def test_missing_msg_id_in_llm_output(self) -> None:
        from at_orchestrator.processor import Processor
        task = _make_task(msg_id=9999)

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            # LLM output has msg_id=1001 but task has msg_id=9999
            mock_classifier.parse_llm_result.return_value = [{
                "msg_id": 1001, "skill_name": "video-analyzer",
                "params": {}, "confidence": 0.9, "reason": "test",
            }]
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.process_classification(
                limit=1, llm_result=_make_llm_result_json("video-analyzer")
            )

        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "no_classification_in_llm_output"


class TestProcessClassificationMultiple:
    """process_classification — batch processing."""

    @pytest.mark.asyncio
    async def test_processes_multiple_tasks(self) -> None:
        from at_orchestrator.processor import Processor
        task1 = _make_task(msg_id=1)
        task2 = _make_task(msg_id=2)

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_pending_tasks = AsyncMock(return_value=[task1, task2])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_classification = AsyncMock()
            mock_classifier.build_batch_classification_prompt.return_value = "prompt"
            mock_classifier.parse_llm_result.return_value = [
                {"msg_id": 1, "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.9, "reason": "test"},
                {"msg_id": 2, "skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.9, "reason": "test"},
            ]
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.process_classification(
                limit=10, llm_result=_make_llm_result_json("video-analyzer", bvid="BV1xx")
            )

        assert len(results) == 2
        assert results[0]["status"] == "classified"
        assert results[1]["status"] == "classified"
        assert mock_db.update_classification.call_count == 2


class TestProcessClassificationBuildPromptException:
    """process_classification — build prompt exception propagates."""

    @pytest.mark.asyncio
    async def test_build_prompt_exception_caught(self) -> None:
        from at_orchestrator.processor import Processor
        task = _make_task()

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_pending_tasks = AsyncMock(return_value=[task])
            mock_classifier.build_batch_classification_prompt.side_effect = RuntimeError("boom")
            processor = Processor(client=MagicMock(), sender_uid=12345)
            with pytest.raises(RuntimeError, match="boom"):
                await processor.process_classification(limit=1)


# ══════════════════════════════════════════════════════════════════════
# Phase 2: build_skill_prompts
# ══════════════════════════════════════════════════════════════════════


class TestBuildSkillPrompts:
    """Phase 2a: build_skill_prompts — reads classified tasks, prints prompts."""

    @pytest.mark.asyncio
    async def test_empty_classified_returns_empty(self) -> None:
        from at_orchestrator.processor import Processor
        with patch("at_orchestrator.processor.db") as mock_db:
            mock_db.get_tasks_by_status = AsyncMock(return_value=[])
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.build_skill_prompts(limit=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_prints_prompts_and_sets_prompting(self, capsys: pytest.CaptureFixture[str]) -> None:
        from at_orchestrator.processor import Processor
        import json as _json
        task = _make_task(
            msg_id=1, source="reply", status="classified",
            classification_result=_json.dumps({"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}}),
        )

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier.build_skill_prompt.return_value = "SKILL_PROMPT_MOCK"
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.build_skill_prompts(limit=5)

        captured = capsys.readouterr()
        assert "SKILL_PROMPT_MOCK" in captured.out
        assert "msg_id=1" in captured.out
        mock_db.update_task_status.assert_called_with(1, "reply", "prompting")
        assert results[0]["status"] == "prompting"

    @pytest.mark.asyncio
    async def test_handles_missing_classification_result(self, capsys: pytest.CaptureFixture[str]) -> None:
        from at_orchestrator.processor import Processor
        task = _make_task(msg_id=1, source="reply", status="classified")
        # no classification_result key

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier.build_skill_prompt.return_value = "FALLBACK_PROMPT"
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.build_skill_prompts(limit=5)

        assert results[0]["status"] == "prompting"


class TestApplySkillResults:
    """Phase 2b: apply_skill_results — applies LLM output to prompting tasks."""

    @pytest.mark.asyncio
    async def test_empty_prompting_returns_empty(self) -> None:
        from at_orchestrator.processor import Processor
        with patch("at_orchestrator.processor.db") as mock_db:
            mock_db.get_tasks_by_status = AsyncMock(return_value=[])
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.apply_skill_results(limit=5, llm_result="{}")
        assert results == []

    @pytest.mark.asyncio
    async def test_no_llm_result_returns_empty(self) -> None:
        from at_orchestrator.processor import Processor
        task = _make_task(msg_id=1, source="reply", status="prompting")
        with patch("at_orchestrator.processor.db") as mock_db:
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.apply_skill_results(limit=5, llm_result=None)
        assert results == []

    @pytest.mark.asyncio
    async def test_single_result_applied_to_task(self) -> None:
        from at_orchestrator.processor import Processor
        import json as _json
        task = _make_task(
            msg_id=1, source="reply", status="prompting",
            classification_result=_json.dumps({"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}}),
        )
        llm_text = _json.dumps({"msg_id": 1, "reply_content": "分析完成", "bvid": "BV1xx"})

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_skill_result = AsyncMock()
            mock_classifier._extract_json_text.return_value = None
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.apply_skill_results(limit=5, llm_result=llm_text)

        mock_db.update_skill_result.assert_called_once_with(
            1, "reply",
            _json.dumps({"msg_id": 1, "reply_content": "分析完成", "bvid": "BV1xx"}, ensure_ascii=False),
            "分析完成"
        )
        assert results[0]["status"] == "pending_reply"

    @pytest.mark.asyncio
    async def test_single_result_no_msg_id_applied_to_single_task(self) -> None:
        from at_orchestrator.processor import Processor
        import json as _json
        task = _make_task(
            msg_id=1, source="reply", status="prompting",
            classification_result=_json.dumps({"skill_name": "video-analyzer"}),
        )
        # LLM output has no msg_id, but only 1 task is prompting
        llm_text = _json.dumps({"reply_content": "分析完成"})

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_skill_result = AsyncMock()
            mock_db.update_task_status = AsyncMock()
            mock_classifier._extract_json_text.return_value = None
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.apply_skill_results(limit=5, llm_result=llm_text)

        assert results[0]["status"] == "pending_reply"

    @pytest.mark.asyncio
    async def test_no_match_marked_failed(self) -> None:
        from at_orchestrator.processor import Processor
        import json as _json
        task = _make_task(
            msg_id=1, source="reply", status="prompting",
            classification_result=_json.dumps({"skill_name": "video-analyzer"}),
        )
        # Different msg_id
        llm_text = _json.dumps([{"msg_id": 999, "reply_content": "x"}])

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.classifier") as mock_classifier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_classifier._extract_json_text.return_value = None
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.apply_skill_results(limit=5, llm_result=llm_text)

        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "no_skill_result_found"


# ══════════════════════════════════════════════════════════════════════
# Phase 3: execute_replies
# ══════════════════════════════════════════════════════════════════════


class TestExecuteRepliesEmpty:
    """Phase 3: execute_replies — handles empty queue."""

    @pytest.mark.asyncio
    async def test_empty_pending_reply_returns_empty(self) -> None:
        from at_orchestrator.processor import Processor
        with patch("at_orchestrator.processor.db") as mock_db:
            mock_db.get_tasks_by_status = AsyncMock(return_value=[])
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.execute_replies(limit=5)
        assert results == []


class TestExecuteRepliesComment:
    """Phase 3: execute_replies — comment reply."""

    @pytest.mark.asyncio
    async def test_comment_reply_success(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(
            msg_id=1, source="reply", status="pending_reply",
            subject_id=12345, reply_method="分析完成",
        )

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.replier") as mock_replier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_replier.reply_comment = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.execute_replies(limit=5)

        mock_replier.reply_comment.assert_called_once()
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "comment"

    @pytest.mark.asyncio
    async def test_comment_reply_fails(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(
            msg_id=1, source="reply", status="pending_reply",
            subject_id=12345, reply_method="分析完成",
        )

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.replier") as mock_replier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_replier.reply_comment = AsyncMock(return_value=False)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.execute_replies(limit=5)

        assert results[0]["status"] == "failed"
        assert results[0]["error"] == "reply_failed"


class TestExecuteRepliesPM:
    """Phase 3: execute_replies — PM reply."""

    @pytest.mark.asyncio
    async def test_pm_reply_success(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        long_content = "x" * 500
        task = _make_task(
            msg_id=1, source="reply", status="pending_reply",
            subject_id=12345, user_mid=99999, reply_method=long_content,
        )

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.replier") as mock_replier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_replier.check_session_detail = AsyncMock(return_value=True)
            mock_replier.reply_pm = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.execute_replies(limit=5)

        mock_replier.reply_pm.assert_called_once_with(client, 12345, 99999, long_content)
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "pm"

    @pytest.mark.asyncio
    async def test_pm_fallback_to_comment(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        long_content = "x" * 500
        task = _make_task(
            msg_id=1, source="reply", status="pending_reply",
            subject_id=12345, user_mid=99999, reply_method=long_content,
        )

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.replier") as mock_replier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_replier.check_session_detail = AsyncMock(return_value=False)
            mock_replier.reply_comment = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.execute_replies(limit=5)

        mock_replier.reply_pm.assert_not_called()
        mock_replier.reply_comment.assert_called_once()
        assert results[0]["status"] == "replied"
        assert results[0]["reply_method"] == "comment"


class TestExecuteRepliesException:
    """Phase 3: execute_replies — exception handling."""

    @pytest.mark.asyncio
    async def test_reply_exception_caught(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(
            msg_id=1, source="reply", status="pending_reply",
            subject_id=12345, reply_method="分析完成",
        )

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.replier") as mock_replier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_replier.reply_comment = AsyncMock(side_effect=ConnectionError("network down"))
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.execute_replies(limit=5)

        mock_db.update_task_status.assert_any_call(1, "reply", "failed", error="reply_exception: network down")
        assert results[0]["status"] == "failed"


class TestExecuteRepliesMultiple:
    """Phase 3: execute_replies — batch reply."""

    @pytest.mark.asyncio
    async def test_processes_multiple_tasks(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task1 = _make_task(msg_id=1, status="pending_reply", subject_id=100, reply_method="ok")
        task2 = _make_task(msg_id=2, status="pending_reply", subject_id=200, reply_method="ok")

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.replier") as mock_replier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task1, task2])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_replier.reply_comment = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.execute_replies(limit=5)

        assert len(results) == 2
        assert results[0]["status"] == "replied"
        assert results[1]["status"] == "replied"


class TestExecuteRepliesResultStructure:
    """Phase 3: execute_replies — result structure."""

    @pytest.mark.asyncio
    async def test_result_has_all_required_keys(self) -> None:
        from at_orchestrator.processor import Processor
        client = MagicMock()
        task = _make_task(
            msg_id=1, status="pending_reply",
            subject_id=12345, reply_method="分析完成",
        )

        with (
            patch("at_orchestrator.processor.db") as mock_db,
            patch("at_orchestrator.processor.replier") as mock_replier,
        ):
            mock_db.get_tasks_by_status = AsyncMock(return_value=[task])
            mock_db.update_task_status = AsyncMock()
            mock_db.update_task_reply = AsyncMock()
            mock_replier.reply_comment = AsyncMock(return_value=True)
            processor = Processor(client=client, sender_uid=12345)
            results = await processor.execute_replies(limit=1)

        r = results[0]
        assert r["msg_id"] == 1
        assert r["source"] == "reply"
        assert r["status"] == "replied"
        assert r["reply_method"] == "comment"
        assert r["error"] is None


# ══════════════════════════════════════════════════════════════════════
# Backward compat: process_pending
# ══════════════════════════════════════════════════════════════════════


class TestProcessPendingBackwardCompat:
    """process_pending() — backward-compat wrapper delegates to process_classification."""

    @pytest.mark.asyncio
    async def test_delegates_to_process_classification(self) -> None:
        from at_orchestrator.processor import Processor
        with patch("at_orchestrator.processor.db") as mock_db:
            mock_db.get_pending_tasks = AsyncMock(return_value=[])
            processor = Processor(client=MagicMock(), sender_uid=12345)
            results = await processor.process_pending(limit=5)
        assert results == []


class TestProcessorConstructor:
    """Processor __init__ — parametrised with client and sender_uid."""

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
