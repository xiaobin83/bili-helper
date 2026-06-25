"""Tests for src/classifier_llm.py.

Covers validation logic, prompt building, type-based skipping,
existing-folder matching, and the ``"未分类"`` fallback path.
"""

import pytest

from src.classifier_llm import classify_by_llm, _validate_category
from src.models import ClassificationResult, Folder, FavoritedItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    id: int = 1,
    type: int = 2,
    title: str = "测试视频",
    bvid: str = "BV1xx411c7mD",
) -> FavoritedItem:
    return FavoritedItem(
        id=id,
        type=type,
        title=title,
        bvid=bvid,
        upper_name="UP主",
        upper_mid=200,
        attr=0,
        fav_time=1234567890,
    )


def _make_folder(
    id: int = 1,
    fid: int = 0,
    title: str = "默认收藏夹",
) -> Folder:
    return Folder(id=id, fid=fid, mid=100, attr=1, title=title, media_count=0)


# ---------------------------------------------------------------------------
# _validate_category
# ---------------------------------------------------------------------------


class TestValidateCategory:
    """Unit tests for the internal _validate_category helper."""

    def test_valid_short_name(self):
        assert _validate_category("科技") == "科技"

    def test_valid_six_chars(self):
        assert _validate_category("游戏攻略教程") == "游戏攻略教程"

    def test_valid_two_chars_boundary(self):
        assert _validate_category("音乐") == "音乐"

    def test_valid_six_chars_boundary(self):
        assert _validate_category("科普人文历史") == "科普人文历史"

    def test_empty_string(self):
        assert _validate_category("") is None

    def test_whitespace_only(self):
        assert _validate_category("   ") is None

    def test_whitespace_trim(self):
        assert _validate_category("  科技  ") == "科技"

    def test_too_short_after_trim(self):
        assert _validate_category(" 科 ") is None  # single char

    def test_too_long_all_chinese(self):
        # 12 Chinese characters — exceeds the 6-char limit
        assert _validate_category("这是一个非常长的类别名称") is None

    def test_only_digits(self):
        assert _validate_category("123456") is None

    def test_only_ascii_letters(self):
        assert _validate_category("tech") is None

    def test_only_punctuation(self):
        assert _validate_category("!@#$%") is None

    def test_chinese_with_digits(self):
        # 科技 is 2 chars — enough after extraction
        assert _validate_category("科技123") == "科技"

    def test_chinese_with_ascii(self):
        assert _validate_category("科技abc") == "科技"

    def test_chinese_with_punctuation(self):
        assert _validate_category("科技！@#") == "科技"

    def test_fallback_too_few_chars(self):
        # Only one Chinese character survives extraction → invalid
        assert _validate_category("科123") is None

    def test_fallback_extracts_from_mixed(self):
        # Common LLM artifact: "类别：科技 - 推荐"
        assert _validate_category("类别：科技 - 推荐") == "类别科技推荐"

    def test_english_category_rejected(self):
        # "Music" isn't Chinese → no chars extracted → invalid
        assert _validate_category("Music Video") is None

    def test_japanese_kana_rejected(self):
        # ひらがな is not in CJK Unified Ideographs range
        assert _validate_category("ゲーム") is None


# ---------------------------------------------------------------------------
# classify_by_llm
# ---------------------------------------------------------------------------


