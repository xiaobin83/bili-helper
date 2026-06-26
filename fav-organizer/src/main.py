#!/usr/bin/env python3
"""CLI entry point for fav-organizer — Bilibili favorites organizer.

Three independent commands::

    # Phase 1: scan & prepare (requires auth)
    uv run fav-organizer classify --folder "默认收藏夹"
    uv run fav-organizer classify --all [--clear-cache]

    # Phase 2: plan (no auth — reads local state + classifications)
    uv run fav-organizer plan
    uv run fav-organizer plan --classification result.json

    # Phase 3: execute (requires auth)
    uv run fav-organizer execute
    uv run fav-organizer execute --plan plan.json
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

from bili_core.auth import check_expired, get_credentials
from bili_core.http_client import BiliHTTPClient
from bili_core.signing import sign_params
from src.classifier_llm import classify_items
from src.confirm import confirm_execution
from src.dedup import detect_duplicates
from src.fav_api import FavAPI
from src.models import (
    BatchMeta,
    ClassificationEntry,
    ClassificationResult,
    ClassificationResultList,
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
from src.planner import build_plan
from src.scanner import scan_invalid
from src.state_manager import StateManager
from src.video_api import VideoInfoAPI

# Path to the credential file (same location as before)
_AUTH_FILE = Path(__file__).resolve().parent.parent / ".auth.json"

# ======================================================================
# Preview / Markdown generation (used by "plan" command)
# ======================================================================


def _summary_bar(plan: OrganizePlan) -> str:
    """Return a compact summary line for the plan."""
    parts = []
    if plan.folders_to_create:
        parts.append(f"📁 新建 {len(plan.folders_to_create)} 个文件夹")
    if plan.moves:
        moves = [op for op in plan.moves if op.action == "move"]
        copies = [op for op in plan.moves if op.action == "copy"]
        total_moved = sum(len(op.resources) for op in moves)
        total_copied = sum(len(op.resources) for op in copies)
        if total_moved:
            parts.append(f"↗️  移动 {total_moved} 个内容")
        if total_copied:
            parts.append(f"📋 复制 {total_copied} 个内容")
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
    """Return a Markdown section of classification moves/copies grouped by target."""
    if not plan.moves:
        return ""
    lines = [
        "### ↗️ 分类整理计划",
        "",
    ]
    groups: dict[str, list[Operation]] = defaultdict(list)
    for op in plan.moves:
        target = str(op.target) if op.target else "未分类"
        groups[target].append(op)

    for target in sorted(groups):
        ops = groups[target]
        total = sum(len(op.resources) for op in ops)
        sources = ", ".join(sorted({op.source.title for op in ops if op.source}))
        action_label = "移动" if any(op.action == "move" for op in ops) else "复制"
        action_icon = "↗️" if action_label == "移动" else "📋"
        lines.append(f"**{action_icon} → {target}** ({action_label} {total} 个，来源: {sources})")
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
    """Generate a structured Markdown preview of the organize plan."""
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
    blocks.append("**审核后可使用 `fav-organizer execute` 执行**")

    return "\n".join(blocks)


# ======================================================================
# Pipeline cleanup
# ======================================================================


def _cleanup_pipeline_files(mgr: StateManager, plan_path: str | None) -> None:
    """Advance batch or delete intermediate pipeline files after successful execution."""
    if mgr.has_batch_meta():
        meta = mgr.load_batch_meta()
        meta.current_offset += meta.batch_size
        if meta.is_last_batch:
            mgr.delete_file(mgr.FILE_BATCH_META)
            mgr.delete_file(mgr.FILE_CLASSIFICATION)
            if plan_path is None:
                mgr.delete_file(mgr.FILE_PLAN)
            print("✅ 所有批次已完成")
        else:
            mgr.save_batch_meta(meta)
            mgr.delete_file(mgr.FILE_CLASSIFICATION)
            if plan_path is None:
                mgr.delete_file(mgr.FILE_PLAN)
            print(f"📦 第 {meta.current_batch}/{meta.total_batches} 批完成，运行 classify 继续下一批")
    else:
        mgr.delete_file(mgr.FILE_CLASSIFICATION)
        if plan_path is None:
            mgr.delete_file(mgr.FILE_PLAN)


def _cmd_classify_continue(mgr: StateManager) -> int:
    """Create the next batch's classification from existing state.json."""
    try:
        state = mgr.load_state()
    except Exception as e:
        print(f"❌ 读取状态文件失败: {e}")
        return 1

    meta = mgr.load_batch_meta()
    video_items = [it for it in state.items_to_classify if it.type == 2]

    if meta.current_offset >= len(video_items):
        mgr.delete_file(mgr.FILE_BATCH_META)
        print("✅ 所有批次已完成")
        return 0

    batch_end = min(meta.current_offset + meta.batch_size, len(video_items))
    batch_items = video_items[meta.current_offset : batch_end]
    batch_ids = {it.id for it in batch_items}

    classification = ClassificationResultList(
        classifications=[
            ClassificationEntry(item_id=it.id, category="")
            for it in batch_items
        ],
        existing_folder_titles=state.existing_folder_titles,
    )
    mgr.save_classification(classification)

    batch_num = meta.current_batch
    total = meta.total_batches
    print(f"\n📦 第 {batch_num}/{total} 批 ({len(batch_items)} 个视频)")
    print(f"📝 分类模板已更新: {mgr.state_dir / 'classification_result.json'}")
    print(f"请编辑分类后运行: uv run fav-organizer plan")
    return 0


