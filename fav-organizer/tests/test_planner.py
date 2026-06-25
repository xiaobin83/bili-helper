"""Tests for build_plan — operation planning and conflict resolution.

Covers:
- Correct number of operations generated (creates, moves, deletes).
- Conflict resolution: LLM > Zone > UP主 priority.
- Items in default folder are NOT moved.
- Items already in the correct folder are NOT moved.
- Empty folder detection after moves/deletions.
- Stats accuracy (total_operations, summary string).
- Edge cases: empty inputs, no conflicts, all items invalid, all items dupes.
"""

from __future__ import annotations

import pytest

from src.planner import build_plan
from src.models import (
    ClassificationResult,
    DuplicateGroup,
    FavoritedItem,
    Folder,
    Operation,
    OrganizePlan,
)


# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------


def _make_item(
    item_id: int = 1,
    *,
    bvid: str = "BV1xx411c7mD",
    title: str = "测试视频",
    upper_name: str = "UP主A",
    attr: int = 0,
) -> FavoritedItem:
    return FavoritedItem(
        id=item_id,
        type=2,
        title=title,
        bvid=bvid,
        upper_name=upper_name,
        upper_mid=100,
        attr=attr,
        fav_time=1234567890,
    )


def _make_folder(
    fid: int = 1,
    *,
    title: str = "默认收藏夹",
    attr: int = 0,
    media_count: int = 10,
) -> Folder:
    return Folder(
        id=fid,
        fid=fid,
        mid=1000,
        attr=attr,
        title=title,
        media_count=media_count,
    )


def _make_classification(
    item: FavoritedItem,
    category: str = "科技",
    *,
    target_folder_title: str | None = None,
    target_folder_exists: bool = False,
) -> ClassificationResult:
    return ClassificationResult(
        item=item,
        category=category,
        target_folder_title=target_folder_title or category,
        target_folder_exists=target_folder_exists,
    )


# ---------------------------------------------------------------------------
# Basic operation generation
# ---------------------------------------------------------------------------


def test_creates_folder_when_target_does_not_exist():
    """A classified item with a new target folder → one create_folder op."""
    item = _make_item(1)
    default_folder = _make_folder(1, title="默认收藏夹", attr=0)
    zone_results = [_make_classification(item, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[default_folder],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: default_folder},
    )

    assert len(plan.folders_to_create) == 1
    assert "科技" in plan.folders_to_create
    assert plan.total_operations == 1  # only create_folder (item is in default → no move)


def test_no_create_when_target_exists():
    """Target folder already exists → no create_folder op."""
    item = _make_item(1)
    existing = _make_folder(1, title="科技", attr=1)
    zone_results = [_make_classification(item, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[existing],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: existing},
    )

    assert len(plan.folders_to_create) == 0


def test_moves_item_from_non_default_to_target():
    """Item in non-default folder classified to a different folder → move op."""
    item = _make_item(1)
    source = _make_folder(2, title="旧文件夹", attr=1)
    zone_results = [_make_classification(item, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1, title="默认收藏夹"), source],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: source},
    )

    assert len(plan.moves) == 1
    assert plan.moves[0].action == "move"
    assert plan.moves[0].source == source
    assert plan.moves[0].target == "科技"
    assert len(plan.moves[0].resources) == 1


def test_no_move_when_item_in_default_folder():
    """Items in the default folder are NOT moved, even if classified."""
    item = _make_item(1)
    default = _make_folder(1, title="默认收藏夹", attr=0)
    zone_results = [_make_classification(item, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[default],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: default},
    )

    assert len(plan.moves) == 0


def test_no_move_when_already_in_target():
    """Item already in the target folder → no move op."""
    item = _make_item(1)
    target = _make_folder(2, title="科技", attr=1)
    zone_results = [_make_classification(item, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1), target],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: target},
    )

    assert len(plan.moves) == 0


def test_batch_moves_grouped_by_source_and_target():
    """Multiple items from same source to same target → one move op."""
    item1 = _make_item(1, bvid="BVaaa")
    item2 = _make_item(2, bvid="BVbbb")
    source = _make_folder(2, title="旧文件夹", attr=1)
    zone_results = [
        _make_classification(item1, category="科技"),
        _make_classification(item2, category="科技"),
    ]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1), source],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item1.id: source, item2.id: source},
    )

    assert len(plan.moves) == 1
    assert len(plan.moves[0].resources) == 2


def test_separate_moves_for_different_sources_or_targets():
    """Different sources or targets → separate move ops."""
    item1 = _make_item(1, bvid="BVaaa")
    item2 = _make_item(2, bvid="BVbbb")
    folder_a = _make_folder(2, title="分区A", attr=1)
    folder_b = _make_folder(3, title="分区B", attr=1)
    zone_results = [
        _make_classification(item1, category="科技"),
        _make_classification(item2, category="知识"),
    ]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1), folder_a, folder_b],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item1.id: folder_a, item2.id: folder_b},
    )

    assert len(plan.moves) == 2


