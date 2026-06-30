"""Tests for Pydantic v2 models in src/types.py.

Covers serialization, deserialization, property behavior, and default values.
"""

import pytest
from pydantic import ValidationError

from src.fav_organizer.models import (
    ClassificationResult,
    Credentials,
    DuplicateGroup,
    Folder,
    FavoritedItem,
    Operation,
    OrganizePlan,
    VideoInfo,
)


# ---------------------------------------------------------------------------
# Folder
# ---------------------------------------------------------------------------


class TestFolder:
    def test_minimal_creation(self):
        folder = Folder(
            id=1, fid=0, mid=100, attr=0, title="默认收藏夹", media_count=5
        )
        assert folder.id == 1
        assert folder.fid == 0
        assert folder.mid == 100
        assert folder.attr == 0
        assert folder.title == "默认收藏夹"
        assert folder.media_count == 5

    def test_is_default_true_when_attr_zero(self):
        folder = Folder(
            id=1, fid=0, mid=100, attr=0, title="默认", media_count=0
        )
        assert folder.is_default is True

    def test_is_default_false_when_attr_nonzero(self):
        folder = Folder(
            id=2, fid=1, mid=100, attr=1, title="普通收藏夹", media_count=10
        )
        assert folder.is_default is False

    def test_is_default_false_when_attr_large(self):
        folder = Folder(
            id=3, fid=2, mid=100, attr=9, title="失效", media_count=0
        )
        assert folder.is_default is False

    def test_serialization(self):
        folder = Folder(
            id=1, fid=0, mid=100, attr=0, title="测试", media_count=3
        )
        data = folder.model_dump()
        assert data == {
            "id": 1,
            "fid": 0,
            "mid": 100,
            "attr": 0,
            "title": "测试",
            "media_count": 3,
        }

    def test_deserialization(self):
        data = {
            "id": 1,
            "fid": 0,
            "mid": 100,
            "attr": 1,
            "title": "测试",
            "media_count": 5,
        }
        folder = Folder.model_validate(data)
        assert folder.id == 1
        assert folder.attr == 1
        assert folder.is_default is False


# ---------------------------------------------------------------------------
# FavoritedItem
# ---------------------------------------------------------------------------


class TestFavoritedItem:
    def test_minimal_creation(self):
        item = FavoritedItem(
            id=10,
            type=2,
            title="测试视频",
            bvid="BV1xx411c7mD",
            upper_name="UP主",
            upper_mid=200,
            attr=0,
            fav_time=1234567890,
        )
        assert item.id == 10
        assert item.type == 2
        assert item.bvid == "BV1xx411c7mD"
        assert item.upper_name == "UP主"
        assert item.upper_mid == 200

    def test_is_valid_true_when_attr_zero(self):
        item = FavoritedItem(
            id=1,
            type=2,
            title="有效",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=0,
            fav_time=100,
        )
        assert item.is_valid is True

    def test_is_valid_false_when_attr_one(self):
        item = FavoritedItem(
            id=2,
            type=2,
            title="失效-其他",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=1,
            fav_time=100,
        )
        assert item.is_valid is False

    def test_is_valid_false_when_attr_nine(self):
        item = FavoritedItem(
            id=3,
            type=2,
            title="UP主删除",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=9,
            fav_time=100,
        )
        assert item.is_valid is False

    def test_serialization_roundtrip(self):
        item = FavoritedItem(
            id=1,
            type=2,
            title="视频",
            bvid="BV1xx411c7mD",
            upper_name="UP主",
            upper_mid=200,
            attr=0,
            fav_time=1234567890,
        )
        data = item.model_dump()
        restored = FavoritedItem.model_validate(data)
        assert restored.id == item.id
        assert restored.bvid == item.bvid
        assert restored.is_valid == item.is_valid

    def test_content_type_video(self):
        item = FavoritedItem(
            id=1,
            type=2,
            title="视频",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=0,
            fav_time=100,
        )
        assert item.type == 2  # video

    def test_content_type_audio(self):
        item = FavoritedItem(
            id=2,
            type=12,
            title="音频",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=0,
            fav_time=100,
        )
        assert item.type == 12  # audio


# ---------------------------------------------------------------------------
# ClassificationResult
# ---------------------------------------------------------------------------


class TestClassificationResult:
    def test_default_exists_false(self):
        item = FavoritedItem(
            id=1,
            type=2,
            title="视频",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=0,
            fav_time=100,
        )
        result = ClassificationResult(
            item=item,
            category="科技",
            target_folder_title="科技区",
        )
        assert result.target_folder_exists is False

    def test_override_default(self):
        item = FavoritedItem(
            id=1,
            type=2,
            title="视频",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=0,
            fav_time=100,
        )
        result = ClassificationResult(
            item=item,
            category="科技",
            target_folder_title="科技区",
            target_folder_exists=True,
        )
        assert result.target_folder_exists is True

    def test_serialization_includes_item(self):
        item = FavoritedItem(
            id=1,
            type=2,
            title="视频",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=0,
            fav_time=100,
        )
        result = ClassificationResult(
            item=item, category="娱乐", target_folder_title="娱乐区"
        )
        data = result.model_dump()
        assert data["item"]["bvid"] == "BV1xx411c7mD"
        assert data["category"] == "娱乐"


# ---------------------------------------------------------------------------
# Operation
# ---------------------------------------------------------------------------