# ======================================================================
# Phase 1: classify — scan, dedup, prepare for LLM classification
# ======================================================================


async def _collect_until_count(
    fav_api: FavAPI,
    folder: Folder,
    all_items: list[FavoritedItem],
    item_folder_map: dict[int, int],
    invalid_entries: list[InvalidItemEntry],
    remaining: int,
) -> tuple[int, list[FavoritedItem]]:
    """Paginate through folder contents until *remaining* video items are collected.

    Deducts 1 from *remaining* for each valid video (type==2) found.
    Collects invalid items (attr != 0) into *invalid_entries* inline.
    Stops early once remaining ≤ 0. Returns (remaining, collected_items).
    """
    page = 1
    collected: list[FavoritedItem] = []
    while True:
        page_items, has_more = await fav_api.get_folder_contents(
            media_id=folder.id, page=page,
        )
        for item in page_items:
            collected.append(item)
            if item.is_valid:
                all_items.append(item)
                item_folder_map[item.id] = folder.id
                if item.type == 2:
                    remaining -= 1
                if remaining <= 0:
                    break
            else:
                invalid_entries.append(InvalidItemEntry(
                    item=item,
                    folder_id=folder.id,
                    folder_title=folder.title,
                ))
        if not has_more or remaining <= 0:
            break
        page += 1
    return remaining, collected


