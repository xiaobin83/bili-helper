"""
End-to-end integration tests for fav-organizer 3-command pipeline.

Tests the full flow: classify → plan → execute with mocked externals.
No real B站 API calls are made.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bili_core.auth import Credentials as AuthCredentialsDC
from src.fav_organizer.main import cmd_classify, cmd_plan, cmd_execute
from src.fav_organizer.preview import generate_preview
from src.fav_organizer.models import (
    ClassificationEntry,
    ClassificationResult,
    ClassificationResultList,
    DuplicateGroup,
    FavoritedItem,
    Folder,
    InvalidItemEntry,
    Operation,
    OrganizePlan,
    PlanDeleteEntry,
    PlanFile,
    PlanMoveEntry,
    PlanResourceRef,
    StateData,
)
from src.fav_organizer.state_manager import StateManager


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _folder(fid: int, *, title: str = "默认收藏夹", attr: int = 0) -> Folder:
    return Folder(id=fid, fid=fid, mid=1000, attr=attr, title=title, media_count=10)


def _item(item_id: int, *, bvid: str = "BV1xx411c7mD", title: str = "测试视频",
          attr: int = 0, intro: str = "", zone_tname: str = "") -> FavoritedItem:
    return FavoritedItem(
        id=item_id, type=2, title=title, bvid=bvid,
        upper_name="UP主A", upper_mid=100, attr=attr, fav_time=1234567890,
        intro=intro, zone_tname=zone_tname,
    )


def _credentials() -> AuthCredentialsDC:
    return AuthCredentialsDC(sessdata="mock_sess", bili_jct="mock_jct", buvid3="mock_buv", mid=1000)


def _make_http_mock() -> MagicMock:
    mock = MagicMock()
    mock.__aenter__.return_value = mock
    mock.__aexit__.return_value = False
    mock.close = AsyncMock()
    return mock


# ── classify command ───────────────────────────────────────────────────


class TestCmdClassify:
    """Tests for cmd_classify — scans folders, prepares state."""

    @pytest.mark.asyncio
    async def test_classify_folder_scope(self):
        """classify --folder scans only the specified folder."""
        folders = [_folder(1, title="默认收藏夹", attr=0), _folder(2, title="科技", attr=1)]
        items = [_item(1, bvid="BV001", title="测试")]

        mock_fav = MagicMock()
        mock_fav.list_all_folders = AsyncMock(return_value=folders)
        mock_fav.get_all_contents = AsyncMock(return_value=items)

        with (
            patch("src.fav_organizer.main.get_credentials", return_value=_credentials()),
            patch("src.fav_organizer.main.check_expired", return_value=False),
            patch("src.fav_organizer.main.BiliHTTPClient") as mock_http_cls,
            patch("src.fav_organizer.main.FavAPI", return_value=mock_fav),
            patch("src.fav_organizer.main.VideoInfoAPI") as mock_video_cls,
            patch("src.fav_organizer.main.sign_params"),
            patch("src.fav_organizer.main.scan_invalid", return_value=[]),
            patch("src.fav_organizer.main.detect_duplicates", return_value=[]),
            patch.object(StateManager, "save_state"),
            patch.object(StateManager, "save_classification"),
        ):
            mock_http_cls.return_value = _make_http_mock()
            mock_video = MagicMock()
            mock_video.is_cached = MagicMock(return_value=True)
            mock_video.get_video_info = AsyncMock(return_value={"desc": "测试简介", "tname": "科技"})
            mock_video_cls.return_value = mock_video

            result = await cmd_classify(scope_kind="folder", scope_value="默认收藏夹")
            assert result == 0

    @pytest.mark.asyncio
    async def test_classify_nonexistent_folder(self):
        """Specifying a folder that doesn't exist → error exit."""
        mock_fav = MagicMock()
        mock_fav.list_all_folders = AsyncMock(return_value=[_folder(1, title="默认收藏夹")])

        with (
            patch("src.fav_organizer.main.get_credentials", return_value=_credentials()),
            patch("src.fav_organizer.main.check_expired", return_value=False),
            patch("src.fav_organizer.main.BiliHTTPClient") as mock_http_cls,
            patch("src.fav_organizer.main.FavAPI", return_value=mock_fav),
            patch("src.fav_organizer.main.sign_params"),
        ):
            mock_http_cls.return_value = _make_http_mock()
            result = await cmd_classify(scope_kind="folder", scope_value="不存在的文件夹")
            assert result == 1

    @pytest.mark.asyncio
    async def test_classify_empty_folders(self):
        """No folders → friendly message, exit 0."""
        mock_fav = MagicMock()
        mock_fav.list_all_folders = AsyncMock(return_value=[])

        with (
            patch("src.fav_organizer.main.get_credentials", return_value=_credentials()),
            patch("src.fav_organizer.main.check_expired", return_value=False),
            patch("src.fav_organizer.main.BiliHTTPClient") as mock_http_cls,
            patch("src.fav_organizer.main.FavAPI", return_value=mock_fav),
            patch("src.fav_organizer.main.sign_params"),
        ):
            mock_http_cls.return_value = _make_http_mock()
            result = await cmd_classify(scope_kind="all", scope_value="全部")
            assert result == 0

    @pytest.mark.asyncio
    async def test_classify_expired_auth(self):
        """Expired SESSDATA → error exit 1."""
        with (
            patch("src.fav_organizer.main.get_credentials", return_value=_credentials()),
            patch("src.fav_organizer.main.check_expired", return_value=True),
        ):
            result = await cmd_classify(scope_kind="all", scope_value="全部")
            assert result == 1


