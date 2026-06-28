---
name: watch-later-recommender
description: B站（Bilibili）视频智能推荐工具。从热门榜、排行榜和个性化推荐中精选视频，基于用户偏好自动加入稍后再看列表或收藏夹。当用户提到稍后再看推荐、帮我推荐视频、智能推荐稍后再看、B站视频推荐、找值得看的视频、watch later推荐、智能推荐、推荐几个视频时触发。
---

# watch-later-recommender — B站 智能推荐

从全站热门、分区排行榜和个性化推荐中获取候选视频，依据用户偏好配置文件精选视频，自动添加到稍后再看列表或收藏夹。支持 LLM 智能推荐，也支持无 LLM 时的按播放量降序回退。`--target fav` 将推荐结果存入收藏夹（LLM 或回退逻辑自动选择/创建文件夹），`--topic` 支持按主题精筛。

## 使用方式

### 环境准备

首次使用需获取 B站 Cookie：

```bash
export BILI_SESSDATA="..."
export BILI_BILI_JCT="..."
export BILI_BUVID3="..."  # 可选
```

凭证优先级：`--auth-file` 参数 > 环境变量 > `./.auth.json` (CWD) > `~/.bili-helper/auth.json` > 二维码登录。

凭证获取：浏览器 DevTools (F12) → Application → Cookies → `.bilibili.com`

### CLI 命令

```bash
# 完整流程：获取候选 → LLM 精选 → 添加到稍后再看
uv run watch-later-recommender

# 推荐到收藏夹（LLM 自动选择/创建收藏夹）
uv run watch-later-recommender --target fav

# 推荐特定主题到收藏夹
uv run watch-later-recommender --target fav --topic "编程教程"

# 干跑模式：只看推荐结果，不实际添加
uv run watch-later-recommender --dry-run

# 干跑模式预览收藏夹推荐
uv run watch-later-recommender --dry-run --target fav --topic "健身"

# 首次使用：生成偏好配置文件模板
uv run watch-later-recommender --init-prefs

# 使用自定义偏好配置文件
uv run watch-later-recommender --prefs /path/to/my-prefs.yaml

# 自定义推荐数量（最多 10 个）
uv run watch-later-recommender --count 3
```

### 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `--dry-run` | `flag` | 只推荐不实际添加到稍后再看 |
| `--init-prefs` | `flag` | 初始化偏好配置文件（创建模板） |
| `--prefs` | `str` | 自定义偏好配置文件路径（默认 `~/.bili-helper/.watch-later-prefs.yaml`） |
| `--count` | `int` | 推荐视频数量（默认 5，最大 10） |
| `--target` | `str` | 推荐目标：`toview`（稍后再看，默认）/ `fav`（收藏夹） |
| `--topic` | `str` | 推荐主题关键词（仅 `--target fav` 时生效） |
| `--auth-file` | `str` | 自定义凭证文件路径 |
| `--env-prefix` | `str` | 环境变量前缀（默认 `BILI_`） |

### 偏好配置指南

偏好配置文件为 YAML 格式，位于 `~/.bili-helper/.watch-later-prefs.yaml`。首次使用请先运行 `--init-prefs` 生成模板。

```yaml
# 稍后再看智能推荐 - 内容偏好配置
# B站分区 ID 参考: https://api.bilibili.com/x/web-interface/ranking/v2?rid=
categories:
  - name: "技术"
    tids: [36, 188]     # B站分区ID: 知识(36), 数码(188)
    keywords: []
  - name: "生活"
    tids: [160]
    keywords: []
exclude_categories:
  - name: "游戏"
    tids: [4]
surprise_ratio: 0.2      # 惊喜内容比例 (0.0-0.5)
max_duration: 1800       # 最大视频时长(秒), 可选
```

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `categories` | 数组 | 偏好分类列表，每个分类包含名称 `name`、分区 ID 列表 `tids`、可选关键词 `keywords` |
| `exclude_categories` | 数组 | 排除分类列表，结构同 `categories` |
| `surprise_ratio` | float | 惊喜内容比例，推荐偏好分区之外的视频，范围 0.0-0.5 |
| `max_duration` | int | 最大视频时长（秒），可选 |

