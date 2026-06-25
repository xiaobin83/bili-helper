"""LLM-based intelligent categorization for B站 favorites.

Uses a configurable LLM callable to classify favorited items into
2-6 character Chinese category names based on title and intro.

The default llm_func (interactive input) is a fallback for standalone
testing; in production with OpenCode, the callable reads from session
context.
"""

import re
from typing import Callable

from src.models import ClassificationResult, Folder, FavoritedItem

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

# Pre-compiled patterns
_CHINESE_CHAR = re.compile(r"[\u4e00-\u9fff]")
_VALID_CATEGORY = re.compile(r"^[\u4e00-\u9fff]{2,6}$")


def _validate_category(response: str) -> str | None:
    """Validate an LLM response as a valid category name.

    Rules (in order):
    1. Must be 2–6 Chinese characters (CJK Unified Ideographs) — no
       digits, punctuation, spaces, or Latin letters.
    2. If the raw response fails, extract only Chinese characters and
       re-check length.  This handles common LLM artifacts such as
       ``"类别：科技 - 推荐"`` → ``"科技推荐"`` (4 chars → valid).

    Returns the validated/cleaned category name, or ``None`` when no
    valid category can be extracted.
    """
    if not response or not response.strip():
        return None

    cleaned = response.strip()

    # Fast path: the whole string is already a clean category
    if _VALID_CATEGORY.match(cleaned):
        return cleaned

    # Fallback: strip everything except Chinese characters
    chinese_only = "".join(_CHINESE_CHAR.findall(cleaned))
    if 2 <= len(chinese_only) <= 6:
        return chinese_only

    return None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_prompt(item: FavoritedItem, existing_titles: list[str]) -> str:
    """Build the Chinese prompt for LLM classification."""
    intro = getattr(item, "intro", None) or "无"
    titles_str = "、".join(existing_titles) if existing_titles else "无"

    return (
        f"根据以下B站视频的标题和简介，判断最合适的主题类别名称（2-6个中文字）。\n"
        f"标题：{item.title}\n"
        f"简介：{intro}\n"
        f"已有文件夹：{titles_str}\n"
        f"请优先归入已有文件夹，若无匹配则给出新类别名。"
    )


# ---------------------------------------------------------------------------
# Default LLM function (standalone / CLI fallback)
# ---------------------------------------------------------------------------


def _default_llm_func(prompt: str) -> str:
    """Interactive stdin fallback — prints the prompt and reads user input."""
    print(f"\n{'=' * 60}")
    print(prompt)
    print(f"{'=' * 60}")
    return input("请输入类别名称（2-6个中文字）: ").strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_by_llm(
    items: list[FavoritedItem],
    existing_folders: list[Folder],
    llm_func: Callable[[str], str] | None = None,
) -> list[ClassificationResult]:
    """Classify favorited items using an LLM callable.

    For each item that has **not** already been classified by the zone or
    upper-name classifiers, builds a Chinese prompt and invokes
    *llm_func*.  The response is validated (2–6 Chinese characters,
    no special characters); invalid responses fall back to ``"未分类"``.

    Args:
        items:
            Favorited items to classify.  Items with ``type != 2``
            (non-video) are silently skipped — the zone classifier
            already handles them as ``"其他"``.
        existing_folders:
            Current folder list.  Folder titles are included in the
            prompt so the LLM can prefer matching existing folders.
        llm_func:
            ``Callable[[str], str]`` — receives the Chinese prompt and
            must return a category name.  If ``None``, an interactive
            ``input()`` fallback is used (standalone mode).

    Returns:
        One ``ClassificationResult`` per processed item.  The result's
        ``target_folder_exists`` field is ``True`` when the returned
        category matches an existing folder title.
    """
    if llm_func is None:
        llm_func = _default_llm_func

    existing_titles = [f.title for f in existing_folders]
    results: list[ClassificationResult] = []

    for item in items:
        # Non-video items are handled by the zone classifier ("其他")
        if item.type != 2:
            continue

        prompt = _build_prompt(item, existing_titles)
        response = llm_func(prompt)
        category = _validate_category(response)

        if category is None:
            category = "未分类"

        target_exists = (
            category in existing_titles if category != "未分类" else False
        )

        results.append(
            ClassificationResult(
                item=item,
                category=category,
                target_folder_title=category,
                target_folder_exists=target_exists,
            )
        )

    return results