async def cmd_classify(
    *,
    scope_kind: str,
    scope_value: str,
    clear_cache: bool = False,
    count: int | None = None,
    dedup: bool = False,
) -> int:
    """Scan favorites, detect invalid/duplicate, and prepare state for LLM classification.

    1. Auth
    2. List folders (filtered by scope)
    3. Scan invalid items
    4. Detect duplicates
    5. Collect valid items with folder mapping
    6. Fetch video info (disk-cached, 30-day TTL)
    7. Output state.json + summary
    """
    mgr = StateManager()

    if clear_cache:
        mgr.clear_video_cache()

    # Continuation: pick next batch from existing state
    if mgr.has_batch_meta():
        return _cmd_classify_continue(mgr)

    # Auth
    creds = get_credentials(env_prefix="FAV_", auth_file=_AUTH_FILE)
    if check_expired(creds):
        print("❌ 登录已过期，请重新获取 SESSDATA")
        return 1

    http = BiliHTTPClient(sessdata=creds.sessdata, bili_jct=creds.bili_jct)
    try:
        fav_api = FavAPI(
            http_client=http,
            bili_jct=creds.bili_jct,
            signing=sign_params,
        )
        video_api = VideoInfoAPI(http, state_manager=mgr)

        # Get folders
        all_folders = await fav_api.list_all_folders(up_mid=creds.mid)
        if not all_folders:
            print("没有找到收藏夹")
            return 0

        # Filter by scope
        if scope_kind == "folder":
            folders = [
                f for f in all_folders
                if f.title == scope_value and f.title != "稍后再看"
            ]
            if not folders:
                print(f"❌ 未找到收藏夹 '{scope_value}'")
                available = [f.title for f in all_folders if f.title != "稍后再看"]
                if available:
                    print(f"可用的收藏夹: {', '.join(available)}")
                return 1
        else:
            # --all
            folders = [f for f in all_folders if f.title != "稍后再看"]

        print(f"📂 整理范围: {scope_value} ({len(folders)} 个收藏夹)")

        # Scan invalid
        invalid_entries: list[InvalidItemEntry] = []

        if count is not None:
            pass  # invalid items collected inline below
        else:
            print(f"正在扫描失效内容 ({len(folders)} 个收藏夹)...")
            invalid_pairs = await scan_invalid(folders, fav_api)
            print(f"  发现 {len(invalid_pairs)} 个失效内容")
            for item, folder in invalid_pairs:
                invalid_entries.append(InvalidItemEntry(
                    item=item,
                    folder_id=folder.id,
                    folder_title=folder.title,
                ))

        # Dedup
        if dedup:
            print(f"正在检测重复内容 ({len(folders)} 个收藏夹)...")
            duplicates = await detect_duplicates(folders, fav_api)
            print(f"  发现 {len(duplicates)} 组重复内容")
        else:
            duplicates = []

        # Collect all valid items with their source folder mapping
        all_items: list[FavoritedItem] = []
        item_folder_map: dict[int, int] = {}

        if count is not None:
            remaining = count
            for i, folder in enumerate(folders, 1):
                if folder.title == "稍后再看":
                    continue
                remaining, more_items = await _collect_until_count(
                    fav_api, folder, all_items, item_folder_map,
                    invalid_entries, remaining,
                )
                folder_items = len(more_items)
                folder_valid = sum(1 for it in more_items if it.is_valid)
                print(f"  [{i}/{len(folders)}] 📂 {folder.title}: {folder_items} 个内容 ({folder_valid} 有效)")
                if remaining <= 0:
                    remaining_folders = len(folders) - i
                    if remaining_folders:
                        print(f"  ⏭  已收集足够视频，跳过剩余 {remaining_folders} 个收藏夹")
                    break
        else:
            for i, folder in enumerate(folders, 1):
                if folder.title == "稍后再看":
                    continue
                contents = await fav_api.get_all_contents(media_id=folder.id)
                valid_count = 0
                for item in contents:
                    if item.is_valid:
                        all_items.append(item)
                        item_folder_map[item.id] = folder.id
                        valid_count += 1
                print(f"  [{i}/{len(folders)}] 📂 {folder.title}: {len(contents)} 个内容 ({valid_count} 有效)")

        print(f"共 {len(all_items)} 个有效内容待分类")
        if count is not None:
            print(f"  发现 {len(invalid_entries)} 个失效内容（来自已扫描页面）")

        # Fetch video info to enrich items with descriptions
        print(f"正在获取视频信息 ({len(all_items)} 个)...")
        video_items = [it for it in all_items if it.type == 2 and it.bvid]
        cached_count = 0
        fetched_count = 0
        for idx, item in enumerate(video_items):
            if video_api.is_cached(item.bvid):
                cached_count += 1
            else:
                fetched_count += 1

            info = await video_api.get_video_info(item.bvid)
            if info:
                intro = info.get("desc", "") or ""
                # Truncate long descriptions
                if len(intro) > 200:
                    intro = intro[:200] + "…"
                item.intro = intro
                item.zone_tname = info.get("tname", "") or ""

            if (idx + 1) % 20 == 0 or (idx + 1) == len(video_items):
                print(f"  [{idx + 1}/{len(video_items)}] 视频信息 (缓存: {cached_count}, 新获取: {fetched_count})")

        print(f"  ✅ 视频信息: {cached_count} 来自缓存, {fetched_count} 新获取")

        # Build and save state
        existing_titles = [f.title for f in all_folders if f.title != "稍后再看"]
        state = StateData(
            scope_kind=scope_kind,
            scope_value=scope_value,
            folders=[f for f in all_folders if f.title != "稍后再看"],
            invalid_items=invalid_entries,
            duplicate_groups=duplicates,
            item_folder_map=item_folder_map,
            items_to_classify=all_items,
            existing_folder_titles=existing_titles,
        )
        state_path = mgr.save_state(state)

        video_items = [it for it in all_items if it.type == 2]
        video_count = len(video_items)

        if video_count > 50:
            meta = BatchMeta(total_videos=video_count)
            mgr.save_batch_meta(meta)

            batch_items = video_items[:50]
            partial = ClassificationResultList(
                classifications=[
                    ClassificationEntry(item_id=it.id, category="")
                    for it in batch_items
                ],
                existing_folder_titles=existing_titles,
            )
            class_path = mgr.save_classification(partial)

            print(f"\n✅ 状态已保存: {state_path}")
            print(f"📦 共 {video_count} 个视频，分 {meta.total_batches} 批处理")
            print(f"📝 当前第 1/{meta.total_batches} 批: {class_path}")
            print(f"请编辑分类后运行: uv run fav-organizer plan")
            print(f"完成后再次运行 classify 进入下一批")
        else:
            classification = ClassificationResultList(
                classifications=[
                    ClassificationEntry(item_id=it.id, category="")
                    for it in all_items
                ],
                existing_folder_titles=existing_titles,
            )
            class_path = mgr.save_classification(classification)

            print(f"\n✅ 状态已保存: {state_path}")
            print(f"📝 分类模板: {class_path}")
            print(f"\n共 {len(all_items)} 个内容待 LLM 分类。")
            print(f"请编辑 {class_path}，为每个 item 填入 2-6 个中文字的分类名称。")
            print(f"已有文件夹: {', '.join(existing_titles) if existing_titles else '无'}")
            print(f"\n完成后运行: uv run fav-organizer plan")

        return 0

    finally:
        await http.close()