B站分区 ID 速查：知识(36)、数码(188)、生活(160)、游戏(4)、科技(76)、搞笑(138)、影视(181)、动画(1)、音乐(3)、舞蹈(129)、鬼畜(119)、时尚(155)、娱乐(5)。

## 鉴权处理

凭证来源与优先级：`--auth-file` 参数 > 环境变量 `BILI_*` > `./.auth.json` (CWD) > `~/.bili-helper/auth.json` > 二维码登录。

```bash
# 推荐方式（与项目其他工具共享）
export BILI_SESSDATA="..."
export BILI_BILI_JCT="..."
export BILI_BUVID3="..."  # 可选
```

未登录时会自动降级为**游客模式**：
- 跳过个性化推荐源（`index/top/rcmd`）
- 不获取稍后再看列表（不去重）
- 不执行添加操作
- 仅输出候选视频和推荐结果

> **环境变量前缀**：默认 `BILI_*`，可通过 `--env-prefix` 自定义。例如 `--env-prefix MYAPP_` 会读取 `MYAPP_SESSDATA`。

## 工作流

### 场景 1：有偏好配置 + 已登录（完整流程）

```bash
# 1. 初始化偏好配置（仅首次）
uv run watch-later-recommender --init-prefs
# 编辑 ~/.bili-helper/.watch-later-prefs.yaml

# 2. 先干跑预览推荐结果
uv run watch-later-recommender --dry-run

# 3. 确认后实际执行
uv run watch-later-recommender
```

完整流程步骤：

```
┌─────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐   ┌──────────────────┐
│  Phase 1 │   │  Phase 2 │   │  Phase 3 │   │  Phase 4 │   │  Phase 5  │   │    Phase 6       │
│ 热门榜   │ → │ 排行榜   │ → │ 个性化推 │ → │ 去重+过滤 │ → │ LLM 精选  │ → │ 添加到目标       │
│  (x50)   │   │  (x100)  │   │ 荐 (x14) │   │ 广告+已存 │   │ 5 个视频  │   │ toview 或 收藏夹  │
└─────────┘   └──────────┘   └──────────┘   └──────────┘   └───────────┘   └──────────────────┘
```

| 阶段 | 数据源 | API 端点 | 说明 |
|------|--------|----------|------|
| Phase 1 | 全站热门 | `x/web-interface/popular` | 获取前 50 个热门视频，无需登录 |
| Phase 2 | 全站排行榜 | `x/web-interface/ranking/v2` | 获取前 100 个排行视频，无需登录 |
| Phase 3 | 首页个性化推荐 | `x/web-interface/index/top/rcmd` | 获取个性化推荐，需要登录 |
| Phase 4 | 去重+过滤 | — | 去重、过滤广告、排除已在稍后再看的视频，剩余最多 20 个候选。`--target fav` 时跳过与已收藏视频的去重 |
| Phase 5 | LLM 精选 | — | 根据用户偏好从候选池中精选，生成推荐理由。`--target fav` 时 prompt 附带收藏夹列表和主题信息 |
| Phase 6 | 添加到目标 | `x/v2/history/toview/add` / `x/v3/fav/resource/deal` | `--target toview`：添加到稍后再看，检查容量上限（100 个）。`--target fav`：添加到收藏夹，自动选择或新建文件夹 |

### 场景 2：首次使用（初始化偏好配置）

```bash
# 生成偏好配置模板
uv run watch-later-recommender --init-prefs
```

生成 `~/.bili-helper/.watch-later-prefs.yaml` 后，编辑该文件设置你的内容偏好。

### 场景 3：游客模式（无登录，仅推荐不添加）

