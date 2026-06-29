"""Tests for Pydantic v2 models in at_orchestrator.models."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from at_orchestrator.models import (
    ClassificationResult,
    DispatchResult,
    ReplyRequest,
    Task,
    TaskStatus,
)


class TestTaskStatus:
    """TaskStatus enum — all values have correct string values."""

    def test_pending(self) -> None:
        assert TaskStatus.pending.value == "pending"

    def test_classifying(self) -> None:
        assert TaskStatus.classifying.value == "classifying"

    def test_dispatching(self) -> None:
        assert TaskStatus.dispatching.value == "dispatching"

    def test_replying(self) -> None:
        assert TaskStatus.replying.value == "replying"

    def test_replied(self) -> None:
        assert TaskStatus.replied.value == "replied"

    def test_failed(self) -> None:
        assert TaskStatus.failed.value == "failed"

    def test_all_statuses_covered(self) -> None:
        expected = {"pending", "classifying", "dispatching", "replying", "replied", "failed"}
        assert {s.value for s in TaskStatus} == expected


class TestTask:
    """Task model — all fields typed correctly, nullable fields can be None."""

    def test_minimal_valid_task(self) -> None:
        task = Task(
            msg_id=1001,
            source="reply",
            user_mid=12345678,
            user_nickname="测试用户",
            content="你好 @UP主",
            business_id=1,
            subject_id=20220101,
        )
        assert task.msg_id == 1001
        assert task.source == "reply"
        assert task.user_mid == 12345678
        assert task.user_nickname == "测试用户"
        assert task.content == "你好 @UP主"
        assert task.business_id == 1
        assert task.subject_id == 20220101
        assert task.root_id is None
        assert task.source_id is None
        assert task.status == TaskStatus.pending
        assert isinstance(task.created_at, float)
        assert task.processed_at is None
        assert task.reply_method is None
        assert task.reply_error is None
        assert task.cursor_id is None
        assert task.cursor_time is None

    def test_all_fields_populated(self) -> None:
        task = Task(
            msg_id=1002,
            source="at",
            user_mid=87654321,
            user_nickname="另一位用户",
            content="@UP主 看这个视频",
            business_id=2,
            subject_id=30303030,
            root_id=500,
            source_id=600,
            status=TaskStatus.classifying,
            created_at=1234567890.0,
            processed_at=1234567895.0,
            reply_method="comment",
            reply_error=None,
            cursor_id=700,
            cursor_time=1234568000.0,
        )
        assert task.msg_id == 1002
        assert task.source == "at"
        assert task.root_id == 500
        assert task.source_id == 600
        assert task.status == TaskStatus.classifying
        assert task.processed_at == 1234567895.0
        assert task.reply_method == "comment"
        assert task.cursor_id == 700
        assert task.cursor_time == 1234568000.0

    def test_root_id_can_be_none(self) -> None:
        task = Task(
            msg_id=1003,
            source="reply",
            user_mid=1,
            user_nickname="u",
            content="c",
            business_id=1,
            subject_id=1,
        )
        assert task.root_id is None

    def test_source_id_can_be_none(self) -> None:
        task = Task(
            msg_id=1004,
            source="reply",
            user_mid=1,
            user_nickname="u",
            content="c",
            business_id=1,
            subject_id=1,
        )
        assert task.source_id is None

    def test_processed_at_can_be_none(self) -> None:
        task = Task(
            msg_id=1005,
            source="at",
            user_mid=1,
            user_nickname="u",
            content="c",
            business_id=1,
            subject_id=1,
        )
        assert task.processed_at is None

    def test_reply_method_can_be_none(self) -> None:
        task = Task(
            msg_id=1006,
            source="at",
            user_mid=1,
            user_nickname="u",
            content="c",
            business_id=1,
            subject_id=1,
        )
        assert task.reply_method is None

    def test_reply_error_can_be_none(self) -> None:
        task = Task(
            msg_id=1007,
            source="at",
            user_mid=1,
            user_nickname="u",
            content="c",
            business_id=1,
            subject_id=1,
        )
        assert task.reply_error is None

    def test_cursor_id_can_be_none(self) -> None:
        task = Task(
            msg_id=1008,
            source="at",
            user_mid=1,
            user_nickname="u",
            content="c",
            business_id=1,
            subject_id=1,
        )
        assert task.cursor_id is None

    def test_cursor_time_can_be_none(self) -> None:
        task = Task(
            msg_id=1009,
            source="at",
            user_mid=1,
            user_nickname="u",
            content="c",
            business_id=1,
            subject_id=1,
        )
        assert task.cursor_time is None

    def test_extra_fields_are_ignored(self) -> None:
        task = Task.model_validate({
            "msg_id": 1010,
            "source": "reply",
            "user_mid": 1,
            "user_nickname": "u",
            "content": "c",
            "business_id": 1,
            "subject_id": 1,
            "unknown_field": "should be ignored",
            "another_extra": 42,
        })
        assert task.msg_id == 1010
        assert not hasattr(task, "unknown_field")


class TestClassificationResult:
    """ClassificationResult — JSON round-trip, extra ignored, invalid skill_name."""

    def test_valid_skill_name(self) -> None:
        result = ClassificationResult(
            skill_name="video-analyzer",
            params={"bvid": "BV1xx"},
            confidence=0.95,
            reason="需要分析该视频内容",
        )
        assert result.skill_name == "video-analyzer"
        assert result.params == {"bvid": "BV1xx"}
        assert result.confidence == 0.95
        assert result.reason == "需要分析该视频内容"

    def test_json_round_trip(self) -> None:
        original = ClassificationResult(
            skill_name="watch-later-recommender",
            params={"topic": "AI"},
            confidence=0.85,
            reason="推荐AI相关视频",
        )
        data = original.model_dump()
        restored = ClassificationResult.model_validate(data)
        assert restored.skill_name == original.skill_name
        assert restored.params == original.params
        assert restored.confidence == original.confidence
        assert restored.reason == original.reason

    def test_extra_fields_are_ignored(self) -> None:
        result = ClassificationResult.model_validate({
            "skill_name": "dyn-publisher",
            "params": {"text": "hello"},
            "confidence": 0.75,
            "reason": "需要发布动态",
            "extra_field": "will be ignored",
        })
        assert result.skill_name == "dyn-publisher"
        assert "extra_field" not in result.model_dump()

    def test_invalid_skill_name_raises_error(self) -> None:
        with pytest.raises(ValidationError):
            ClassificationResult(
                skill_name="invalid-skill",
                params={},
                confidence=0.5,
                reason="test",
            )

    def test_confidence_range(self) -> None:
        with pytest.raises(ValidationError):
            ClassificationResult(
                skill_name="fav-organizer",
                params={},
                confidence=1.5,  # > 1.0
                reason="too high",
            )
        with pytest.raises(ValidationError):
            ClassificationResult(
                skill_name="fav-organizer",
                params={},
                confidence=-0.1,  # < 0
                reason="too low",
            )

    def test_all_valid_skill_names(self) -> None:
        names = ["video-analyzer", "watch-later-recommender", "dyn-publisher", "fav-organizer", "unknown"]
        for name in names:
            result = ClassificationResult(
                skill_name=name,  # type: ignore[arg-type]
                params={},
                confidence=0.5,
                reason="test",
            )
            assert result.skill_name == name


class TestDispatchResult:
    """DispatchResult — all fields work."""

    def test_minimal_dispatch(self) -> None:
        result = DispatchResult(
            skill="video-analyzer",
            exit_code=0,
            stdout="分析完成",
        )
        assert result.skill == "video-analyzer"
        assert result.exit_code == 0
        assert result.stdout == "分析完成"
        assert result.output_file is None
        assert result.error is None

    def test_full_dispatch(self) -> None:
        result = DispatchResult(
            skill="fav-organizer",
            exit_code=1,
            stdout="",
            output_file="/tmp/output.json",
            error="分类失败",
        )
        assert result.skill == "fav-organizer"
        assert result.exit_code == 1
        assert result.output_file == "/tmp/output.json"
        assert result.error == "分类失败"

    def test_output_file_can_be_none(self) -> None:
        result = DispatchResult(
            skill="dyn-publisher",
            exit_code=0,
            stdout="ok",
        )
        assert result.output_file is None

    def test_error_can_be_none(self) -> None:
        result = DispatchResult(
            skill="dyn-publisher",
            exit_code=0,
            stdout="ok",
        )
        assert result.error is None

    def test_extra_fields_are_ignored(self) -> None:
        result = DispatchResult.model_validate({
            "skill": "video-analyzer",
            "exit_code": 0,
            "stdout": "ok",
            "unexpected": "ignored",
        })
        assert result.skill == "video-analyzer"
        assert "unexpected" not in result.model_dump()


class TestReplyRequest:
    """ReplyRequest — task field accepts dict."""

    def test_with_dict_task(self) -> None:
        req = ReplyRequest(
            task={"msg_id": 1001, "source": "reply"},
            reply_content="感谢反馈！",
            method="comment",
        )
        assert isinstance(req.task, dict)
        assert req.task["msg_id"] == 1001
        assert req.reply_content == "感谢反馈！"
        assert req.method == "comment"

    def test_with_task_model(self) -> None:
        task_obj = Task(
            msg_id=1002,
            source="at",
            user_mid=1,
            user_nickname="u",
            content="c",
            business_id=1,
            subject_id=1,
        )
        req = ReplyRequest(
            task=task_obj,
            reply_content="已收到",
            method="pm",
        )
        assert isinstance(req.task, Task)
        assert req.task.msg_id == 1002
        assert req.reply_content == "已收到"
        assert req.method == "pm"

    def test_method_must_be_comment_or_pm(self) -> None:
        with pytest.raises(ValidationError):
            ReplyRequest(
                task={"msg_id": 1},
                reply_content="test",
                method="email",  # invalid
            )

    def test_extra_fields_are_ignored(self) -> None:
        req = ReplyRequest.model_validate({
            "task": {"msg_id": 1},
            "reply_content": "hello",
            "method": "comment",
            "extra": "ignored",
        })
        assert req.reply_content == "hello"
        assert "extra" not in req.model_dump()