# ======================================================================
# Phase 2: plan — read state + classifications, build plan
# ======================================================================


def cmd_plan(*, classification_path: str | None = None) -> int:
    """Build an organize plan from state.json and classification results.

    1. Load state.json (from classify phase)
    2. Load classification_result.json (agent-filled)
    3. Merge classifications into the state
    4. Build OrganizePlan
    5. Save plan.json
    6. Output markdown preview for user review
    """
    mgr = StateManager()

    # Load state
    try:
        state = mgr.load_state()
    except FileNotFoundError:
        print("❌ 未找到状态文件。请先运行: uv run fav-organizer classify --folder 'xxx'")
        return 1
    except Exception as e:
        print(f"❌ 读取状态文件失败: {e}")
        return 1

    # Load classification
    if classification_path:
        class_path = Path(classification_path)
        if not class_path.exists():
            print(f"❌ 分类文件不存在: {classification_path}")
            return 1
        try:
            raw = class_path.read_text(encoding="utf-8")
            import json
            classification = ClassificationResultList.model_validate(json.loads(raw))
        except Exception as e:
            print(f"❌ 读取分类文件失败: {e}")
            return 1
    else:
        try:
            classification = mgr.load_classification()
        except FileNotFoundError:
            print("❌ 未找到分类结果文件。请先完成 LLM 分类。")
            print(f"   编辑文件: {mgr.state_dir / 'classification_result.json'}")
            return 1
        except Exception as e:
            print(f"❌ 读取分类结果失败: {e}")
            return 1

    classified_ids = {c.item_id for c in classification.classifications}
    state_item_ids = {it.id for it in state.items_to_classify}
    unclassified = state_item_ids - classified_ids

    if mgr.has_batch_meta():
        if unclassified:
            meta = mgr.load_batch_meta()
            print(f"📦 第 {meta.current_batch}/{meta.total_batches} 批 ({len(classified_ids)} 个已分类，{len(unclassified)} 个将在后续批次处理)")
    elif unclassified:
        print(f"⚠️  {len(unclassified)} 个内容尚未分类，将标记为 '未分类'")
        for item_id in unclassified:
            classification.classifications.append(
                ClassificationEntry(item_id=item_id, category="未分类")
            )

    # Rebuild internal state
    # Folder lookup
    folder_by_id: dict[int, Folder] = {f.id: f for f in state.folders}

    # Item lookup
    item_by_id: dict[int, FavoritedItem] = {it.id: it for it in state.items_to_classify}

    # Rebuild item_folder_map (Folder objects)
    item_folder_map: dict[int, Folder] = {}
    for item_id, folder_id in state.item_folder_map.items():
        folder = folder_by_id.get(folder_id)
        if folder and item_id in item_by_id:
            item_folder_map[item_id] = folder

    # Rebuild invalid_items as list[(FavoritedItem, Folder)]
    invalid_pairs: list[tuple[FavoritedItem, Folder]] = []
    for entry in state.invalid_items:
        folder = folder_by_id.get(entry.folder_id)
        if folder:
            invalid_pairs.append((entry.item, folder))

    # Convert ClassificationEntry → ClassificationResult
    classification_results: list[ClassificationResult] = []
    for entry in classification.classifications:
        item = item_by_id.get(entry.item_id)
        if item is None:
            print(f"⚠️  未找到 item id={entry.item_id}，跳过")
            continue

        category = entry.category if entry.category and entry.category.strip() else "未分类"
        target_exists = category in state.existing_folder_titles
        classification_results.append(
            ClassificationResult(
                item=item,
                category=category,
                target_folder_title=category,
                target_folder_exists=target_exists,
            )
        )

    print(f"📊 已合并 {len(classification_results)} 个分类结果")

    # Build plan
    plan = build_plan(
        classifications=classification_results,
        existing_folders=state.folders,
        invalid_items=invalid_pairs,
        duplicate_groups=state.duplicate_groups,
        item_folder_map=item_folder_map,
    )

    print(f"📋 {plan.summary}")
    if plan.empty_folders:
        print(f"📭 建议删除的空文件夹: {', '.join(plan.empty_folders)}")

    # Convert OrganizePlan → PlanFile for serialization
    plan_file = _plan_to_file(plan)
    plan_path = mgr.save_plan(plan_file)

    # Generate markdown preview
    preview = generate_preview(plan)
    print(f"\n{preview}")

    print(f"\n💾 计划已保存: {plan_path}")
    print(f"如需调整分类，编辑 {mgr.state_dir / 'classification_result.json'} 后重新运行 plan")
    print(f"确认无误后运行: uv run fav-organizer execute")

    return 0


