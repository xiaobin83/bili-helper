"""Tests for execute_plan — execution order, batching, error recovery.

Covers:
- Execution order: create → move → delete → clean
- Batching: 40 moves → 2 batches (30+10)
- Error recovery: batch 2 fails, batch 1 still succeeded
- Progress output verification
- Empty plan executes successfully (no operations)
"""

from __future__ import annotations

import pytest

from src.fav_organizer.executor import execute_plan
from src.fav_organizer.models import (
    ExecutionReport,
    FavoritedItem,
    Folder,
    Operation,
    OrganizePlan,
)


# ---------------------------------------------------------------------------
# Test data builders
# ---------------------------------------------------------------------------


def _make_item(
    item_id: int = 1,
    *,
    bvid: str = "BV1xx411c7mD",
    title: str = "测试视频",
    upper_name: str = "UP主A",
    rtype: int = 2,
    attr: int = 0,
) -> FavoritedItem:
    return FavoritedItem(
        id=item_id,
        type=rtype,
        title=title,
        bvid=bvid,
        upper_name=upper_name,
        upper_mid=100 + item_id,
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


# ---------------------------------------------------------------------------
# Mock FavAPI
# ---------------------------------------------------------------------------


class MockFavAPI:
    """Drop-in FavAPI mock that records every call and returns fake data.

    Set ``_fail_on`` to a mapping of ``(method, arg_value) → Exception``
    to simulate failures on specific calls.  ``_next_id`` controls the
    auto-incrementing id returned by ``create_folder``.
    """

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._fail_on: dict[tuple[str, object], Exception] = {}
        self._next_id = 100

    async def create_folder(self, title, intro="", privacy=0):
        call_key = ("create_folder", title)
        self.calls.append(("create_folder", {"title": title}))
        if call_key in self._fail_on:
            raise self._fail_on[call_key]
        fid = self._next_id
        self._next_id += 1
        return {"code": 0, "data": {"id": fid, "title": title}}

    async def move_items(self, src_media_id, tar_media_id, resources, mid):
        self.calls.append(
            (
                "move_items",
                {
                    "src_media_id": src_media_id,
                    "tar_media_id": tar_media_id,
                    "resources": resources,
                    "mid": mid,
                },
            )
        )
        call_key = ("move_items", None)  # match any move
        if call_key in self._fail_on:
            raise self._fail_on[call_key]
        return {"code": 0}

    async def batch_delete(self, media_id, resources):
        self.calls.append(
            ("batch_delete", {"media_id": media_id, "resources": resources})
        )
        return {"code": 0}

    async def clean_invalid(self, media_id):
        self.calls.append(("clean_invalid", {"media_id": media_id}))
        return {"code": 0}


# ---------------------------------------------------------------------------
# Empty plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_plan_executes_successfully():
    """An OrganizePlan with no operations returns zero-attempted report."""
    plan = OrganizePlan(
        total_operations=0,
        folders_to_create=[],
        moves=[],
        deletions=[],
        summary="无需操作",
    )
    api = MockFavAPI()

    report = await execute_plan(plan, api, mid=1000, existing_folders=[])

    assert isinstance(report, ExecutionReport)
    assert report.total_attempted == 0
    assert report.succeeded == 0
    assert report.failed == 0
    assert report.errors == []
    assert report.details == []
    assert api.calls == []


# ---------------------------------------------------------------------------
# Execution order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_order_is_create_move_delete_clean():
    """Verifies that the executor calls the API in the required order."""
    src = _make_folder(1, title="默认收藏夹", attr=0)
    # Item to move from default → 科技
    item = _make_item(1, title="科技视频")
    # Operation: create_folder("科技"), then move item 1
    plan = OrganizePlan(
        total_operations=2,
        folders_to_create=["科技"],
        moves=[
            Operation(
                action="move",
                source=src,
                target="科技",
                resources=[item],
            )
        ],
        deletions=[],
        summary="创建 1 个文件夹, 移动 1 个视频",
    )
    api = MockFavAPI()

    await execute_plan(plan, api, mid=1000, existing_folders=[src])

    method_order = [c[0] for c in api.calls]
    assert method_order == ["create_folder", "move_items"]


@pytest.mark.asyncio
async def test_delete_happens_after_move_and_before_clean():
    """Delete calls must occur between move and clean_invalid."""
    src = _make_folder(1, title="默认收藏夹", attr=0)
    bad_folder = _make_folder(2, title="旧收藏夹", attr=1)
    move_item = _make_item(1, title="科技视频")
    bad_item = _make_item(2, title="失效视频", attr=9, rtype=2)

    plan = OrganizePlan(
        total_operations=3,
        folders_to_create=["科技"],
        moves=[
            Operation(
                action="move",
                source=src,
                target="科技",
                resources=[move_item],
            )
        ],
        deletions=[
            Operation(
                action="batch_delete",
                source=bad_folder,
                resources=[bad_item],
            )
        ],
        summary="创建 1 个文件夹, 移动 1 个视频, 删除 1 个失效内容",
    )
    api = MockFavAPI()

    await execute_plan(plan, api, mid=1000, existing_folders=[src, bad_folder])

    method_order = [c[0] for c in api.calls]
    assert method_order == [
        "create_folder",
        "move_items",
        "batch_delete",
        "clean_invalid",
    ]


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_40_moves_split_into_2_batches():
    """40 resources should produce two move calls: 30 + 10."""
    src = _make_folder(1, title="音乐收藏", attr=1)
    target_folder = _make_folder(2, title="音乐", attr=1)
    items = [_make_item(i, title=f"音乐视频 {i}") for i in range(1, 41)]

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],
        moves=[
            Operation(
                action="move",
                source=src,
                target="音乐",
                resources=items,
            )
        ],
        deletions=[],
        summary="移动 40 个视频到 音乐",
    )
    api = MockFavAPI()

    await execute_plan(
        plan, api, mid=1000, existing_folders=[src, target_folder]
    )

    move_calls = [c for c in api.calls if c[0] == "move_items"]
    assert len(move_calls) == 2

    batch1 = move_calls[0][1]["resources"]
    batch2 = move_calls[1][1]["resources"]
    assert len(batch1) == 30
    assert len(batch2) == 10
    # Verify resource string format: "{id}:{type}"
    assert batch1[0] == "1:2"
    assert batch2[0] == "31:2"


