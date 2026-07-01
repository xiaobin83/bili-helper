"""Plan executor — applies an OrganizePlan against the B站 favorites API.

Executes operations in strict order: create folders → move items →
delete items → clean invalid.  Batches resources into chunks of 30
(the B站 API limit) and recovers gracefully from individual batch
failures so a single error never aborts the whole plan.

Usage::

    report = await execute_plan(plan, fav_api, mid=12345, existing_folders=folders)
    print(f"{report.succeeded}/{report.total_attempted} succeeded")
"""

from __future__ import annotations

from .models import (
    ExecutionDetail,
    ExecutionReport,
    FavoritedItem,
    Folder,
    OrganizePlan,
    PlanFile,
)

# B站 API limits POST calls to at most 30 resources per request
# (bili-apis/docs/fav/resource.md: batch-del, move, copy).
BATCH_SIZE = 30


def _to_resource_strings(items: list[FavoritedItem]) -> list[str]:
    """Convert FavoritedItem list to API resource strings ``"{id}:{type}"``."""
    return [f"{item.id}:{item.type}" for item in items]


async def execute_plan(
    plan: OrganizePlan,
    fav_api,
    mid: int,
    existing_folders: list[Folder],
) -> ExecutionReport:
    """Execute every operation in *plan* against the B站 API via *fav_api*.

    Execution order (fixed, not configurable):

    1. **Create folders** — one API call per new folder.
    2. **Move items** — batched (≤30 resources per call) by source/target.
    3. **Delete items** — invalid then duplicates, batched (≤30 per call).
    4. **Clean invalid** — ``clean_invalid`` on every folder that had deletions.

    A single batch failure is logged and execution continues with the
    next batch — the plan is **never** aborted mid-way.

    Parameters
    ----------
    plan:
        The organize plan produced by ``build_plan()``.
    fav_api:
        An ``FavAPI`` instance (or compatible mock).
    mid:
        Current user's numeric B站 mid.
    existing_folders:
        Current favorites folders (for title → media_id resolution).

    Returns
    -------
    ExecutionReport
        Aggregate statistics including per-step details and error list.
    """
    details: list[ExecutionDetail] = []
    errors: list[str] = []
    total_attempted = 0
    succeeded = 0
    failed = 0

    # ── catalog: folder title → media_id ──────────────────────────────
    title_to_id: dict[str, int] = {
        f.title: f.id for f in existing_folders
    }

    # ── pre-compute total steps for progress ──────────────────────────
    total_steps = len(plan.folders_to_create)

    for op in plan.moves:
        total_steps += max(
            (len(op.resources) + BATCH_SIZE - 1) // BATCH_SIZE, 0
        )

    for op in plan.deletions:
        total_steps += max(
            (len(op.resources) + BATCH_SIZE - 1) // BATCH_SIZE, 0
        )

    # Track folders that received deletions — clean_invalid at the end.
    deleted_folder_ids: set[int] = set()
    for op in plan.deletions:
        if op.source is not None:
            deleted_folder_ids.add(op.source.id)

    total_steps += len(deleted_folder_ids)
    step = 0

    # ═══════════════════════════════════════════════════════════════════
    # Phase 1 — Create folders
    # ═══════════════════════════════════════════════════════════════════
    for title in plan.folders_to_create:
        step += 1
        total_attempted += 1
        desc = f'创建文件夹: "{title}"'
        print(f"[{step}/{total_steps}] {desc}")
        try:
            resp = await fav_api.create_folder(title)
            data = resp.get("data", {})
            if isinstance(data, dict) and "id" in data:
                title_to_id[title] = data["id"]
            succeeded += 1
            details.append(
                ExecutionDetail(step=desc, status="success", message="ok")
            )
        except Exception as exc:
            failed += 1
            msg = f'创建文件夹 "{title}" 失败: {exc}'
            errors.append(msg)
            details.append(
                ExecutionDetail(
                    step=desc, status="failure", message=str(exc)
                )
            )

    # ═══════════════════════════════════════════════════════════════════
    # Phase 2 — Move items
    # ═══════════════════════════════════════════════════════════════════
    for op in plan.moves:
        if not op.resources or op.source is None:
            continue

        # Resolve target folder title to media_id
        target_title: str | None = None
        if isinstance(op.target, str):
            target_title = op.target
        elif isinstance(op.target, Folder):
            target_title = op.target.title

        if target_title is None:
            continue

        tar_media_id = title_to_id.get(target_title)
        if tar_media_id is None:
            total_attempted += 1
            failed += 1
            msg = f'移动失败: 目标文件夹 "{target_title}" 不存在'
            errors.append(msg)
            details.append(
                ExecutionDetail(step="move", status="failure", message=msg)
            )
            continue

        resources = _to_resource_strings(op.resources)
        batches = _chunk(resources, BATCH_SIZE)

        for batch_idx, batch in enumerate(batches):
            step += 1
            total_attempted += 1
            suffix = (
                f" (批次 {batch_idx + 1}/{len(batches)})"
                if len(batches) > 1
                else ""
            )
            desc = (
                f"移动 {len(batch)} 个资源到 \"{target_title}\"{suffix}"
            )
            print(f"[{step}/{total_steps}] {desc}")
            try:
                await fav_api.move_items(
                    src_media_id=op.source.id,
                    tar_media_id=tar_media_id,
                    resources=batch,
                    mid=mid,
                )
                succeeded += 1
                details.append(
                    ExecutionDetail(
                        step=desc,
                        status="success",
                        count=len(batch),
                        message="ok",
                    )
                )
            except Exception as exc:
                failed += 1
                msg = (
                    f'移动失败 (→ "{target_title}"): {exc}'
                )
                errors.append(msg)
                details.append(
                    ExecutionDetail(
                        step=desc,
                        status="failure",
                        count=len(batch),
                        message=str(exc),
                    )
                )

    # ═══════════════════════════════════════════════════════════════════
    # Phase 3 — Delete items (invalid first, then duplicates)
    # ═══════════════════════════════════════════════════════════════════
    for op in plan.deletions:
        if not op.resources or op.source is None:
            continue
        resources = _to_resource_strings(op.resources)
        batches = _chunk(resources, BATCH_SIZE)

        for batch_idx, batch in enumerate(batches):
            step += 1
            total_attempted += 1
            suffix = (
                f" (批次 {batch_idx + 1}/{len(batches)})"
                if len(batches) > 1
                else ""
            )
            desc = (
                f"删除 {len(batch)} 个资源 "
                f"(收藏夹 {op.source.id}){suffix}"
            )
            print(f"[{step}/{total_steps}] {desc}")
            try:
                await fav_api.batch_delete(
                    media_id=op.source.id, resources=batch
                )
                succeeded += 1
                details.append(
                    ExecutionDetail(
                        step=desc,
                        status="success",
                        count=len(batch),
                        message="ok",
                    )
                )
            except Exception as exc:
                failed += 1
                msg = f"删除失败 (收藏夹 {op.source.id}): {exc}"
                errors.append(msg)
                details.append(
                    ExecutionDetail(
                        step=desc,
                        status="failure",
                        count=len(batch),
                        message=str(exc),
                    )
                )

    # ═══════════════════════════════════════════════════════════════════
    # Phase 4 — Clean invalid items from affected folders
    # ═══════════════════════════════════════════════════════════════════
    for folder_id in sorted(deleted_folder_ids):
        step += 1
        total_attempted += 1
        desc = f"清理失效内容 (收藏夹 {folder_id})"
        print(f"[{step}/{total_steps}] {desc}")
        try:
            await fav_api.clean_invalid(media_id=folder_id)
            succeeded += 1
            details.append(
                ExecutionDetail(step=desc, status="success", message="ok")
            )
        except Exception as exc:
            failed += 1
            msg = f"清理失效内容失败 (收藏夹 {folder_id}): {exc}"
            errors.append(msg)
            details.append(
                ExecutionDetail(
                    step=desc, status="failure", message=str(exc)
                )
            )

    return ExecutionReport(
        total_attempted=total_attempted,
        succeeded=succeeded,
        failed=failed,
        errors=errors,
        details=details,
    )


