"""Message classifier — LLM prompt builder and result parser.

Provides:
- ``build_classification_prompt()``: Builds an LLM prompt from a single task.
- ``build_batch_classification_prompt()``: Builds a batch prompt from
  multiple tasks (the recommended flow).
- ``parse_llm_result()``: Extracts and validates JSON (array or single
  object) from an LLM text response.
"""

from __future__ import annotations

import json
import re
from typing import Any

from at_orchestrator.models import VALID_SKILLS

# ──────────────────────────────────────────────────────────────────────
# Business context mapping
# ──────────────────────────────────────────────────────────────────────

_BUSINESS_CONTEXT_MAP: dict[int, str] = {
    1: "视频评论",
    11: "动态回复",
    17: "动态",
}

# ──────────────────────────────────────────────────────────────────────
# Classification prompt template (batch)
# ──────────────────────────────────────────────────────────────────────

_BATCH_CLASSIFICATION_PROMPT = """你是 B站 at-orchestrator 消息分类助手。
请将以下消息逐条分类到最匹配的技能。

可用技能：
- video-analyzer: 询问视频详情、分析视频内容
- watch-later-recommender: 推荐视频、稍后再看推荐
- unknown: 无法匹配以上任何技能

每条消息都需要分类，按顺序输出分类结果数组。

Few-shot 示例（消息列表 → 分类数组）：
消息：
<message id=1001 source=at>分析这个视频BV1xx</message>
<message id=1002 source=reply>今天天气不错</message>

输出：
[
{{"msg_id": 1001, "skill_name": "video-analyzer", "params": {{"bvid": "BV1xx"}}, "confidence": 0.95, "reason": "用户明确要求分析视频"}},
{{"msg_id": 1002, "skill_name": "unknown", "params": {{}}, "confidence": 0.95, "reason": "与B站功能无关的闲聊消息"}}
]

现在请分类以下 {task_count} 条消息：
{messages_block}

业务上下文：{business_context}

请只输出 JSON 数组，不要额外文字。
每条输出格式：
{{"msg_id": ..., "skill_name": "...", "params": {{...}}, "confidence": 0.0-1.0, "reason": "..."}}"""


# ──────────────────────────────────────────────────────────────────────
# Single-task prompt builder (legacy)
# ──────────────────────────────────────────────────────────────────────

_SINGLE_CLASSIFICATION_PROMPT = """你是 B站 at-orchestrator 消息分类助手。
请将以下消息分类到最匹配的技能。

可用技能：
- video-analyzer: 询问视频详情、分析视频内容
- watch-later-recommender: 推荐视频、稍后再看推荐
- unknown: 无法匹配以上任何技能

Few-shot 示例：
消息：
<message id=1001 source=at>分析这个视频BV1xx</message>

输出：
[{{"msg_id": 1001, "skill_name": "video-analyzer", "params": {{"bvid": "BV1xx"}}, "confidence": 0.95, "reason": "用户明确要求分析视频"}}]

现在请分类以下消息：
<message id={msg_id} source={source}>{content}</message>

业务上下文：{business_context}

请只输出 JSON 数组，不要额外文字。
每条格式：
{{"msg_id": ..., "skill_name": "...", "params": {{...}}, "confidence": 0.0-1.0, "reason": "..."}}"""


def build_classification_prompt(task_dict: dict[str, Any]) -> str:
    """Build an LLM classification prompt from a single task dictionary.

    Args:
        task_dict: Dict with ``msg_id``, ``source``, ``content`` and
                   ``business_id``.

    Returns:
        Formatted prompt string expecting a JSON array output.
    """
    msg_id = task_dict.get("msg_id", 0)
    source = task_dict.get("source", "unknown")
    content = task_dict.get("content", "")
    business_id = task_dict.get("business_id", 0)

    business_context = _BUSINESS_CONTEXT_MAP.get(business_id, f"未知业务 (ID: {business_id})")

    return _SINGLE_CLASSIFICATION_PROMPT.format(
        msg_id=msg_id,
        source=source,
        content=content,
        business_context=business_context,
    )


