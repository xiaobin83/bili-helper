"""Scanner — detect invalid / deleted items across favorites folders.

Usage::

    from src.scanner import scan_invalid

    invalid_items = await scan_invalid(folders, fav_api)
    for item, folder in invalid_items:
        print(f"  {item.bvid} ({item.title}) in {folder.title}")
"""

from __future__ import annotations

from .fav_api import FavAPI
from .models import Folder, FavoritedItem

# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


async def scan_invalid(
    folders: list[Folder],
    fav_api: FavAPI,
) -> list[tuple[FavoritedItem, Folder]]:
    """Scan all *folders* and return (item, source_folder) for each
    invalid / deleted entry.

    **attr filter** (from ``bili-apis/docs/fav/list.md``)::

        0  = normal / valid  →  **skipped**
        1  = deleted (other reason)  →  **included**
        9  = deleted by UP主        →  **included**

    The folder titled ``稍后再看`` (Watch Later) is always skipped.

    Parameters
    ----------
    folders:
        List of ``Folder`` instances to inspect.  The caller is responsible
        for obtaining them (usually from ``FavAPI.list_all_folders()``).
    fav_api:
        An initialised ``FavAPI`` client used to fetch folder contents.

    Returns
    -------
    list[(FavoritedItem, Folder)]
        Every invalid item paired with the folder it was found in.
    """
    results: list[tuple[FavoritedItem, Folder]] = []

    valid_folders = [f for f in folders if f.title != "稍后再看"]
    total = len(valid_folders)
    for i, folder in enumerate(valid_folders, 1):
        items = await fav_api.get_all_contents(media_id=folder.id)
        invalid_count = 0
        for item in items:
            if item.attr != 0:
                results.append((item, folder))
                invalid_count += 1
        print(f"  [{i}/{total}] 📂 {folder.title}: {len(items)} 个, {invalid_count} 失效", flush=True)

    return results
