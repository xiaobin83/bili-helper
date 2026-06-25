"""Tests for src/classifier_llm.py.

Covers validation logic, prompt building, existing-folder matching,
and the ``"未分类"`` fallback path. All item types are now processed.
"""

import pytest

from src.classifier_llm import classify_items, validate_category, build_classification_prompt
from src.models import ClassificationResult, Folder, FavoritedItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    id: int = 1,
    type: int = 2,
    title: str = "测试视频",
    bvid: str = "BV1xx411c7mD",
    *,
    intro: str = "",
    zone_tname: str = "",
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
        intro=intro,
        zone_tname=zone_tname,
    )


def _make_folder(
    id: int = 1,
    fid: int = 0,
    title: str = "默认收藏夹",
) -> Folder:
    return Folder(id=id, fid=fid, mid=100, attr=1, title=title, media_count=0)


# ---------------------------------------------------------------------------
# validate_category
# ---------------------------------------------------------------------------


class TestValidateCategory:
    """Unit tests for validate_category helper."""

    def test_valid_short_name(self):
        assert validate_category("科技") == "科技"

    def test_valid_six_chars(self):
        assert validate_category("游戏攻略教程") == "游戏攻略教程"

    def test_valid_two_chars_boundary(self):
        assert validate_category("音乐") == "音乐"

    def test_valid_six_chars_boundary(self):
        assert validate_category("科普人文历史") == "科普人文历史"

    def test_empty_string(self):
        assert validate_category("") is None

    def test_whitespace_only(self):
        assert validate_category("   ") is None

    def test_whitespace_trim(self):
        assert validate_category("  科技  ") == "科技"

    def test_too_short_after_trim(self):
        assert validate_category(" 科 ") is None

    def test_too_long_all_chinese(self):
        assert validate_category("这是一个非常长的类别名称") is None

    def test_only_digits(self):
        assert validate_category("123456") is None

    def test_only_ascii_letters(self):
        assert validate_category("tech") is None

    def test_only_punctuation(self):
        assert validate_category("!@#$%") is None

    def test_chinese_with_digits(self):
        assert validate_category("科技123") == "科技"

    def test_chinese_with_ascii(self):
        assert validate_category("科技abc") == "科技"

    def test_chinese_with_punctuation(self):
        assert validate_category("科技！@#") == "科技"

    def test_fallback_too_few_chars(self):
        assert validate_category("科123") is None

    def test_fallback_extracts_from_mixed(self):
        assert validate_category("类别：科技 - 推荐") == "类别科技推荐"

    def test_english_category_rejected(self):
        assert validate_category("Music Video") is None

    def test_japanese_kana_rejected(self):
        assert validate_category("ゲーム") is None


# ---------------------------------------------------------------------------
# classify_items
# ---------------------------------------------------------------------------


