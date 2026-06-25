#!/usr/bin/env python3
"""CLI entry point for fav-organizer — Bilibili favorites organizer.

Usage:

    # Preview only (dry-run)
    python src/main.py --dry-run

    # Full pipeline: preview → confirm → execute
    python src/main.py

The entry point is also registered in ``pyproject.toml`` as ``fav-organizer``:

    uv run fav-organizer --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict

from src.auth import check_expired, get_credentials
from src.classifier_llm import classify_by_llm
from src.classifier_upper import classify_by_upper
from src.classifier_zone import classify_by_zone
from src.confirm import confirm_execution
from src.dedup import detect_duplicates
from src.fav_api import FavAPI
from src.http_client import BiliHTTPClient
from src.planner import build_plan
from src.scanner import scan_invalid
from src.signing import sign_params
from src.models import Folder, Operation, OrganizePlan
from src.video_api import VideoInfoAPI

# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def _summary_bar(plan: OrganizePlan) -> str:
    """Return a compact summary line for the plan."""
    parts = []
    if plan.folders_to_create:
        parts.append(f"📁 新建 {len(plan.folders_to_create)} 个文件夹")
    if plan.moves:
        total_moved = sum(len(op.resources) for op in plan.moves)
        parts.append(f"↗️  移动 {total_moved} 个内容")
    if plan.deletions:
        total_del = sum(len(op.resources) for op in plan.deletions)
        parts.append(f"🗑️  删除 {total_del} 个内容")
    return "  ".join(parts) if parts else "✅ 无需任何操作"


def _invalid_table(plan: OrganizePlan) -> str:
    """Return a Markdown table of invalid/deleted items."""
    if not plan.deletions:
        return ""
    lines = [
        "### 🗑️ 失效/重复内容",
        "",
        "| # | 标题 | 来源文件夹 | 操作 |",
        "|---|------|-----------|------|",
    ]
    idx = 0
    for op in plan.deletions:
        if op.action != "batch_delete":
            continue
        src = op.source.title if op.source else "—"
        for res in op.resources:
            idx += 1
            lines.append(f"| {idx} | {res.title or '—'} | {src} | 删除 |")
    return "\n".join(lines)


def _move_table(plan: OrganizePlan) -> str:
    """Return a Markdown section of classification moves grouped by target."""
    if not plan.moves:
        return ""
    lines = [
        "### ↗️ 分类移动计划",
        "",
    ]
    # Group by target folder
    groups: dict[str, list[Operation]] = defaultdict(list)
    for op in plan.moves:
        target = str(op.target) if op.target else "未分类"
        groups[target].append(op)

    for target in sorted(groups):
        ops = groups[target]
        total = sum(len(op.resources) for op in ops)
        sources = ", ".join(sorted({op.source.title for op in ops if op.source}))
        lines.append(f"**→ {target}** ({total} 个，来源: {sources})")
        lines.append("")
        for op in ops:
            for res in op.resources:
                lines.append(f"  - {res.title or '—'} ({res.bvid})")
        lines.append("")

    return "\n".join(lines)


def _new_folder_section(plan: OrganizePlan) -> str:
    """Return a Markdown list of folders to be created."""
    if not plan.folders_to_create:
        return ""
    lines = [
        "### 📁 新文件夹",
        "",
    ]
    for title in plan.folders_to_create:
        lines.append(f"- **{title}**")
    lines.append("")
    return "\n".join(lines)


def generate_preview(plan: OrganizePlan) -> str:
    """Generate a structured Markdown preview of the organize plan.

    The preview includes:
    - Summary statistics
    - Invalid/duplicate content table
    - Classification move plan grouped by target folder
    - New folder list
    - Empty folder suggestions
    """
    blocks: list[str] = [
        "# 🗂️ 收藏夹整理计划",
        "",
        _summary_bar(plan),
        "",
        "---",
        "",
        _new_folder_section(plan),
        _invalid_table(plan),
        _move_table(plan),
    ]

    blocks.append("---")
    blocks.append("")
    blocks.append("**是否执行以上操作？(y/N)**")

    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


async def execute_plan(plan: OrganizePlan, fav_api: FavAPI) -> None:
    """Execute the organize plan step by step.

    Order:
    1. Create new folders
    2. Move items
    3. Delete invalid/duplicate items

    Operations are batched (≤30 resources per API call).
    Failures in one batch are logged but do not stop subsequent steps.
    """
    total_steps = len(plan.folders_to_create) + len(plan.moves) + len(plan.deletions)
    step = 0

    # Step 1: Create folders
    for title in plan.folders_to_create:
        step += 1
        try:
            await fav_api.create_folder(title=title)
            print(f"📁 [{step}/{total_steps}] 创建文件夹 '{title}'")
        except Exception as exc:
            print(f"⚠️  创建文件夹 '{title}' 失败: {exc}")

    # Step 2: Move items (copy + delete from source)
    # B站 uses copy + batch_delete to emulate move
    for op in plan.moves:
        step += 1
        if not op.resources or not op.source:
            continue
        resource_ids = [f"{r.id}:{r.type}" for r in op.resources]
        # Move in batches of 30
        batch_size = 30
        for i in range(0, len(resource_ids), batch_size):
            batch = resource_ids[i : i + batch_size]
            target_id = op.target.id if isinstance(op.target, Folder) else 0
            try:
                await fav_api.move_items(
                    src_media_id=op.source.id,
                    tar_media_id=target_id,
                    resources=batch,
                    mid=op.source.mid,
                )
                print(
                    f"↗️  [{step}/{total_steps}] 移动 {len(batch)} 个内容"
                    f" 到 '{op.target}'"
                )
            except Exception as exc:
                print(f"⚠️  移动失败: {exc}")

    # Step 3: Delete invalid/duplicate items
    for op in plan.deletions:
        step += 1
        if not op.resources or not op.source:
            continue
        resource_ids = [f"{r.id}:{r.type}" for r in op.resources]
        batch_size = 30
        for i in range(0, len(resource_ids), batch_size):
            batch = resource_ids[i : i + batch_size]
            try:
                await fav_api.batch_delete(
                    media_id=op.source.id,
                    resources=batch,
                )
                print(
                    f"🗑️  [{step}/{total_steps}] 从 '{op.source.title}'"
                    f" 删除 {len(batch)} 个内容"
                )
            except Exception as exc:
                print(f"⚠️  删除失败: {exc}")


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


async def run_pipeline(*, dry_run: bool = False) -> int:
    """Run the full organize pipeline.

    Pipeline steps:
        1. Auth → :func:`get_credentials`
        2. Expiry check → :func:`check_expired`
        3. Build :class:`FavAPI` with :class:`BiliHTTPClient`
        4. Scanner → :func:`scan_invalid`
        5. Dedup → :func:`detect_duplicates`
        6. Classifiers → zone, upper, LLM
        7. Planner → :func:`build_plan`
        8. Preview → :func:`generate_preview`
        9. Confirm → :func:`confirm_execution` (skipped in dry-run)
        10. Execute → :func:`execute_plan` (skipped in dry-run)

    Parameters
    ----------
    dry_run:
        When ``True``, only preview is shown; nothing is executed.

    Returns
    -------
    int
        Exit code (0 = success, 1 = error).
    """
    # Step 1-2: Auth
    creds = get_credentials()
    if check_expired(creds):
        print("❌ 登录已过期，请重新获取 SESSDATA")
        return 1

    # Step 3: Build API clients
    http = BiliHTTPClient(sessdata=creds.sessdata, bili_jct=creds.bili_jct)
    try:
        fav_api = FavAPI(
            http_client=http,
            bili_jct=creds.bili_jct,
            signing=sign_params,
        )
        video_api = VideoInfoAPI(http)

        # Get folders
        folders = await fav_api.list_all_folders(up_mid=creds.mid)
        if not folders:
            print("没有找到收藏夹")
            return 0

        # Step 4: Scanner
        print(f"正在扫描失效内容 ({len(folders)} 个收藏夹)...")
        invalid_items = await scan_invalid(folders, fav_api)
        print(f"  发现 {len(invalid_items)} 个失效内容")

        # Step 5: Dedup
        print(f"正在检测重复内容 ({len(folders)} 个收藏夹)...")
        duplicates = await detect_duplicates(folders, fav_api)
        print(f"  发现 {len(duplicates)} 组重复内容")

        # Step 6: Classifiers
        # Collect all valid items with their source folder mapping
        all_items: list = []
        item_folder_map: dict[int, Folder] = {}
        for i, folder in enumerate(folders, 1):
            if folder.title == "稍后再看":
                continue
            contents = await fav_api.get_all_contents(media_id=folder.id)
            valid_count = 0
            for item in contents:
                if item.is_valid:
                    all_items.append(item)
                    item_folder_map[item.id] = folder
                    valid_count += 1
            print(f"  [{i}/{len(folders)}] 📂 {folder.title}: {len(contents)} 个内容 ({valid_count} 有效)")

        print(f"正在分类 {len(all_items)} 个内容...")
        print("  分区归类...")
        zone_results = await classify_by_zone(all_items, video_api)
        print("  UP主归类...")
        upper_results = classify_by_upper(all_items, folders)
        print("  LLM 智能分类...")
        llm_results = classify_by_llm(all_items, folders)

        # Step 7: Planner
        plan = build_plan(
            zone_results=zone_results,
            upper_results=upper_results,
            llm_results=llm_results,
            existing_folders=folders,
            invalid_items=invalid_items,
            duplicate_groups=duplicates,
            item_folder_map=item_folder_map,
        )

        # Step 8: Preview
        preview_text = generate_preview(plan)

        if dry_run:
            print(preview_text)
            return 0

        # Step 9: Confirm
        if not confirm_execution(preview_text):
            print("已取消")
            return 0

        # Step 10: Execute
        print("正在执行整理...")
        await execute_plan(plan, fav_api)

        print("✅ 整理完成")
        return 0

    finally:
        await http.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli() -> None:
    """CLI entry point registered in ``pyproject.toml`` as fav-organizer."""
    parser = argparse.ArgumentParser(
        description="B站收藏夹整理工具 — 清理失效、去重、智能分类",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览整理计划，不执行任何操作",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run_pipeline(dry_run=args.dry_run)))


if __name__ == "__main__":
    cli()
