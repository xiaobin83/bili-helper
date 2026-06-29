"""Tests for at_orchestrator.classifier — prompt builder and LLM result parser."""

from __future__ import annotations

import json

import pytest

from at_orchestrator.classifier import build_classification_prompt, parse_llm_result


# ──────────────────────────────────────────────────────────────────────
# build_classification_prompt
# ──────────────────────────────────────────────────────────────────────


class TestBuildClassificationPrompt:
    """Prompt builder — returns string with required structural elements."""

    def test_returns_string(self) -> None:
        task = {"content": "分析这个视频BV1xx", "business_id": 1}
        result = build_classification_prompt(task)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_wraps_content_in_message_tags(self) -> None:
        content = "帮我推荐几个视频"
        task = {"content": content, "business_id": 1}
        prompt = build_classification_prompt(task)
        assert "<message>帮我推荐几个视频</message>" in prompt

    def test_contains_all_skill_names(self) -> None:
        task = {"content": "test", "business_id": 1}
        prompt = build_classification_prompt(task)
        assert "video-analyzer" in prompt
        assert "watch-later-recommender" in prompt
        assert "dyn-publisher" in prompt
        assert "fav-organizer" in prompt
        assert "unknown" in prompt

    def test_contains_at_least_4_few_shot_examples(self) -> None:
        task = {"content": "test", "business_id": 1}
        prompt = build_classification_prompt(task)
        # Each few-shot example should contain a skill_name assignment
        occurrences = prompt.count('"skill_name"')
        # At least 4 few-shot examples should have "skill_name" in them
        assert occurrences >= 4, f"Expected >=4 few-shot examples, found {occurrences} 'skill_name' occurrences"

    def test_contains_business_context_section(self) -> None:
        task = {"content": "test", "business_id": 1}
        prompt = build_classification_prompt(task)
        assert "业务上下文" in prompt or "上下文" in prompt

    def test_business_context_for_dynamic_reply(self) -> None:
        task = {"content": "test", "business_id": 11}
        prompt = build_classification_prompt(task)
        assert "动态回复" in prompt

    def test_business_context_for_dynamic(self) -> None:
        task = {"content": "test", "business_id": 17}
        prompt = build_classification_prompt(task)
        assert "动态" in prompt

    def test_business_context_for_comment(self) -> None:
        task = {"content": "test", "business_id": 1}
        prompt = build_classification_prompt(task)
        assert "视频评论" in prompt

    def test_business_context_for_unknown_id(self) -> None:
        """Unknown business_id should still produce a valid prompt."""
        task = {"content": "test", "business_id": 999}
        prompt = build_classification_prompt(task)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_prompt_instructs_json_only_output(self) -> None:
        task = {"content": "test", "business_id": 1}
        prompt = build_classification_prompt(task)
        # Should instruct to output only JSON, no extra text
        assert "JSON" in prompt
        assert "不要额外文字" in prompt or "只输出" in prompt or "only" in prompt.lower()

    def test_prompt_instructs_output_format(self) -> None:
        task = {"content": "test", "business_id": 1}
        prompt = build_classification_prompt(task)
        assert "skill_name" in prompt
        assert "params" in prompt
        assert "confidence" in prompt
        assert "reason" in prompt

    def test_content_with_special_characters(self) -> None:
        task = {"content": "分析 BV1xx <script>alert(1)</script> & \"quotes\"", "business_id": 1}
        prompt = build_classification_prompt(task)
        assert "BV1xx" in prompt
        # The message tags should protect against injection
        assert "<message>" in prompt
        assert "</message>" in prompt

    def test_content_is_not_injected_outside_message_tags(self) -> None:
        """Content should only appear inside <message> tags, not elsewhere as prompt."""
        content = "DISREGARD_PREVIOUS_INSTRUCTIONS"
        task = {"content": content, "business_id": 1}
        prompt = build_classification_prompt(task)
        # Count occurrences - should appear exactly once (inside message tags)
        # But first let's ensure it's inside the message tags
        msg_start = prompt.index("<message>")
        msg_end = prompt.index("</message>") + len("</message>")
        before_msg = prompt[:msg_start]
        after_msg = prompt[msg_end:]
        assert content not in before_msg
        assert content not in after_msg
        # Content should be wrapped in message tags
        assert f"<message>{content}</message>" in prompt


# ──────────────────────────────────────────────────────────────────────
# parse_llm_result
# ──────────────────────────────────────────────────────────────────────