@pytest.mark.asyncio
async def test_30_resources_exactly_one_batch():
    """Exactly 30 resources → one batch (boundary)."""
    src = _make_folder(1, title="音乐收藏", attr=1)
    target_folder = _make_folder(2, title="音乐", attr=1)
    items = [_make_item(i) for i in range(1, 31)]

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],
        moves=[
            Operation(
                action="move",
                source=src,
                target="音乐",
                resources=items,
            )
        ],
        deletions=[],
        summary="移动 30 个视频",
    )
    api = MockFavAPI()

    await execute_plan(
        plan, api, mid=1000, existing_folders=[src, target_folder]
    )

    move_calls = [c for c in api.calls if c[0] == "move_items"]
    assert len(move_calls) == 1
    assert len(move_calls[0][1]["resources"]) == 30


@pytest.mark.asyncio
async def test_deletions_also_batched():
    """Deletion resources are also batched at 30."""
    folder = _make_folder(2, title="旧收藏夹", attr=1)
    items = [_make_item(i, attr=9) for i in range(1, 61)]

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],
        moves=[],
        deletions=[
            Operation(
                action="batch_delete",
                source=folder,
                resources=items,
            )
        ],
        summary="删除 60 个失效内容",
    )
    api = MockFavAPI()

    await execute_plan(
        plan, api, mid=1000, existing_folders=[folder]
    )

    del_calls = [c for c in api.calls if c[0] == "batch_delete"]
    assert len(del_calls) == 2
    assert len(del_calls[0][1]["resources"]) == 30
    assert len(del_calls[1][1]["resources"]) == 30


# ---------------------------------------------------------------------------
# Error recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_failure_does_not_stop_execution():
    """When a batch fails, subsequent batches still execute."""
    src = _make_folder(1, title="默认收藏夹", attr=0)
    target = _make_folder(2, title="目标", attr=1)
    items = [_make_item(i) for i in range(1, 41)]

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],
        moves=[
            Operation(
                action="move",
                source=src,
                target="目标",
                resources=items,
            )
        ],
        deletions=[],
        summary="移动 40 个视频",
    )
    api = MockFavAPI()
    # Fail the first call to move_items
    api._fail_on[("move_items", None)] = RuntimeError("API 限流")

    report = await execute_plan(
        plan, api, mid=1000, existing_folders=[src, target]
    )

    # Both batches were attempted
    move_calls = [c for c in api.calls if c[0] == "move_items"]
    assert len(move_calls) == 2

    # First batch failed, second also failed (same key)
    assert report.failed >= 1
    assert len(report.errors) >= 1
    assert any("限流" in e for e in report.errors)


