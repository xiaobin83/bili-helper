"""Duplicate detector for Bilibili favorites folders.

Identifies items in the default (attr==0) folder that also exist in one
or more named folders.  Duplicates in the default folder are flagged while
items appearing across multiple *named* folders are preserved (valid
multi-category placement).

Rules
-----
1. Same (id, type) in **default + named** folder → flag default copy.
2. Same (id, type) in **multiple named** folders → preserve (valid).
3. Different ids sharing the same **bvid** → never flag (different segments).
"""

from __future__ import annotations

from collections import defaultdict

from .fav_api import FavAPI
from .models import DuplicateGroup, FavoritedItem, Folder


async def detect_duplicates(
    folders: list[Folder],
    fav_api: FavAPI,
) -> list[DuplicateGroup]:
    """Scan all folders for duplicates and return flagged groups.

    For every folder the function fetches the lightweight ``(id, type, bvid)``
    triples via ``FavAPI.get_all_folder_ids`` and builds an inverted index:
    ``(id, type) → list[Folder]``.  The index is then inspected against the
    three dedup rules.

    Parameters
    ----------
    folders:
        Every folder owned by the current user (typically returned by
        ``FavAPI.list_all_folders``).
    fav_api:
        Configured ``FavAPI`` instance used to query folder contents.

    Returns
    -------
    list[DuplicateGroup]
        One ``DuplicateGroup`` per duplicate discovered in the default
        folder.  Empty list when no duplicates exist.
    """
    if not folders:
        return []

    # ------------------------------------------------------------------
    # Separate default from named folders
    # ------------------------------------------------------------------
    default_folder: Folder | None = None
    named_folders: list[Folder] = []

    for f in folders:
        if f.is_default:
            default_folder = f
        else:
            named_folders.append(f)

    # No default folder or no named folders → nothing to dedup
    if default_folder is None or not named_folders:
        return []

    # ------------------------------------------------------------------
    # Build (id, type) → [Folder] index + bvid lookup
    # ------------------------------------------------------------------
    id_type_to_folders: dict[tuple[int, int], list[Folder]] = defaultdict(list)
    id_type_to_bvid: dict[tuple[int, int], str] = {}

    for folder in folders:
        resources = await fav_api.get_all_folder_ids(folder.id)
        for r in resources:
            key = (r["id"], r["type"])
            id_type_to_folders[key].append(folder)
            if key not in id_type_to_bvid:
                id_type_to_bvid[key] = r.get("bvid", "")

    # ------------------------------------------------------------------
    # Apply rules and build DuplicateGroup list
    # ------------------------------------------------------------------
    results: list[DuplicateGroup] = []

    for (rid, rtype), folder_list in id_type_to_folders.items():
        if len(folder_list) < 2:
            continue  # unique item — no duplicate possible

        has_default = any(f.is_default for f in folder_list)
        has_named = any(not f.is_default for f in folder_list)
        only_named = not has_default and has_named

        # Rule 2: multiple named folders — valid multi-category, preserve
        if only_named:
            continue

        # Rule 1: default + named → flag default's copy
        if has_default and has_named:
            target = next(f for f in folder_list if not f.is_default)
            item = FavoritedItem(
                id=rid,
                type=rtype,
                bvid=id_type_to_bvid.get((rid, rtype), ""),
                title="",
                upper_name="",
                upper_mid=0,
                attr=0,
                fav_time=0,
            )
            results.append(
                DuplicateGroup(
                    item=item,
                    source_folders=[default_folder],
                    target_folder=target,
                )
            )

    return results