def build_batch_classification_prompt(tasks: list[dict[str, Any]]) -> str:
    """Build an LLM classification prompt from multiple tasks.

    Args:
        tasks: List of task dicts, each with ``msg_id``, ``source``,
               ``content``, and ``business_id``.

    Returns:
        Formatted prompt string expecting a JSON array output with one
        entry per input message.
    """
    # Build messages block
    lines: list[str] = []
    contexts: set[str] = set()
    for t in tasks:
        msg_id = t.get("msg_id", 0)
        source = t.get("source", "unknown")
        content = t.get("content", "")
        lines.append(
            f"<message id={msg_id} source={source}>{content}</message>"
        )
        business_id = t.get("business_id", 0)
        ctx = _BUSINESS_CONTEXT_MAP.get(business_id, f"未知业务 (ID: {business_id})")
        contexts.add(ctx)

    messages_block = "\n".join(lines)
    business_context = ", ".join(sorted(contexts)) if contexts else "未知"

    return _BATCH_CLASSIFICATION_PROMPT.format(
        task_count=len(tasks),
        messages_block=messages_block,
        business_context=business_context,
    )


# ──────────────────────────────────────────────────────────────────────
# JSON extraction
# ──────────────────────────────────────────────────────────────────────

# Regex for ```json ... ``` and ``` ... ``` fences.
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)

# Regex for outermost { ... } or [ ... ] as fallback.
_BARE_JSON_RE = re.compile(r"(\[.*\]|\{.*\})", re.DOTALL)


def _extract_json_text(text: str) -> str | None:
    """Extract a JSON string (array or object) from LLM output text.

    Tries ```json/``` fences first, then falls back to raw [{...}] or
    {...} boundaries.
    """
    # Prefer ```json / ``` code fences
    m = _FENCE_RE.search(text)
    if m:
        candidate = m.group(1).strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate

    # Fallback: find outermost [...] or {...}
    m = _BARE_JSON_RE.search(text)
    if m:
        return m.group(1)

    return None


# ──────────────────────────────────────────────────────────────────────
# Per-entry validation
# ──────────────────────────────────────────────────────────────────────


def _validate_classification(data: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and normalize a single classification dict.

    Returns a clean dict with the 4 required keys plus optional
    ``msg_id``, or ``None`` if validation fails.
    """
    if not isinstance(data, dict):
        return None

    required_keys = {"skill_name", "params", "confidence", "reason"}
    if not required_keys.issubset(data.keys()):
        return None

    skill_name = data["skill_name"]
    params = data["params"]
    confidence = data["confidence"]
    reason = data["reason"]

    if not isinstance(skill_name, str) or skill_name not in VALID_SKILLS:
        return None
    if not isinstance(params, dict):
        return None
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return None
    confidence = float(confidence)
    if confidence < 0.0 or confidence > 1.0:
        return None
    if not isinstance(reason, str):
        return None

    result: dict[str, Any] = {
        "skill_name": skill_name,
        "params": params,
        "confidence": confidence,
        "reason": reason,
    }

    msg_id = data.get("msg_id")
    if msg_id is not None:
        result["msg_id"] = msg_id

    return result


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def parse_llm_result(llm_text: str) -> list[dict[str, Any]] | None:
    """Extract and validate classification results from LLM text output.

    The LLM is expected to return a JSON array of classification objects,
    one per input message.  A single JSON object is also accepted
    (wrapped into a list for backward compatibility).

    Args:
        llm_text: Raw text response from an LLM.

    Returns:
        A list of validated classification dicts (each with keys
        ``skill_name``, ``params``, ``confidence``, ``reason``, and
        optionally ``msg_id``), or ``None`` if no valid JSON was found.

    Invalid entries are silently filtered out.  If the LLM returns a
    single object instead of an array, it is wrapped into a single-element
    list.
    """
    json_str = _extract_json_text(llm_text)
    if json_str is None:
        return None

    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    # Normalise to list
    items: list[Any]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = [data]
    else:
        return None

    validated: list[dict[str, Any]] = []
    for item in items:
        v = _validate_classification(item)
        if v is not None:
            validated.append(v)

    return validated if validated else None