class TestParseLLMResultJsonFences:
    """parse_llm_result — extracts JSON from ```json fences."""

    def test_extracts_from_json_fence(self) -> None:
        llm_text = """Here is my classification:

```json
{"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.95, "reason": "用户要求分析视频"}
```

This is the result."""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["skill_name"] == "video-analyzer"
        assert result["params"] == {"bvid": "BV1xx"}
        assert result["confidence"] == 0.95
        assert result["reason"] == "用户要求分析视频"

    def test_extracts_from_generic_code_fence(self) -> None:
        """``` without json specifier should also work."""
        llm_text = """```
{"skill_name": "watch-later-recommender", "params": {"topic": "AI"}, "confidence": 0.9, "reason": "用户请求推荐"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["skill_name"] == "watch-later-recommender"
        assert result["params"] == {"topic": "AI"}

    def test_newlines_inside_json_fence(self) -> None:
        """JSON with newlines inside the fence should parse correctly (re.DOTALL)."""
        llm_text = """```json
{
  "skill_name": "dyn-publisher",
  "params": {
    "text": "hello world"
  },
  "confidence": 0.8,
  "reason": "用户想发动态"
}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["skill_name"] == "dyn-publisher"
        assert result["params"] == {"text": "hello world"}
        assert result["confidence"] == 0.8

    def test_multiple_json_blocks_uses_first_fence(self) -> None:
        """When multiple JSON blocks exist, first ```json fence wins."""
        llm_text = """```json
{"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.95, "reason": "分析视频"}
```
Some more text...
```json
{"skill_name": "unknown", "params": {}, "confidence": 0.1, "reason": "fallback"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["skill_name"] == "video-analyzer"

    def test_conf_in_fence_range(self) -> None:
        llm_text = """```json
{"skill_name": "fav-organizer", "params": {}, "confidence": 0, "reason": "no confidence"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["confidence"] == 0.0

    def test_high_conf_in_fence(self) -> None:
        llm_text = """```json
{"skill_name": "video-analyzer", "params": {}, "confidence": 1.0, "reason": "full"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["confidence"] == 1.0


class TestParseLLMResultBareJSON:
    """parse_llm_result — extracts bare JSON without fences."""

    def test_extracts_bare_json(self) -> None:
        llm_text = """Classification result: {"skill_name": "fav-organizer", "params": {}, "confidence": 0.9, "reason": "整理收藏夹"} Done."""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["skill_name"] == "fav-organizer"
        assert result["params"] == {}
        assert result["confidence"] == 0.9

    def test_extracts_bare_json_with_surrounding_noise(self) -> None:
        llm_text = "Sure! Here you go:\n\n{\"skill_name\": \"unknown\", \"params\": {}, \"confidence\": 0.2, \"reason\": \"无法分类\"}\n\nHope that helps!"
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["skill_name"] == "unknown"
        assert result["params"] == {}
        assert result["confidence"] == 0.2

    def test_bare_json_with_newlines_inside(self) -> None:
        """Bare JSON with internal newlines should still be found (re.DOTALL)."""
        llm_text = """Here is my result:
{
  "skill_name": "unknown",
  "params": {},
  "confidence": 0.1,
  "reason": "not sure"
}
End."""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["skill_name"] == "unknown"


class TestParseLLMResultErrors:
    """parse_llm_result — returns None on invalid input."""

    def test_returns_none_when_no_json(self) -> None:
        result = parse_llm_result("This is just plain text with no JSON at all.")
        assert result is None

    def test_returns_none_when_empty_string(self) -> None:
        result = parse_llm_result("")
        assert result is None

    def test_returns_none_when_truncated_json(self) -> None:
        result = parse_llm_result('{"skill_name": "video-analyzer", "params": {')
        assert result is None

    def test_returns_none_when_invalid_json_in_fence(self) -> None:
        llm_text = """```json
{invalid json content here}}
```"""
        result = parse_llm_result(llm_text)
        assert result is None

    def test_returns_none_when_not_a_dict(self) -> None:
        llm_text = """```json
["skill_name", "params"]
```"""
        result = parse_llm_result(llm_text)
        assert result is None

    def test_returns_none_when_json_is_just_number(self) -> None:
        llm_text = "42"
        result = parse_llm_result(llm_text)
        assert result is None

    def test_returns_none_when_missing_required_key(self) -> None:
        """Missing 'skill_name' key should cause validation failure."""
        llm_text = """```json
{"params": {}, "confidence": 0.5, "reason": "missing skill_name"}
```"""
        result = parse_llm_result(llm_text)
        assert result is None

    def test_returns_none_when_missing_params(self) -> None:
        """Missing 'params' key should cause validation failure."""
        llm_text = """```json
{"skill_name": "unknown", "confidence": 0.5, "reason": "missing params"}
```"""
        result = parse_llm_result(llm_text)
        assert result is None

    def test_returns_none_when_missing_confidence(self) -> None:
        """Missing 'confidence' key should cause validation failure."""
        llm_text = """```json
{"skill_name": "unknown", "params": {}, "reason": "missing confidence"}
```"""
        result = parse_llm_result(llm_text)
        assert result is None

    def test_returns_none_when_missing_reason(self) -> None:
        """Missing 'reason' key should cause validation failure."""
        llm_text = """```json
{"skill_name": "unknown", "params": {}, "confidence": 0.5}
```"""
        result = parse_llm_result(llm_text)
        assert result is None

    def test_returns_none_when_invalid_skill_name(self) -> None:
        llm_text = """```json
{"skill_name": "non-existent-skill", "params": {}, "confidence": 0.5, "reason": "bad"}
```"""
        result = parse_llm_result(llm_text)
        assert result is None

    def test_returns_none_when_confidence_out_of_range(self) -> None:
        llm_text = """```json
{"skill_name": "unknown", "params": {}, "confidence": 1.5, "reason": "too high"}
```"""
        result = parse_llm_result(llm_text)
        assert result is None

    def test_conf_negative_in_bare(self) -> None:
        llm_text = """{"skill_name": "unknown", "params": {}, "confidence": -0.5, "reason": "negative"}"""
        result = parse_llm_result(llm_text)
        assert result is None

    def test_returns_none_when_params_not_dict(self) -> None:
        llm_text = """```json
{"skill_name": "unknown", "params": "not a dict", "confidence": 0.5, "reason": "bad params"}
```"""
        result = parse_llm_result(llm_text)
        assert result is None

    def test_returns_none_when_reason_not_string(self) -> None:
        llm_text = """```json
{"skill_name": "unknown", "params": {}, "confidence": 0.5, "reason": 123}
```"""
        result = parse_llm_result(llm_text)
        assert result is None


class TestParseLLMResultEdgeCases:
    """parse_llm_result — handles various edge cases correctly."""

    def test_params_empty_dict(self) -> None:
        llm_text = """```json
{"skill_name": "fav-organizer", "params": {}, "confidence": 0.9, "reason": "整理"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["params"] == {}

    def test_confidence_as_float_zero(self) -> None:
        llm_text = """```json
{"skill_name": "unknown", "params": {}, "confidence": 0.0, "reason": "zero"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["confidence"] == 0.0

    def test_confidence_as_float_one(self) -> None:
        llm_text = """```json
{"skill_name": "video-analyzer", "params": {"bvid": "x"}, "confidence": 1.0, "reason": "certain"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["confidence"] == 1.0

    def test_handles_nested_params(self) -> None:
        llm_text = """```json
{
  "skill_name": "watch-later-recommender",
  "params": {"topic": "AI", "count": 5, "target": "toview"},
  "confidence": 0.88,
  "reason": "多层参数推荐"
}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["params"] == {"topic": "AI", "count": 5, "target": "toview"}

    def test_handles_chinese_text_in_reason(self) -> None:
        llm_text = """```json
{"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.92, "reason": "用户明确要求分析视频BV1xx的内容"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert "明确要求" in result["reason"]

    def test_code_fence_with_spaces_before_json(self) -> None:
        llm_text = """```json
    
    
{"skill_name": "unknown", "params": {}, "confidence": 0.3, "reason": "blank lines before"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["skill_name"] == "unknown"

    def test_text_before_fence_ignored(self) -> None:
        llm_text = """I've thought about this carefully and determined that:

```json
{"skill_name": "dyn-publisher", "params": {"text": "发布内容"}, "confidence": 0.75, "reason": "发布动态"}
```

Let me know if you need adjustments!"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["skill_name"] == "dyn-publisher"

    def test_only_json_fence_no_other_text(self) -> None:
        llm_text = """```json
{"skill_name": "fav-organizer", "params": {}, "confidence": 0.85, "reason": "仅JSON"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert result["skill_name"] == "fav-organizer"


class TestParseLLMResultDictStructure:
    """parse_llm_result — returned dict has correct structure and types."""

    def test_result_has_all_required_keys(self) -> None:
        llm_text = """```json
{"skill_name": "unknown", "params": {}, "confidence": 0.5, "reason": "test"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert set(result.keys()) == {"skill_name", "params", "confidence", "reason"}

    def test_params_is_dict(self) -> None:
        llm_text = """```json
{"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.9, "reason": "test"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert isinstance(result["params"], dict)

    def test_confidence_is_float(self) -> None:
        llm_text = """```json
{"skill_name": "unknown", "params": {}, "confidence": 0.5, "reason": "test"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert isinstance(result["confidence"], float)

    def test_reason_is_string(self) -> None:
        llm_text = """```json
{"skill_name": "unknown", "params": {}, "confidence": 0.5, "reason": "reasonable"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert isinstance(result["reason"], str)

    def test_skill_name_is_string(self) -> None:
        llm_text = """```json
{"skill_name": "unknown", "params": {}, "confidence": 0.5, "reason": "test"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        assert isinstance(result["skill_name"], str)

    def test_extra_keys_are_allowed(self) -> None:
        """Extra keys in LLM output should not cause failure."""
        llm_text = """```json
{"skill_name": "unknown", "params": {}, "confidence": 0.5, "reason": "test", "extra_field": "ignored"}
```"""
        result = parse_llm_result(llm_text)
        assert result is not None
        # Extra field is in the raw dict but our validated dict should only have 4 keys
        assert set(result.keys()) == {"skill_name", "params", "confidence", "reason"}
