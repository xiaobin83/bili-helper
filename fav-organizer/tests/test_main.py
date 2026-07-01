"""
Tests for main.py — CLI entry point, 3 commands (classify/plan/execute), preview.

Covers:
- CLI subcommand parsing (classify, plan, execute)
- generate_preview produces correct markdown
- cmd_plan reads state + classification, produces plan
- cmd_execute loads plan, confirms, executes
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from src.fav_organizer.main import cli, cmd_classify, cmd_plan, cmd_execute
from src.fav_organizer.preview import generate_preview
from src.fav_organizer.models import (
    ClassificationEntry,
    ClassificationResultList,
    FavoritedItem,
    Folder,
    InvalidItemEntry,
    Operation,
    OrganizePlan,
    PlanFile,
    PlanMoveEntry,
    PlanDeleteEntry,
    PlanResourceRef,
    StateData,
)
from src.fav_organizer.state_manager import StateManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_folder(
    fid: int = 1,
    *,
    title: str = "默认收藏夹",
    attr: int = 0,
    media_count: int = 10,
    mid: int = 1000,
) -> Folder:
    return Folder(id=fid, fid=fid, mid=mid, attr=attr, title=title, media_count=media_count)


def _make_item(
    item_id: int = 1,
    *,
    bvid: str = "BV1xx411c7mD",
    title: str = "测试视频",
    attr: int = 0,
    intro: str = "",
    zone_tname: str = "",
) -> FavoritedItem:
    return FavoritedItem(
        id=item_id, type=2, title=title, bvid=bvid,
        upper_name="UP主A", upper_mid=100, attr=attr, fav_time=1234567890,
        intro=intro, zone_tname=zone_tname,
    )


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestCli:
    """Tests for the cli() entry point with subcommands."""

    async def _mock_coro(self, **kwargs):
        return 0

    def test_classify_subcommand(self):
        """classify --folder 'xxx' triggers cmd_classify."""
        with (
            patch("src.fav_organizer.main.cmd_classify", new_callable=AsyncMock) as mock_cmd,
            patch("src.fav_organizer.main.sys.exit"),
            patch("src.fav_organizer.main.sys.argv", ["fav-organizer", "classify", "--folder", "默认收藏夹"]),
        ):
            mock_cmd.return_value = 0
            cli()
            mock_cmd.assert_called_once_with(
                scope_kind="folder", scope_value="默认收藏夹", clear_cache=False, count=None, dedup=False
            )

    def test_classify_all(self):
        """classify --all triggers cmd_classify with scope all."""
        with (
            patch("src.fav_organizer.main.cmd_classify", new_callable=AsyncMock) as mock_cmd,
            patch("src.fav_organizer.main.sys.exit"),
            patch("src.fav_organizer.main.sys.argv", ["fav-organizer", "classify", "--all"]),
        ):
            mock_cmd.return_value = 0
            cli()
            mock_cmd.assert_called_once_with(
                scope_kind="all", scope_value="全部", clear_cache=False, count=None, dedup=False
            )

    def test_plan_subcommand(self):
        """plan subcommand triggers cmd_plan."""
        with (
            patch("src.fav_organizer.main.cmd_plan") as mock_cmd,
            patch("src.fav_organizer.main.sys.exit"),
            patch("src.fav_organizer.main.sys.argv", ["fav-organizer", "plan"]),
        ):
            mock_cmd.return_value = 0
            cli()
            mock_cmd.assert_called_once_with(classification_path=None)

    def test_execute_subcommand(self):
        """execute subcommand triggers cmd_execute."""
        with (
            patch("src.fav_organizer.main.cmd_execute", new_callable=AsyncMock) as mock_cmd,
            patch("src.fav_organizer.main.sys.exit"),
            patch("src.fav_organizer.main.sys.argv", ["fav-organizer", "execute"]),
        ):
            mock_cmd.return_value = 0
            cli()
            mock_cmd.assert_called_once_with(plan_path=None)

    def test_classify_with_count(self):
        """--count N is forwarded to cmd_classify."""
        with (
            patch("src.fav_organizer.main.cmd_classify", new_callable=AsyncMock) as mock_cmd,
            patch("src.fav_organizer.main.sys.exit"),
            patch("src.fav_organizer.main.sys.argv", ["fav-organizer", "classify", "--folder", "默认收藏夹", "--count", "10"]),
        ):
            mock_cmd.return_value = 0
            cli()
            mock_cmd.assert_called_once_with(
                scope_kind="folder", scope_value="默认收藏夹", clear_cache=False, count=10, dedup=False
            )

    def test_classify_with_dedup(self):
        """--dedup is forwarded to cmd_classify."""
        with (
            patch("src.fav_organizer.main.cmd_classify", new_callable=AsyncMock) as mock_cmd,
            patch("src.fav_organizer.main.sys.exit"),
            patch("src.fav_organizer.main.sys.argv", ["fav-organizer", "classify", "--folder", "默认收藏夹", "--dedup"]),
        ):
            mock_cmd.return_value = 0
            cli()
            mock_cmd.assert_called_once_with(
                scope_kind="folder", scope_value="默认收藏夹", clear_cache=False, count=None, dedup=True
            )

    def test_no_subcommand_shows_help(self):
        """No subcommand prints help."""
        with (
            patch("src.fav_organizer.main.sys.exit") as mock_exit,
            patch("src.fav_organizer.main.sys.argv", ["fav-organizer"]),
        ):
            cli()
            mock_exit.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# cmd_plan (sync — no asyncio)
# ---------------------------------------------------------------------------


class TestCmdPlan:
    """Tests for cmd_plan — reads state + classification, builds plan."""

    def test_missing_state_file(self):
        """No state.json → error exit code 1."""
        with patch.object(StateManager, "load_state", side_effect=FileNotFoundError):
            result = cmd_plan()
            assert result == 1

    def test_produces_plan_with_classifications(self, tmp_path):
        """With state + classifications, produces a valid plan."""
        folder = _make_folder(1, title="默认收藏夹", attr=0)
        source = _make_folder(2, title="旧收藏", attr=1)
        item = _make_item(1, bvid="BV001", title="Python教程")
        item_invalid = _make_item(2, bvid="BVbad", attr=9)

        state = StateData(
            scope_kind="all", scope_value="全部",
            folders=[folder, source],
            invalid_items=[InvalidItemEntry(item=item_invalid, folder_id=source.id, folder_title=source.title)],
            duplicate_groups=[],
            item_folder_map={item.id: source.id, item_invalid.id: source.id},
            items_to_classify=[item, item_invalid],
            existing_folder_titles=["默认收藏夹", "旧收藏"],
        )

        classification = ClassificationResultList(
            classifications=[
                ClassificationEntry(item_id=item.id, category="编程"),
                ClassificationEntry(item_id=item_invalid.id, category=""),
            ]
        )

        with (
            patch.object(StateManager, "load_state", return_value=state),
            patch.object(StateManager, "load_classification", return_value=classification),
            patch.object(StateManager, "save_plan"),
            patch("src.fav_organizer.main.generate_preview", return_value="PREVIEW"),
            patch("builtins.print"),
        ):
            result = cmd_plan()
            assert result == 0


# ---------------------------------------------------------------------------
# generate_preview
# ---------------------------------------------------------------------------


class TestGeneratePreview:
    """Tests for the generate_preview function."""

    def test_empty_plan(self):
        empty_plan = OrganizePlan(
            total_operations=0, folders_to_create=[], moves=[], deletions=[],
            summary="无需任何操作",
        )
        result = generate_preview(empty_plan)
        assert "# 🗂️ 收藏夹整理计划" in result
        assert "✅ 无需任何操作" in result

    def test_plan_with_operations(self):
        folder = _make_folder(2, title="旧收藏", attr=1)
        item = _make_item(1)
        plan = OrganizePlan(
            total_operations=2,
            folders_to_create=["科技区"],
            moves=[Operation(action="move", source=folder, target="科技区", resources=[item])],
            deletions=[Operation(action="batch_delete", source=folder, resources=[item])],
            summary="需要创建 1 个文件夹，移动 1 个内容，删除 1 个内容",
        )
        result = generate_preview(plan)
        assert "# 🗂️ 收藏夹整理计划" in result
        assert "📁 新建 1 个文件夹" in result
        assert "科技区" in result

    def test_preview_shows_execute_hint(self):
        plan = OrganizePlan(
            total_operations=0, folders_to_create=[], moves=[], deletions=[],
            summary="无需任何操作",
        )
        result = generate_preview(plan)
        assert "fav-organizer execute" in result


# ---------------------------------------------------------------------------
# PlanFile serialization round-trip
# ---------------------------------------------------------------------------


class TestPlanFileRoundTrip:
    """Verify PlanFile serializes and deserializes correctly."""

    def test_planfile_json_roundtrip(self):
        pf = PlanFile(
            folders_to_create=["科技"],
            moves=[
                PlanMoveEntry(
                    source_folder_id=1, source_folder_title="旧收藏",
                    target_title="科技",
                    resources=[PlanResourceRef(id=1, type=2, bvid="BV001", title="视频A")],
                )
            ],
            deletions=[
                PlanDeleteEntry(
                    source_folder_id=1, source_folder_title="旧收藏",
                    reason="invalid",
                    resources=[PlanResourceRef(id=2, type=2, bvid="BV002", title="失效视频")],
                )
            ],
            summary="test summary",
        )

        dumped = pf.model_dump_json()
        loaded = PlanFile.model_validate_json(dumped)
        assert loaded.folders_to_create == ["科技"]
        assert len(loaded.moves) == 1
        assert loaded.moves[0].resources[0].bvid == "BV001"
        assert len(loaded.deletions) == 1


# ---------------------------------------------------------------------------
# StateData serialization round-trip
# ---------------------------------------------------------------------------


class TestStateDataRoundTrip:
    """Verify StateData serializes and deserializes correctly."""

    def test_state_json_roundtrip(self):
        folder = _make_folder(1, title="默认收藏夹")
        item = _make_item(1, bvid="BV001", intro="学习Python", zone_tname="知识")

        state = StateData(
            scope_kind="folder", scope_value="默认收藏夹",
            folders=[folder],
            invalid_items=[],
            duplicate_groups=[],
            item_folder_map={item.id: folder.id},
            items_to_classify=[item],
            existing_folder_titles=["默认收藏夹"],
        )

        dumped = state.model_dump_json()
        loaded = StateData.model_validate_json(dumped)
        assert loaded.scope_value == "默认收藏夹"
        assert len(loaded.items_to_classify) == 1
        assert loaded.items_to_classify[0].intro == "学习Python"
        assert loaded.items_to_classify[0].zone_tname == "知识"
