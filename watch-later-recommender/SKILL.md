---
name: watch-later-recommender
description: B站（Bilibili）视频智能推荐工具。支持推荐到稍后再看列表或收藏夹。基于热门/排行榜/个性化推荐筛选，LLM 精选后自动添加。当用户提到稍后再看推荐、帮我推荐视频、收藏夹推荐、B站视频推荐、找值得看的视频、watch later推荐、智能推荐、推荐几个视频、放入收藏夹时触发。
---

# watch-later-recommender — B站 智能推荐

从全站热门、分区排行榜和个性化推荐中获取候选视频，依据用户偏好配置文件，通过 LLM 智能精选视频，自动添加到稍后再看列表或收藏夹。

**两阶段工作流**：
1. **生成 prompt**：工具采集候选视频 → 构建 LLM prompt → 打印到 stdout
2. **应用 LLM 结果**：Agent 将 prompt 发送给 LLM → 将 LLM 返回结果通过 `--apply-llm-result` 传回工具执行添加

**核心能力**：
- 推荐到稍后再看列表（默认）
- 推荐到收藏夹（LLM 自动选择已有收藏夹或新建）
- `--topic "关键词"`：用 B站 搜索替代热门/排行源，获取主题相关视频作为候选池
- `--folder-name "名称"`：覆盖 LLM 选择的收藏夹

## Agent 使用指南（最重要！）

当用户请求推荐视频时，Agent 必须按以下两阶段流程执行：

### 阶段 1：生成 LLM prompt

```bash
# 推荐到稍后再看（默认）
uv run watch-later-recommender --count 5

# 推荐到收藏夹
uv run watch-later-recommender --target fav --count 5

# 按主题搜索 + 推荐到收藏夹
uv run watch-later-recommender --target fav --topic "AI Agent" --count 5
```

工具会输出一个 LLM prompt（包含用户偏好、候选视频列表、输出格式要求）到 stdout。**Agent 必须读取此 prompt**。

### 阶段 2：调用 LLM 并应用结果

1. **Agent 将 prompt 发送给 LLM**（通过 `task()` 或直接调用），不添加额外指令。
2. LLM 返回的结果包含两部分：
   - **人性化推荐总结**（文本）：给用户阅读
   - **结构化 JSON**（在 ` ```json ` 代码块中）：给工具解析
3. **Agent 将 LLM 返回的完整文本保存到临时文件**，然后执行：

```bash
# 应用 LLM 结果（--target 不需要，工具自动从 JSON 推断）
uv run watch-later-recommender --apply-llm-result /tmp/llm-output.json

# 如果想预览但不实际执行
uv run watch-later-recommender --apply-llm-result /tmp/llm-output.json --dry-run

# 覆盖 LLM 选择的收藏夹
uv run watch-later-recommender --apply-llm-result /tmp/llm-output.json --folder-name "感兴趣"
```

> **关键**：阶段 2 不需要传 `--target`、`--topic` 或 `--count`——这些信息已编码在 LLM 返回的 JSON 中，工具自动推断。

### LLM 返回的 JSON 格式

LLM prompt 会指导 LLM 输出如下格式：

**稍后再看 (toview)**：
```json
{
  "bvids": ["BV...", "BV..."],
  "reasons": ["推荐理由1", "推荐理由2"],
  "surprise_count": 0
}
```

**收藏夹 (fav)**：
```json
{
  "bvids": ["BV...", "BV..."],
  "reasons": ["推荐理由1", "推荐理由2"],
  "surprise_count": 1,
  "target_action": "add_to_existing",
  "target_folder": "AI Agent",
  "folder_description": ""
}
```

工具通过 `target_action` 字段自动推断目标：
- `"add_to_existing"` 或 `"create_new"` → 添加到收藏夹
- 其他 / 不存在 → 添加到稍后再看

### 完整示例：Agent 执行流程

```python
# 用户说：推荐5个AI Agent开发相关的视频，放入AI Agent收藏夹

# Step 1: 生成 prompt
output = bash("uv run watch-later-recommender --target fav --topic 'AI Agent' --count 5")
prompt = extract_prompt_from_output(output)

# Step 2: 调用 LLM（注意：不添加额外指令，prompt 已自包含）
llm_response = task(category="ultrabrain", prompt=prompt)