class TestClassifyByLLM:
    """Integration-style tests with mocked llm_func."""

    # -- basic classification ------------------------------------------------

    def test_valid_classification_new_folder(self):
        """A valid category that doesn't match any existing folder."""
        item = _make_item(title="Python入门教程")
        folders = [_make_folder(title="默认收藏夹")]

        results = classify_by_llm(
            [item], folders, llm_func=lambda _: "编程"
        )
        assert len(results) == 1
        r = results[0]
        assert r.category == "编程"
        assert r.target_folder_title == "编程"
        assert r.target_folder_exists is False

    def test_valid_classification_existing_folder(self):
        """A valid category that matches an existing folder title."""
        item = _make_item(title="机器学习实战")
        folders = [
            _make_folder(id=1, title="默认收藏夹"),
            _make_folder(id=2, fid=1, title="编程"),
        ]

        results = classify_by_llm(
            [item], folders, llm_func=lambda _: "编程"
        )
        r = results[0]
        assert r.category == "编程"
        assert r.target_folder_exists is True

    # -- invalid responses → "未分类" --------------------------------------

    def test_empty_response_gets_weifenlei(self):
        item = _make_item()
        results = classify_by_llm(
            [item], [], llm_func=lambda _: ""
        )
        r = results[0]
        assert r.category == "未分类"
        assert r.target_folder_title == "未分类"
        assert r.target_folder_exists is False

    def test_too_long_response_gets_weifenlei(self):
        item = _make_item()
        results = classify_by_llm(
            [item], [],
            llm_func=lambda _: "这是一个非常长的类别名称",
        )
        assert results[0].category == "未分类"

    def test_special_chars_response_gets_weifenlei(self):
        item = _make_item()
        results = classify_by_llm(
            [item], [], llm_func=lambda _: "!@#$%"
        )
        assert results[0].category == "未分类"

    def test_whitespace_only_gets_weifenlei(self):
        item = _make_item()
        results = classify_by_llm(
            [item], [], llm_func=lambda _: "   \n  "
        )
        assert results[0].category == "未分类"

    # -- type-based skipping -------------------------------------------------

    def test_type_two_is_processed(self):
        """type==2 (video) is processed by LLM classifier."""
        item = _make_item(type=2)
        results = classify_by_llm(
            [item], [], llm_func=lambda _: "科技"
        )
        assert len(results) == 1

    def test_type_non_two_is_skipped(self):
        """type!=2 items (audio/article) are silently skipped."""
        for t in (12, 4, 1):
            item = _make_item(type=t)
            results = classify_by_llm(
                [item], [], llm_func=lambda _: "测试"
            )
            assert len(results) == 0, f"type={t} should be skipped"

    def test_mixed_types_only_videos_processed(self):
        items = [
            _make_item(id=1, type=2, title="视频A"),
            _make_item(id=2, type=12, title="音频B"),
            _make_item(id=3, type=2, title="视频C"),
        ]
        results = classify_by_llm(
            items, [], llm_func=lambda _: "分类"
        )
        assert len(results) == 2
        assert results[0].item.id == 1
        assert results[1].item.id == 3

    # -- multiple items ------------------------------------------------------

    def test_multiple_items_each_classified(self):
        items = [
            _make_item(id=1, title="Python基础"),
            _make_item(id=2, title="机器学习"),
        ]
        folders = [_make_folder(title="编程")]

        def mock(prompt: str) -> str:
            if "Python" in prompt:
                return "编程"
            return "人工智能"

        results = classify_by_llm(items, folders, llm_func=mock)
        assert len(results) == 2
        assert results[0].category == "编程"
        assert results[0].target_folder_exists is True
        assert results[1].category == "人工智能"
        assert results[1].target_folder_exists is False

    # -- prompt content verification -----------------------------------------

    def test_prompt_includes_title(self):
        item = _make_item(title="Python异步编程")

        prompts: list[str] = []

        def capture(prompt: str) -> str:
            prompts.append(prompt)
            return "编程"

        classify_by_llm([item], [], llm_func=capture)
        prompt = prompts[0]
        assert "Python异步编程" in prompt
        # intro defaults to "无" when item has no intro attribute
        assert "简介：无" in prompt

    def test_prompt_includes_existing_folders(self):
        item = _make_item()
        folders = [
            _make_folder(title="编程"),
            _make_folder(title="科技"),
        ]
        prompts: list[str] = []

        classify_by_llm(
            [item], folders, llm_func=lambda p: prompts.append(p) or "科技"
        )
        prompt = prompts[0]
        assert "编程" in prompt
        assert "科技" in prompt
        assert "已有文件夹" in prompt

    def test_prompt_no_existing_folders(self):
        item = _make_item()
        prompts: list[str] = []

        classify_by_llm(
            [item], [], llm_func=lambda p: prompts.append(p) or "科技"
        )
        assert "无" in prompts[0]

    def test_prompt_structure(self):
        item = _make_item(title="Docker入门")
        prompts: list[str] = []

        classify_by_llm(
            [item], [], llm_func=lambda p: prompts.append(p) or "容器"
        )
        prompt = prompts[0]
        assert "标题：" in prompt
        assert "简介：" in prompt
        assert "已有文件夹：" in prompt
        assert "2-6个中文字" in prompt
        assert "优先归入已有文件夹" in prompt

    # -- return type ---------------------------------------------------------

    def test_returns_classification_results(self):
        item = _make_item()
        folders = [_make_folder(title="科技")]

        results = classify_by_llm(
            [item], folders, llm_func=lambda _: "科技"
        )
        assert isinstance(results, list)
        assert all(isinstance(r, ClassificationResult) for r in results)

    def test_empty_input_returns_empty(self):
        results = classify_by_llm([], [], llm_func=lambda _: "科技")
        assert results == []
