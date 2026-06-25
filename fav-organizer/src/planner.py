"""Operation planner — consolidates classifier outputs into an OrganizePlan.

Resolves conflicts between classifiers, generates create/move/delete operations,
detects folders that would become empty, and produces a human-readable summary.

Usage::

    plan = build_plan(
        zone_results=zone_classifications,
        upper_results=upper_classifications,
        llm_results=llm_classifications,
        existing_folders=folders,
        invalid_items=scanner_results,
        duplicate_groups=dedup_results,
        item_folder_map=item_to_folder,
    )
    print(plan.summary)
"""

from __future__ import annotations

from collections import defaultdict

from src.models import (
    ClassificationResult,
    DuplicateGroup,
    FavoritedItem,
    Folder,
    Operation,
    OrganizePlan,
)


def build_plan(
    zone_results: list[ClassificationResult],
    upper_results: list[ClassificationResult],
    llm_results: list[ClassificationResult],
    existing_folders: list[Folder],
    invalid_items: list[tuple[FavoritedItem, Folder]],
    duplicate_groups: list[DuplicateGroup],
    item_folder_map: dict[int, Folder],
) -> OrganizePlan:
    """Consolidate all classifier outputs into a unified OrganizePlan.

    Parameters
    ----------
    zone_results:
        Classification results from the zone (partition) classifier.
    upper_results:
        Classification results from the UP主-name classifier.
    llm_results:
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
    # Step 1 — Conflict resolution: LLM > Zone > UP主
    # ------------------------------------------------------------------
    merged: dict[int, ClassificationResult] = {}

    # Lowest priority: UP主 classifier
    for r in upper_results:
        merged[r.item.id] = r

    # Medium priority: Zone classifier (overwrites UP主)
    for r in zone_results:
        merged[r.item.id] = r

    # Highest priority: LLM classifier (overwrites Zone / UP主)
    for r in llm_results:
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
    # Step 4 — Generate move operations (only from non-default folders)
    # ------------------------------------------------------------------
    # Group moves by (source_folder_id, target_title) for batching
    move_groups: dict[tuple[int, str], list[FavoritedItem]] = defaultdict(list)

    for item_id, result in merged.items():
        source_folder = item_folder_map.get(item_id)
        if source_folder is None:
            continue  # no source folder info → cannot move

        # Skip items already in the target folder, or in the default folder
        if source_folder.title == result.target_folder_title:
            continue
        if source_folder.is_default:
            continue

        key = (source_folder.id, result.target_folder_title)
        move_groups[key].append(result.item)

    move_ops: list[Operation] = []
    for (src_id, target_title), items in move_groups.items():
        source_folder = item_folder_map.get(items[0].id)
        # source_folder should always be found here since we used it as the key
        if source_folder is None:
            # Fallback: find the folder by id
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
        # All duplicates from dedup are in the default folder
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
    # After all moves, check which folders would become empty.
    # A folder is empty if all its items are either moved out or deleted.
    moved_item_ids: set[int] = set()
    for op in move_ops:
        for item in op.resources:
            moved_item_ids.add(item.id)

    deleted_item_ids: set[int] = set()
    for op in delete_ops:
        for item in op.resources:
            deleted_item_ids.add(item.id)

    # For each source folder involved in moves, check if it becomes empty
    # We can detect by checking if the folder has items other than the moved ones.
    # Since we don't have the full folder contents here, we detect based on what
    # we know: if ALL items we know about from a folder are moved/deleted.
    # Note: this is a best-effort detection based on available information.

    # Collect per-folder item counts from item_folder_map
    folder_item_counts: dict[int, int] = defaultdict(int)
    folder_item_ids: dict[int, set[int]] = defaultdict(set)
    for item_id, folder in item_folder_map.items():
        folder_item_counts[folder.id] += 1
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
