"""Operation planner — converts LLM classifications into an OrganizePlan.

Resolves classification results into create/move/delete operations,
detects folders that would become empty, and produces a human-readable
summary.

Usage::

    plan = build_plan(
        classifications=llm_classifications,
        existing_folders=folders,
        invalid_items=invalid_items,
        duplicate_groups=duplicate_groups,
        item_folder_map=item_to_folder,
    )
    print(plan.summary)
"""

from __future__ import annotations

from collections import defaultdict

from .models import (
    ClassificationResult,
    DuplicateGroup,
    FavoritedItem,
    Folder,
    Operation,
    OrganizePlan,
)


def build_plan(
    classifications: list[ClassificationResult],
    existing_folders: list[Folder],
    invalid_items: list[tuple[FavoritedItem, Folder]],
    duplicate_groups: list[DuplicateGroup],
    item_folder_map: dict[int, Folder],
) -> OrganizePlan:
    """Consolidate LLM classifier output into a unified OrganizePlan.

    Parameters
    ----------
    classifications:
        Classification results from the LLM classifier.
    existing_folders:
        Current favorite folders owned by the user.
    invalid_items:
        (item, source_folder) pairs detected by the invalid-item scanner.
    duplicate_groups:
        Duplicate groups detected by the dedup detector.
    item_folder_map:
        Mapping from ``item.id`` to its current ``Folder``.  Needed to
        determine whether a classified item should be moved.

    Returns
    -------
    OrganizePlan
        A complete plan with all operations, stats, and a Chinese summary.
    """
    # ------------------------------------------------------------------
    # Step 1 — Build merged classification dict
    # ------------------------------------------------------------------
    merged: dict[int, ClassificationResult] = {}
    for r in classifications:
        merged[r.item.id] = r

    # ------------------------------------------------------------------
    # Step 2 — Collect unique target folder titles & check existence
    # ------------------------------------------------------------------
    existing_titles = {f.title for f in existing_folders}
    target_titles: set[str] = set()
    for r in merged.values():
        target_titles.add(r.target_folder_title)

    # Folders that need to be created (don't exist yet)
    new_folder_titles = sorted(target_titles - existing_titles)

    # Update target_folder_exists for results whose target is a new folder
    for r in merged.values():
        if r.target_folder_title in existing_titles:
            r.target_folder_exists = True

    # ------------------------------------------------------------------
    # Step 3 — Generate create_folder operations
    # ------------------------------------------------------------------
    create_ops: list[Operation] = []
    for title in new_folder_titles:
        create_ops.append(
            Operation(action="create_folder", target=title, resources=[])
        )

    # ------------------------------------------------------------------
    # Step 4 — Generate move (default folder) / copy (other folders) operations
    # ------------------------------------------------------------------
    # Default folders: move items out to target categories.
    # Non-default folders: copy items to target categories (don't remove from source).
    move_groups: dict[tuple[int, str], list[FavoritedItem]] = defaultdict(list)
    copy_groups: dict[tuple[int, str], list[FavoritedItem]] = defaultdict(list)

    for item_id, result in merged.items():
        source_folder = item_folder_map.get(item_id)
        if source_folder is None:
            continue

        if source_folder.title == result.target_folder_title:
            continue

        key = (source_folder.id, result.target_folder_title)
        if source_folder.is_default:
            move_groups[key].append(result.item)
        else:
            copy_groups[key].append(result.item)

    move_ops: list[Operation] = []
    for (src_id, target_title), items in move_groups.items():
        source_folder = item_folder_map.get(items[0].id)
        if source_folder is None:
            source_folder = next(
                (f for f in existing_folders if f.id == src_id), None
            )
        if source_folder is None:
            continue
        move_ops.append(
            Operation(
                action="move",
                source=source_folder,
                target=target_title,
                resources=items,
            )
        )

    for (src_id, target_title), items in copy_groups.items():
        source_folder = item_folder_map.get(items[0].id)
        if source_folder is None:
            source_folder = next(
                (f for f in existing_folders if f.id == src_id), None
            )
        if source_folder is None:
            continue
        move_ops.append(
            Operation(
                action="copy",
                source=source_folder,
                target=target_title,
                resources=items,
            )
        )

    # ------------------------------------------------------------------
    # Step 5 — Generate batch_delete for invalid items
    # ------------------------------------------------------------------
    invalid_groups: dict[int, list[FavoritedItem]] = defaultdict(list)
    invalid_source_folders: dict[int, Folder] = {}
    for item, folder in invalid_items:
        invalid_groups[folder.id].append(item)
        invalid_source_folders[folder.id] = folder

    delete_ops: list[Operation] = []
    for folder_id, items in invalid_groups.items():
        folder = invalid_source_folders[folder_id]
        delete_ops.append(
            Operation(
                action="batch_delete",
                source=folder,
                resources=items,
            )
        )

    # ------------------------------------------------------------------
    # Step 6 — Generate batch_delete for duplicate items in default folder
    # ------------------------------------------------------------------
    if duplicate_groups:
        dup_source_folders: dict[int, list[FavoritedItem]] = defaultdict(list)
        dup_folder_ref: dict[int, Folder] = {}
        for dg in duplicate_groups:
            for src_folder in dg.source_folders:
                dup_source_folders[src_folder.id].append(dg.item)
                dup_folder_ref[src_folder.id] = src_folder

        for folder_id, items in dup_source_folders.items():
            folder = dup_folder_ref[folder_id]
            delete_ops.append(
                Operation(
                    action="batch_delete",
                    source=folder,
                    resources=items,
                )
            )

    # ------------------------------------------------------------------
    # Step 7 — Empty folder detection
    # ------------------------------------------------------------------
    moved_item_ids: set[int] = set()
    for op in move_ops:
        for item in op.resources:
            moved_item_ids.add(item.id)

    deleted_item_ids: set[int] = set()
    for op in delete_ops:
        for item in op.resources:
            deleted_item_ids.add(item.id)

    folder_item_ids: dict[int, set[int]] = defaultdict(set)
    for item_id, folder in item_folder_map.items():
        folder_item_ids[folder.id].add(item_id)

    empty_folder_ids: set[int] = set()
    for folder_id, all_ids in folder_item_ids.items():
        remaining = all_ids - moved_item_ids - deleted_item_ids
        if not remaining:
            empty_folder_ids.add(folder_id)

    # ------------------------------------------------------------------
    # Step 8 — Stats & summary
    # ------------------------------------------------------------------
    total_moves = sum(len(op.resources) for op in move_ops)
    total_deletions = sum(len(op.resources) for op in delete_ops)
    total_operations = len(create_ops) + len(move_ops) + len(delete_ops)

    summary = (
        f"需要创建 {len(create_ops)} 个文件夹，"
        f"移动 {total_moves} 个视频到对应分类，"
        f"删除 {total_deletions} 个失效/重复内容"
    )

    # Build folder-title mapping for empty folder names
    folder_title_map: dict[int, str] = {}
    for folder in existing_folders:
        folder_title_map[folder.id] = folder.title
    for item_id, folder in item_folder_map.items():
        folder_title_map[folder.id] = folder.title

    empty_folder_titles = sorted({
        folder_title_map[fid]
        for fid in empty_folder_ids
        if fid in folder_title_map
    })

    return OrganizePlan(
        total_operations=total_operations,
        folders_to_create=new_folder_titles,
        moves=move_ops,
        deletions=delete_ops,
        summary=summary,
        empty_folders=empty_folder_titles,
    )
