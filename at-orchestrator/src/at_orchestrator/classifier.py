"""Message classifier — LLM prompt builder and result parser.

Provides:
- ``build_classification_prompt()``: Builds an LLM prompt to classify an AT message
  into one of 3 skills (video-analyzer, watch-later-recommender, unknown).
- ``parse_llm_result()``: Extracts and validates JSON from an LLM text response.
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
# Classification prompt template
# ──────────────────────────────────────────────────────────────────────

_CLASSIFICATION_PROMPT = """你是 B站 at-orchestrator 消息分类助手。
请将以下 @ 消息分类到最匹配的技能。

可用技能：
- video-analyzer: 询问视频详情、分析视频内容
- watch-later-recommender: 推荐视频、稍后再看推荐
- unknown: 无法匹配以上任何技能

Few-shot 示例：
1. 消息: "分析这个视频BV1xx"
   输出: {{"skill_name": "video-analyzer", "params": {{"bvid": "BV1xx"}}, "confidence": 0.95, "reason": "用户明确要求分析视频"}}

2. 消息: "今天天气不错"
   输出: {{"skill_name": "unknown", "params": {{}}, "confidence": 0.95, "reason": "与B站功能无关的闲聊消息"}}

现在请分类以下消息：
<message>{content}</message>

业务上下文：{business_context}

请只输出 JSON，不要额外文字。
使用以下格式：
{{"skill_name": "...", "params": {{...}}, "confidence": 0.0-1.0, "reason": "..."}}"""


def build_classification_prompt(task_dict: dict[str, Any]) -> str:
    """Build an LLM classification prompt from a task dictionary.

    Args:
        task_dict: Dict with ``content`` (user message text) and
                   ``business_id`` (int context identifier).

    Returns:
        Formatted prompt string ready for LLM consumption.

    The prompt includes skill descriptions, 2 few-shot examples,
    user content wrapped in ``<message>...</message>`` tags for injection
    protection, and business context.
    """
    content = task_dict.get("content", "")
    business_id = task_dict.get("business_id", 0)

    business_context = _BUSINESS_CONTEXT_MAP.get(business_id, f"未知业务 (ID: {business_id})")

    return _CLASSIFICATION_PROMPT.format(
        content=content,
        business_context=business_context,
    )


# ──────────────────────────────────────────────────────────────────────
# JSON extraction
# ──────────────────────────────────────────────────────────────────────

# Regex to match `` ```json ... ``` `` or `` ``` ... ``` `` fences.
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)

# Regex to match outermost ``{ ... }`` as a fallback when no fences.
_BARE_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_text(text: str) -> str | None:
    """Extract a JSON object string from LLM output text.

    Tries `` ```json ... ``` `` fences first, then falls back to raw
    ``{ ... }`` boundaries.

    Args:
        text: Raw LLM response text.

    Returns:
        Raw JSON string on success, ``None`` if nothing found.
    """
    # Prefer ```json / ``` code fences
    m = _FENCE_RE.search(text)
    if m:
        candidate = m.group(1).strip()
        if candidate.startswith("{"):
            return candidate

    # Fallback: find outermost { ... }
    m = _BARE_JSON_RE.search(text)
    if m:
        return m.group(0)

    return None


# ──────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────

def _validate_classification(data: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and normalize a parsed classification dict.

    Returns a clean dict with only the 4 expected keys, or ``None`` if
    the data fails structural or value validation.
    """
    # Must be a dict
    if not isinstance(data, dict):
        return None

    # Required keys
    required_keys = {"skill_name", "params", "confidence", "reason"}
    if not required_keys.issubset(data.keys()):
        return None

    skill_name = data["skill_name"]
    params = data["params"]
    confidence = data["confidence"]
    reason = data["reason"]

    # Validate skill_name
    if not isinstance(skill_name, str) or skill_name not in VALID_SKILLS:
        return None

    # Validate params is a dict
    if not isinstance(params, dict):
        return None

    # Validate confidence is a float in [0, 1]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return None
    confidence = float(confidence)
    if confidence < 0.0 or confidence > 1.0:
        return None

    # Validate reason is a non-empty string
    if not isinstance(reason, str):
        return None

    return {
        "skill_name": skill_name,
        "params": params,
        "confidence": confidence,
        "reason": reason,
    }


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def parse_llm_result(llm_text: str) -> dict[str, Any] | None:
    """Extract and validate a classification result from LLM text output.

    Args:
        llm_text: Raw text response from an LLM (expected to contain JSON).

    Returns:
        A dict with keys ``skill_name``, ``params``, ``confidence``,
        ``reason`` on success, or ``None`` if no valid JSON was found or
        the data fails validation.

    The function handles:
    - `` ```json ... ``` `` and `` ``` ... ``` `` code fences
    - bare ``{ ... }`` JSON blocks
    - multi-line JSON (via ``re.DOTALL``)
    """
    json_str = _extract_json_text(llm_text)
    if json_str is None:
        return None

    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None

    return _validate_classification(data)