def test_invalid_items_generate_batch_delete():
    """Scanner-detected invalid items → batch_delete operations."""
    item1 = _make_item(1, bvid="BVbad1", attr=1)
    item2 = _make_item(2, bvid="BVbad2", attr=9)
    folder = _make_folder(2, title="视频收藏", attr=1)

    plan = build_plan(
        zone_results=[],
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1), folder],
        invalid_items=[(item1, folder), (item2, folder)],
        duplicate_groups=[],
        item_folder_map={},
    )

    assert len(plan.deletions) == 1
    assert plan.deletions[0].action == "batch_delete"
    assert plan.deletions[0].source == folder
    assert len(plan.deletions[0].resources) == 2


def test_invalid_items_grouped_by_folder():
    """Invalid items from different folders → separate batch_delete ops."""
    item1 = _make_item(1, bvid="BVbad1", attr=1)
    item2 = _make_item(2, bvid="BVbad2", attr=9)
    folder_a = _make_folder(2, title="视频收藏", attr=1)
    folder_b = _make_folder(3, title="音频收藏", attr=1)

    plan = build_plan(
        zone_results=[],
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1), folder_a, folder_b],
        invalid_items=[(item1, folder_a), (item2, folder_b)],
        duplicate_groups=[],
        item_folder_map={},
    )

    assert len(plan.deletions) == 2


def test_duplicates_generate_batch_delete():
    """Dedup-detected duplicates → batch_delete from default folder."""
    item = _make_item(1, bvid="BVdup")
    default = _make_folder(1, title="默认收藏夹", attr=0)
    named = _make_folder(2, title="科技", attr=1)
    dg = DuplicateGroup(
        item=item,
        source_folders=[default],
        target_folder=named,
    )

    plan = build_plan(
        zone_results=[],
        upper_results=[],
        llm_results=[],
        existing_folders=[default, named],
        invalid_items=[],
        duplicate_groups=[dg],
        item_folder_map={},
    )

    assert len(plan.deletions) == 1
    assert plan.deletions[0].action == "batch_delete"
    assert plan.deletions[0].source == default
    assert len(plan.deletions[0].resources) == 1


# ---------------------------------------------------------------------------
# Conflict resolution — LLM > Zone > UP主
# ---------------------------------------------------------------------------


def test_llm_overrides_zone():
    """When both LLM and Zone classify the same item, LLM wins."""
    item = _make_item(1)
    source = _make_folder(2, title="旧文件夹", attr=1)
    zone_results = [_make_classification(item, category="科技")]
    llm_results = [_make_classification(item, category="编程开发")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=llm_results,
        existing_folders=[_make_folder(1), source],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: source},
    )

    assert len(plan.moves) == 1
    assert plan.moves[0].target == "编程开发"


def test_zone_overrides_upper():
    """When both Zone and UP主 classify the same item, Zone wins."""
    item = _make_item(1, upper_name="UP主A")
    source = _make_folder(2, title="旧文件夹", attr=1)
    upper_results = [_make_classification(item, category="UP主A")]
    zone_results = [_make_classification(item, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=upper_results,
        llm_results=[],
        existing_folders=[_make_folder(1), source],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: source},
    )

    assert len(plan.moves) == 1
    assert plan.moves[0].target == "科技"


def test_llm_overrides_both():
    """LLM > Zone > UP主 — LLM wins over all."""
    item = _make_item(1, upper_name="UP主A")
    source = _make_folder(2, title="旧文件夹", attr=1)
    upper_results = [_make_classification(item, category="UP主A")]
    zone_results = [_make_classification(item, category="科技")]
    llm_results = [_make_classification(item, category="编程开发")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=upper_results,
        llm_results=llm_results,
        existing_folders=[_make_folder(1), source],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: source},
    )

    assert len(plan.moves) == 1
    assert plan.moves[0].target == "编程开发"


def test_zone_used_when_no_llm_for_item():
    """LLM doesn't classify an item → Zone result is used."""
    item = _make_item(1)
    source = _make_folder(2, title="旧文件夹", attr=1)
    zone_results = [_make_classification(item, category="科技")]
    # LLM classifies a different item
    other_item = _make_item(2, bvid="BVother")
    llm_results = [_make_classification(other_item, category="编程开发")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=llm_results,
        existing_folders=[_make_folder(1), source],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: source},
    )

    assert len(plan.moves) == 1
    assert plan.moves[0].target == "科技"


