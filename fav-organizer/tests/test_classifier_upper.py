"""Tests for classifier_upper.py -- classify_by_upper function.

Covers grouping by UP主 name, existing folder detection, exact match rules,
and edge cases.
"""

from src.classifier_upper import classify_by_upper
from src.models import ClassificationResult, FavoritedItem, Folder


class TestClassifyByUpper:
    """Test suite for classify_by_upper()."""

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_item(item_id: int, upper_name: str, **kwargs) -> FavoritedItem:
        return FavoritedItem(
            id=item_id,
            type=2,
            title=f"video_{item_id}",
            bvid=f"BV1{item_id:010d}",
            upper_name=upper_name,
            upper_mid=item_id,
            attr=0,
            fav_time=100,
            **kwargs,
        )

    @staticmethod
    def _make_folder(folder_id: int, title: str) -> Folder:
        return Folder(
            id=folder_id,
            fid=0,
            mid=100,
            attr=1,
            title=title,
            media_count=5,
        )

    # ------------------------------------------------------------------
    # grouping
    # ------------------------------------------------------------------

    def test_same_upper_grouped_together(self):
        """All items from the same UP主 share the same target folder."""
        items = [
            self._make_item(1, "张三"),
            self._make_item(2, "张三"),
        ]
        results = classify_by_upper(items, existing_folders=[])
        assert len(results) == 2
        for r in results:
            assert r.target_folder_title == "张三"
            assert r.category == "张三"

    def test_different_uppers_separate_folders(self):
        """Items from different UP主s get different target folders."""
        items = [
            self._make_item(1, "张三"),
            self._make_item(2, "李四"),
            self._make_item(3, "张三"),
            self._make_item(4, "王五"),
        ]
        results = classify_by_upper(items, existing_folders=[])
        assert len(results) == 4

        for r in results:
            assert r.category == r.item.upper_name
            assert r.target_folder_title == r.item.upper_name

    # ------------------------------------------------------------------
    # existing folder detection
    # ------------------------------------------------------------------

    def test_existing_folder_marked_true(self):
        """target_folder_exists=True when a folder with the same title exists."""
        items = [self._make_item(1, "张三"), self._make_item(2, "李四")]
        folders = [self._make_folder(10, "张三")]
        results = classify_by_upper(items, folders)
        by_id = {r.item.id: r for r in results}

        assert by_id[1].target_folder_exists is True   # "张三" folder exists
        assert by_id[2].target_folder_exists is False  # "李四" folder missing

    def test_no_existing_folders_all_false(self):
        """target_folder_exists is False for all when no folders exist."""
        items = [self._make_item(1, "张三"), self._make_item(2, "李四")]
        results = classify_by_upper(items, existing_folders=[])
        assert all(r.target_folder_exists is False for r in results)

    def test_all_existing_folders_all_true(self):
        """target_folder_exists is True for all when every UP主 has a folder."""
        items = [
            self._make_item(1, "张三"),
            self._make_item(2, "李四"),
            self._make_item(3, "王五"),
        ]
        folders = [
            self._make_folder(10, "张三"),
            self._make_folder(20, "李四"),
            self._make_folder(30, "王五"),
        ]
        results = classify_by_upper(items, folders)
        assert all(r.target_folder_exists for r in results)

    # ------------------------------------------------------------------
    # exact match rules
    # ------------------------------------------------------------------

    def test_exact_match_name_no_prefix(self):
        """Folder title must match upper.name exactly; 'UP主-' prefix does not match."""
        items = [self._make_item(1, "张三")]
        folders = [self._make_folder(10, "UP主-张三")]
        results = classify_by_upper(items, folders)

        assert results[0].target_folder_exists is False
        assert results[0].target_folder_title == "张三"

    def test_name_variants_not_merged(self):
        """Different name variants of the same UP主 are treated separately."""
        items = [
            self._make_item(1, "张三"),
            self._make_item(2, "张三_Official"),
            self._make_item(3, "张三_official"),
        ]
        results = classify_by_upper(items, [])
        titles = {r.target_folder_title for r in results}
        assert titles == {"张三", "张三_Official", "张三_official"}

    # ------------------------------------------------------------------
    # edge cases
    # ------------------------------------------------------------------

    def test_empty_items_returns_empty_list(self):
        results = classify_by_upper([], existing_folders=[])
        assert results == []

    def test_empty_string_upper_name(self):
        """An item with an empty upper_name should still produce a result."""
        items = [self._make_item(1, "")]
        results = classify_by_upper(items, [])
        assert len(results) == 1
        assert results[0].target_folder_title == ""

    def test_items_mixed_validity_all_classified(self):
        """Both valid and invalid items are still classified."""
        items = [
            self._make_item(1, "张三"),
            FavoritedItem(
                id=2,
                type=2,
                title="deleted",
                bvid="BV1de00000000",
                upper_name="张三",
                upper_mid=1,
                attr=1,  # deleted
                fav_time=100,
            ),
        ]
        results = classify_by_upper(items, [])
        assert len(results) == 2
        assert all(r.category == "张三" for r in results)

    def test_return_type_is_classification_result(self):
        """Every result is a ClassificationResult instance."""
        items = [self._make_item(1, "张三")]
        results = classify_by_upper(items, [])
        assert all(isinstance(r, ClassificationResult) for r in results)
