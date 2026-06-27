"""Preference configuration schema and loader for watch-later-recommender.

Reads user content preferences from a YAML config file at
``~/.bili-helper/.watch-later-prefs.yaml`` and validates with Pydantic.
"""

from pathlib import Path

import yaml

from watch_later_recommender.models import CategoryPref, PrefsConfig

DEFAULT_PREFS_PATH = Path.home() / ".bili-helper" / ".watch-later-prefs.yaml"

PREF_TEMPLATE = """# 稍后再看智能推荐 - 内容偏好配置
# B站分区 ID 参考: https://api.bilibili.com/x/web-interface/ranking/v2?rid=
categories:
  - name: "技术"        # 偏好分类名称
    tids: [36, 188]     # B站分区ID: 知识(36), 数码(188)
    keywords: []        # 可选关键词
  - name: "生活"
    tids: [160]
    keywords: []
exclude_categories:
  - name: "游戏"
    tids: [4]
surprise_ratio: 0.2      # 惊喜内容比例 (0.0-0.5)
max_duration: 1800       # 最大视频时长(秒), 可选
"""


def load_prefs(path: Path | None = None) -> PrefsConfig:
    """Load and validate user preference config from YAML file.

    Args:
        path: Path to config file. Defaults to ``DEFAULT_PREFS_PATH``.

    Returns:
        Validated ``PrefsConfig`` instance.

    Raises:
        FileNotFoundError: Config file does not exist. Prints template to stderr.
        ValueError: YAML content fails Pydantic validation.
        yaml.YAMLError: YAML is malformed.
    """
    if path is None:
        path = DEFAULT_PREFS_PATH

    if not path.exists():
        print(PREF_TEMPLATE)
        raise FileNotFoundError(
            f"未找到偏好配置文件，已输出模板到终端。请创建 {path}"
        )

    raw = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"偏好配置格式错误: {e}") from e

    try:
        return PrefsConfig.model_validate(data)
    except Exception as e:
        raise ValueError(f"偏好配置校验失败: {e}") from e


def init_prefs(path: Path | None = None) -> Path:
    """Create a default preference config file from ``PREF_TEMPLATE``.

    Does NOT overwrite an existing file (idempotent).

    Args:
        path: Output path. Defaults to ``DEFAULT_PREFS_PATH``.

    Returns:
        The path to the created (or existing) config file.
    """
    if path is None:
        path = DEFAULT_PREFS_PATH

    if path.exists():
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PREF_TEMPLATE, encoding="utf-8")
    return path
