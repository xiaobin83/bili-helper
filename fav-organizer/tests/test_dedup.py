"""Tests for dedup.py — duplicate detector logic.

Covers:
- Default + named folder duplicate detection (Rule 1).
- Multi-named folder preservation (Rule 2).
- Different ids with same bvid treated independently (Rule 3).
- Edge cases: empty folders, no default folder, no named folders,
  unique items only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from src.dedup import detect_duplicates
from src.models import Folder


# ---------------------------------------------------------------------------
# Helpers — build Folder objects quickly
# ---------------------------------------------------------------------------


def _folder(id_: int, title: str, attr: int = 1, media_count: int = 10) -> Folder:
    """Shorthand for constructing a Folder with minimal boilerplate."""
    return Folder(
        id=id_,
        fid=id_,
        mid=100,
        attr=attr,
        title=title,
        media_count=media_count,
    )


def _default_folder(id_: int = 1, title: str = "默认收藏夹") -> Folder:
    """Default folder (attr==0, ``is_default`` is True)."""
    return _folder(id_, title, attr=0)


# ---------------------------------------------------------------------------
# Fake FavAPI — returns pre-configured get_all_folder_ids responses
# ---------------------------------------------------------------------------


@dataclass
class FakeFavAPI:
    """Controllable fake for ``FavAPI.get_all_folder_ids``.

    ``folder_resources`` maps folder id → list of {id, type, bvid} dicts
    that would be returned by ``get_all_folder_ids``.
    """

    folder_resources: dict[int, list[dict[str, Any]]] = field(default_factory=dict)

    async def get_all_folder_ids(self, media_id: int) -> list[dict[str, Any]]:
        return self.folder_resources.get(media_id, [])


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestDetectDuplicates:
    """Core dedup logic tests."""

    @pytest.mark.asyncio
    async def test_empty_folder_list_returns_empty(self):
        """No folders → no duplicates."""
        api = FakeFavAPI()
        result = await detect_duplicates([], api)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_default_folder_returns_empty(self):
        """Only named folders, no default → nothing to flag."""
        folders = [_folder(1, "科技"), _folder(2, "游戏")]
        api = FakeFavAPI(
            {
                1: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
                2: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
            }
        )
        result = await detect_duplicates(folders, api)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_named_folders_returns_empty(self):
        """Only default folder, no named → nothing to flag."""
        folders = [_default_folder(1)]
        api = FakeFavAPI(
            {1: [{"id": 100, "type": 2, "bvid": "BV1xx"}]}
        )
        result = await detect_duplicates(folders, api)
        assert result == []

    @pytest.mark.asyncio
    async def test_unique_items_no_duplicates(self):
        """Every (id, type) appears in only one folder → no duplicates."""
        default = _default_folder(1)
        named = _folder(2, "科技")
        folders = [default, named]
        api = FakeFavAPI(
            {
                1: [{"id": 100, "type": 2, "bvid": "BV1aa"}],
                2: [{"id": 200, "type": 2, "bvid": "BV1bb"}],
            }
        )
        result = await detect_duplicates(folders, api)
        assert result == []

    @pytest.mark.asyncio
    async def test_rule1_default_and_named_duplicate(self):
        """Same (id, type) in default + one named folder → flag default copy."""
        default = _default_folder(1)
        named = _folder(2, "科技")
        folders = [default, named]
        api = FakeFavAPI(
            {
                1: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
                2: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
            }
        )
        result = await detect_duplicates(folders, api)

        assert len(result) == 1
        dup = result[0]
        assert dup.item.id == 100
        assert dup.item.type == 2
        assert dup.item.bvid == "BV1xx"
        assert len(dup.source_folders) == 1
        assert dup.source_folders[0].id == default.id
        assert dup.source_folders[0].is_default is True
        assert dup.target_folder.id == named.id
        assert dup.target_folder.is_default is False

    @pytest.mark.asyncio
    async def test_rule1_default_and_multiple_named(self):
        """Same (id,type) in default + two named folders → flag default copy once."""
        default = _default_folder(1)
        named_a = _folder(2, "科技")
        named_b = _folder(3, "游戏")
        folders = [default, named_a, named_b]
        api = FakeFavAPI(
            {
                1: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
                2: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
                3: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
            }
        )
        result = await detect_duplicates(folders, api)

        assert len(result) == 1
        dup = result[0]
        assert dup.item.id == 100
        assert dup.source_folders[0].is_default is True
        # target_folder should be one of the named folders
        assert dup.target_folder.is_default is False
        assert dup.target_folder.id in {named_a.id, named_b.id}

    @pytest.mark.asyncio
    async def test_rule2_only_named_folders_preserved(self):
        """Same (id, type) in two named folders (no default) → preserve, no flag."""
        folders = [_folder(1, "科技"), _folder(2, "游戏")]
        api = FakeFavAPI(
            {
                1: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
                2: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
            }
        )
        result = await detect_duplicates(folders, api)
        assert result == []

    @pytest.mark.asyncio
    async def test_rule3_different_ids_same_bvid_not_flagged(self):
        """Different ids sharing the same bvid → independent items, no flag."""
        default = _default_folder(1)
        named = _folder(2, "科技")
        folders = [default, named]
        # id=100 and id=101 share BV1xx but are different (id, type) pairs
        api = FakeFavAPI(
            {
                1: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
                2: [{"id": 101, "type": 2, "bvid": "BV1xx"}],
            }
        )
        result = await detect_duplicates(folders, api)
        assert result == []

    @pytest.mark.asyncio
    async def test_rule3_different_ids_same_bvid_one_duplicate(self):
        """One (id,type) duplicated across default+named, another distinct id same bvid → only flag the true duplicate."""
        default = _default_folder(1)
        named = _folder(2, "科技")
        folders = [default, named]
        api = FakeFavAPI(
            {
                1: [
                    {"id": 100, "type": 2, "bvid": "BV1xx"},
                    {"id": 200, "type": 2, "bvid": "BV1yy"},
                ],
                2: [
                    {"id": 100, "type": 2, "bvid": "BV1xx"},  # duplicate
                    {"id": 201, "type": 2, "bvid": "BV1yy"},  # different id, same bvid → not duplicate
                ],
            }
        )
        result = await detect_duplicates(folders, api)

        # Only id=100 is a true duplicate
        assert len(result) == 1
        assert result[0].item.id == 100

    @pytest.mark.asyncio
    async def test_multiple_duplicates_across_folders(self):
        """Several items duplicated between default and named folders."""
        default = _default_folder(1)
        named = _folder(2, "科技")
        folders = [default, named]
        api = FakeFavAPI(
            {
                1: [
                    {"id": 100, "type": 2, "bvid": "BV1aa"},
                    {"id": 200, "type": 2, "bvid": "BV1bb"},
                    {"id": 300, "type": 12, "bvid": ""},
                ],
                2: [
                    {"id": 100, "type": 2, "bvid": "BV1aa"},
                    {"id": 200, "type": 2, "bvid": "BV1bb"},
                    {"id": 400, "type": 2, "bvid": "BV1cc"},  # unique to named
                ],
            }
        )
        result = await detect_duplicates(folders, api)

        assert len(result) == 2
        dup_ids = {d.item.id for d in result}
        assert dup_ids == {100, 200}
        for d in result:
            assert d.source_folders[0].is_default is True
            assert d.target_folder.id == named.id

    @pytest.mark.asyncio
    async def test_duplicate_item_has_bvid_preserved(self):
        """The duplicate FavoritedItem carries the correct bvid from the API."""
        default = _default_folder(1)
        named = _folder(2, "科技")
        folders = [default, named]
        api = FakeFavAPI(
            {
                1: [{"id": 100, "type": 2, "bvid": "BV1hello42"}],
                2: [{"id": 100, "type": 2, "bvid": "BV1hello42"}],
            }
        )
        result = await detect_duplicates(folders, api)
        assert len(result) == 1
        assert result[0].item.bvid == "BV1hello42"

    @pytest.mark.asyncio
    async def test_folder_with_empty_resources(self):
        """One folder is empty → other duplicates still detected."""
        default = _default_folder(1)
        named = _folder(2, "科技")
        folders = [default, named]
        api = FakeFavAPI(
            {
                1: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
                2: [],  # named folder is empty
            }
        )
        result = await detect_duplicates(folders, api)
        assert result == []

    @pytest.mark.asyncio
    async def test_duplicate_source_folders_is_list(self):
        """source_folders is a list[Folder], not a bare Folder."""
        default = _default_folder(1)
        named = _folder(2, "科技")
        folders = [default, named]
        api = FakeFavAPI(
            {
                1: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
                2: [{"id": 100, "type": 2, "bvid": "BV1xx"}],
            }
        )
        result = await detect_duplicates(folders, api)
        assert len(result) == 1
        assert isinstance(result[0].source_folders, list)
        assert all(isinstance(f, Folder) for f in result[0].source_folders)

    @pytest.mark.asyncio
    async def test_mixed_types_default_duplicate(self):
        """Duplicates detected correctly with mixed resource types (e.g., type 2 video, type 12 audio)."""
        default = _default_folder(1)
        named = _folder(2, "混合")
        folders = [default, named]
        api = FakeFavAPI(
            {
                1: [
                    {"id": 100, "type": 2, "bvid": "BV1xx"},   # video
                    {"id": 500, "type": 12, "bvid": ""},        # audio
                ],
                2: [
                    {"id": 100, "type": 2, "bvid": "BV1xx"},   # duplicate video
                    {"id": 500, "type": 12, "bvid": ""},        # duplicate audio
                ],
            }
        )
        result = await detect_duplicates(folders, api)
        assert len(result) == 2
        dup_types = {(d.item.id, d.item.type) for d in result}
        assert dup_types == {(100, 2), (500, 12)}