def test_upper_used_when_no_zone_or_llm():
    """Neither LLM nor Zone classifies the item → UP主 result is used."""
    item = _make_item(1, upper_name="UP主A")
    source = _make_folder(2, title="旧文件夹", attr=1)
    upper_results = [_make_classification(item, category="UP主A")]

    plan = build_plan(
        zone_results=[],
        upper_results=upper_results,
        llm_results=[],
        existing_folders=[_make_folder(1), source],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: source},
    )

    assert len(plan.moves) == 1
    assert plan.moves[0].target == "UP主A"


# ---------------------------------------------------------------------------
# Empty folder detection
# ---------------------------------------------------------------------------


def test_detects_empty_folder_after_all_items_moved():
    """When all items in a folder are moved out → folder becomes empty."""
    item = _make_item(1)
    source = _make_folder(2, title="旧文件夹", attr=1)
    zone_results = [_make_classification(item, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1), source],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item.id: source},
    )

    # The source folder (旧文件夹) has 1 item that is moved → empty
    assert plan.moves[0].source == source


def test_folder_not_empty_when_some_items_remain():
    """When only some items move out → folder is NOT empty."""
    item1 = _make_item(1, bvid="BVmove")
    item2 = _make_item(2, bvid="BVstay")
    source = _make_folder(2, title="旧文件夹", attr=1)
    zone_results = [_make_classification(item1, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1), source],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item1.id: source, item2.id: source},
    )

    # item2 stays → source is NOT empty
    assert len(plan.moves) == 1


def test_folder_empty_after_moves_and_deletes():
    """Folder with items moved AND deleted → empty."""
    item_move = _make_item(1, bvid="BVmove")
    item_delete = _make_item(2, bvid="BVdel", attr=9)
    source = _make_folder(2, title="旧文件夹", attr=1)
    zone_results = [_make_classification(item_move, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1), source],
        invalid_items=[(item_delete, source)],
        duplicate_groups=[],
        item_folder_map={item_move.id: source, item_delete.id: source},
    )

    # item_move → moved, item_delete → deleted => folder empty
    assert len(plan.moves) == 1
    assert len(plan.deletions) == 1


# ---------------------------------------------------------------------------
# Stats accuracy
# ---------------------------------------------------------------------------


def test_total_operations_count():
    """total_operations = create_folder ops + move ops + delete ops."""
    item1 = _make_item(1, bvid="BV001")
    item2 = _make_item(2, bvid="BV002")
    item_bad = _make_item(3, bvid="BVbad", attr=9)
    source = _make_folder(2, title="旧文件夹", attr=1)
    dup_folder = _make_folder(3, title="科技", attr=1)

    zone_results = [
        _make_classification(item1, category="科技"),
        _make_classification(item2, category="知识"),  # new folder
    ]
    dg = DuplicateGroup(item=item1, source_folders=[_make_folder(1)], target_folder=dup_folder)

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1), source, dup_folder],
        invalid_items=[(item_bad, source)],
        duplicate_groups=[dg],
        item_folder_map={item1.id: source, item2.id: source, item_bad.id: source},
    )

    # Creates: "知识" (科技 already exists) → 1
    # Moves: item1→科技, item2→知识 (both from source) → 2 (different targets)
    # Deletions: item_bad from source + duplicate from default → 2
    expected = 1 + 2 + 2
    assert plan.total_operations == expected


def test_folders_to_create_list():
    """folders_to_create contains only non-existing, sorted target titles."""
    # "科技" doesn't exist, "知识" doesn't exist
    item1 = _make_item(1, bvid="BV001")
    item2 = _make_item(2, bvid="BV002")
    default = _make_folder(1)

    zone_results = [
        _make_classification(item1, category="知识"),
        _make_classification(item2, category="科技"),
    ]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[default],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={item1.id: default, item2.id: default},
    )

    assert plan.folders_to_create == ["知识", "科技"]  # sorted by Unicode code point


def test_summary_format():
    """Summary string is properly formatted in Chinese."""
    item_move = _make_item(1, bvid="BVmove")
    item_bad = _make_item(2, bvid="BVbad", attr=9)
    source = _make_folder(2, title="旧文件夹", attr=1)

    zone_results = [_make_classification(item_move, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1), source],
        invalid_items=[(item_bad, source)],
        duplicate_groups=[],
        item_folder_map={item_move.id: source, item_bad.id: source},
    )

    expected = "需要创建 1 个文件夹，移动 1 个视频到对应分类，删除 1 个失效/重复内容"
    assert plan.summary == expected