@pytest.mark.asyncio
async def test_batch1_succeeds_batch2_fails_still_reports_both():
    """Batch 1 succeeds, batch 2 fails → report shows 1 success + 1 failure."""
    src = _make_folder(1, title="默认收藏夹", attr=0)
    target = _make_folder(2, title="目标", attr=1)
    items = [_make_item(i) for i in range(1, 41)]

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],
        moves=[
            Operation(
                action="move",
                source=src,
                target="目标",
                resources=items,
            )
        ],
        deletions=[],
        summary="移动 40 个视频",
    )
    api = MockFavAPI()

    # Allow first call, fail second
    call_count = [0]

    original_move = api.move_items

    async def selective_move(src_media_id, tar_media_id, resources, mid):
        call_count[0] += 1
        api.calls.append(
            (
                "move_items",
                {
                    "src_media_id": src_media_id,
                    "tar_media_id": tar_media_id,
                    "resources": resources,
                    "mid": mid,
                },
            )
        )
        if call_count[0] == 2:
            raise RuntimeError("第二批次失败")
        return {"code": 0}

    api.move_items = selective_move

    report = await execute_plan(
        plan, api, mid=1000, existing_folders=[src, target]
    )

    assert report.succeeded == 1
    assert report.failed == 1
    assert report.total_attempted == 2
    assert len(report.errors) == 1
    assert "第二批次失败" in report.errors[0]


@pytest.mark.asyncio
async def test_create_folder_failure_does_not_block_moves():
    """A folder creation failure is logged, subsequent moves still run."""
    src = _make_folder(1, title="默认收藏夹", attr=0)
    existing = _make_folder(2, title="音乐", attr=1)
    item = _make_item(1, title="音乐视频")

    plan = OrganizePlan(
        total_operations=2,
        folders_to_create=["科技", "动漫"],
        moves=[
            Operation(
                action="move",
                source=src,
                target="音乐",
                resources=[item],
            )
        ],
        deletions=[],
        summary="创建 2 个文件夹, 移动 1 个视频",
    )
    api = MockFavAPI()
    api._fail_on[("create_folder", "科技")] = RuntimeError("创建失败")

    report = await execute_plan(
        plan, api, mid=1000, existing_folders=[src, existing]
    )

    # "科技" creation failed, "动漫" succeeded, move also ran
    assert report.failed >= 1
    create_calls = [c for c in api.calls if c[0] == "create_folder"]
    move_calls = [c for c in api.calls if c[0] == "move_items"]
    assert len(create_calls) == 2  # both attempted
    assert len(move_calls) == 1  # move to 音乐 still happened


# ---------------------------------------------------------------------------
# Progress output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_output_format(capsys):
    """Progress lines follow [N/M] format with Chinese descriptions."""
    src = _make_folder(1, title="默认收藏夹", attr=0)
    target = _make_folder(2, title="音乐", attr=1)
    items = [_make_item(i) for i in range(1, 3)]

    plan = OrganizePlan(
        total_operations=2,
        folders_to_create=["科技"],
        moves=[
            Operation(
                action="move",
                source=src,
                target="音乐",
                resources=items,
            )
        ],
        deletions=[],
        summary="创建 1 个文件夹, 移动 2 个视频",
    )
    api = MockFavAPI()

    await execute_plan(plan, api, mid=1000, existing_folders=[src, target])

    captured = capsys.readouterr().out
    lines = [l for l in captured.splitlines() if l.strip()]

    assert len(lines) >= 1
    for line in lines:
        # Must start with [N/M]
        assert "[" in line and "/" in line and "]" in line
        assert "创建文件夹" in line or "移动" in line