# ── plan command ────────────────────────────────────────────────────────


class TestCmdPlan:
    """Tests for cmd_plan — reads state + classifications, builds plan."""

    def test_plan_with_valid_data(self):
        """Valid state + classification → plan.json saved, preview shown."""
        folder = _folder(1, title="默认收藏夹", attr=0)
        source = _folder(2, title="旧收藏", attr=1)
        item = _item(1, bvid="BV001", title="Python教程", intro="学Python")

        state = StateData(
            scope_kind="all", scope_value="全部",
            folders=[folder, source],
            invalid_items=[],
            duplicate_groups=[],
            item_folder_map={item.id: source.id},
            items_to_classify=[item],
            existing_folder_titles=["默认收藏夹", "旧收藏"],
        )

        classification = ClassificationResultList(
            classifications=[ClassificationEntry(item_id=item.id, category="编程")]
        )

        with (
            patch.object(StateManager, "load_state", return_value=state),
            patch.object(StateManager, "load_classification", return_value=classification),
            patch.object(StateManager, "save_plan") as mock_save,
            patch("builtins.print"),
        ):
            result = cmd_plan()
            assert result == 0
            mock_save.assert_called_once()

    def test_plan_missing_state(self):
        """No state.json → error."""
        with patch.object(StateManager, "load_state", side_effect=FileNotFoundError):
            result = cmd_plan()
            assert result == 1

    def test_plan_unclassified_items_gets_default(self):
        """Items without classification get '未分类'."""
        folder = _folder(1, title="默认收藏夹", attr=0)
        item = _item(1, bvid="BV001")

        state = StateData(
            scope_kind="all", scope_value="全部",
            folders=[folder],
            invalid_items=[],
            duplicate_groups=[],
            item_folder_map={item.id: folder.id},
            items_to_classify=[item],
            existing_folder_titles=["默认收藏夹"],
        )

        # Only 1 item in state, but 0 in classification
        classification = ClassificationResultList(classifications=[])

        with (
            patch.object(StateManager, "load_state", return_value=state),
            patch.object(StateManager, "load_classification", return_value=classification),
            patch.object(StateManager, "save_plan"),
            patch("builtins.print"),
        ):
            result = cmd_plan()
            assert result == 0

    def test_plan_with_external_classification_file(self, tmp_path):
        """External classification_result.json is read correctly."""
        class_file = tmp_path / "my_classification.json"
        classification = ClassificationResultList(
            classifications=[ClassificationEntry(item_id=1, category="科技")]
        )
        class_file.write_text(classification.model_dump_json(), encoding="utf-8")

        folder = _folder(1, title="默认收藏夹")
        item = _item(1, bvid="BV001")

        state = StateData(
            scope_kind="all", scope_value="全部",
            folders=[folder],
            invalid_items=[],
            duplicate_groups=[],
            item_folder_map={item.id: folder.id},
            items_to_classify=[item],
            existing_folder_titles=["默认收藏夹"],
        )

        with (
            patch.object(StateManager, "load_state", return_value=state),
            patch.object(StateManager, "save_plan"),
            patch("builtins.print"),
        ):
            result = cmd_plan(classification_path=str(class_file))
            assert result == 0


# ── execute command ─────────────────────────────────────────────────────


