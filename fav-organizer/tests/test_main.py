"""
Tests for main.py — CLI entry point, pipeline orchestration, preview, executor.

Covers:
- CLI argument parsing (--dry-run flag)
- run_pipeline async orchestrator (dry-run and normal modes)
- generate_preview produces correct markdown
- execute_plan calls fav_api methods with correct arguments
- Pipeline modules are called in the expected order
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

# Add src to path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from src.main import cli, execute_plan, generate_preview, run_pipeline
from src.models import (
    ClassificationResult,
    DuplicateGroup,
    FavoritedItem,
    Folder,
    Operation,
    OrganizePlan,
)


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
    return Folder(
        id=fid,
        fid=fid,
        mid=mid,
        attr=attr,
        title=title,
        media_count=media_count,
    )


def _make_item(
    item_id: int = 1,
    *,
    bvid: str = "BV1xx411c7mD",
    title: str = "测试视频",
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


def _make_plan() -> OrganizePlan:
    """Create a simple plan for testing preview and execution."""
    folder = _make_folder(2, title="旧收藏", attr=1)
    item = _make_item(1)
    move_op = Operation(
        action="move",
        source=folder,
        target="科技区",
        resources=[item],
    )
    delete_op = Operation(
        action="batch_delete",
        source=folder,
        resources=[item],
    )
    return OrganizePlan(
        total_operations=2,
        folders_to_create=["科技区"],
        moves=[move_op],
        deletions=[delete_op],
        summary="需要创建 1 个文件夹，移动 1 个内容，删除 1 个内容",
    )


# ---------------------------------------------------------------------------
# Helpers: mock HTTP client
# ---------------------------------------------------------------------------


def _make_http_mock() -> MagicMock:
    """Create a properly configured mock for BiliHTTPClient async context manager."""
    mock_http = MagicMock()
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = False
    mock_http.close = AsyncMock()
    return mock_http


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestCli:
    """Tests for the cli() entry point."""

    def test_dry_run_flag(self):
        """--dry-run flag should be passed through to run_pipeline."""
        with patch("src.main.run_pipeline") as mock_run:
            mock_run.return_value = 0
            with patch("src.main.sys.exit") as mock_exit:
                with patch(
                    "src.main.sys.argv", ["fav-organizer", "--dry-run"]
                ):
                    cli()
                    mock_run.assert_called_once_with(dry_run=True)
                    mock_exit.assert_called_once_with(0)

    def test_normal_mode(self):
        """Without --dry-run, run_pipeline is called with dry_run=False."""
        with patch("src.main.run_pipeline") as mock_run:
            mock_run.return_value = 0
            with patch("src.main.sys.exit") as mock_exit:
                with patch("src.main.sys.argv", ["fav-organizer"]):
                    cli()
                    mock_run.assert_called_once_with(dry_run=False)
                    mock_exit.assert_called_once_with(0)

    def test_exit_code_propagated(self):
        """Exit code from run_pipeline is passed to sys.exit."""
        with patch("src.main.run_pipeline") as mock_run:
            mock_run.return_value = 42
            with patch("src.main.sys.exit") as mock_exit:
                with patch("src.main.sys.argv", ["fav-organizer"]):
                    cli()
                    mock_exit.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# run_pipeline — pipeline orchestration
# ---------------------------------------------------------------------------


class TestRunPipeline:
    """Tests for the async run_pipeline orchestrator."""

    @pytest.mark.asyncio
    async def test_dry_run_sends_preview_no_execute(self):
        """In dry-run mode, preview is printed but execute_plan is NOT called."""
        plan = _make_plan()
        creds = MagicMock()
        creds.mid = 1000

        folders = [_make_folder(1, title="默认收藏夹", attr=0)]
        items = [_make_item(1)]
        mock_fav_api = MagicMock()
        mock_fav_api.list_all_folders = AsyncMock(return_value=folders)
        mock_fav_api.get_all_contents = AsyncMock(return_value=items)

        with (
            patch("src.main.get_credentials", return_value=creds),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav_api),
            patch("src.main.VideoInfoAPI"),
            patch("src.main.scan_invalid", return_value=[]),
            patch("src.main.detect_duplicates", return_value=[]),
            patch("src.main.classify_by_zone", return_value=[]),
            patch("src.main.classify_by_upper", return_value=[]),
            patch("src.main.classify_by_llm", return_value=[]),
            patch("src.main.build_plan", return_value=plan),
            patch("src.main.generate_preview", return_value="PREVIEW TEXT"),
            patch("src.main.confirm_execution") as mock_confirm,
            patch("src.main.execute_plan") as mock_execute,
        ):
            mock_http_cls.return_value = _make_http_mock()

            result = await run_pipeline(dry_run=True)

            assert result == 0
            mock_confirm.assert_not_called()
            mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_mode_calls_confirm_and_execute(self):
        """In normal mode, confirm_execution is called and if True, execute_plan runs."""
        plan = _make_plan()
        creds = MagicMock()
        creds.mid = 1000

        folders = [_make_folder(1, title="默认收藏夹", attr=0)]
        items = [_make_item(1)]
        mock_fav_api = MagicMock()
        mock_fav_api.list_all_folders = AsyncMock(return_value=folders)
        mock_fav_api.get_all_contents = AsyncMock(return_value=items)

        with (
            patch("src.main.get_credentials", return_value=creds),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav_api),
            patch("src.main.VideoInfoAPI"),
            patch("src.main.scan_invalid", return_value=[]),
            patch("src.main.detect_duplicates", return_value=[]),
            patch("src.main.classify_by_zone", return_value=[]),
            patch("src.main.classify_by_upper", return_value=[]),
            patch("src.main.classify_by_llm", return_value=[]),
            patch("src.main.build_plan", return_value=plan),
            patch("src.main.generate_preview", return_value="PREVIEW"),
            patch("src.main.confirm_execution", return_value=True),
            patch("src.main.execute_plan") as mock_execute,
        ):
            mock_http_cls.return_value = _make_http_mock()

            result = await run_pipeline(dry_run=False)

            assert result == 0
            mock_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_false_skips_execute(self):
        """When user declines confirmation, execute_plan is NOT called."""
        plan = _make_plan()
        creds = MagicMock()
        creds.mid = 1000

        folders = [_make_folder(1, title="默认收藏夹", attr=0)]
        items = [_make_item(1)]
        mock_fav_api = MagicMock()
        mock_fav_api.list_all_folders = AsyncMock(return_value=folders)
        mock_fav_api.get_all_contents = AsyncMock(return_value=items)

        with (
            patch("src.main.get_credentials", return_value=creds),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav_api),
            patch("src.main.VideoInfoAPI"),
            patch("src.main.scan_invalid", return_value=[]),
            patch("src.main.detect_duplicates", return_value=[]),
            patch("src.main.classify_by_zone", return_value=[]),
            patch("src.main.classify_by_upper", return_value=[]),
            patch("src.main.classify_by_llm", return_value=[]),
            patch("src.main.build_plan", return_value=plan),
            patch("src.main.generate_preview", return_value="PREVIEW"),
            patch("src.main.confirm_execution", return_value=False),
            patch("src.main.execute_plan") as mock_execute,
        ):
            mock_http_cls.return_value = _make_http_mock()

            result = await run_pipeline(dry_run=False)

            assert result == 0
            mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_creds_exits_early(self):
        """Expired credentials return exit code 1 before any pipeline step."""
        with (
            patch("src.main.get_credentials") as mock_get_creds,
            patch("src.main.check_expired", return_value=True),
        ):
            mock_creds = MagicMock()
            mock_get_creds.return_value = mock_creds

            result = await run_pipeline(dry_run=False)

            assert result == 1

    @pytest.mark.asyncio
    async def test_pipeline_steps_called_in_order(self):
        """Pipeline functions are invoked with the correct arguments."""
        creds = MagicMock()
        creds.mid = 1000
        folders = [
            _make_folder(1, title="默认收藏夹", attr=0),
            _make_folder(2, title="科技", attr=1),
        ]
        items = [_make_item(1)]
        plan = _make_plan()

        mock_fav = MagicMock()
        mock_fav.list_all_folders = AsyncMock(return_value=folders)
        mock_fav.get_all_contents = AsyncMock(return_value=items)

        with (
            patch("src.main.get_credentials", return_value=creds),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI"),
            patch("src.main.scan_invalid", return_value=[]),
            patch("src.main.detect_duplicates", return_value=[]),
            patch(
                "src.main.classify_by_zone", return_value=[]
            ) as mock_zone,
            patch(
                "src.main.classify_by_upper", return_value=[]
            ) as mock_upper,
            patch(
                "src.main.classify_by_llm", return_value=[]
            ) as mock_llm,
            patch(
                "src.main.build_plan", return_value=plan
            ) as mock_plan,
            patch(
                "src.main.generate_preview", return_value="PREVIEW"
            ),
            patch(
                "src.main.confirm_execution", return_value=True
            ),
            patch("src.main.execute_plan") as mock_execute,
        ):
            mock_http_cls.return_value = _make_http_mock()

            result = await run_pipeline(dry_run=False)

            assert result == 0
            mock_zone.assert_called_once()
            mock_upper.assert_called_once()
            mock_llm.assert_called_once()
            mock_plan.assert_called_once()
            mock_execute.assert_called_once()


# ---------------------------------------------------------------------------
# generate_preview
# ---------------------------------------------------------------------------


class TestGeneratePreview:
    """Tests for the generate_preview function."""

    def test_empty_plan(self):
        """Empty plan produces a message about no operations needed."""
        folders = _make_folder(1, title="默认收藏夹", attr=0)
        empty_plan = _make_plan()
        # Rebuild with no operations
        empty_plan = OrganizePlan(
            total_operations=0,
            folders_to_create=[],
            moves=[],
            deletions=[],
            summary="无需任何操作",
        )
        result = generate_preview(empty_plan)

        assert "# 🗂️ 收藏夹整理计划" in result
        assert "无需任何操作" in result

    def test_plan_with_operations(self):
        """Plan with operations shows all sections."""
        plan = _make_plan()
        result = generate_preview(plan)

        assert "# 🗂️ 收藏夹整理计划" in result
        assert "📁 新建 1 个文件夹" in result
        assert "科技区" in result
        assert "删除" in result
        assert "分类移动计划" in result
        assert "是否执行以上操作？(y/N)" in result

    def test_preview_contains_summary_bar(self):
        """Summary bar shows create/move/delete counts."""
        plan = _make_plan()
        result = generate_preview(plan)

        assert "📁" in result
        assert "↗️" in result
        assert "🗑️" in result

    def test_new_folder_listed(self):
        """Folders to create are listed in the preview."""
        plan = _make_plan()
        result = generate_preview(plan)

        assert "📁 新文件夹" in result
        assert "科技区" in result

    def test_move_plan_shown(self):
        """Classification move plan is included."""
        plan = _make_plan()
        result = generate_preview(plan)

        assert "↗️ 分类移动计划" in result
        assert "科技区" in result
        assert "BV1xx411c7mD" in result


# ---------------------------------------------------------------------------
# execute_plan
# ---------------------------------------------------------------------------


class TestExecutePlan:
    """Tests for the async execute_plan function."""

    @pytest.mark.asyncio
    async def test_creates_folders(self):
        """New folders are created via fav_api.create_folder."""
        plan = _make_plan()
        fav_api = MagicMock()
        fav_api.create_folder = AsyncMock()

        await execute_plan(plan, fav_api)

        fav_api.create_folder.assert_called_once_with(title="科技区")

    @pytest.mark.asyncio
    async def test_moves_items(self):
        """Items are moved via fav_api.move_items."""
        folder = _make_folder(2, title="旧收藏", attr=1)
        item = _make_item(1)
        target_folder = _make_folder(3, title="科技区", attr=1)
        move_op = Operation(
            action="move",
            source=folder,
            target=target_folder,
            resources=[item],
        )
        plan = OrganizePlan(
            total_operations=2,
            folders_to_create=[],
            moves=[move_op],
            deletions=[],
            summary="移动 1 个内容",
        )
        fav_api = MagicMock()
        fav_api.move_items = AsyncMock()

        await execute_plan(plan, fav_api)

        fav_api.move_items.assert_called_once()
        call_args = fav_api.move_items.call_args
        assert call_args[1]["src_media_id"] == folder.id
        assert "1:2" in call_args[1]["resources"]

    @pytest.mark.asyncio
    async def test_deletes_items(self):
        """Invalid/duplicate items are deleted via fav_api.batch_delete."""
        folder = _make_folder(2, title="旧收藏", attr=1)
        item = _make_item(1)
        delete_op = Operation(
            action="batch_delete",
            source=folder,
            resources=[item],
        )
        plan = OrganizePlan(
            total_operations=1,
            folders_to_create=[],
            moves=[],
            deletions=[delete_op],
            summary="删除 1 个内容",
        )
        fav_api = MagicMock()
        fav_api.batch_delete = AsyncMock()

        await execute_plan(plan, fav_api)

        fav_api.batch_delete.assert_called_once()
        call_args = fav_api.batch_delete.call_args
        assert call_args[1]["media_id"] == folder.id
        assert "1:2" in call_args[1]["resources"]

    @pytest.mark.asyncio
    async def test_execute_empty_plan_does_nothing(self):
        """An empty plan should result in no API calls."""
        plan = OrganizePlan(
            total_operations=0,
            folders_to_create=[],
            moves=[],
            deletions=[],
            summary="无需任何操作",
        )
        fav_api = MagicMock()
        fav_api.create_folder = AsyncMock()
        fav_api.move_items = AsyncMock()
        fav_api.batch_delete = AsyncMock()

        await execute_plan(plan, fav_api)

        fav_api.create_folder.assert_not_called()
        fav_api.move_items.assert_not_called()
        fav_api.batch_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_continue_on_error(self):
        """Execution continues if one batch fails."""
        plan = _make_plan()
        fav_api = MagicMock()
        fav_api.create_folder = AsyncMock(side_effect=Exception("API error"))
        fav_api.move_items = AsyncMock()
        fav_api.batch_delete = AsyncMock()

        # Should not raise despite create_folder failing
        await execute_plan(plan, fav_api)

        # Should continue to move and delete steps
        fav_api.move_items.assert_called()
        fav_api.batch_delete.assert_called()
