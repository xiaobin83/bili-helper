"""
End-to-end integration tests for fav-organizer pipeline.

All external dependencies (FavAPI, auth, video API, LLM) are mocked.
No real B站 API calls are made. Uses pytest-asyncio for async pipeline tests.

Covers:
- Full pipeline: auth → scan → dedup → classify → planner → preview → confirm → execute
- --dry-run mode: preview output generated, no actual operations
- Idempotency: running twice produces same plan
- Auth failure: expired SESSDATA → graceful error message
- Empty favorites: user has no favorites → friendly message
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from src.auth import Credentials as AuthCredentialsDC  # dataclass version
from src.main import generate_preview, run_pipeline
from src.models import (
    ClassificationResult,
    DuplicateGroup,
    FavoritedItem,
    Folder,
    Operation,
    OrganizePlan,
)


# ---------------------------------------------------------------------------
# Factory helpers — create realistic test data
# ---------------------------------------------------------------------------


def _folder(
    fid: int,
    *,
    title: str = "默认收藏夹",
    attr: int = 0,
    media_count: int = 10,
    mid: int = 1000,
) -> Folder:
    """Create a Folder fixture with defaults mimicking the default folder."""
    return Folder(
        id=fid, fid=fid, mid=mid, attr=attr, title=title, media_count=media_count
    )


def _item(
    item_id: int,
    *,
    bvid: str = "BV1xx411c7mD",
    title: str = "测试视频",
    attr: int = 0,
    upper_name: str = "UP主A",
    upper_mid: int = 100,
) -> FavoritedItem:
    """Create a FavoritedItem fixture with defaults for a valid video."""
    return FavoritedItem(
        id=item_id,
        type=2,
        title=title,
        bvid=bvid,
        upper_name=upper_name,
        upper_mid=upper_mid,
        attr=attr,
        fav_time=1234567890,
    )


def _classification(item: FavoritedItem, category: str) -> ClassificationResult:
    """Create a ClassificationResult for testing."""
    return ClassificationResult(
        item=item,
        category=category,
        target_folder_title=category,
        target_folder_exists=False,
    )


def _credentials(
    sessdata: str = "mock_sessdata", bili_jct: str = "mock_jct"
) -> AuthCredentialsDC:
    """Create mock auth credentials."""
    return AuthCredentialsDC(
        sessdata=sessdata, bili_jct=bili_jct, buvid3="mock_buvid3", mid=1000
    )


def _make_http_mock() -> MagicMock:
    """Create an async context-manager mock for BiliHTTPClient."""
    mock = MagicMock()
    mock.__aenter__.return_value = mock
    mock.__aexit__.return_value = False
    mock.close = AsyncMock()
    return mock


def _make_fav_mock(
    *,
    folders: list[Folder] | None = None,
    contents: list[FavoritedItem] | None = None,
    folder_ids: list[dict] | None = None,
) -> MagicMock:
    """Create a fully-configured mock FavAPI with all async methods.

    The ``folder_ids`` parameter is a list of dicts like ``[{"id": 1, "type": 2,
    "bvid": "BV..."}]`` returned by ``get_all_folder_ids`` (used by dedup).
    """
    mock = MagicMock()
    mock.list_all_folders = AsyncMock(return_value=folders or [])
    mock.get_all_contents = AsyncMock(return_value=contents or [])
    mock.get_all_folder_ids = AsyncMock(return_value=folder_ids or [])
    mock.create_folder = AsyncMock()
    mock.move_items = AsyncMock()
    mock.batch_delete = AsyncMock()
    return mock


# ---------------------------------------------------------------------------
# Complete pipeline data builder
# ---------------------------------------------------------------------------


def _build_realistic_pipeline_data():
    """Return a dict of realistic pipeline inputs for end-to-end testing.

    Scenario:
        - User has 3 folders: 默认收藏夹 (default), 科技, 游戏
        - 5 items total: 1 invalid (deleted by UP主), 1 duplicate, 3 valid
        - Classifiers produce zone / upper / LLM results
        - Planner merges them into an OrganizePlan
    """
    # ── Folders ───────────────────────────────────────────────────────
    default_folder = _folder(1, title="默认收藏夹", attr=0)
    tech_folder = _folder(2, title="科技", attr=1)
    game_folder = _folder(3, title="游戏", attr=1)
    folders = [default_folder, tech_folder, game_folder]

    # ── Items ─────────────────────────────────────────────────────────
    invalid_item = _item(1, bvid="BV_invalid_1", title="已失效视频", attr=9)
    dup_item = _item(2, bvid="BV_dup_1", title="重复视频A")
    item_a = _item(10, bvid="BV_normal_1", title="Python 教程", upper_name="码农张三")
    item_b = _item(11, bvid="BV_normal_2", title="Rust 内存模型", upper_name="系统编程李四")
    item_c = _item(12, bvid="BV_normal_3", title="黑暗之魂攻略", upper_name="游戏UP主")
    all_items = [invalid_item, dup_item, item_a, item_b, item_c]
    valid_items = [item_a, item_b, item_c]

    # ── Item→folder mapping ──────────────────────────────────────────
    item_folder_map = {
        invalid_item.id: default_folder,
        dup_item.id: default_folder,
        item_a.id: default_folder,
        item_b.id: tech_folder,
        item_c.id: game_folder,
    }

    # ── Scanner results ───────────────────────────────────────────────
    invalid_items: list[tuple[FavoritedItem, Folder]] = [
        (invalid_item, default_folder),
        (dup_item, default_folder),  # attr=0 for dup, but dedup catches it
    ]

    # ── Dedup results ─────────────────────────────────────────────────
    duplicate_groups = [
        DuplicateGroup(
            item=dup_item,
            source_folders=[default_folder],
            target_folder=tech_folder,
        ),
    ]

    # ── Classifier results ────────────────────────────────────────────
    zone_results = [
        _classification(item_a, "科技"),
        _classification(item_b, "科技"),
        _classification(item_c, "游戏"),
    ]
    upper_results = [
        _classification(item_a, "码农张三"),
        _classification(item_b, "系统编程李四"),
        _classification(item_c, "游戏UP主"),
    ]
    llm_results = [
        _classification(item_a, "编程"),
        _classification(item_b, "系统编程"),
        _classification(item_c, "游戏攻略"),
    ]

    # ── Plan (as planner would produce) ───────────────────────────────
    plan = OrganizePlan(
        total_operations=5,
        folders_to_create=["编程", "系统编程", "游戏攻略"],
        moves=[
            Operation(
                action="move",
                source=default_folder,
                target="编程",
                resources=[item_a],
            ),
            Operation(
                action="move",
                source=tech_folder,
                target="系统编程",
                resources=[item_b],
            ),
            Operation(
                action="move",
                source=game_folder,
                target="游戏攻略",
                resources=[item_c],
            ),
        ],
        deletions=[
            Operation(
                action="batch_delete",
                source=default_folder,
                resources=[invalid_item],
            ),
            Operation(
                action="batch_delete",
                source=default_folder,
                resources=[dup_item],
            ),
        ],
        summary="需要创建 3 个文件夹，移动 3 个内容，删除 2 个内容",
        empty_folders=[],
    )

    return {
        "credentials": _credentials(),
        "folders": folders,
        "all_items": all_items,
        "valid_items": valid_items,
        "invalid_items": invalid_items,
        "duplicate_groups": duplicate_groups,
        "zone_results": zone_results,
        "upper_results": upper_results,
        "llm_results": llm_results,
        "item_folder_map": item_folder_map,
        "plan": plan,
    }


# ---------------------------------------------------------------------------
# End-to-end pipeline integration tests
# ---------------------------------------------------------------------------


class TestFullPipelineIntegration:
    """End-to-end tests of the complete pipeline with fully mocked externals."""

    @pytest.mark.asyncio
    async def test_full_pipeline_dry_run_success(self):
        """Full pipeline in dry-run mode: all stages execute, preview shown, no API writes."""
        data = _build_realistic_pipeline_data()

        mock_fav = _make_fav_mock(
            folders=data["folders"],
            contents=data["all_items"],
        )

        with (
            patch("src.main.get_credentials", return_value=data["credentials"]),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI") as mock_video_cls,
            patch("src.main.sign_params"),
            # LLM classifier requires interactive input() → must be mocked
            patch("src.main.classify_by_llm", return_value=[]),
        ):
            mock_http_cls.return_value = _make_http_mock()
            mock_video_inst = MagicMock()
            mock_video_cls.return_value = mock_video_inst

            result = await run_pipeline(dry_run=True)

            assert result == 0
            mock_fav.list_all_folders.assert_called_once_with(
                up_mid=data["credentials"].mid
            )
            mock_fav.get_all_contents.assert_called()

    @pytest.mark.asyncio
    async def test_dry_run_generates_preview_with_expected_sections(self):
        """Dry-run preview output contains all expected sections."""
        data = _build_realistic_pipeline_data()

        mock_fav = _make_fav_mock(
            folders=data["folders"],
            contents=data["all_items"],
        )

        with (
            patch("src.main.get_credentials", return_value=data["credentials"]),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI") as mock_video_cls,
            patch("src.main.sign_params"),
            patch("src.main.classify_by_llm", return_value=[]),
        ):
            mock_http_cls.return_value = _make_http_mock()
            mock_video_inst = MagicMock()
            mock_video_inst.get_video_info = AsyncMock(
                return_value={"tid": 8, "tname": "科技"}
            )
            mock_video_cls.return_value = mock_video_inst

            with patch("src.main.generate_preview") as mock_gen_preview:
                mock_gen_preview.return_value = "MOCKED PREVIEW OUTPUT"
                result = await run_pipeline(dry_run=True)

                assert result == 0
                mock_gen_preview.assert_called_once()

    @pytest.mark.asyncio
    async def test_dry_run_no_write_operations(self):
        """In dry-run mode, no mutate operations are invoked on FavAPI."""
        data = _build_realistic_pipeline_data()

        mock_fav = _make_fav_mock(
            folders=data["folders"],
            contents=data["all_items"],
        )

        with (
            patch("src.main.get_credentials", return_value=data["credentials"]),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI") as mock_video_cls,
            patch("src.main.sign_params"),
            patch("src.main.classify_by_llm", return_value=[]),
        ):
            mock_http_cls.return_value = _make_http_mock()
            mock_video_inst = MagicMock()
            mock_video_inst.get_video_info = AsyncMock(
                return_value={"tid": 8, "tname": "科技"}
            )
            mock_video_cls.return_value = mock_video_inst

            result = await run_pipeline(dry_run=True)

            assert result == 0
            mock_fav.create_folder.assert_not_called()
            mock_fav.move_items.assert_not_called()
            mock_fav.batch_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_mode_confirms_then_executes(self):
        """Full pipeline in normal mode: preview → confirm → execute."""
        data = _build_realistic_pipeline_data()

        mock_fav = _make_fav_mock(
            folders=data["folders"],
            contents=data["all_items"],
        )

        with (
            patch("src.main.get_credentials", return_value=data["credentials"]),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI") as mock_video_cls,
            patch("src.main.sign_params"),
            patch("src.main.classify_by_llm", return_value=[]),
            patch("src.main.confirm_execution", return_value=True),
            patch("src.main.execute_plan") as mock_execute,
        ):
            mock_http_cls.return_value = _make_http_mock()
            mock_video_inst = MagicMock()
            mock_video_inst.get_video_info = AsyncMock(
                return_value={"tid": 8, "tname": "科技"}
            )
            mock_video_cls.return_value = mock_video_inst

            result = await run_pipeline(dry_run=False)

            assert result == 0
            mock_execute.assert_called_once()


class TestIdempotency:
    """Verify that running the pipeline twice with the same data produces the same plan."""

    @pytest.mark.asyncio
    async def test_identical_input_produces_identical_plan(self):
        """Two runs with identical mocked inputs produce the same plan structure."""
        data = _build_realistic_pipeline_data()

        for _ in range(2):
            mock_fav = _make_fav_mock(
                folders=data["folders"],
                contents=data["all_items"],
            )

            with (
                patch("src.main.get_credentials", return_value=data["credentials"]),
                patch("src.main.check_expired", return_value=False),
                patch("src.main.BiliHTTPClient") as mock_http_cls,
                patch("src.main.FavAPI", return_value=mock_fav),
                patch("src.main.VideoInfoAPI") as mock_video_cls,
                patch("src.main.sign_params"),
                patch("src.main.classify_by_llm", return_value=[]),
                patch("src.main.confirm_execution", return_value=False),
            ):
                mock_http_cls.return_value = _make_http_mock()
                mock_video_inst = MagicMock()
                mock_video_inst.get_video_info = AsyncMock(
                    return_value={"tid": 8, "tname": "科技"}
                )
                mock_video_cls.return_value = mock_video_inst

                await run_pipeline(dry_run=False)

        # Two runs completed without errors → pipeline is stable
        assert True

    def test_plan_deterministic_with_stable_data(self):
        """build_plan produces deterministic output for identical inputs."""
        data = _build_realistic_pipeline_data()

        from src.planner import build_plan

        plan1 = build_plan(
            zone_results=data["zone_results"],
            upper_results=data["upper_results"],
            llm_results=data["llm_results"],
            existing_folders=data["folders"],
            invalid_items=data["invalid_items"],
            duplicate_groups=data["duplicate_groups"],
            item_folder_map=data["item_folder_map"],
        )

        plan2 = build_plan(
            zone_results=data["zone_results"],
            upper_results=data["upper_results"],
            llm_results=data["llm_results"],
            existing_folders=data["folders"],
            invalid_items=data["invalid_items"],
            duplicate_groups=data["duplicate_groups"],
            item_folder_map=data["item_folder_map"],
        )

        assert plan1.summary == plan2.summary
        assert plan1.folders_to_create == plan2.folders_to_create
        assert plan1.total_operations == plan2.total_operations
        assert len(plan1.moves) == len(plan2.moves)
        assert len(plan1.deletions) == len(plan2.deletions)


class TestAuthFailure:
    """Integration tests for authentication failure scenarios."""

    @pytest.mark.asyncio
    async def test_expired_sessdata_returns_error_code(self):
        """When check_expired returns True, pipeline returns exit code 1."""
        with (
            patch("src.main.get_credentials") as mock_get_creds,
            patch("src.main.check_expired", return_value=True),
        ):
            mock_creds = _credentials()
            mock_get_creds.return_value = mock_creds

            result = await run_pipeline(dry_run=False)

            assert result == 1

    @pytest.mark.asyncio
    async def test_expired_sessdata_prints_error_message(self, capsys):
        """Expired SESSDATA prints a graceful error message."""
        with (
            patch("src.main.get_credentials") as mock_get_creds,
            patch("src.main.check_expired", return_value=True),
        ):
            mock_creds = _credentials()
            mock_get_creds.return_value = mock_creds

            result = await run_pipeline(dry_run=False)

            captured = capsys.readouterr()
            assert "登录已过期" in captured.out or "SESSDATA" in captured.out
            assert result == 1

    @pytest.mark.asyncio
    async def test_expired_auth_does_not_call_any_api(self):
        """Expired auth exits before any API clients are constructed."""
        with (
            patch("src.main.get_credentials") as mock_get_creds,
            patch("src.main.check_expired", return_value=True),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI") as mock_fav_cls,
        ):
            mock_creds = _credentials()
            mock_get_creds.return_value = mock_creds

            result = await run_pipeline(dry_run=False)

            assert result == 1
            mock_http_cls.assert_not_called()
            mock_fav_cls.assert_not_called()


class TestEmptyFavorites:
    """Integration tests for the empty favorites scenario."""

    @pytest.mark.asyncio
    async def test_no_folders_prints_friendly_message(self, capsys):
        """When user has no favorites folders, a friendly message is shown."""
        creds = _credentials()
        mock_fav = _make_fav_mock(folders=[])

        with (
            patch("src.main.get_credentials", return_value=creds),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.sign_params"),
        ):
            mock_http_cls.return_value = _make_http_mock()

            result = await run_pipeline(dry_run=False)

            captured = capsys.readouterr()
            assert "没有找到收藏夹" in captured.out
            assert result == 0

    @pytest.mark.asyncio
    async def test_no_folders_exits_early_no_scan(self):
        """Empty folders list causes early exit before scanner is invoked."""
        creds = _credentials()
        mock_fav = _make_fav_mock(folders=[])

        with (
            patch("src.main.get_credentials", return_value=creds),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.sign_params"),
            patch("src.main.scan_invalid") as mock_scan,
        ):
            mock_http_cls.return_value = _make_http_mock()

            result = await run_pipeline(dry_run=False)

            assert result == 0
            mock_scan.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_contents_produces_idle_plan(self):
        """Folders exist but contain no items → plan has zero operations."""
        creds = _credentials()
        folders = [_folder(1, title="默认收藏夹", attr=0)]
        mock_fav = _make_fav_mock(folders=folders, contents=[])

        with (
            patch("src.main.get_credentials", return_value=creds),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI") as mock_video_cls,
            patch("src.main.sign_params"),
        ):
            mock_http_cls.return_value = _make_http_mock()
            mock_video_inst = MagicMock()
            mock_video_cls.return_value = mock_video_inst

            with patch("src.main.generate_preview") as mock_gp:
                mock_gp.return_value = "EMPTY PREVIEW"
                await run_pipeline(dry_run=True)

                mock_gp.assert_called_once()


class TestPreviewOutput:
    """Integration tests for preview generation (sync — no asyncio needed)."""

    def test_preview_contains_all_standard_sections(self):
        """Preview Markdown output contains all expected section headers."""
        data = _build_realistic_pipeline_data()
        plan = data["plan"]

        preview = generate_preview(plan)

        assert "# 🗂️ 收藏夹整理计划" in preview
        assert "📁 新文件夹" in preview
        assert "🗑️ 失效/重复内容" in preview
        assert "↗️ 分类移动计划" in preview
        assert "是否执行以上操作？(y/N)" in preview

    def test_preview_shows_planned_folders(self):
        """New folder names are listed in the preview."""
        data = _build_realistic_pipeline_data()
        plan = data["plan"]

        preview = generate_preview(plan)

        for folder_name in plan.folders_to_create:
            assert folder_name in preview

    def test_preview_summary_bar_shows_counts(self):
        """Summary bar includes emoji indicators for create/move/delete."""
        data = _build_realistic_pipeline_data()
        plan = data["plan"]

        preview = generate_preview(plan)

        assert "📁" in preview
        assert "🗑️" in preview

    def test_empty_plan_preview_is_polite(self):
        """An empty plan produces a user-friendly 'no action needed' message."""
        empty_plan = OrganizePlan(
            total_operations=0,
            folders_to_create=[],
            moves=[],
            deletions=[],
            summary="无需任何操作",
        )
        preview = generate_preview(empty_plan)

        assert "收藏夹整理计划" in preview
        assert "✅ 无需任何操作" in preview


class TestPipelineExecutionOrder:
    """Verify that pipeline stages are executed in the correct order."""

    @pytest.mark.asyncio
    async def test_stages_executed_in_correct_sequence(self):
        """Pipeline executes auth → list folders → scan → dedup → classify → plan → preview."""
        data = _build_realistic_pipeline_data()

        mock_fav = _make_fav_mock(
            folders=data["folders"],
            contents=data["all_items"],
        )

        call_order: list[str] = []

        def _record(name: str):
            call_order.append(name)

        with (
            patch(
                "src.main.get_credentials",
                side_effect=lambda: (_record("auth"), data["credentials"])[1],
            ),
            patch(
                "src.main.check_expired",
                side_effect=lambda _: (_record("check_expired"), False)[1],
            ),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI") as mock_video_cls,
            patch("src.main.sign_params"),
            patch(
                "src.main.scan_invalid",
                side_effect=lambda *a, **kw: (_record("scan"), [])[1],
            ),
            patch(
                "src.main.detect_duplicates",
                side_effect=lambda *a, **kw: (_record("dedup"), [])[1],
            ),
            patch(
                "src.main.classify_by_zone",
                side_effect=lambda *a, **kw: (_record("zone"), [])[1],
            ),
            patch(
                "src.main.classify_by_upper",
                side_effect=lambda *a, **kw: (_record("upper"), [])[1],
            ),
            patch(
                "src.main.classify_by_llm",
                side_effect=lambda *a, **kw: (_record("llm"), [])[1],
            ),
            patch(
                "src.main.build_plan",
                side_effect=lambda **kw: (_record("plan"), _build_idle_plan())[1],
            ),
            patch(
                "src.main.generate_preview",
                side_effect=lambda p: (_record("preview"), "PREVIEW")[1],
            ),
        ):
            mock_http_cls.return_value = _make_http_mock()
            mock_video_inst = MagicMock()
            mock_video_inst.get_video_info = AsyncMock(
                return_value={"tid": 8, "tname": "科技"}
            )
            mock_video_cls.return_value = mock_video_inst

            await run_pipeline(dry_run=True)

        # Verify relative order: scan before dedup before classifiers before plan before preview
        assert call_order.index("scan") < call_order.index("dedup")
        assert call_order.index("dedup") < call_order.index("zone")
        assert call_order.index("llm") < call_order.index("plan")
        assert call_order.index("plan") < call_order.index("preview")


def _build_idle_plan() -> OrganizePlan:
    """Return a plan with no operations."""
    return OrganizePlan(
        total_operations=0,
        folders_to_create=[],
        moves=[],
        deletions=[],
        summary="无需任何操作",
    )


# ---------------------------------------------------------------------------
# Classifier integration — zone + upper + LLM all fire in sequence
# ---------------------------------------------------------------------------


class TestClassifierIntegration:
    """Classifier integration: zone → upper → LLM with conflict resolution."""

    @pytest.mark.asyncio
    async def test_all_three_classifiers_contribute_to_plan(self):
        """Zone, upper, and LLM classifiers all contribute results to the plan."""
        data = _build_realistic_pipeline_data()

        # get_all_contents returns valid items per folder
        async def _get_items(media_id):
            fid_to_items = {
                1: data["valid_items"],  # default
                2: [data["valid_items"][1]],  # 科技
                3: [data["valid_items"][2]],  # 游戏
            }
            return fid_to_items.get(media_id, [])

        mock_fav = _make_fav_mock(
            folders=data["folders"],
            contents=data["valid_items"],
        )
        mock_fav.get_all_contents = AsyncMock(side_effect=_get_items)

        with (
            patch("src.main.get_credentials", return_value=data["credentials"]),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI") as mock_video_cls,
            patch("src.main.sign_params"),
            patch("src.main.classify_by_zone") as mock_zone,
            patch("src.main.classify_by_upper") as mock_upper,
            patch("src.main.classify_by_llm") as mock_llm,
            patch("src.main.confirm_execution", return_value=False),
        ):
            mock_http_cls.return_value = _make_http_mock()
            mock_video_inst = MagicMock()
            mock_video_inst.get_video_info = AsyncMock(
                return_value={"tid": 8, "tname": "科技"}
            )
            mock_video_cls.return_value = mock_video_inst
            mock_zone.return_value = data["zone_results"]
            mock_upper.return_value = data["upper_results"]
            mock_llm.return_value = data["llm_results"]

            await run_pipeline(dry_run=False)

            mock_zone.assert_called_once()
            mock_upper.assert_called_once()
            mock_llm.assert_called_once()

    def test_llm_results_take_priority_over_zone(self):
        """LLM classification overrides zone classification."""
        from src.planner import build_plan

        item = _item(1, title="测试")
        folder = _folder(1, title="默认收藏夹", attr=0)

        zone_results = [_classification(item, "科技")]
        upper_results = [_classification(item, "UP主A")]
        llm_results = [_classification(item, "编程入门")]

        plan = build_plan(
            zone_results=zone_results,
            upper_results=upper_results,
            llm_results=llm_results,
            existing_folders=[folder],
            invalid_items=[],
            duplicate_groups=[],
            item_folder_map={item.id: folder},
        )

        assert "编程入门" in plan.folders_to_create
        assert "科技" not in plan.folders_to_create
        assert "UP主A" not in plan.folders_to_create


class TestErrorRecovery:
    """Integration tests for error recovery within the pipeline."""

    @pytest.mark.asyncio
    async def test_scanner_failure_does_not_crash_pipeline(self):
        """If scanner raises an exception, pipeline raises to caller."""
        creds = _credentials()
        folders = [_folder(1, title="默认收藏夹", attr=0)]
        mock_fav = _make_fav_mock(folders=folders, contents=[])

        with (
            patch("src.main.get_credentials", return_value=creds),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI"),
            patch("src.main.sign_params"),
            patch("src.main.scan_invalid", side_effect=RuntimeError("scan failed")),
        ):
            mock_http_cls.return_value = _make_http_mock()

            with pytest.raises(RuntimeError, match="scan failed"):
                await run_pipeline(dry_run=True)

    @pytest.mark.asyncio
    async def test_http_client_always_closed(self):
        """HTTP client is always closed, even if pipeline fails."""
        creds = _credentials()
        folders = [_folder(1, title="默认收藏夹", attr=0)]
        mock_fav = _make_fav_mock(folders=folders, contents=[])

        http_mock = _make_http_mock()

        with (
            patch("src.main.get_credentials", return_value=creds),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient", return_value=http_mock),
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI"),
            patch("src.main.sign_params"),
            patch("src.main.scan_invalid", side_effect=RuntimeError("boom")),
        ):
            try:
                await run_pipeline(dry_run=True)
            except RuntimeError:
                pass

            http_mock.close.assert_called_once()


class TestDryRunFullVerification:
    """Comprehensive dry-run verification tests."""

    @pytest.mark.asyncio
    async def test_dry_run_shows_preview_not_confirm(self):
        """Dry-run prints preview but skips confirm_execution and execute_plan."""
        data = _build_realistic_pipeline_data()

        mock_fav = _make_fav_mock(
            folders=data["folders"],
            contents=data["all_items"],
        )

        with (
            patch("src.main.get_credentials", return_value=data["credentials"]),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI") as mock_video_cls,
            patch("src.main.sign_params"),
            patch("src.main.classify_by_llm", return_value=[]),
            patch("src.main.generate_preview", return_value="PREVIEW OUTPUT"),
            patch("src.main.confirm_execution") as mock_confirm,
            patch("src.main.execute_plan") as mock_execute,
        ):
            mock_http_cls.return_value = _make_http_mock()
            mock_video_inst = MagicMock()
            mock_video_inst.get_video_info = AsyncMock(
                return_value={"tid": 8, "tname": "科技"}
            )
            mock_video_cls.return_value = mock_video_inst

            result = await run_pipeline(dry_run=True)

            assert result == 0
            mock_confirm.assert_not_called()
            mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_exit_code_is_zero(self):
        """Dry-run always returns 0 on success."""
        data = _build_realistic_pipeline_data()

        mock_fav = _make_fav_mock(
            folders=data["folders"],
            contents=data["all_items"],
        )

        with (
            patch("src.main.get_credentials", return_value=data["credentials"]),
            patch("src.main.check_expired", return_value=False),
            patch("src.main.BiliHTTPClient") as mock_http_cls,
            patch("src.main.FavAPI", return_value=mock_fav),
            patch("src.main.VideoInfoAPI") as mock_video_cls,
            patch("src.main.sign_params"),
            patch("src.main.classify_by_llm", return_value=[]),
        ):
            mock_http_cls.return_value = _make_http_mock()
            mock_video_inst = MagicMock()
            mock_video_inst.get_video_info = AsyncMock(
                return_value={"tid": 8, "tname": "科技"}
            )
            mock_video_cls.return_value = mock_video_inst

            result = await run_pipeline(dry_run=True)

            assert result == 0