class TestCmdExecute:
    """Tests for cmd_execute — loads plan.json, confirms, executes."""

    @pytest.mark.asyncio
    async def test_execute_with_plan(self):
        """Valid plan → confirmed → executed."""
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
                    resources=[PlanResourceRef(id=2, type=2, bvid="BV002", title="失效")],
                )
            ],
            summary="需要创建 1 个文件夹，移动 1 个内容，删除 1 个内容",
        )

        mock_fav = MagicMock()
        mock_fav.create_folder = AsyncMock(return_value={"data": {"id": 100}})
        mock_fav.move_items = AsyncMock()
        mock_fav.batch_delete = AsyncMock()

        with (
            patch.object(StateManager, "load_plan", return_value=pf),
            patch("src.fav_organizer.main.get_credentials", return_value=_credentials()),
            patch("src.fav_organizer.main.check_expired", return_value=False),
            patch("src.fav_organizer.main.BiliHTTPClient") as mock_http_cls,
            patch("src.fav_organizer.main.FavAPI", return_value=mock_fav),
            patch("src.fav_organizer.main.sign_params"),
            patch("src.fav_organizer.main.confirm_execution", return_value=True),
        ):
            mock_http_cls.return_value = _make_http_mock()
            result = await cmd_execute()
            assert result == 0
            mock_fav.create_folder.assert_called_once_with(title="科技")
            mock_fav.move_items.assert_called()
            mock_fav.batch_delete.assert_called()

    @pytest.mark.asyncio
    async def test_execute_empty_plan(self):
        """Empty plan → no API calls, exit 0."""
        pf = PlanFile(folders_to_create=[], moves=[], deletions=[], summary="无需任何操作")

        with (
            patch.object(StateManager, "load_plan", return_value=pf),
            patch("src.fav_organizer.main.confirm_execution"),
        ):
            result = await cmd_execute()
            assert result == 0

    @pytest.mark.asyncio
    async def test_execute_missing_plan(self):
        """No plan.json → error."""
        with patch.object(StateManager, "load_plan", side_effect=FileNotFoundError):
            result = await cmd_execute()
            assert result == 1

    @pytest.mark.asyncio
    async def test_execute_confirm_declined(self):
        """User declines → no API calls."""
        pf = PlanFile(folders_to_create=["科技"], moves=[], deletions=[], summary="创建 1 个文件夹")

        with (
            patch.object(StateManager, "load_plan", return_value=pf),
            patch("src.fav_organizer.main.confirm_execution", return_value=False),
            patch("src.fav_organizer.main.get_credentials"),
        ):
            result = await cmd_execute()
            assert result == 0

    @pytest.mark.asyncio
    async def test_execute_continue_on_error(self):
        """Batch failure doesn't stop execution."""
        pf = PlanFile(
            folders_to_create=["科技", "知识"],
            moves=[],
            deletions=[],
            summary="创建 2 个文件夹",
        )

        mock_fav = MagicMock()
        mock_fav.create_folder = AsyncMock(side_effect=[Exception("API error"), {"data": {"id": 200}}])

        with (
            patch.object(StateManager, "load_plan", return_value=pf),
            patch("src.fav_organizer.main.get_credentials", return_value=_credentials()),
            patch("src.fav_organizer.main.check_expired", return_value=False),
            patch("src.fav_organizer.main.BiliHTTPClient") as mock_http_cls,
            patch("src.fav_organizer.main.FavAPI", return_value=mock_fav),
            patch("src.fav_organizer.main.sign_params"),
            patch("src.fav_organizer.main.confirm_execution", return_value=True),
        ):
            mock_http_cls.return_value = _make_http_mock()
            result = await cmd_execute()
            assert result == 0  # continues despite first folder failure


# ── Preview output ──────────────────────────────────────────────────────


class TestPreviewOutput:
    """Preview Markdown tests."""

    def test_preview_contains_standard_sections(self):
        plan = OrganizePlan(
            total_operations=3,
            folders_to_create=["科技"],
            moves=[Operation(action="move", source=_folder(1, title="旧"), target="科技",
                             resources=[_item(1, bvid="BV001", title="视频")])],
            deletions=[Operation(action="batch_delete", source=_folder(1, title="旧"),
                                 resources=[_item(2, bvid="BV002", title="失效视频")])],
            summary="需要创建 1 个文件夹，移动 1 个内容，删除 1 个内容",
        )

        preview = generate_preview(plan)

        assert "# 🗂️ 收藏夹整理计划" in preview
        assert "📁 新文件夹" in preview
        assert "🗑️ 失效/重复内容" in preview
        assert "↗️ 分类整理计划" in preview
        assert "fav-organizer execute" in preview

    def test_empty_preview(self):
        plan = OrganizePlan(
            total_operations=0, folders_to_create=[], moves=[], deletions=[],
            summary="无需任何操作",
        )
        preview = generate_preview(plan)
        assert "✅ 无需任何操作" in preview
