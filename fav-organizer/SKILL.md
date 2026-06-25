---
name: fav-organizer
description: B站（Bilibili）收藏夹自动整理工具。一键扫描、清理失效内容、去重、LLM 智能分类你的收藏夹。当用户提到整理B站收藏、清理失效收藏、收藏夹分类、收藏夹去重、B站收藏管理、bilibili favorites整理时触发。
---

# fav-organizer — B站收藏夹整理

你协助用户整理 B站（Bilibili）收藏夹。工具采用三阶段管线：**classify → plan → execute**。

## 三阶段工作流

```
classify (数据采集) → 分类结果.json (Agent 填写) → plan (生成计划) → execute (执行)
```

### 阶段 1: classify — 扫描收藏夹，准备数据

```bash
# 整理指定收藏夹
uv run fav-organizer classify --folder "默认收藏夹"

# 整理所有收藏夹
uv run fav-organizer classify --all

# 清除视频缓存后重新扫描
uv run fav-organizer classify --all --clear-cache
```

**此阶段完成：**
- 鉴权（二维码/环境变量/`.auth.json`）
- 列出收藏夹内容
- 扫描失效视频（UP主删除 / 平台删除）
- 检测重复内容（默认文件夹 vs 命名文件夹）
- 获取视频元数据（简介、分区），缓存到磁盘（30 天 TTL）
- 输出 `state.json` + `classification_result.json` 模板

### 阶段 2: Agent 填写分类

阶段 1 输出 `classification_result.json`：

```json
{
  "version": "1.0",
  "classifications": [
    {"item_id": 123, "category": ""},
    {"item_id": 456, "category": ""}
  ]
}
```

**Agent 的职责：** 为每个 `item_id` 填写 `category`（2-6 个中文字），例如 `"编程"`、`"游戏攻略"`。

Agent 可以使用 `src/classifier_llm.py` 中的 `build_classification_prompt()` 为每个 item 生成中文分类提示词（包含标题、简介、已有文件夹），调用 LLM 决策后调用 `validate_category()` 验证结果。

### 阶段 3: plan — 生成整理计划

```bash
# 使用默认分类结果文件
uv run fav-organizer plan

# 指定外部分类结果文件
uv run fav-organizer plan --classification my_result.json
```

**此阶段完成：**
- 读取 `state.json` 和 `classification_result.json`
- 合并分类结果，生成 `OrganizePlan`
- 输出 `plan.json` + Markdown 预览
- 用户可反复修改 `classification_result.json` 后重新 `plan`

### 阶段 4: execute — 执行整理

```bash
# 执行默认计划
uv run fav-organizer execute

# 执行指定计划
uv run fav-organizer execute --plan my_plan.json
```

**执行顺序：** 创建文件夹 → 移动内容（每批 ≤30）→ 删除失效/重复内容。单批失败不影响后续。

## 中间文件格式

所有中间数据存储在 `.fav-organizer/` 目录（gitignored）：

| 文件 | 产生于 | 消费于 | 说明 |
|------|--------|--------|------|
| `state.json` | `classify` | `plan` | 完整扫描状态 |
| `classification_result.json` | Agent 填写 | `plan` | LLM 分类结果 |
| `plan.json` | `plan` | `execute` | 可执行计划 |
| `video_cache.json` | `classify` | `classify` | 视频信息缓存（30 天 TTL） |

## 鉴权处理

凭证优先级：`.auth.json`（二维码登录）> 环境变量 > 自动触发登录。

```bash
export FAV_SESSDATA="..."
export FAV_BILI_JCT="..."
export FAV_BUVID3="..."  # 可选
```

获取方式：浏览器 DevTools (F12) → Application → Cookies → `.bilibili.com`

## 错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| 鉴权过期（code=-101） | 引导用户重新登录 |
| CSRF 校验失败（code=-111） | 提示更新 bili_jct |
| 限流（HTTP 412/429） | 指数退避重试（最多 3 次） |
| 网络错误 | 重试 3 次，失败后报错 |
| API 错误 | 单批失败不影响后续 |

## 限制（v2）

- LLM 分类为唯一分类器（无分区/UP主自动归类）
- 不支持重命名/合并已有文件夹
- 不提供回滚/撤销功能
- 不操作他人创建的收藏夹
- 空文件夹仅标注，不自动删除

## 依赖安装

```bash
uv sync
```
