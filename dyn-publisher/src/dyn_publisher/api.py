"""Bilibili dynamic publish API client."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

from bili_core.errors import (
    PUBLISH_ERR_IMAGE_TOO_SMALL,
    PUBLISH_ERR_NOT_LOGIN,
    PUBLISH_ERR_NO_IMAGE,
    PUBLISH_ERR_PARAM,
    PublishError,
)
from bili_core.http_client import BiliHTTPClient

# ── Constants ──────────────────────────────────────────────────

CATEGORY_DAILY = "daily"
CATEGORY_DRAW = "draw"
CATEGORY_COS = "cos"

SCENE_TEXT = 1
SCENE_IMAGE = 2

# ── Error message mapping ──────────────────────────────────────

_PUBLISH_ERROR_MESSAGES: dict[int, str] = {
    PUBLISH_ERR_NO_IMAGE: "未添加图片",
    PUBLISH_ERR_PARAM: "参数错误",
    PUBLISH_ERR_IMAGE_TOO_SMALL: "图片尺寸过小（需≥420px）",
    PUBLISH_ERR_NOT_LOGIN: "账号未登录，请提供有效的 SESSDATA",
}


def _check_publish_error(result: dict[str, Any]) -> dict[str, Any]:
    """Raise ``PublishError`` for known publish error codes."""
    code = result.get("code", 0)
    if code in _PUBLISH_ERROR_MESSAGES:
        raise PublishError(code, _PUBLISH_ERROR_MESSAGES[code])
    return result


class DynPublisherAPI:
    """Client for B站 dynamic publishing APIs.

    Provides methods for publishing text dynamics, image-text dynamics,
    and uploading images to B站's BFS (bilifile service).
    """

    def __init__(
        self,
        sessdata: str,
        bili_jct: str,
        buvid3: str = "",
        min_interval: float = 3.0,
    ) -> None:
        self._client = BiliHTTPClient(
            sessdata=sessdata,
            bili_jct=bili_jct,
            buvid3=buvid3,
            min_interval=min_interval,
        )

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        await self._client.close()

    async def __aenter__(self) -> DynPublisherAPI:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # ── publish_text ────────────────────────────────────────

    async def publish_text(
        self,
        content: str,
        *,
        at_uids: str = "",
        ctrl: str = "[]",
        extension: str = '{"emoji_type":1,"from":{"emoji_type":1},"flag_cfg":{}}',
        close_comment: bool = False,
        up_choose_comment: bool = False,
    ) -> dict[str, Any]:
        """Publish a pure text dynamic.

        Calls ``dynamic_svr/v1/dynamic_svr/create`` with form-encoded
        data (``type=4`` for text).

        Args:
            content: Dynamic text content. Supports @-mentions when
                combined with *at_uids* and *ctrl*.
            at_uids: Comma-separated UIDs of @-mentioned users.
            ctrl: JSON array controlling @-mention formatting.
            extension: JSON object with emoji type and location info.
            close_comment: Disable comments on this dynamic.
            up_choose_comment: Enable curated comments (精选评论).

        Returns:
            ``{"code": 0, "data": {"dynamic_id_str": "...", ...}}``
        """
        url = "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/create"
        data: dict[str, Any] = {
            "dynamic_id": 0,
            "type": 4,
            "rid": 0,
            "content": content,
            "at_uids": at_uids,
            "ctrl": ctrl,
            "extension": extension,
            "up_choose_comment": int(up_choose_comment),
            "up_close_comment": int(close_comment),
            "csrf_token": self._client.bili_jct,
        }
        # BiliHTTPClient.post() auto-injects csrf, so we only add csrf_token
        result = await self._client.post(url, data=data)
        return _check_publish_error(result)

    # ── publish_image ───────────────────────────────────────

    async def publish_image(
        self,
        text: str,
        image_paths: str | list[str],
        *,
        category: str = CATEGORY_DAILY,
        categories: list[str] | None = None,
        close_comment: bool = False,
        up_choose_comment: bool = False,
    ) -> dict[str, Any]:
        """Publish an image-text dynamic (supports 1–9 images).

        Two-step process per image:
        1. Upload image via ``upload_bfs`` → get image URL + dimensions
        2. Create dynamic with all images via ``x/dynamic/feed/create/dyn`` (JSON)

        Args:
            text: Text caption for the dynamic.
            image_paths: Single image path or list of paths (max 9).
            category: Default image category for all images — ``"daily"``,
                ``"draw"``, or ``"cos"``. Overridden per-image by *categories*.
            categories: Per-image category list (must match *image_paths* length).
                If ``None``, all images use *category*.
            close_comment: Disable comments on this dynamic.
            up_choose_comment: Enable curated comments (精选评论).

        Returns:
            Result from ``create/dyn`` endpoint, e.g.:
            ``{"code": 0, "data": {"dyn_id_str": "...", "dyn_type": 2, ...}}``
        """
        # Normalize to list
        if isinstance(image_paths, str):
            paths = [image_paths]
            cats = [category]
        else:
            paths = image_paths
            if categories is not None:
                if len(categories) != len(paths):
                    return {
                        "code": PUBLISH_ERR_PARAM,
                        "message": f"categories length ({len(categories)}) != paths length ({len(paths)})",
                    }
                cats = categories
            else:
                cats = [category] * len(paths)

        if not paths:
            return {"code": PUBLISH_ERR_NO_IMAGE, "message": "No images provided"}

        # Step 1: Upload all images
        uploaded: list[dict[str, Any]] = []
        for path, cat in zip(paths, cats):
            upload_result = await self.upload_image(path, category=cat)
            _check_publish_error(upload_result)

            img_data = upload_result.get("data", {})
            image_url = img_data.get("image_url", "")
            if not image_url:
                return {
                    "code": PUBLISH_ERR_NO_IMAGE,
                    "message": f"No image url after upload: {path}",
                }
            uploaded.append(img_data)

        # Step 2: Build pics array and send the create/dyn request
        dyn_url = "https://api.bilibili.com/x/dynamic/feed/create/dyn"

        contents = [{"raw_text": text, "type": 1, "biz_id": ""}]

        pics = [
            {
                "img_src": d["image_url"],
                "img_width": d.get("image_width", 0),
                "img_height": d.get("image_height", 0),
                "img_size": 0,  # upload_bfs response does not include img_size
            }
            for d in uploaded
        ]

        payload: dict[str, Any] = {
            "dyn_req": {
                "content": {"contents": contents},
                "pics": pics,
                "option": {
                    "close_comment": int(close_comment),
                    "up_choose_comment": int(up_choose_comment),
                },
                "scene": SCENE_IMAGE,
                "upload_id": (
                    f"{self._client.bili_jct}"
                    f"_{int(time.time())}"
                    f"_{random.randint(1000, 9999)}"
                ),
                "meta": {
                    "app_meta": {
                        "from": "create.dynamic.web",
                        "mobi_app": "web",
                    }
                },
            }
        }

        # post_json with csrf_in_url=True injects csrf into the URL query string
        result = await self._client.post_json(dyn_url, data=payload, csrf_in_url=True)
        return _check_publish_error(result)

    # ── upload_image ────────────────────────────────────────

    async def upload_image(
        self, file_path: str, category: str = CATEGORY_DAILY
    ) -> dict[str, Any]:
        """Upload an image for use in a dynamic.

        Calls ``x/dynamic/feed/draw/upload_bfs`` (multipart/form-data).

        Args:
            file_path: Local path to the image (jpg/png/gif).
            category: ``"daily"`` (default), ``"draw"``, or ``"cos"``.
                Non-daily types require width/height >= 420px.

        Returns:
            ``{"code": 0, "data": {"image_url": "...", "image_width": ..., "image_height": ...}}``

            Error codes:
            - -1: No image provided
            - -2: Parameter error
            - -3: Image too small (< 420px for non-daily)
            - -4: Not logged in
            - -7: Image info error
        """
        # Validate file exists
        path = Path(file_path)
        if not path.exists():
            return {"code": PUBLISH_ERR_NO_IMAGE, "message": f"Image not found: {file_path}"}

        url = "https://api.bilibili.com/x/dynamic/feed/draw/upload_bfs"
        form_data: dict[str, str] = {"category": category}
        # upload_file() auto-injects csrf if missing from form_data

        result = await self._client.upload_file(url, str(path), form_data=form_data)
        return _check_publish_error(result)