@pytest.mark.asyncio
async def test_progress_shows_batch_info(capsys):
    """When batching, progress output includes batch indices."""
    src = _make_folder(1, title="默认收藏夹", attr=0)
    target = _make_folder(2, title="音乐", attr=1)
    items = [_make_item(i) for i in range(1, 35)]  # 34 items → 2 batches

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],
        moves=[
            Operation(
                action="move",
                source=src,
                target="音乐",
                resources=items,
            )
        ],
        deletions=[],
        summary="移动 34 个视频",
    )
    api = MockFavAPI()

    await execute_plan(plan, api, mid=1000, existing_folders=[src, target])

    captured = capsys.readouterr().out
    assert "批次 1" in captured
    assert "批次 2" in captured


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_target_folder_not_found():
    """When target title is not in title_to_id, the move is skipped with error."""
    src = _make_folder(1, title="默认收藏夹", attr=0)
    item = _make_item(1)

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],  # not creating "不存在"
        moves=[
            Operation(
                action="move",
                source=src,
                target="不存在",
                resources=[item],
            )
        ],
        deletions=[],
        summary="移动 1 个视频到不存在的文件夹",
    )
    api = MockFavAPI()

    report = await execute_plan(
        plan, api, mid=1000, existing_folders=[src]
    )

    assert report.failed >= 1
    assert any("不存在" in e for e in report.errors)
    # No actual move API call should have been made
    move_calls = [c for c in api.calls if c[0] == "move_items"]
    assert len(move_calls) == 0


@pytest.mark.asyncio
async def test_operation_with_zero_resources_is_skipped():
    """An operation with an empty resources list produces no API calls."""
    src = _make_folder(1, title="默认收藏夹", attr=0)

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],
        moves=[
            Operation(
                action="move",
                source=src,
                target="音乐",
                resources=[],  # empty
            )
        ],
        deletions=[],
        summary="无资源移动",
    )
    api = MockFavAPI()

    report = await execute_plan(
        plan, api, mid=1000, existing_folders=[src]
    )

    move_calls = [c for c in api.calls if c[0] == "move_items"]
    assert len(move_calls) == 0
    assert report.total_attempted == 0


@pytest.mark.asyncio
async def test_operation_with_none_source_is_skipped():
    """An operation with source=None is silently skipped."""
    item = _make_item(1)

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],
        moves=[
            Operation(
                action="move",
                source=None,
                target="音乐",
                resources=[item],
            )
        ],
        deletions=[],
        summary="无源文件夹",
    )
    api = MockFavAPI()

    report = await execute_plan(
        plan, api, mid=1000, existing_folders=[]
    )

    assert report.total_attempted == 0


@pytest.mark.asyncio
async def test_clean_invalid_only_called_for_folders_with_deletions():
    """clean_invalid is only called on folders that had batch_delete ops."""
    folder_a = _make_folder(1, title="A", attr=1)
    folder_b = _make_folder(2, title="B", attr=1)
    items = [_make_item(1, attr=9)]

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],
        moves=[],
        deletions=[
            Operation(
                action="batch_delete",
                source=folder_a,
                resources=items,
            )
        ],
        summary="删除 1 个失效内容",
    )
    api = MockFavAPI()

    await execute_plan(
        plan, api, mid=1000, existing_folders=[folder_a, folder_b]
    )

    clean_calls = [c for c in api.calls if c[0] == "clean_invalid"]
    assert len(clean_calls) == 1
    assert clean_calls[0][1]["media_id"] == folder_a.id


@pytest.mark.asyncio
async def test_report_details_include_step_info():
    """ExecutionReport.details contains per-step ExecutionDetail entries."""
    src = _make_folder(1, title="默认收藏夹", attr=0)
    target = _make_folder(2, title="音乐", attr=1)
    item = _make_item(1)

    plan = OrganizePlan(
        total_operations=1,
        folders_to_create=[],
        moves=[
            Operation(
                action="move",
                source=src,
                target="音乐",
                resources=[item],
            )
        ],
        deletions=[],
        summary="移动 1 个视频",
    )
    api = MockFavAPI()

    report = await execute_plan(
        plan, api, mid=1000, existing_folders=[src, target]
    )

    assert len(report.details) == 1
    detail = report.details[0]
    assert detail.status == "success"
    assert detail.count == 1
    assert detail.message == "ok"
    assert "音乐" in detail.step