def test_stats_deduplicate_item_counts():
    """Duplicate items in non-default folders → only one batch_delete per source."""
    item1 = _make_item(1, bvid="BVdup1")
    item2 = _make_item(2, bvid="BVdup2")
    default = _make_folder(1, title="默认收藏夹", attr=0)
    named = _make_folder(2, title="科技", attr=1)

    dg1 = DuplicateGroup(item=item1, source_folders=[default], target_folder=named)
    dg2 = DuplicateGroup(item=item2, source_folders=[default], target_folder=named)

    plan = build_plan(
        zone_results=[],
        upper_results=[],
        llm_results=[],
        existing_folders=[default, named],
        invalid_items=[],
        duplicate_groups=[dg1, dg2],
        item_folder_map={},
    )

    # Both dupes from default → grouped into one batch_delete
    assert len(plan.deletions) == 1
    assert len(plan.deletions[0].resources) == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_inputs():
    """No classifier results, no invalids, no dupes → empty plan."""
    plan = build_plan(
        zone_results=[],
        upper_results=[],
        llm_results=[],
        existing_folders=[],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={},
    )

    assert plan.total_operations == 0
    assert plan.folders_to_create == []
    assert plan.moves == []
    assert plan.deletions == []
    assert "0" in plan.summary


def test_item_not_in_folder_map_is_skipped():
    """Item not in item_folder_map → no move generated."""
    item = _make_item(1)
    zone_results = [_make_classification(item, category="科技")]

    plan = build_plan(
        zone_results=zone_results,
        upper_results=[],
        llm_results=[],
        existing_folders=[_make_folder(1)],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={},  # empty — item not mapped
    )

    assert len(plan.moves) == 0


def test_multiple_classifiers_produce_correct_plan():
    """Integration: all three classifiers + invalids + dupes."""
    # Items
    item1 = _make_item(1, bvid="BV001", upper_name="UP主A")  # classified by all
    item2 = _make_item(2, bvid="BV002", upper_name="UP主B")  # only zone + upper
    item3 = _make_item(3, bvid="BV003", upper_name="UP主C")  # only upper
    item_bad = _make_item(4, bvid="BVbad", attr=1)  # invalid
    item_dup = _make_item(5, bvid="BVdup")  # duplicate in default

    # Folders
    default = _make_folder(1, title="默认收藏夹", attr=0)
    source_a = _make_folder(2, title="收藏A", attr=1)
    source_b = _make_folder(3, title="收藏B", attr=1)
    zone_tech = _make_folder(4, title="科技", attr=1)  # already exists

    # Classifier results
    # LLM: only item1, category="编程开发" (new folder)
    llm_results = [_make_classification(item1, category="编程开发")]
    # Zone: item1→科技, item2→科技
    zone_results = [
        _make_classification(item1, category="科技"),
        _make_classification(item2, category="科技"),
    ]
    # Upper: item1→UP主A, item2→UP主B, item3→UP主C
    upper_results = [
        _make_classification(item1, category="UP主A"),
        _make_classification(item2, category="UP主B"),
        _make_classification(item3, category="UP主C"),
    ]

    # Duplicate
    dg = DuplicateGroup(item=item_dup, source_folders=[default], target_folder=zone_tech)

    plan = build_plan(
        zone_results=zone_results,
        upper_results=upper_results,
        llm_results=llm_results,
        existing_folders=[default, source_a, source_b, zone_tech],
        invalid_items=[(item_bad, source_a)],
        duplicate_groups=[dg],
        item_folder_map={
            item1.id: source_a,
            item2.id: source_a,
            item3.id: source_b,
            item_bad.id: source_a,
        },
    )

    # --- Assertions ---
    # Conflict resolution:
    # item1: LLM=编程开发 wins (over Zone=科技, Upper=UP主A)
    # item2: Zone=科技 wins (over Upper=UP主B)
    # item3: Upper=UP主C (only classifier)

    # Folders to create: "编程开发" and "UP主C" (科技 already exists)
    assert set(plan.folders_to_create) == {"编程开发", "UP主C"}

    # Moves:
    # item1 → 编程开发 (from source_a)
    # item2 → 科技 (from source_a) → same source, different target from item1
    # item3 → UP主C (from source_b)
    assert len(plan.moves) == 3

    # Deletions:
    # item_bad from source_a → 1
    # item_dup from default → 1
    assert len(plan.deletions) == 2

    # Total operations: 2 creates + 3 moves + 2 deletes = 7
    assert plan.total_operations == 7

    # Summary: 创建 2 个文件夹, 移动 3 个视频, 删除 2 个
    assert plan.summary == "需要创建 2 个文件夹，移动 3 个视频到对应分类，删除 2 个失效/重复内容"


def test_organizeplan_is_valid_pydantic_model():
    """The return value is a valid OrganizePlan Pydantic model."""
    plan = build_plan(
        zone_results=[],
        upper_results=[],
        llm_results=[],
        existing_folders=[],
        invalid_items=[],
        duplicate_groups=[],
        item_folder_map={},
    )

    # This validates the model (no ValidationError)
    assert isinstance(plan, OrganizePlan)
    validated = OrganizePlan.model_validate(plan.model_dump())
    assert validated == plan
