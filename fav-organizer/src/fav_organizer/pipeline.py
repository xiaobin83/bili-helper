"""Pipeline state management — batch continuation and cleanup.

Functions here manage the multi-batch pipeline lifecycle: advancing
batches after partial execution and cleaning up intermediate files
after successful execution.
"""

from __future__ import annotations

from .models import ClassificationEntry, ClassificationResultList
from .state_manager import StateManager


def cleanup_pipeline_files(mgr: StateManager, plan_path: str | None) -> None:
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


def cmd_prepare_next_batch(mgr: StateManager) -> int:
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
