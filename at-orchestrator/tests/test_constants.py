"""Tests for at_orchestrator.constants and mapping tables in models.py.

Verifies that all magic numbers are replaced by named constants and
all mapping tables are complete and correct.
"""

from __future__ import annotations

import math

from at_orchestrator.constants import (
    BUSINESS_ID_TO_TYPE,
    DB_PATH_DEFAULT,
    MAX_COMMENT_CHARS,
    MAX_PM_CJK_CHARS,
    SKILL_CLI_MAP,
    SUBPROCESS_TIMEOUT,
    WORKSPACE_ROOT,
)


class TestBusinessIdToType:
    """BUSINESS_ID_TO_TYPE mapping — covers all known business IDs."""

    def test_video_maps_to_type_1(self) -> None:
        assert BUSINESS_ID_TO_TYPE[1] == 1

    def test_dynamic_maps_to_type_17(self) -> None:
        assert BUSINESS_ID_TO_TYPE[11] == 17

    def test_article_maps_to_type_17(self) -> None:
        assert BUSINESS_ID_TO_TYPE[17] == 17

    def test_all_known_business_ids_covered(self) -> None:
        expected = {1, 11, 17}
        assert set(BUSINESS_ID_TO_TYPE.keys()) == expected

    def test_unknown_business_id_not_present(self) -> None:
        assert 99 not in BUSINESS_ID_TO_TYPE


class TestSkillCliMap:
    """SKILL_CLI_MAP — covers all 4 registered skills with correct CLI config."""

    def test_all_four_skills_present(self) -> None:
        assert set(SKILL_CLI_MAP.keys()) == {
            "video-analyzer",
            "watch-later-recommender",
            "dyn-publisher",
            "fav-organizer",
        }

    def test_video_analyzer_config(self) -> None:
        cfg = SKILL_CLI_MAP["video-analyzer"]
        assert cfg["command"] == "video-analyzer"
        assert cfg["output_flag"] == "--output"

    def test_watch_later_config(self) -> None:
        cfg = SKILL_CLI_MAP["watch-later-recommender"]
        assert cfg["command"] == "watch-later-recommender"
        assert "subcommand" not in cfg
        assert "output_flag" not in cfg

    def test_dyn_publisher_config(self) -> None:
        cfg = SKILL_CLI_MAP["dyn-publisher"]
        assert cfg["command"] == "dyn-publisher"
        assert cfg["subcommand"] == "publish"

    def test_fav_organizer_config(self) -> None:
        cfg = SKILL_CLI_MAP["fav-organizer"]
        assert cfg["command"] == "fav-organizer"
        assert cfg["subcommand"] == "classify"


class TestNumericConstants:
    """Numeric constants — bounds and sanity checks."""

    def test_max_comment_chars(self) -> None:
        assert MAX_COMMENT_CHARS == 1000

    def test_max_pm_cjk_chars_within_byte_budget(self) -> None:
        # B站 PM API has a ~2000 byte limit; CJK characters are 3 bytes
        # in UTF-8, so floor(2000/3) = 666 is the safe upper bound.
        assert MAX_PM_CJK_CHARS <= math.floor(2000 / 3)

    def test_max_pm_cjk_chars_value(self) -> None:
        assert MAX_PM_CJK_CHARS == 600

    def test_subprocess_timeout(self) -> None:
        assert SUBPROCESS_TIMEOUT == 120

    def test_db_path_default(self) -> None:
        assert DB_PATH_DEFAULT == ".at-orchestrator/tasks.db"

    def test_workplace_root_is_string(self) -> None:
        assert isinstance(WORKSPACE_ROOT, str)
        assert len(WORKSPACE_ROOT) > 0
