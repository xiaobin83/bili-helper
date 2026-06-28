# watch-later-recommender: 收藏夹目标支持

**日期**: 2026-06-28
**状态**: 设计已批准

## 概述

为 watch-later-recommender 添加收藏夹支持，让用户能选择将推荐视频放入稍后再看或指定收藏夹。
当用户提供临时主题（`--topic`）时，将其作为高权重偏好注入 LLM Prompt，使推荐更聚焦。

## CLI 参数变更

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `--target` | `str` | `toview` | 目标位置：`toview`（稍后再看）/ `fav`（收藏夹） |
| `--topic` | `str` | `""` | 临时偏好主题，注入 LLM Prompt 并放大权重 |

## 流程变更

```
用户: uv run watch-later-recommender --target fav --topic "拳击" --count 5

Phase 1-3: 候选获取 (不变)
  → 热门(50) + 排行榜(100) + 推荐(14)

Phase 4: 去重过滤 (不变)

Phase 4.5: 获取收藏夹列表 ★新增
  → GET /x/v3/fav/folder/created/list-all?up_mid={mid}
  → 解析为 Folder 列表 (id, title, media_count)

Phase 5: 构建 LLM Prompt ★扩展
  → 原有: 偏好 + 候选列表
  → 新增: 收藏夹列表 + 临时主题(高权重)
  → LLM 输出: bvids + reasons + target_action + target_folder

Phase 6: 执行 ★扩展
  → target=toview: 调用 add_to_toview() (不变)
  → target=fav: 调用 add_to_fav_folder() / create_fav_folder()
```

## LLM Prompt 变更

当前 Prompt 仅包含候选视频列表。新增两个上下文块：

### 1. 用户收藏夹

```
## 用户收藏夹
用户有以下收藏夹（名称 | 现有视频数）：
- 格斗 (15)
- 技术 (8)
- 生活 (12)
- AI学习 (5)
- ...
```

### 2. 临时偏好（当 `--topic` 提供时）

```
## 本次推荐主题
用户本次特别关注: "拳击"
请优先从候选视频中筛选与"拳击"相关的内容。
```

### LLM 输出格式扩展

```json
{
  "bvids": ["BV...", "BV...", "BV...", "BV...", "BV..."],
  "reasons": ["理由1", "理由2", "理由3", "理由4", "理由5"],
  "surprise_count": 0,
  "target_action": "add_to_existing" | "create_new",
  "target_folder": "格斗精选",
  "folder_description": "精选格斗教学和高光比赛"
}
```

| 字段 | 说明 |
|------|------|
| `target_action` | `add_to_existing` = 加入已有收藏夹 / `create_new` = 新建 |
| `target_folder` | 已有收藏夹名称 或 新建收藏夹名称（2-6 中文字） |
| `folder_description` | 新建时使用的收藏夹简介 |

## API 新增

### BiliAPIClient 新增方法

```python
async def list_fav_folders(self, up_mid: int) -> list[Folder]
  → GET /x/v3/fav/folder/created/list-all?up_mid={up_mid}
  → 需要 Wbi 签名
  → 返回用户的所有收藏夹

async def add_to_fav_folder(self, aid: int, add_media_ids: list[int]) -> dict
  → POST /x/v3/fav/resource/add
  → data: {resources: "{aid}:2", add_media_ids: "{media_id}"}
  → 需要 CSRF (bili_jct)

async def create_fav_folder(self, name: str, intro: str = "", privacy: int = 0) -> dict
  → POST /x/v3/fav/folder/add
  → data: {title: name, intro: intro, privacy: privacy}
  → 返回新文件夹的 media_id
```

### 鉴权说明

收藏夹写操作（add/create）需要登录态。`list_fav_folders` 需要 Wbi 签名。

已有 `BiliAPIClient` 自带 `BiliHTTPClient`，需额外引入 `sign_params` 为 GET 请求签名。

## Fallback 逻辑（无 LLM 场景）

当前 CLI 模式下（`fallback_selection`）按播放量排序。当 `--target fav` 时：

1. 统计推荐视频的分区分布（按 tid 聚合）
2. 匹配用户偏好配置中的分类名称
3. 从已有收藏夹中查找名称最匹配的（如格斗类视频多 → 找含"格斗/拳击/MMA"的收藏夹）
4. 无匹配 → 以偏好分类名 + "精选" 为名新建

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `models.py` | 新增 `Folder` 模型；`RecommendationResult` 扩展 `target_action`, `target_folder`, `folder_description` |
| `api_client.py` | 新增 `list_fav_folders()`, `add_to_fav_folder()`, `create_fav_folder()`；引入 Wbi 签名 |
| `recommender.py` | `fetch_candidates()` 后新增获取收藏夹流程；`build_llm_prompt()` 扩展参数；`add_recommendations()` 扩展支持 fav；新增 `fallback_fav_selection()` |
| `main.py` | 新增 `--target`, `--topic` 参数；流程控制分发到 toview/fav 路径 |

## 不变的部分

- 候选视频获取逻辑（热门/排行/推荐）
- 去重、广告过滤逻辑
- `--dry-run` 模式
- `--count` 参数
- `--init-prefs` 初始化偏好
- 游客模式降级（无登录时跳过）