# Step 3: 保存 LLM 返回的完整文本（含总结和 JSON）
write("/tmp/llm-output.json", llm_response)

# Step 4: 应用结果（不需要 --target fav，工具自动推断）
result = bash("uv run watch-later-recommender --apply-llm-result /tmp/llm-output.json")

# Step 5: 将人性化总结和添加结果展示给用户
```

## 使用方式

### 环境准备

首次使用需获取 B站 Cookie：

```bash
export BILI_SESSDATA="..."
export BILI_BILI_JCT="..."
export BILI_BUVID3="..."  # 可选
```

凭证优先级：`--auth-file` 参数 > 环境变量 > `./.auth.json` (CWD) > `~/.bili-helper/auth.json` > 二维码登录。

### CLI 命令

```bash
# === 阶段 1：生成 prompt（Agent 读取后发送给 LLM） ===

# 推荐到稍后再看
uv run watch-later-recommender --count 5

# 推荐到收藏夹
uv run watch-later-recommender --target fav --count 5

# 按主题搜索推荐
uv run watch-later-recommender --target fav --topic "AI Agent" --count 5

# === 阶段 2：应用 LLM 结果 ===

# 从文件读取 LLM 结果并执行添加
uv run watch-later-recommender --apply-llm-result llm-output.json

# 干跑预览（不实际添加）
uv run watch-later-recommender --apply-llm-result llm-output.json --dry-run

# 覆盖 LLM 选择的收藏夹
uv run watch-later-recommender --apply-llm-result llm-output.json --folder-name "感兴趣"

# 从 stdin 读取
cat llm-output.json | uv run watch-later-recommender --apply-llm-result -

# === 其他 ===

# 生成偏好配置文件模板
uv run watch-later-recommender --init-prefs

# 使用自定义偏好配置
uv run watch-later-recommender --prefs /path/to/my-prefs.yaml --count 3

# 自定义推荐数量（最大 10）
uv run watch-later-recommender --count 8
```

### 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `--target` | `str` | 推荐目标（**仅阶段 1 生成 prompt 时需要**）：`toview`（稍后再看，默认）/ `fav`（收藏夹）。阶段 2 无需指定，由 LLM 结果自动推断 |
| `--topic` | `str` | 推荐主题关键词，使用搜索获取相关视频作为候选池（**仅阶段 1 需要**） |
| `--count` | `int` | 推荐视频数量（默认 5，最大 10，**仅阶段 1 需要**） |
| `--apply-llm-result` | `str` | **应用 LLM 推荐结果**（阶段 2）：JSON 文件路径、`-` 从 stdin 读取、或 JSON 字符串 |
| `--folder-name` | `str` | 覆盖 LLM 选择的收藏夹名称（仅阶段 2 生效） |
| `--dry-run` | `flag` | 阶段 2 中预览 LLM 推荐结果，不实际添加 |
| `--init-prefs` | `flag` | 初始化偏好配置文件（创建模板） |
| `--prefs` | `str` | 自定义偏好配置文件路径（默认 `~/.bili-helper/.watch-later-prefs.yaml`） |
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
- 不执行添加操作（包括 `--apply-llm-result`）
- 仅输出候选视频和 prompt

> **环境变量前缀**：默认 `BILI_*`，可通过 `--env-prefix` 自定义。

## 内部流水线

工具内部的 6 个 Phase（在两次 CLI 调用中执行）：

```
Phase 1-4 (数据采集)        Phase 5 (LLM 精选)           Phase 6 (执行添加)
┌─────────────────────┐     ┌─────────────────┐     ┌──────────────────────┐
│ 热门榜(x50)          │     │                 │     │                      │
│ 排行榜(x100)   去重  │ ──→ │ 工具生成 prompt  │ ──→ │ Agent 调用 LLM       │
│ 个性化推荐(x14) 过滤  │     │ 打印到 stdout    │     │ 获取推荐结果          │
│ → 候选池(≤20)        │     │                 │     │                      │
└─────────────────────┘     └─────────────────┘     └──────────┬───────────┘
                                                               │
                                          ┌────────────────────┘
                                          ▼
                                   ┌──────────────────────┐
                                   │ --apply-llm-result   │
                                   │ 解析 JSON            │
                                   │ 校验 bvid            │
                                   │ 执行添加             │
                                   └──────────────────────┘
