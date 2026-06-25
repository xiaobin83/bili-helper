"""Bilibili favorites API client — full CRUD for favorites folders and content.

All GET requests are Wbi-signed via ``sign_params``. All POST requests
automatically include ``csrf=bili_jct``. Every HTTP call goes through
``BiliHTTPClient`` so auth, rate-limiting and retries are centralized.
"""

from __future__ import annotations

from typing import Callable

from .http_client import BiliHTTPClient
from .models import Folder, FavoritedItem


class FavAPI:
    """Complete Bilibili favorites CRUD operations.

    Constructor accepts three collaborators injected from outside so the
    class itself has zero knowledge of cookie management, signing algorithm
    details, or transport-level concerns.

    Parameters
    ----------
    http_client:
        Pre-configured ``BiliHTTPClient`` instance (handles auth, rate
        limiting, retries).
    bili_jct:
        CSRF token string extracted from the ``bili_jct`` cookie.  Used
        as the ``csrf`` field in every POST request.
    signing:
        A callable with signature ``(params: dict) -> dict`` that returns
        the same dict with Wbi signing fields (``w_rid``, ``wts``)
        appended.  Typically ``sign_params`` from ``signing.py``.
    """

    BASE_URL = "https://api.bilibili.com"

    def __init__(
        self,
        http_client: BiliHTTPClient,
        bili_jct: str,
        signing: Callable[..., dict],
    ) -> None:
        self._http = http_client
        self._bili_jct = bili_jct
        self._sign = signing

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """Issue a signed GET request and return the parsed JSON body."""
        raw_params: dict = dict(params or {})
        signed = self._sign(raw_params)
        return await self._http.get(f"{self.BASE_URL}{path}", params=signed)

    async def _post(self, path: str, data: dict | None = None) -> dict:
        """Issue a POST request with CSRF injected and return parsed JSON."""
        payload: dict = dict(data or {})
        payload["csrf"] = self._bili_jct
        return await self._http.post(f"{self.BASE_URL}{path}", data=payload)

    # ------------------------------------------------------------------
    # Read methods (GET + Wbi signing)
    # ------------------------------------------------------------------

    async def list_all_folders(self, up_mid: int) -> list[Folder]:
        """Return every favorites folder created by the given user.

        Calls ``GET /x/v3/fav/folder/created/list-all?up_mid={up_mid}``.
        """
        result = await self._get(
            "/x/v3/fav/folder/created/list-all",
            {"up_mid": up_mid},
        )
        data = result.get("data")
        if not data or not isinstance(data, dict):
            return []
        folder_dicts: list[dict] = data.get("list", []) or []
        return [Folder(**f) for f in folder_dicts]

    async def get_folder_contents(
        self,
        media_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[FavoritedItem], bool]:
        """Return one page of folder contents plus a has_more flag.

        Calls ``GET /x/v3/fav/resource/list``.

        Returns
        -------
        (items, has_more) — ``items`` is the list of favourited resources
        on this page; ``has_more`` is ``True`` when additional pages exist.
        """
        result = await self._get(
            "/x/v3/fav/resource/list",
            {"media_id": media_id, "pn": page, "ps": page_size, "platform": "web"},
        )
        data = result.get("data")
        if not data or not isinstance(data, dict):
            return [], False
        medias: list[dict] = data.get("medias", []) or []
        has_more: bool = bool(data.get("has_more", False))
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
        """Return every resource ID/type/bvid triple inside a folder.

        Calls ``GET /x/v3/fav/resource/ids?media_id={media_id}``.
        """
        result = await self._get(
            "/x/v3/fav/resource/ids",
            {"media_id": media_id, "platform": "web"},
        )
        data = result.get("data")
        if not data or not isinstance(data, list):
            return []
        return data

    async def batch_get_info(self, resources: list[str]) -> list[dict]:
        """Fetch detailed metadata for up to 20 resources at once.

        Calls ``GET /x/v3/fav/resource/infos?resources=...``.

        Parameters
        ----------
        resources:
            Resource identifiers in ``"{id}:{type}"`` format, e.g.
            ``["583785685:2", "523:21", "15664:12"]``.
        """
        result = await self._get(
            "/x/v3/fav/resource/infos",
            {"resources": ",".join(resources)},
        )
        data = result.get("data")
        if not data or not isinstance(data, list):
            return []
        return data

    async def get_all_contents(self, media_id: int) -> list[FavoritedItem]:
        """Return **every** item in a folder by paginating automatically.

        Iterates ``get_folder_contents`` until ``has_more`` is ``False``.
        """
        all_items: list[FavoritedItem] = []
        page = 1
        while True:
            items, has_more = await self.get_folder_contents(media_id, page=page)
            all_items.extend(items)
            if not has_more:
                break
            page += 1
            if page % 5 == 0:
                print(f"    第 {page} 页...", flush=True)
        return all_items

    # ------------------------------------------------------------------
    # Write methods (POST + CSRF)
    # ------------------------------------------------------------------

    async def create_folder(
        self,
        title: str,
        intro: str = "",
        privacy: int = 0,
    ) -> dict:
        """Create a new favorites folder.

        Calls ``POST /x/v3/fav/folder/add``.

        Parameters
        ----------
        privacy:
            ``0`` = public (default), ``1`` = private.
        """
        return await self._post(
            "/x/v3/fav/folder/add",
            {"title": title, "intro": intro, "privacy": privacy},
        )

    async def copy_items(
        self,
        src_media_id: int,
        tar_media_id: int,
        resources: list[str],
        mid: int,
    ) -> dict:
        """Copy items from one folder to another.

        Calls ``POST /x/v3/fav/resource/copy``.

        Parameters
        ----------
        resources:
            Resource identifiers in ``"{id}:{type}"`` format.
        mid:
            Current user's numeric mid.
        """
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

    async def move_items(
        self,
        src_media_id: int,
        tar_media_id: int,
        resources: list[str],
        mid: int,
    ) -> dict:
        """Move items from one folder to another.

        Calls ``POST /x/v3/fav/resource/move``.
        """
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

    async def batch_delete(
        self,
        media_id: int,
        resources: list[str],
    ) -> dict:
        """Delete (un-favourite) items from a folder.

        Calls ``POST /x/v3/fav/resource/batch-del``.
        """
        return await self._post(
            "/x/v3/fav/resource/batch-del",
            {
                "media_id": media_id,
                "resources": ",".join(resources),
                "platform": "web",
            },
        )

    async def delete_folders(self, media_ids: list[int]) -> dict:
        """Delete one or more favourite folders.

        Calls ``POST /x/v3/fav/folder/del`` with comma-separated
        ``media_ids`` (upstream supports multiple ids in one request).
        """
        return await self._post(
            "/x/v3/fav/folder/del",
            {"media_ids": ",".join(str(m) for m in media_ids)},
        )

    async def clean_invalid(self, media_id: int) -> dict:
        """Remove every invalid/deleted item from a folder.

        Calls ``POST /x/v3/fav/resource/clean``.
        """
        return await self._post(
            "/x/v3/fav/resource/clean",
            {"media_id": media_id},
        )
