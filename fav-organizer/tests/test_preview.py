"""Tests for format_preview — Markdown preview formatter.

Covers:
- All 7 sections present in output when plan has data.
- Table headers contain title and bvid columns.
- Per-section content accuracy (counts, item details, folder names).
- Edge cases: empty plan (no operations), no deletions, no moves, no folders.
"""

from __future__ import annotations

import pytest

from src.preview import format_preview
from src.models import FavoritedItem, Folder, Operation, OrganizePlan


# ---------------------------------------------------------------------------
# Helpers — build OrganizePlan objects with controllable data
# ---------------------------------------------------------------------------


def _item(
    item_id: int = 1,
    *,
    title: str = "测试视频",
    bvid: str = "BV1xx411c7mD",
    attr: int = 0,
) -> FavoritedItem:
    return FavoritedItem(
        id=item_id,
        type=2,
        title=title,
        bvid=bvid,
        upper_name="UP主A",
        upper_mid=100,
        attr=attr,
        fav_time=1234567890,
    )


def _folder(
    fid: int = 1,
    *,
    title: str = "默认收藏夹",
    attr: int = 0,
) -> Folder:
    return Folder(
        id=fid,
        fid=fid,
        mid=100,
        attr=attr,
        title=title,
        media_count=5,
    )


def _plan(
    *,
    folders_to_create: list[str] | None = None,
    moves: list[Operation] | None = None,
    deletions: list[Operation] | None = None,
    empty_folders: list[str] | None = None,
    summary: str = "需要创建 0 个文件夹，移动 0 个视频到对应分类，删除 0 个失效/重复内容",
) -> OrganizePlan:
    de = deletions or []
    mv = moves or []
    fc = folders_to_create or []
    ef = empty_folders or []
    total = len(fc) + len(mv) + len(de)
    return OrganizePlan(
        total_operations=total,
        folders_to_create=fc,
        moves=mv,
        deletions=de,
        summary=summary,
        empty_folders=ef,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFormatPreview:
    """Core preview formatting behaviour."""

    # ------------------------------------------------------------------
    # Section 1 — stats overview
    # ------------------------------------------------------------------

    def test_title_and_stats_present(self):
        """Section 1: title '整理预览' and stats line with operation counts."""
        plan = _plan()
        md = format_preview(plan)

        assert "# 整理预览" in md
        assert "## 概览" in md
        assert "共需执行" in md
        assert "0 个" in md  # "0 个操作" split across **0** markdown bold

    def test_stats_counts_accurate(self):
        """Stats section shows correct counts for creates, moves, deletions."""
        items = [_item(1, bvid="BV001"), _item(2, bvid="BV002")]
        source = _folder(1, title="旧文件夹", attr=1)
        move_op = Operation(
            action="move",
            source=source,
            target="科技区",
            resources=items,
        )
        plan = _plan(
            folders_to_create=["科技区"],
            moves=[move_op],
            summary="需要创建 1 个文件夹，移动 2 个视频到对应分类，删除 0 个失效/重复内容",
        )
        md = format_preview(plan)

        # total_operations: 1 create_folder + 1 move = 2
        assert "共需执行 **2** 个操作" in md
        assert "创建 **1** 个文件夹" in md
        assert "移动 **2** 个视频" in md
        assert "删除 **0** 个失效/重复内容" in md
        assert plan.summary in md

    # ------------------------------------------------------------------
    # Section 2 — invalid content table
    # ------------------------------------------------------------------

    def test_invalid_content_table_headers(self):
        """Section 2: table has title, bvid, and source folder columns."""
        bad_item = _item(1, title="失效视频", bvid="BVbad1", attr=1)
        source = _folder(1, title="视频收藏", attr=1)
        delete_op = Operation(
            action="batch_delete",
            source=source,
            resources=[bad_item],
        )
        plan = _plan(deletions=[delete_op])
        md = format_preview(plan)

        assert "## 失效内容" in md
        assert "| 标题 | BV号 | 来源文件夹 |" in md
        assert "失效视频" in md
        assert "`BVbad1`" in md
        assert "视频收藏" in md

    def test_invalid_content_skipped_when_no_invalids(self):
        """Section 2 omitted when no invalid-content deletions exist."""
        valid_item = _item(1, bvid="BVgood", attr=0)
        default = _folder(1, title="默认收藏夹", attr=0)
        dup_op = Operation(
            action="batch_delete",
            source=default,
            resources=[valid_item],
        )
        plan = _plan(deletions=[dup_op])
        md = format_preview(plan)

        assert "## 失效内容" not in md

    def test_invalid_content_multiple_items(self):
        """Multiple invalid items in the same deletion appear in the table."""
        bad1 = _item(1, title="失效A", bvid="BVbad1", attr=1)
        bad2 = _item(2, title="失效B", bvid="BVbad2", attr=9)
        source = _folder(1, title="收藏夹A", attr=1)
        delete_op = Operation(
            action="batch_delete",
            source=source,
            resources=[bad1, bad2],
        )
        plan = _plan(deletions=[delete_op])
        md = format_preview(plan)

        assert "失效A" in md
        assert "失效B" in md
        assert "`BVbad1`" in md
        assert "`BVbad2`" in md
        assert "收藏夹A" in md

    # ------------------------------------------------------------------
    # Section 3 — duplicate content table
    # ------------------------------------------------------------------

    def test_duplicate_content_table_headers(self):
        """Section 3: table has title, bvid, source folder, and action columns."""
        dup_item = _item(1, title="重复视频", bvid="BVdup1", attr=0)
        default = _folder(1, title="默认收藏夹", attr=0)
        dup_op = Operation(
            action="batch_delete",
            source=default,
            resources=[dup_item],
        )
        plan = _plan(deletions=[dup_op])
        md = format_preview(plan)

        assert "## 重复内容" in md
        assert "| 标题 | BV号 | 来源文件夹 | 操作 |" in md
        assert "重复视频" in md
        assert "`BVdup1`" in md
        assert "默认收藏夹" in md
        assert "| 删除 |" in md

    def test_duplicate_content_skipped_when_no_duplicates(self):
        """Section 3 omitted when no duplicate deletions exist."""
        bad_item = _item(1, bvid="BVbad", attr=1)
        source = _folder(1, title="视频收藏", attr=1)
        del_op = Operation(
            action="batch_delete",
            source=source,
            resources=[bad_item],
        )
        plan = _plan(deletions=[del_op])
        md = format_preview(plan)

        assert "## 重复内容" not in md

    def test_duplicate_content_multiple_items(self):
        """Multiple duplicate items in the same deletion appear."""
        dup1 = _item(1, title="重复A", bvid="BVdupA", attr=0)
        dup2 = _item(2, title="重复B", bvid="BVdupB", attr=0)
        default = _folder(1, title="默认收藏夹", attr=0)
        dup_op = Operation(
            action="batch_delete",
            source=default,
            resources=[dup1, dup2],
        )
        plan = _plan(deletions=[dup_op])
        md = format_preview(plan)

        assert "重复A" in md
        assert "重复B" in md
        assert "`BVdupA`" in md
        assert "`BVdupB`" in md

    # ------------------------------------------------------------------
    # Section 4 — classification moves
    # ------------------------------------------------------------------

    def test_moves_grouped_by_target_folder(self):
        """Section 4: moves grouped under target folder headings."""
        item1 = _item(1, title="科技视频1", bvid="BVtech1")
        item2 = _item(2, title="科技视频2", bvid="BVtech2")
        item3 = _item(3, title="游戏视频", bvid="BVgame1")
        source_a = _folder(1, title="旧收藏夹", attr=1)
        source_b = _folder(2, title="其他收藏", attr=1)

        move_tech = Operation(
            action="move",
            source=source_a,
            target="科技区",
            resources=[item1, item2],
        )
        move_game = Operation(
            action="move",
            source=source_b,
            target="游戏区",
            resources=[item3],
        )
        plan = _plan(folders_to_create=["科技区", "游戏区"], moves=[move_tech, move_game])
        md = format_preview(plan)

        assert "## 分类移动" in md
        assert "科技区" in md
        assert "游戏区" in md
        assert "2 个视频" in md  # 科技区 group count
        assert "1 个视频" in md  # 游戏区 group count
        assert "科技视频1" in md
        assert "科技视频2" in md
        assert "游戏视频" in md
        # Source folder names appear
        assert "旧收藏夹" in md
        assert "其他收藏" in md
        # Bilibili URLs
        assert "https://www.bilibili.com/video/BVtech1" in md
        assert "https://www.bilibili.com/video/BVgame1" in md

    def test_moves_skipped_when_none(self):
        """Section 4 omitted when plan has no moves."""
        plan = _plan()
        md = format_preview(plan)
        assert "## 分类移动" not in md

    # ------------------------------------------------------------------
    # Section 5 — new folders
    # ------------------------------------------------------------------

    def test_new_folders_section(self):
        """Section 5: bullet list of folders to create."""
        plan = _plan(folders_to_create=["科技区", "游戏区", "知识区"])
        md = format_preview(plan)

        assert "## 需要创建的文件夹" in md
        assert "- **科技区**" in md
        assert "- **游戏区**" in md
        assert "- **知识区**" in md

    def test_new_folders_skipped_when_none(self):
        """Section 5 omitted when no folders need creating."""
        plan = _plan()
        md = format_preview(plan)
        assert "## 需要创建的文件夹" not in md

    # ------------------------------------------------------------------
    # Section 6 — empty folder advisory
    # ------------------------------------------------------------------

    def test_empty_folder_advisory(self):
        """Section 6: advisory with list of empty folder names."""
        plan = _plan(empty_folders=["旧收藏夹", "测试文件夹"])
        md = format_preview(plan)

        assert "## 空文件夹提醒" in md
        assert "以下文件夹整理后将为空，建议手动删除（本次不执行）" in md
        assert "- 旧收藏夹" in md
        assert "- 测试文件夹" in md

    def test_empty_folder_skipped_when_none(self):
        """Section 6 omitted when no folders become empty."""
        plan = _plan()
        md = format_preview(plan)
        assert "## 空文件夹提醒" not in md

    # ------------------------------------------------------------------
    # Section 7 — confirmation prompt
    # ------------------------------------------------------------------

    def test_confirmation_prompt(self):
        """Section 7: confirmation prompt always present."""
        plan = _plan()
        md = format_preview(plan)

        assert "---" in md
        assert "**是否执行以上操作？(y/n)**" in md

    # ------------------------------------------------------------------
    # Full integration — all seven sections present
    # ------------------------------------------------------------------

    def test_all_seven_sections_present(self):
        """All 7 sections appear when plan has data for every category."""
        # Build a comprehensive plan that exercises every section
        item_tech1 = _item(1, title="科技A", bvid="BVtechA")
        item_tech2 = _item(2, title="科技B", bvid="BVtechB")
        item_game = _item(3, title="游戏", bvid="BVgameX")
        item_bad = _item(4, title="失效", bvid="BVdead", attr=1)
        item_dup = _item(5, title="重复", bvid="BVdupX", attr=0)

        source_old = _folder(1, title="旧收藏", attr=1)
        source_b = _folder(2, title="其他", attr=1)
        default = _folder(3, title="默认收藏夹", attr=0)

        moves = [
            Operation(
                action="move",
                source=source_old,
                target="科技区",
                resources=[item_tech1, item_tech2],
            ),
            Operation(
                action="move",
                source=source_b,
                target="游戏区",
                resources=[item_game],
            ),
        ]
        deletions = [
            Operation(
                action="batch_delete",
                source=source_old,
                resources=[item_bad],
            ),
            Operation(
                action="batch_delete",
                source=default,
                resources=[item_dup],
            ),
        ]

        plan = _plan(
            folders_to_create=["科技区", "游戏区"],
            moves=moves,
            deletions=deletions,
            empty_folders=["旧收藏", "其他"],
            summary="需要创建 2 个文件夹，移动 3 个视频到对应分类，删除 2 个失效/重复内容",
        )
        md = format_preview(plan)

        # Section 1 — Title + stats
        assert "# 整理预览" in md
        assert "共需执行" in md
        assert plan.summary in md

        # Section 2 — Invalid content table
        assert "## 失效内容" in md
        assert "| 标题 | BV号 | 来源文件夹 |" in md
        assert "失效" in md
        assert "`BVdead`" in md

        # Section 3 — Duplicate content table
        assert "## 重复内容" in md
        assert "| 标题 | BV号 | 来源文件夹 | 操作 |" in md
        assert "重复" in md
        assert "`BVdupX`" in md
        assert "| 删除 |" in md

        # Section 4 — Classification moves
        assert "## 分类移动" in md
        assert "科技区（2 个视频）" in md
        assert "游戏区（1 个视频）" in md
        assert "科技A" in md
        assert "游戏" in md

        # Section 5 — New folders
        assert "## 需要创建的文件夹" in md
        assert "- **科技区**" in md
        assert "- **游戏区**" in md

        # Section 6 — Empty folder advisory
        assert "## 空文件夹提醒" in md
        assert "- 旧收藏" in md
        assert "- 其他" in md

        # Section 7 — Confirmation prompt
        assert "**是否执行以上操作？(y/n)**" in md


class TestFormatPreviewEdgeCases:
    """Preview behaviour for boundary conditions."""

    def test_empty_plan_minimal_output(self):
        """Empty plan: only sections 1 and 7 appear, no empty tables."""
        plan = _plan()
        md = format_preview(plan)

        assert "# 整理预览" in md
        assert "共需执行 **0** 个操作" in md
        assert "**是否执行以上操作？(y/n)**" in md

        # No other sections should appear
        assert "## 失效内容" not in md
        assert "## 重复内容" not in md
        assert "## 分类移动" not in md
        assert "## 需要创建的文件夹" not in md
        assert "## 空文件夹提醒" not in md

    def test_only_invalid_content(self):
        """Plan with only invalid deletions: sections 1, 2, 7."""
        bad = _item(1, title="已删除", bvid="BVdel", attr=1)
        source = _folder(1, title="收藏", attr=1)
        del_op = Operation(
            action="batch_delete",
            source=source,
            resources=[bad],
        )
        plan = _plan(deletions=[del_op])
        md = format_preview(plan)

        assert "## 失效内容" in md
        assert "## 重复内容" not in md
        assert "## 分类移动" not in md

    def test_only_duplicate_content(self):
        """Plan with only duplicate deletions: sections 1, 3, 7."""
        dup = _item(1, title="重复", bvid="BVdup", attr=0)
        default = _folder(1, title="默认收藏夹", attr=0)
        del_op = Operation(
            action="batch_delete",
            source=default,
            resources=[dup],
        )
        plan = _plan(deletions=[del_op])
        md = format_preview(plan)

        assert "## 重复内容" in md
        assert "## 失效内容" not in md
        assert "## 分类移动" not in md

    def test_plan_with_no_deletions(self):
        """Plan with moves but no deletions: sections 1, 4, 5, 7."""
        item_x = _item(1, title="科技", bvid="BVx")
        source = _folder(1, title="杂项", attr=1)
        move_op = Operation(
            action="move",
            source=source,
            target="科技区",
            resources=[item_x],
        )
        plan = _plan(folders_to_create=["科技区"], moves=[move_op])
        md = format_preview(plan)

        assert "## 分类移动" in md
        assert "## 需要创建的文件夹" in md
        assert "## 失效内容" not in md
        assert "## 重复内容" not in md

    def test_all_deletions_have_source_folder(self):
        """Every deletion table row references a source folder."""
        bad = _item(1, title="失效", bvid="BVbad", attr=1)
        source = _folder(1, title="视频收藏", attr=1)
        del_op = Operation(
            action="batch_delete",
            source=source,
            resources=[bad],
        )
        plan = _plan(deletions=[del_op])
        md = format_preview(plan)

        # The source folder name appears somewhere in the deletion section
        assert "视频收藏" in md

    def test_duplicate_deletion_default_source(self):
        """Duplicate deletion shows default folder as source."""
        dup = _item(1, title="重复", bvid="BVdup", attr=0)
        default = _folder(1, title="默认收藏夹", attr=0)
        del_op = Operation(
            action="batch_delete",
            source=default,
            resources=[dup],
        )
        plan = _plan(deletions=[del_op])
        md = format_preview(plan)

        assert "默认收藏夹" in md

    def test_returns_string(self):
        """format_preview always returns a non-empty string."""
        plan = _plan()
        md = format_preview(plan)
        assert isinstance(md, str)
        assert len(md) > 0