```

| 阶段 | 数据源 | API 端点 | CLI 调用 |
|------|--------|----------|----------|
| Phase 1 | 全站热门 | `x/web-interface/popular` | 第一次 |
| Phase 2 | 全站排行榜 | `x/web-interface/ranking/v2` | 第一次 |
| Phase 3 | 个性化推荐 | `x/web-interface/index/top/rcmd` | 第一次 |
| Phase 4 | 去重+过滤 | — | 第一次 |
| Phase 5 | LLM 精选 | **Agent 调用 LLM** | Agent 负责 |
| Phase 6 | 添加 | `x/v2/history/toview/add` 或 `x/v3/fav/resource/add` | 第二次 (`--apply-llm-result`) |

### LLM Prompt 内容

工具生成的 prompt 自包含，Agent 不应添加额外指令。prompt 包含：

1. 用户偏好描述（偏好分类、排除分类、惊喜比例、最大时长）
2. 收藏夹列表（仅 `--target fav` 时，列出用户收藏夹供 LLM 选择目标文件夹）
3. 主题描述（仅 `--topic` 时）
4. 候选视频列表（最多 20 个，含标题、分区、UP主、播放量、点赞数、时长）
5. 输出格式要求（人性化总结 + ` ```json ` 代码块）

### parse_llm_result() 校验逻辑

工具通过 `parse_llm_result()` 解析 LLM 返回的 JSON：
- 优先从 ` ```json ` 代码块提取，回退到原始 `{...}` 边界
- 校验所有 bvid 是否在候选池中
- 校验 bvid 数量与 reasons 数量一致
- 若校验失败，工具报错退出（不会回退到任何自动选择逻辑）

## 错误处理

| 退出码 | 含义 | 处理建议 |
|--------|------|---------|
| 0 | 执行成功 | — |
| 1 | 运行错误（未获取到候选视频、登录过期、LLM 结果解析失败） | 检查网络、登录状态和 LLM 返回格式 |
| 2 | 请求频率过高（限流） | 等待片刻后重试 |
| 3 | 未找到偏好配置文件 | 先运行 `--init-prefs` 生成模板 |
| 4 | 稍后再看列表空间不足（≥95/100） | 先清理稍后再看列表再重试 |

| B站 API 错误码 | 含义 | 处理建议 |
|----------------|------|---------|
| -101 | 登录已过期 | 重新获取 SESSDATA |
| -111 | CSRF 校验失败 | 更新 bili_jct |
| 90001 | 稍后再看列表已满 | 清理稍后再看后重试 |
| 90003 | 视频已删除 | 忽略该视频，继续添加其他 |
| HTTP 412/429 | 请求频率过高 | 等待后重试（内置指数退避，最多 3 次） |
| 网络错误 | 连接失败 | 自动重试 3 次，仍失败则报错 |

### 收藏夹操作错误场景

| 错误场景 | 表现 | 解决办法 |
|----------|------|---------|
| 未登录 | 跳过添加操作，仅输出 prompt | 先配置 B站 Cookie |
| 创建收藏夹失败 | API 返回非 0 code | 检查收藏夹数量上限（B站限制约 100 个） |
| 添加到收藏夹失败 | 单个视频添加返回错误 | 跳过该视频，继续添加其余视频 |
| 目标收藏夹不存在 | add_to_existing 但未找到同名文件夹 | 检查名称，或用 `--folder-name` 指定正确名称 |

## 限制

- 稍后再看列表最大容量 100 个（B站 API 限制），超过 95 个时不允许新增
- 单次最多推荐 10 个视频（B站 API 限制）
- 传递至 LLM 的候选池上限为 20 个（去重过滤后按播放量排序取前 20）
- 数据源请求间隔为 2 秒（内置限流，避免触发风控）
- 个性化推荐（`index/top/rcmd`）需要登录，无需登录时跳过该源
- 搜索候选（`--topic`）需要登录态，未登录时不可用
- 仅支持视频类型，不处理专栏、直播等其他内容
- 所有推荐决策均由 LLM 做出，工具只负责数据采集和执行添加

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