class TestOperation:
    def test_create_folder_operation(self):
        op = Operation(action="create_folder", resources=[])
        assert op.action == "create_folder"
        assert op.source is None
        assert op.target is None
        assert op.resources == []

    def test_move_operation(self):
        source = Folder(
            id=1, fid=0, mid=100, attr=1, title="源", media_count=5
        )
        target = Folder(
            id=2, fid=1, mid=100, attr=1, title="目标", media_count=10
        )
        item = FavoritedItem(
            id=1,
            type=2,
            title="视频",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=0,
            fav_time=100,
        )
        op = Operation(
            action="move", source=source, target=target, resources=[item]
        )
        assert op.action == "move"
        assert op.source == source
        assert op.target == target
        assert len(op.resources) == 1

    def test_clean_operation_no_source(self):
        op = Operation(action="clean", resources=[])
        assert op.action == "clean"
        assert op.source is None

    def test_invalid_action_raises(self):
        with pytest.raises(ValidationError):
            Operation(action="invalid_action", resources=[])

    def test_all_valid_actions(self):
        for action in ("create_folder", "move", "copy", "batch_delete", "clean"):
            op = Operation(action=action, resources=[])
            assert op.action == action


# ---------------------------------------------------------------------------
# OrganizePlan
# ---------------------------------------------------------------------------


class TestOrganizePlan:
    def test_minimal_plan(self):
        plan = OrganizePlan(
            total_operations=2,
            folders_to_create=["科技区"],
            moves=[],
            deletions=[],
            summary="创建1个文件夹，移动0个视频",
        )
        assert plan.total_operations == 2
        assert plan.folders_to_create == ["科技区"]
        assert plan.moves == []
        assert plan.deletions == []

    def test_with_operations(self):
        op = Operation(action="move", resources=[])
        plan = OrganizePlan(
            total_operations=1,
            folders_to_create=[],
            moves=[op],
            deletions=[],
            summary="移动1个视频",
        )
        assert plan.moves[0].action == "move"

    def test_empty_plan(self):
        plan = OrganizePlan(
            total_operations=0,
            folders_to_create=[],
            moves=[],
            deletions=[],
            summary="无需操作",
        )
        assert plan.total_operations == 0


# ---------------------------------------------------------------------------
# VideoInfo
# ---------------------------------------------------------------------------


class TestVideoInfo:
    def test_creation(self):
        info = VideoInfo(bvid="BV1xx411c7mD", tid=4, tname="科技")
        assert info.bvid == "BV1xx411c7mD"
        assert info.tid == 4
        assert info.tname == "科技"

    def test_serialization(self):
        info = VideoInfo(bvid="BV1xx411c7mD", tid=4, tname="科技")
        data = info.model_dump()
        assert data == {"bvid": "BV1xx411c7mD", "tid": 4, "tname": "科技"}

    def test_deserialization(self):
        data = {"bvid": "BV1xx411c7mD", "tid": 4, "tname": "科技"}
        info = VideoInfo.model_validate(data)
        assert info.bvid == "BV1xx411c7mD"


# ---------------------------------------------------------------------------
# DuplicateGroup
# ---------------------------------------------------------------------------


class TestDuplicateGroup:
    def test_creation(self):
        item = FavoritedItem(
            id=1,
            type=2,
            title="重复视频",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=0,
            fav_time=100,
        )
        f1 = Folder(
            id=1, fid=0, mid=100, attr=1, title="收藏夹1", media_count=10
        )
        f2 = Folder(
            id=2, fid=1, mid=100, attr=1, title="收藏夹2", media_count=5
        )
        target = Folder(
            id=3, fid=2, mid=100, attr=0, title="目标", media_count=0
        )
        group = DuplicateGroup(
            item=item, source_folders=[f1, f2], target_folder=target
        )
        assert group.item.bvid == "BV1xx411c7mD"
        assert len(group.source_folders) == 2
        assert group.target_folder.title == "目标"

    def test_single_source(self):
        item = FavoritedItem(
            id=1,
            type=2,
            title="视频",
            bvid="BV1xx411c7mD",
            upper_name="UP",
            upper_mid=1,
            attr=0,
            fav_time=100,
        )
        f1 = Folder(
            id=1, fid=0, mid=100, attr=1, title="收藏夹A", media_count=5
        )
        target = Folder(
            id=2, fid=1, mid=100, attr=0, title="保留", media_count=0
        )
        group = DuplicateGroup(
            item=item, source_folders=[f1], target_folder=target
        )
        assert len(group.source_folders) == 1


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class TestCredentials:
    def test_minimal_creation(self):
        creds = Credentials(sessdata="abc", bili_jct="def")
        assert creds.sessdata == "abc"
        assert creds.bili_jct == "def"
        assert creds.buvid3 == ""
        assert creds.mid == 0

    def test_full_creation(self):
        creds = Credentials(
            sessdata="abc", bili_jct="def", buvid3="xyz", mid=12345
        )
        assert creds.buvid3 == "xyz"
        assert creds.mid == 12345

    def test_serialization(self):
        creds = Credentials(sessdata="abc", bili_jct="def")
        data = creds.model_dump()
        assert data == {
            "sessdata": "abc",
            "bili_jct": "def",
            "buvid3": "",
            "mid": 0,
        }

    def test_deserialization(self):
        data = {
            "sessdata": "abc",
            "bili_jct": "def",
            "buvid3": "xyz",
            "mid": 999,
        }
        creds = Credentials.model_validate(data)
        assert creds.mid == 999