class TestClassifyItems:
    """Integration-style tests with mocked llm_func."""

    def test_valid_classification_new_folder(self):
        item = _make_item(title="Python入门教程")
        folders = [_make_folder(title="默认收藏夹")]

        results = classify_items([item], folders, llm_func=lambda _: "编程")
        assert len(results) == 1
        r = results[0]
        assert r.category == "编程"
        assert r.target_folder_title == "编程"
        assert r.target_folder_exists is False

    def test_valid_classification_existing_folder(self):
        item = _make_item(title="机器学习实战")
        folders = [
            _make_folder(id=1, title="默认收藏夹"),
            _make_folder(id=2, fid=1, title="编程"),
        ]

        results = classify_items([item], folders, llm_func=lambda _: "编程")
        r = results[0]
        assert r.category == "编程"
        assert r.target_folder_exists is True

    def test_empty_response_gets_weifenlei(self):
        item = _make_item()
        results = classify_items([item], [], llm_func=lambda _: "")
        r = results[0]
        assert r.category == "未分类"
        assert r.target_folder_title == "未分类"
        assert r.target_folder_exists is False

    def test_too_long_response_gets_weifenlei(self):
        item = _make_item()
        results = classify_items([item], [], llm_func=lambda _: "这是一个非常长的类别名称")
        assert results[0].category == "未分类"

    def test_special_chars_response_gets_weifenlei(self):
        item = _make_item()
        results = classify_items([item], [], llm_func=lambda _: "!@#$%")
        assert results[0].category == "未分类"

    def test_whitespace_only_gets_weifenlei(self):
        item = _make_item()
        results = classify_items([item], [], llm_func=lambda _: "   \n  ")
        assert results[0].category == "未分类"

    def test_all_types_are_processed(self):
        """All item types are now processed (no type-based skipping)."""
        for t in (2, 12, 4, 1):
            item = _make_item(type=t)
            results = classify_items([item], [], llm_func=lambda _: "测试")
            assert len(results) == 1, f"type={t} should be processed"
            assert results[0].item.type == t

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

        results = classify_items(items, folders, llm_func=mock)
        assert len(results) == 2
        assert results[0].category == "编程"
        assert results[0].target_folder_exists is True
        assert results[1].category == "人工智能"
        assert results[1].target_folder_exists is False

    def test_prompt_includes_title(self):
        item = _make_item(title="Python异步编程")

        prompts: list[str] = []

        def capture(prompt: str) -> str:
            prompts.append(prompt)
            return "编程"

        classify_items([item], [], llm_func=capture)
        prompt = prompts[0]
        assert "Python异步编程" in prompt
        assert "简介：无" in prompt

    def test_prompt_includes_intro_when_present(self):
        item = _make_item(title="Docker教程", intro="学习Docker容器化技术")
        prompts: list[str] = []

        classify_items([item], [], llm_func=lambda p: prompts.append(p) or "容器")
        prompt = prompts[0]
        assert "简介：学习Docker容器化技术" in prompt

    def test_prompt_includes_zone_when_present(self):
        item = _make_item(title="教程", zone_tname="科技")
        prompts: list[str] = []

        classify_items([item], [], llm_func=lambda p: prompts.append(p) or "科技")
        prompt = prompts[0]
        assert "分区：科技" in prompt

    def test_prompt_includes_existing_folders(self):
        item = _make_item()
        folders = [_make_folder(title="编程"), _make_folder(title="科技")]

        prompts: list[str] = []

        classify_items([item], folders, llm_func=lambda p: prompts.append(p) or "科技")
        prompt = prompts[0]
        assert "编程" in prompt
        assert "科技" in prompt
        assert "已有文件夹" in prompt

    def test_prompt_no_existing_folders(self):
        item = _make_item()
        prompts: list[str] = []

        classify_items([item], [], llm_func=lambda p: prompts.append(p) or "科技")
        assert "无" in prompts[0]

    def test_prompt_structure(self):
        item = _make_item(title="Docker入门")
        prompts: list[str] = []

        classify_items([item], [], llm_func=lambda p: prompts.append(p) or "容器")
        prompt = prompts[0]
        assert "标题：" in prompt
        assert "简介：" in prompt
        assert "已有文件夹：" in prompt
        assert "2-6个中文字" in prompt
        assert "优先归入已有文件夹" in prompt

    def test_returns_classification_results(self):
        item = _make_item()
        folders = [_make_folder(title="科技")]

        results = classify_items([item], folders, llm_func=lambda _: "科技")
        assert isinstance(results, list)
        assert all(isinstance(r, ClassificationResult) for r in results)

    def test_empty_input_returns_empty(self):
        results = classify_items([], [], llm_func=lambda _: "科技")
        assert results == []


class TestBuildClassificationPrompt:
    def test_prompt_with_intro_and_zone(self):
        item = _make_item(title="Python高级教程", intro="深入学习Python语言", zone_tname="知识")
        prompt = build_classification_prompt(item, ["编程", "科技"])
        assert "Python高级教程" in prompt
        assert "深入学习Python语言" in prompt
        assert "分区：知识" in prompt
        assert "编程" in prompt
        assert "科技" in prompt

    def test_prompt_without_intro_and_zone(self):
        item = _make_item(title="测试视频")
        prompt = build_classification_prompt(item, [])
        assert "简介：无" in prompt
        assert "分区" not in prompt