不设置任何 Cookie 环境变量，或登录失败时自动降级：

```bash
uv run watch-later-recommender --dry-run
```

游客模式下：
- 跳过个性化推荐源（Phase 3）
- 跳过稍后再看去重（Phase 4 的已存在过滤）
- 不执行添加（Phase 6）

### LLM 推荐流程

此工具生成 LLM prompt 后，由 Agent 调用 LLM 进行推荐。LLM prompt 包含：

1. 用户偏好描述（偏好分类、排除分类、惊喜比例、最大时长）
2. **收藏夹列表**（仅 `--target fav` 时，列出用户收藏夹，供 LLM 选择目标文件夹）
3. **主题描述**（仅 `--topic` 时，告知 LLM 优先推荐主题相关的视频）
4. 候选视频列表（最多 20 个，含标题、分区、UP主、播放量、点赞数、时长）
5. JSON 输出格式要求：
   - `--target toview`：`bvids`、`reasons`、`surprise_count`
   - `--target fav`：额外包含 `target_action`（`add_to_existing` / `create_new`）、`target_folder`、`folder_description`

LLM 输出通过 `parse_llm_result()` 解析和校验：
- 提取 JSON 并验证结构
- 校验所有 bvid 是否在候选池中
- 校验数量一致
- （`--target fav`）校验 `target_action` 和 `target_folder`

若 LLM 调用失败，自动回退：
- `--target toview`：按播放量排序选择热门视频
- `--target fav`：按播放量排序 → 统计分区分布 → 匹配偏好分类 → 选择或新建收藏夹

## 错误处理

| 退出码 | 含义 | 处理建议 |
|--------|------|---------|
| 0 | 执行成功 | — |
| 1 | 运行错误（未获取到候选视频、登录过期） | 检查网络和登录状态 |
| 2 | 请求频率过高（限流） | 等待片刻后重试 |
| 3 | 未找到偏好配置文件 | 先运行 `--init-prefs` 生成模板 |
| 4 | 稍后再看列表空间不足（≥95/100，仅 `--target toview`） | 先清理稍后再看列表再重试 |

| B站 API 错误码 | 含义 | 处理建议 |
|----------------|------|---------|
| -101 | 登录已过期 | 重新获取 SESSDATA |
| -111 | CSRF 校验失败 | 更新 bili_jct |
| 90001 | 稍后再看列表已满 | 清理稍后再看后重试 |
| 90003 | 视频已删除 | 忽略该视频，继续添加其他 |
| HTTP 412/429 | 请求频率过高 | 等待后重试（内置指数退避，最多 3 次） |
| 网络错误 | 连接失败 | 自动重试 3 次，仍失败则报错 |

## 限制

- 稍后再看列表最大容量 100 个（B站 API 限制），超过 95 个时不允许新增
- 单次最多推荐 10 个视频（B站 API 限制）
- 传递至 LLM 的候选池上限为 20 个（去重过滤后按播放量排序取前 20）
- 数据源请求间隔为 2 秒（内置限流，避免触发风控）
- 个性化推荐（`index/top/rcmd`）需要登录，无需登录时跳过该源
- 仅支持视频类型，不处理专栏、直播等其他内容
- LLM 推荐为建议性质，Agent 可使用 fallback 策略（按播放量排序）确保可靠性

## 依赖安装

```bash
cd watch-later-recommender && uv sync
```

## 与项目其他工具共享凭证

watch-later-recommender 与 fav-organizer、dyn-publisher、video-analyzer 共享 B站 凭证。使用统一的环境变量前缀 `BILI_*`。

```bash
# 通用方式
export BILI_SESSDATA="..."
export BILI_BILI_JCT="..."
export BILI_BUVID3="..."
```

也可通过 `--auth-file` 参数指定单独的凭证文件，或使用 `--env-prefix` 使用不同的环境变量前缀隔离凭证。
