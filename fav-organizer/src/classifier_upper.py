"""Classify favorited items by UP主 name.

Groups items by their uploader (UP主) name and assigns them to folders
named after the UP主.
"""

from src.models import ClassificationResult, FavoritedItem, Folder


def classify_by_upper(
    items: list[FavoritedItem],
    existing_folders: list[Folder],
) -> list[ClassificationResult]:
    """Group favorited items by UP主 name.

    Each item is classified into a folder named after its UP主. If a folder
    with that name already exists, ``target_folder_exists`` is set to True.

    Parameters
    ----------
    items:
        Favorited items to classify.
    existing_folders:
        Existing favorite folders to check against.

    Returns
    -------
        One :class:`ClassificationResult` per item.
    """
    existing_titles = {f.title for f in existing_folders}

    results: list[ClassificationResult] = []
    for item in items:
        upper = item.upper_name
        results.append(
            ClassificationResult(
                item=item,
                category=upper,
                target_folder_title=upper,
                target_folder_exists=upper in existing_titles,
            )
        )

    return results
