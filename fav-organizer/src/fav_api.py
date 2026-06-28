"""Bilibili favorites API client — model-aware wrapper around bili-core FavClient.

Delegates all raw API calls to ``bili_core.fav.FavClient`` and converts
the returned dicts into ``Folder`` / ``FavoritedItem`` model objects.
"""

from __future__ import annotations

from typing import Callable

from bili_core.fav import FavClient as _FavClient
from bili_core.http_client import BiliHTTPClient

from .models import Folder, FavoritedItem


class FavAPI:
    """Bilibili favorites CRUD — model-returning wrapper.

    Constructor accepts three collaborators so the class itself has
    zero knowledge of cookie management, signing algorithm details,
    or transport-level concerns.

    Parameters
    ----------
    http_client:
        Pre-configured ``BiliHTTPClient`` instance.
    bili_jct:
        CSRF token string (used only for callers that read it directly).
    signing:
        Wbi signing callable (``sign_params``).
    """

    def __init__(
        self,
        http_client: BiliHTTPClient,
        bili_jct: str,
        signing: Callable[..., dict],
    ) -> None:
        self._client = _FavClient(http_client=http_client, signing=signing)
        self._bili_jct = bili_jct

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    async def list_all_folders(self, up_mid: int) -> list[Folder]:
        """Return every favorites folder created by the given user."""
        raw = await self._client.list_folders(up_mid)
        return [Folder(**f) for f in raw]

    async def get_folder_contents(
        self,
        media_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[FavoritedItem], bool]:
        """Return one page of folder contents plus a has_more flag."""
        medias, has_more = await self._client.get_folder_contents(
            media_id, page=page, page_size=page_size,
        )
        items: list[FavoritedItem] = [
            FavoritedItem(
                id=m["id"],
                type=m["type"],
                title=m["title"],
                bvid=m.get("bvid", ""),
                upper_name=(m.get("upper") or {}).get("name", ""),
                upper_mid=(m.get("upper") or {}).get("mid", 0),
                attr=m.get("attr", 0),
                fav_time=m.get("fav_time", 0),
            )
            for m in medias
        ]
        return items, has_more

    async def get_all_folder_ids(self, media_id: int) -> list[dict]:
        """Return every resource ID/type/bvid triple inside a folder."""
        return await self._client.get_folder_ids(media_id)

    async def batch_get_info(self, resources: list[str]) -> list[dict]:
        """Fetch detailed metadata for up to 20 resources at once."""
        return await self._client.batch_get_info(resources)

    async def get_all_contents(self, media_id: int) -> list[FavoritedItem]:
        """Return **every** item in a folder by auto-paginating."""
        raw = await self._client.get_all_contents(media_id)
        return [
            FavoritedItem(
                id=m["id"],
                type=m["type"],
                title=m["title"],
                bvid=m.get("bvid", ""),
                upper_name=(m.get("upper") or {}).get("name", ""),
                upper_mid=(m.get("upper") or {}).get("mid", 0),
                attr=m.get("attr", 0),
                fav_time=m.get("fav_time", 0),
            )
            for m in raw
        ]

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    async def create_folder(
        self,
        title: str,
        intro: str = "",
        privacy: int = 0,
    ) -> dict:
        """Create a new favorites folder.

        Parameters
        ----------
        privacy:
            ``0`` = public (default), ``1`` = private.
        """
        return await self._client.create_folder(title, intro, privacy)

    async def copy_items(
        self,
        src_media_id: int,
        tar_media_id: int,
        resources: list[str],
        mid: int,
    ) -> dict:
        """Copy items from one folder to another.

        Parameters
        ----------
        resources:
            Resource identifiers in ``"{id}:{type}"`` format.
        mid:
            Current user's numeric mid.
        """
        return await self._client.copy_items(src_media_id, tar_media_id, resources, mid)

    async def move_items(
        self,
        src_media_id: int,
        tar_media_id: int,
        resources: list[str],
        mid: int,
    ) -> dict:
        """Move items from one folder to another."""
        return await self._client.move_items(src_media_id, tar_media_id, resources, mid)

    async def batch_delete(
        self,
        media_id: int,
        resources: list[str],
    ) -> dict:
        """Delete (un-favourite) items from a folder."""
        return await self._client.delete_items(media_id, resources)

    async def delete_folders(self, media_ids: list[int]) -> dict:
        """Delete one or more favourite folders."""
        return await self._client.delete_folders(media_ids)

    async def clean_invalid(self, media_id: int) -> dict:
        """Remove every invalid/deleted item from a folder."""
        return await self._client.clean_invalid(media_id)