def _plan_to_file(plan: OrganizePlan) -> PlanFile:
    """Convert an in-memory OrganizePlan to a serializable PlanFile."""
    moves = [
        PlanMoveEntry(
            action=op.action if op.action in ("move", "copy") else "move",
            source_folder_id=op.source.id if op.source else 0,
            source_folder_title=op.source.title if op.source else "",
            target_title=str(op.target) if op.target else "",
            resources=[
                PlanResourceRef(id=r.id, type=r.type, bvid=r.bvid, title=r.title)
                for r in op.resources
            ],
        )
        for op in plan.moves
    ]

    deletions = [
        PlanDeleteEntry(
            source_folder_id=op.source.id if op.source else 0,
            source_folder_title=op.source.title if op.source else "",
            reason="invalid",  # simplified — executor handles both cases
            resources=[
                PlanResourceRef(id=r.id, type=r.type, bvid=r.bvid, title=r.title)
                for r in op.resources
            ],
        )
        for op in plan.deletions
    ]

    return PlanFile(
        folders_to_create=plan.folders_to_create,
        moves=moves,
        deletions=deletions,
        empty_folders=plan.empty_folders,
        summary=plan.summary,
    )


# ======================================================================
# Phase 3: execute — read plan.json, confirm, run
# ======================================================================


