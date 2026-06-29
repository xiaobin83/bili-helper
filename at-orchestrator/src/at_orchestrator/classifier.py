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


# ──────────────────────────────────────────────────────────────────────
# Skill prompt builders (Phase 2)
# ──────────────────────────────────────────────────────────────────────

_VIDEO_ANALYZER_PROMPT = """你是 B站 视频分析助手。用户请求分析一个B站视频，请执行以下任务：

1. 获取视频详情、热评、高能进度条、AI总结、播放地址等信息
2. 生成人性化的回复文本，总结视频内容并回应评论者

任务信息：
- 视频 BV号: {bvid}
- 用户评论: {content}
- 用户昵称: {nickname}

请输出两部分：
1. 回复文本（直接回复给用户的文字，人性化、友好）
2. JSON 结构化数据

输出格式：
```
回复文本：
{{你的回复内容}}

```json
{{
  "reply_content": "回复文本（与上面一致）",
  "bvid": "{bvid}",
  "skill": "video-analyzer"
}}
```"""

_WATCH_LATER_PROMPT = """你是 B站 视频推荐助手。用户请求推荐视频，请执行以下任务：

1. 根据用户的需求（主题、数量等）从热门和排行中精选视频
2. 生成推荐回复文本

任务信息：
- 推荐主题: {topic}
- 用户评论: {content}
- 用户昵称: {nickname}

请输出两部分：
1. 推荐回复文本（告诉用户你推荐了哪些视频）
2. JSON 结构化数据

输出格式：
```
推荐回复：
{{你的回复内容}}

```json
{{
  "reply_content": "推荐回复文本（与上面一致）",
  "recommended_bvids": ["BV1xx", "BV2xx"],
  "reasons": ["原因1", "原因2"],
  "skill": "watch-later-recommender"
}}
```"""

_GENERIC_SKILL_PROMPT = """你是 B站 互动助手。用户提出了以下请求，请根据内容生成恰当的回复。

任务信息：
- 技能: {skill_name}
- 用户评论: {content}
- 用户昵称: {nickname}

请输出两部分：
1. 回复文本（直接回复给用户的文字）
2. JSON 结构化数据

输出格式：
```
回复文本：
{{你的回复内容}}

```json
{{
  "reply_content": "回复文本（与上面一致）",
  "skill": "{skill_name}"
}}
```"""


def build_skill_prompt(task_dict: dict[str, Any], classification: dict[str, Any]) -> str:
    """Build an LLM prompt for a specific skill based on classification.

    Supports ``video-analyzer`` (video analysis) and
    ``watch-later-recommender`` (video recommendation).  Unknown skills
    receive a generic prompt.

    Args:
        task_dict: A task dict with ``content`` and ``user_nickname``.
        classification: A classification dict with ``skill_name`` and
                        ``params``.

    Returns:
        Formatted prompt string instructing the LLM to produce both
        human-readable reply text and structured JSON data.
    """
    skill_name: str = classification.get("skill_name", "unknown")
    params: dict[str, Any] = classification.get("params", {})
    content: str = str(task_dict.get("content", ""))
    nickname: str = str(task_dict.get("user_nickname", "用户"))

    if skill_name == "video-analyzer":
        bvid = params.get("bvid", "BV1xx")
        return _VIDEO_ANALYZER_PROMPT.format(
            bvid=bvid, content=content, nickname=nickname
        )

    if skill_name == "watch-later-recommender":
        topic = params.get("topic", content if content else "热门推荐")
        return _WATCH_LATER_PROMPT.format(
            topic=topic, content=content, nickname=nickname
        )

    return _GENERIC_SKILL_PROMPT.format(
        skill_name=skill_name, content=content, nickname=nickname
    )


def parse_skill_result(skill_name: str, llm_text: str) -> dict[str, Any] | None:
    """Extract structured skill result from LLM text output.

    Parses the LLM response for a given skill, extracting
    ``reply_content`` (the text to post as a reply) and any
    skill-specific structured data.

    Args:
        skill_name: The skill name (e.g. ``"video-analyzer"``).
        llm_text: Raw text response from an LLM.

    Returns:
        A dict with at least ``"reply_content"`` and optionally
        skill-specific keys like ``"bvid"`` or ``"recommended_bvids"``,
        or ``None`` if no valid result could be extracted.
    """
    json_str = _extract_json_text(llm_text)
    parsed: dict[str, Any] = {}

    # Try to parse JSON from the output
    if json_str is not None:
        try:
            parsed = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            parsed = {}

    # Extract reply_content — prefer JSON field, fall back to raw text
    reply_content = ""
    if isinstance(parsed.get("reply_content"), str) and parsed["reply_content"].strip():
        reply_content = parsed["reply_content"]
    else:
        reply_content = llm_text.strip()

    result: dict[str, Any] = {
        "skill": skill_name,
        "reply_content": reply_content,
    }

    if skill_name == "video-analyzer":
        bvid = parsed.get("bvid", "")
        result["bvid"] = bvid
    elif skill_name == "watch-later-recommender":
        bvids = parsed.get("recommended_bvids", [])
        reasons = parsed.get("reasons", [])
        result["recommended_bvids"] = bvids if isinstance(bvids, list) else []
        result["reasons"] = reasons if isinstance(reasons, list) else []

    return result
