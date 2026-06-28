"""Bilibili favorites API client — full CRUD for folders and content.

All methods return raw API response dicts so downstream consumers can
construct their own models without bili-core depending on any specific
model library.

Usage::

    from bili_core.fav import FavClient
    from bili_core.http_client import BiliHTTPClient
    from bili_core.signing import sign_params

    http = BiliHTTPClient(sessdata="...", bili_jct="...")
    fav = FavClient(http_client=http, signing=sign_params)

    folders = await fav.list_folders(up_mid=12345)
    items, has_more = await fav.get_folder_contents(media_id=67890)
"""

from __future__ import annotations

import logging

from bili_core.api_base import BaseBiliClient
from bili_core.http_client import BiliHTTPClient

logger = logging.getLogger(__name__)


class FavClient(BaseBiliClient):
    """Bilibili favorites CRUD client — all folder & content operations.

    Extends ``BaseBiliClient`` so every request (signed GET or CSRF-POST)
    is handled by the shared transport layer.  All public methods return
    raw ``dict`` / ``list[dict]`` from the API — no model classes.

    Parameters
    ----------
    http_client:
        Pre-configured ``BiliHTTPClient`` instance.
    signing:
        Wbi signing callable (``sign_params``).
    """

    # ------------------------------------------------------------------
    # Folder listing
    # ------------------------------------------------------------------

    async def list_folders(self, up_mid: int) -> list[dict]:
        """Return every favorites folder created by *up_mid*.

        Calls ``GET /x/v3/fav/folder/created/list-all?up_mid={up_mid}``
        (Wbi-signed).  Returns ``[]`` on error or missing auth.
        """
        if not self._has_auth:
            logger.info("list_folders: skipped (no auth)")
            return []
        try:
            raw = await self._signed_get(
                "/x/v3/fav/folder/created/list-all",
                {"up_mid": up_mid},
            )
            if raw.get("code") != 0:
                logger.warning("list_folders: code=%s", raw.get("code"))
                return []
            data = raw.get("data")
            if not data or not isinstance(data, dict):
                return []
            return data.get("list", []) or []
        except Exception as e:
            logger.warning("list_folders failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Folder contents
    # ------------------------------------------------------------------

    async def get_folder_contents(
        self,
        media_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], bool]:
        """Return one page of folder contents plus a ``has_more`` flag.

        Calls ``GET /x/v3/fav/resource/list`` (Wbi-signed).

        Returns ``([], False)`` on error.
        """
        try:
            raw = await self._signed_get(
                "/x/v3/fav/resource/list",
                {"media_id": media_id, "pn": page, "ps": page_size, "platform": "web"},
            )
            data = raw.get("data")
            if not data or not isinstance(data, dict):
                return [], False
            medias: list[dict] = data.get("medias", []) or []
            has_more: bool = bool(data.get("has_more", False))
            return medias, has_more
        except Exception as e:
            logger.warning("get_folder_contents failed: %s", e)
            return [], False

    async def get_folder_ids(self, media_id: int) -> list[dict]:
        """Return every resource ID/type/bvid triple inside a folder.

        Calls ``GET /x/v3/fav/resource/ids?media_id={media_id}``
        (Wbi-signed).  Returns ``[]`` on error.
        """
        try:
            raw = await self._signed_get(
                "/x/v3/fav/resource/ids",
                {"media_id": media_id, "platform": "web"},
            )
            data = raw.get("data")
            if not data or not isinstance(data, list):
                return []
            return data
        except Exception as e:
            logger.warning("get_folder_ids failed: %s", e)
            return []

    async def batch_get_info(self, resources: list[str]) -> list[dict]:
        """Fetch detailed metadata for up to 20 resources at once.

        Calls ``GET /x/v3/fav/resource/infos?resources=...`` (Wbi-signed).

        Parameters
        ----------
        resources:
            Resource identifiers in ``"{id}:{type}"`` format, e.g.
            ``["583785685:2", "523:21"]``.
        """
        try:
            raw = await self._signed_get(
                "/x/v3/fav/resource/infos",
                {"resources": ",".join(resources)},
            )
            data = raw.get("data")
            if not data or not isinstance(data, list):
                return []
            return data
        except Exception as e:
            logger.warning("batch_get_info failed: %s", e)
            return []

    async def get_all_contents(self, media_id: int) -> list[dict]:
        """Return **every** item in a folder by auto-paginating.

        Iterates ``get_folder_contents`` until ``has_more`` is ``False``.
        """
        all_items: list[dict] = []
        page = 1
        while True:
            items, has_more = await self.get_folder_contents(media_id, page=page)
            all_items.extend(items)
            if not has_more:
                break
            page += 1
            if page % 5 == 0:
                logger.debug("get_all_contents page %d for media_id=%d", page, media_id)
        return all_items

    # ------------------------------------------------------------------
    # Folder CRUD
    # ------------------------------------------------------------------

    async def create_folder(
        self,
        title: str,
        intro: str = "",
        privacy: int = 0,
    ) -> dict:
        """Create a new favorites folder.

        Calls ``POST /x/v3/fav/folder/add`` (CSRF-authenticated).

        Parameters
        ----------
        privacy:
            ``0`` = public (default), ``1`` = private.

        Returns ``{"code": -1, "message": "..."}`` on error.
        """
        if not self._has_auth:
            return {"code": -1, "message": "未登录，无法创建收藏夹"}
        try:
            raw = await self._post(
                "/x/v3/fav/folder/add",
                {"title": title, "intro": intro, "privacy": privacy},
            )
            return {
                "code": raw.get("code", -1),
                "message": raw.get("message", ""),
                "data": raw.get("data") or {},
            }
        except Exception as e:
            logger.warning("create_folder failed: %s", e)
            return {"code": -1, "message": str(e)}

    async def delete_folders(self, media_ids: list[int]) -> dict:
        """Delete one or more favourite folders.

        Calls ``POST /x/v3/fav/folder/del`` with comma-separated
        ``media_ids`` (upstream supports multiple ids in one request).
        """
        try:
            raw = await self._post(
                "/x/v3/fav/folder/del",
                {"media_ids": ",".join(str(m) for m in media_ids)},
            )
            return raw
        except Exception as e:
            logger.warning("delete_folders failed: %s", e)
            return {"code": -1, "message": str(e)}

    # ------------------------------------------------------------------
    # Content CRUD
    # ------------------------------------------------------------------

    async def add_video(self, aid: int, media_ids: list[int]) -> dict:
        """Add a video to one or more favorites folders.

        Calls ``POST /medialist/gateway/coll/resource/deal`` (CSRF-authenticated).

        Parameters
        ----------
        aid:
            Video avid.
        media_ids:
            Target folder media_id(s).

        Returns ``{"code": -1, "message": "..."}`` on error.
        """
        if not self._has_auth:
            return {"code": -1, "message": "未登录，无法添加到收藏夹"}
        try:
            raw = await self._post(
                "/medialist/gateway/coll/resource/deal",
                {
                    "rid": aid,
                    "type": 2,  # 2 = video
                    "add_media_ids": ",".join(str(m) for m in media_ids),
                    "del_media_ids": "",
                },
            )
            return {"code": raw.get("code", -1), "message": raw.get("message", "")}
        except Exception as e:
            logger.warning("add_video failed: %s", e)
            return {"code": -1, "message": str(e)}

    async def copy_items(
        self,
        src_media_id: int,
        tar_media_id: int,
        resources: list[str],
        mid: int,
    ) -> dict:
        """Copy items from one folder to another.

        Calls ``POST /x/v3/fav/resource/copy`` (CSRF-authenticated).

        Parameters
        ----------
        resources:
            Resource identifiers in ``"{id}:{type}"`` format.
        mid:
            Current user's numeric mid.
        """
        try:
            return await self._post(
                "/x/v3/fav/resource/copy",
                {
                    "src_media_id": src_media_id,
                    "tar_media_id": tar_media_id,
                    "mid": mid,
                    "resources": ",".join(resources),
                    "platform": "web",
                },
            )
        except Exception as e:
            logger.warning("copy_items failed: %s", e)
            return {"code": -1, "message": str(e)}

    async def move_items(
        self,
        src_media_id: int,
        tar_media_id: int,
        resources: list[str],
        mid: int,
    ) -> dict:
        """Move items from one folder to another.

        Calls ``POST /x/v3/fav/resource/move`` (CSRF-authenticated).
        """
        try:
            return await self._post(
                "/x/v3/fav/resource/move",
                {
                    "src_media_id": src_media_id,
                    "tar_media_id": tar_media_id,
                    "mid": mid,
                    "resources": ",".join(resources),
                    "platform": "web",
                },
            )
        except Exception as e:
            logger.warning("move_items failed: %s", e)
            return {"code": -1, "message": str(e)}

    async def delete_items(
        self,
        media_id: int,
        resources: list[str],
    ) -> dict:
        """Delete (un-favourite) items from a folder.

        Calls ``POST /x/v3/fav/resource/batch-del`` (CSRF-authenticated).
        """
        try:
            return await self._post(
                "/x/v3/fav/resource/batch-del",
                {
                    "media_id": media_id,
                    "resources": ",".join(resources),
                    "platform": "web",
                },
            )
        except Exception as e:
            logger.warning("delete_items failed: %s", e)
            return {"code": -1, "message": str(e)}

    async def clean_invalid(self, media_id: int) -> dict:
        """Remove every invalid/deleted item from a folder.

        Calls ``POST /x/v3/fav/resource/clean`` (CSRF-authenticated).
        """
        try:
            return await self._post(
                "/x/v3/fav/resource/clean",
                {"media_id": media_id},
            )
        except Exception as e:
            logger.warning("clean_invalid failed: %s", e)
            return {"code": -1, "message": str(e)}