def _chunk(items: list[str], size: int) -> list[list[str]]:
    """Split *items* into sub-lists of at most *size* elements each."""
    return [items[i : i + size] for i in range(0, len(items), size)]


# ======================================================================
# PlanFile executor — used by the ``execute`` CLI subcommand
# ======================================================================


async def execute_plan_file(
    plan_file: PlanFile,
    fav_api,
    mid: int,
) -> None:
    """Execute a deserialized ``PlanFile`` against the B站 API.

    Order: collect existing folder mappings → create folders →
    move/copy items → delete items.  Batches of ≤30 resources.
    Failures are logged but don't stop execution.
    """
    BATCH = 30
    step = 0

    # Collect existing folder id↔title mappings
    title_to_id: dict[str, int] = {}
    try:
        existing = await fav_api.list_all_folders(up_mid=mid)
        for f in existing:
            title_to_id[f.title] = f.id
    except Exception as exc:
        print(f"⚠️  获取已有文件夹失败: {exc}")

    # Count total steps
    total_steps = len(plan_file.folders_to_create)
    for m in plan_file.moves:
        total_steps += max((len(m.resources) + BATCH - 1) // BATCH, 0)
    for d in plan_file.deletions:
        total_steps += max((len(d.resources) + BATCH - 1) // BATCH, 0)

    # ── Phase 1: Create folders ───────────────────────────────────
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

    # ── Phase 2: Move / Copy items ────────────────────────────────
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

    # ── Phase 3: Delete items ─────────────────────────────────────
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