async def cmd_execute(*, plan_path: str | None = None) -> int:
    """Execute a saved organize plan.

    1. Auth
    2. Load plan.json
    3. Show summary & confirm
    4. Execute: create folders → move items → delete items
    """
    mgr = StateManager()

    # Load plan
    if plan_path:
        pp = Path(plan_path)
        if not pp.exists():
            print(f"❌ 计划文件不存在: {plan_path}")
            return 1
        try:
            import json
            plan_file = PlanFile.model_validate(json.loads(pp.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"❌ 读取计划文件失败: {e}")
            return 1
    else:
        try:
            plan_file = mgr.load_plan()
        except FileNotFoundError:
            print("❌ 未找到计划文件。请先运行: uv run fav-organizer plan")
            return 1
        except Exception as e:
            print(f"❌ 读取计划文件失败: {e}")
            return 1

    # Show summary
    print(f"\n📋 整理计划:")
    if plan_file.folders_to_create:
        print(f"  📁 新建 {len(plan_file.folders_to_create)} 个文件夹: {', '.join(plan_file.folders_to_create)}")
    total_move = sum(len(m.resources) for m in plan_file.moves)
    if total_move:
        print(f"  ↗️  移动 {total_move} 个内容")
    total_delete = sum(len(d.resources) for d in plan_file.deletions)
    if total_delete:
        print(f"  🗑️  删除 {total_delete} 个内容")

    if not plan_file.moves and not plan_file.deletions and not plan_file.folders_to_create:
        print("  ✅ 无需任何操作")
        return 0

    # Confirm
    if not confirm_execution(plan_file.summary or "执行以上操作？"):
        print("已取消")
        return 0

    # Auth
    creds = get_credentials(env_prefix="FAV_", auth_file=_AUTH_FILE)
    if check_expired(creds):
        print("❌ 登录已过期，请重新获取 SESSDATA")
        return 1

    http = BiliHTTPClient(sessdata=creds.sessdata, bili_jct=creds.bili_jct)
    try:
        fav_api = FavAPI(
            http_client=http,
            bili_jct=creds.bili_jct,
            signing=sign_params,
        )

        print("正在执行整理...")
        await _execute_plan_file(plan_file, fav_api, mid=creds.mid)
        print("✅ 整理完成")

        # Clean up intermediate files after successful execution
        _cleanup_pipeline_files(mgr, plan_path)
        return 0

    finally:
        await http.close()


async def cmd_delete_empty() -> int:
    """Scan all folders, delete those with zero items.

    Skips the default folder (B站 does not allow deleting it).
    """
    creds = get_credentials(env_prefix="FAV_", auth_file=_AUTH_FILE)
    if check_expired(creds):
        print("❌ 登录已过期，请重新获取 SESSDATA")
        return 1

    http = BiliHTTPClient(sessdata=creds.sessdata, bili_jct=creds.bili_jct)
    try:
        fav_api = FavAPI(
            http_client=http,
            bili_jct=creds.bili_jct,
            signing=sign_params,
        )

        folders = await fav_api.list_all_folders(up_mid=creds.mid)
        empty = [
            f for f in folders
            if f.media_count == 0 and not f.is_default and f.title != "稍后再看"
        ]

        if not empty:
            print("✅ 没有空收藏夹")
            return 0

        print(f"发现 {len(empty)} 个空收藏夹:")
        for f in empty:
            print(f"  📂 {f.title}")

        if not confirm_execution(f"删除以上 {len(empty)} 个空收藏夹？"):
            print("已取消")
            return 0

        media_ids = [f.id for f in empty]
        await fav_api.delete_folders(media_ids)
        print(f"✅ 已删除 {len(empty)} 个空收藏夹")
        return 0

    finally:
        await http.close()


async def _execute_plan_file(
    plan_file: PlanFile,
    fav_api: FavAPI,
    mid: int,
) -> None:
    """Execute a PlanFile against the B站 API.

    Order: collect existing folders → create folders → move items →
    delete items.
    Batches of ≤30 resources. Failures logged but don't stop execution.
    """
    BATCH = 30
    step = 0

    # ── Phase 0: Collect existing folder id↔title mappings ──────────
    title_to_id: dict[str, int] = {}
    try:
        existing = await fav_api.list_all_folders(up_mid=mid)
        for f in existing:
            title_to_id[f.title] = f.id
    except Exception as exc:
        print(f"⚠️  获取已有文件夹失败: {exc}")

    # ── Count total steps ───────────────────────────────────────────
    total_steps = len(plan_file.folders_to_create)
    for m in plan_file.moves:
        total_steps += max((len(m.resources) + BATCH - 1) // BATCH, 0)
    for d in plan_file.deletions:
        total_steps += max((len(d.resources) + BATCH - 1) // BATCH, 0)

    # ── Phase 1: Create folders ─────────────────────────────────────
    for title in plan_file.folders_to_create:
        step += 1
        try:
            resp = await fav_api.create_folder(title=title)
            data = resp.get("data", {})
            if isinstance(data, dict) and "id" in data:
                title_to_id[title] = data["id"]
            print(f"📁 [{step}/{total_steps}] 创建文件夹 '{title}'")
        except Exception as exc:
            print(f"⚠️  创建文件夹 '{title}' 失败: {exc}")

    # ── Phase 2: Move / Copy items ───────────────────────────────────
    move_entries = [m for m in plan_file.moves if m.action == "move"]
    copy_entries = [m for m in plan_file.moves if m.action == "copy"]

    for m in move_entries:
        if not m.resources:
            continue

        tar_id = title_to_id.get(m.target_title, 0)
        if tar_id == 0:
            print(f"⚠️  目标文件夹 '{m.target_title}' 不存在，跳过移动")
            continue

        resources = [f"{r.id}:{r.type}" for r in m.resources]
        batches = [resources[i:i + BATCH] for i in range(0, len(resources), BATCH)]

        for batch_idx, batch in enumerate(batches):
            step += 1
            suffix = f" (批次 {batch_idx + 1}/{len(batches)})" if len(batches) > 1 else ""
            desc = f"移动 {len(batch)} 个资源到 '{m.target_title}'{suffix}"
            print(f"↗️  [{step}/{total_steps}] {desc}")
            try:
                await fav_api.move_items(
                    src_media_id=m.source_folder_id,
                    tar_media_id=tar_id,
                    resources=batch,
                    mid=mid,
                )
            except Exception as exc:
                print(f"⚠️  移动失败: {exc}")

    for c in copy_entries:
        if not c.resources:
            continue

        tar_id = title_to_id.get(c.target_title, 0)
        if tar_id == 0:
            print(f"⚠️  目标文件夹 '{c.target_title}' 不存在，跳过复制")
            continue

        resources = [f"{r.id}:{r.type}" for r in c.resources]
        batches = [resources[i:i + BATCH] for i in range(0, len(resources), BATCH)]

        for batch_idx, batch in enumerate(batches):
            step += 1
            suffix = f" (批次 {batch_idx + 1}/{len(batches)})" if len(batches) > 1 else ""
            desc = f"复制 {len(batch)} 个资源到 '{c.target_title}'{suffix}"
            print(f"📋 [{step}/{total_steps}] {desc}")
            try:
                await fav_api.copy_items(
                    src_media_id=c.source_folder_id,
                    tar_media_id=tar_id,
                    resources=batch,
                    mid=mid,
                )
            except Exception as exc:
                print(f"⚠️  复制失败: {exc}")

    # ── Phase 3: Delete items ───────────────────────────────────────
    for d in plan_file.deletions:
        if not d.resources:
            continue

        resources = [f"{r.id}:{r.type}" for r in d.resources]
        batches = [resources[i:i + BATCH] for i in range(0, len(resources), BATCH)]

        for batch_idx, batch in enumerate(batches):
            step += 1
            suffix = f" (批次 {batch_idx + 1}/{len(batches)})" if len(batches) > 1 else ""
            desc = f"删除 {len(batch)} 个资源 (收藏夹 {d.source_folder_id}){suffix}"
            print(f"🗑️  [{step}/{total_steps}] {desc}")
            try:
                await fav_api.batch_delete(
                    media_id=d.source_folder_id,
                    resources=batch,
                )
            except Exception as exc:
                print(f"⚠️  删除失败: {exc}")


# ======================================================================
# CLI
# ======================================================================


def cli() -> None:
    """CLI entry point registered in pyproject.toml as fav-organizer."""
    parser = argparse.ArgumentParser(
        description="B站收藏夹整理工具 — 清理失效、去重、LLM 智能分类",
    )
    sub = parser.add_subparsers(dest="command", help="可用命令")

    # classify
    p_classify = sub.add_parser("classify", help="扫描收藏夹，准备分类数据")
    scope_group = p_classify.add_mutually_exclusive_group(required=True)
    scope_group.add_argument(
        "--folder", type=str, metavar="NAME",
        help="指定要整理的收藏夹名称",
    )
    scope_group.add_argument(
        "--all", action="store_true",
        help="整理所有收藏夹",
    )
    p_classify.add_argument(
        "--clear-cache", action="store_true",
        help="清除视频信息磁盘缓存后重新获取",
    )
    p_classify.add_argument(
        "--count", type=int, metavar="N",
        help="仅整理前 N 个收藏内容（默认：全部）",
    )
    p_classify.add_argument(
        "--dedup", action="store_true",
        help="启用重复内容检测",
    )

    # plan
    p_plan = sub.add_parser("plan", help="读取分类结果，生成整理计划")
    p_plan.add_argument(
        "--classification", type=str, metavar="PATH",
        help="分类结果 JSON 文件路径（默认: .fav-organizer/classification_result.json）",
    )

    # execute
    p_execute = sub.add_parser("execute", help="执行整理计划")
    p_execute.add_argument(
        "--plan", type=str, metavar="PATH",
        help="计划 JSON 文件路径（默认: .fav-organizer/plan.json）",
    )

    sub.add_parser("delete-empty", help="删除所有空收藏夹（跳过默认收藏夹）")

    args = parser.parse_args()

    if args.command == "classify":
        scope_kind = "all" if args.all else "folder"
        scope_value = "全部" if args.all else args.folder
        sys.exit(asyncio.run(cmd_classify(
            scope_kind=scope_kind,
            scope_value=scope_value,
            clear_cache=args.clear_cache,
            count=args.count,
            dedup=args.dedup,
        )))
    elif args.command == "plan":
        sys.exit(cmd_plan(classification_path=args.classification))
    elif args.command == "execute":
        sys.exit(asyncio.run(cmd_execute(plan_path=args.plan)))
    elif args.command == "delete-empty":
        sys.exit(asyncio.run(cmd_delete_empty()))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    cli()
