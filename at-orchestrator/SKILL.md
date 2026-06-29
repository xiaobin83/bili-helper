---
name: at-orchestrator
description: 处理B站@消息、B站互动编排、自动回复、B站动态响应、B站智能分类回复、bilibili消息处理、at消息编排
---

# at-orchestrator — B站 @ 消息互动编排

编排 B站 up主 收到的 @ 消息和回复通知：自动扫描、LLM 智能分类、分派到对应 skill 执行、智能路由回复。将用户的 @ 消息自动转化为 skill 调用。

**两阶段工作流**：
1. **fetch + 分类**：工具拉取新消息 → 构建 LLM 分类 prompt → 打印到 stdout
2. **处理与回复**：Agent 将 prompt 发送给 LLM → 将 LLM 返回结果通过 `--apply-llm-result` 传回工具 → 工具分派到子 skill 执行 → 智能路由回复

**核心能力**：
- 自动拉取 @ 消息和回复通知（B站 API `x/msgfeed/at` + `x/msgfeed/reply`）
- LLM 分类到 3 种 skill（video-analyzer / watch-later-recommender / unknown）
- 异步子进程分派子 skill
- 智能路由回复：短结果 → 评论回复，长结果 → 私信
- SQLite 持久化（任务状态机 + 游标分页）

## Agent 使用指南（最重要！）

当用户请求处理 B站 @ 消息时，Agent 必须按以下两阶段流程执行：

### 阶段 1：拉取消息，生成 LLM prompt

```bash
# 拉取新 @ 消息并生成 LLM 分类 prompt
uv run at-orchestrator fetch
```

工具会输出一个 LLM prompt（包含消息内容、业务上下文、分类要求）到 stdout。**Agent 必须读取此 prompt**。

### 阶段 2：调用 LLM 并应用结果

1. **Agent 读取 prompt**。工具输出的 prompt 已包含消息内容和分类要求，Agent 无需添加额外指令。
2. **Agent 将 prompt 发送给 LLM**（通过 `task()` 或直接调用），不添加其他额外指令。
3. LLM 返回 ` ```json ` 代码块中包裹的 JSON，格式如下：

```json
{
  "skill_name": "video-analyzer",
  "params": {"bvid": "BV1xx"},
  "confidence": 0.95,
  "reason": "用户明确要求分析视频"
}
```

4. **Agent 将 LLM 返回的完整文本保存到临时文件**，然后执行：

```bash
# 处理 LLM 分类结果（处理 1 条消息）
uv run at-orchestrator process --apply-llm-result /tmp/llm-output.json

# 处理多条消息
uv run at-orchestrator process --limit 5 --apply-llm-result /tmp/llm-output.json

# 干跑预览（不执行分派和回复）
uv run at-orchestrator process --limit 1 --dry-run

# 从 stdin 读取
cat /tmp/llm-output.json | uv run at-orchestrator process --apply-llm-result -
```

> **关键**：阶段 1 的 `fetch` 和阶段 2 的 `process --apply-llm-result` 可以分开执行。Agent 也可先 `process --dry-run` 预览 prompt，再调 LLM 后执行。

### LLM 返回的 JSON 格式

```json
{
  "skill_name": "video-analyzer",
  "params": {"bvid": "BV1xx"},
  "confidence": 0.95,
  "reason": "用户明确要求分析视频"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `skill_name` | `str` | 命中的 skill 名称，必须为 3 种之一 |
| `params` | `dict` | 分派给子 skill 的参数 |
| `confidence` | `float` | 置信度，范围 0.0-1.0 |
| `reason` | `str` | 分类理由（非空字符串） |

### 完整示例：Agent 执行流程

```python
# 用户说：帮我看一下B站有没有人@我

# Step 1: 拉取消息，生成 prompt
output = bash("uv run at-orchestrator fetch")
prompt = extract_prompt_from_output(output)

# Step 2: 将 prompt 发送给 LLM
llm_response = task(category="ultrabrain", prompt=prompt)

# Step 3: 保存 LLM 返回的完整文本
write("/tmp/llm-output.json", llm_response)

# Step 4: 处理分类结果（工具自动分派并回复）
result = bash("uv run at-orchestrator process --apply-llm-result /tmp/llm-output.json")

# Step 5: 将处理结果展示给用户
```

## CLI 命令

### fetch — 拉取 @ 消息

```bash
# 拉取新消息（自动游标分页 + 增量循环拉取）
uv run at-orchestrator fetch

# 只拉取回复通知，不拉取 @消息
uv run at-orchestrator fetch --source reply

# 只拉取 @消息
uv run at-orchestrator fetch --source at

# 只拉取指定日期之后的消息
uv run at-orchestrator fetch --after-date 2026-06-30
uv run at-orchestrator fetch --after-date "2026-06-30T00:00:00+08:00"
```

从 B站 API `x/msgfeed/at` 和 `x/msgfeed/reply` 拉取新的 @ 消息和回复通知，自动存储到 SQLite 数据库。

**增量拉取机制**：每次 `fetch` 会循环翻页，逐页入库。遇到以下条件之一自动停止：
- 当前页第一条消息已在库中（dedup 断点）
- API 返回 `cursor.is_end: true`（已到末尾）

| 参数 | 说明 |
|------|------|
| `--source reply\|at` | 只拉取一种来源，默认两者都拉 |
| `--after-date DATE` | 只拉取该日期之后的消息（ISO 8601，默认昨天 00:00 本地时间） |

### process — 处理消息

```bash
# 处理待处理消息（打印 prompt，等待 LLM 结果）
uv run at-orchestrator process

# 仅处理回复通知（source=reply），不处理 @消息
uv run at-orchestrator process --source reply

# 仅处理 @消息（source=at），不处理回复通知
uv run at-orchestrator process --source at

# 干跑：只打印 prompt，不分派不回复
uv run at-orchestrator process --limit 1 --dry-run

# 应用 LLM 分类结果
uv run at-orchestrator process --limit 1 --apply-llm-result result.json

# 处理多条消息
uv run at-orchestrator process --limit 5 --apply-llm-result result.json

# 从 stdin 读取 LLM 结果
cat result.json | uv run at-orchestrator process --apply-llm-result -
```

### status — 查看状态

```bash
# 查看任务状态计数
uv run at-orchestrator status
```

### reset — 重置数据库

```bash
# 重置数据库（需要 --force 确认）
uv run at-orchestrator reset --force
```

## 参数说明

### 全局参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `--db-path` | `str` | SQLite 数据库路径（默认 `~/.bili-helper/at-orchestrator.db`） |
| `--auth-file` | `str` | 自定义凭证文件路径 |
| `--env-prefix` | `str` | 环境变量前缀（默认 `BILI_`） |

### process 子命令参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `--limit` | `int` | 最多处理 N 条待处理任务（默认 1） |
| `--dry-run` | `flag` | 干跑模式：打印分类 prompt，不执行分派和回复 |
| `--source` | `str` | 按来源过滤：`"reply"`（回复通知）或 `"at"`（@消息），默认不过滤 |
| `--apply-llm-result` | `str` | **应用 LLM 分类结果**：JSON 文件路径、`-` 从 stdin 读取、或 JSON 字符串 |

### reset 子命令参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `--force` | `flag` | 确认重置数据库（必需，否则拒绝执行） |

## 分类逻辑

at-orchestrator 将 LLM 分类结果映射到 3 种 skill。LLM prompt 由工具自动生成，包含以下 2 个 few-shot 示例：

| # | 消息 | 分类结果 | 说明 |
|---|------|---------|------|
| 1 | "分析这个视频BV1xx" | `{"skill_name": "video-analyzer", "params": {"bvid": "BV1xx"}, "confidence": 0.95, "reason": "用户明确要求分析视频"}` | 视频分析 |
| 2 | "今天天气不错" | `{"skill_name": "unknown", "params": {}, "confidence": 0.95, "reason": "与B站功能无关的闲聊消息"}` | 无法匹配 |

### 可用技能

| 技能 | 适用场景 | 分派命令 |
|------|---------|---------|
| `video-analyzer` | 用户询问视频详情、分析视频内容 | `uv run video-analyzer --bvid <bvid> --output <file>` |
| `watch-later-recommender` | 用户请求推荐视频、稍后再看推荐 | `uv run watch-later-recommender --target <target> --count <n>` |
| `unknown` | 无法匹配以上任何技能 | 不执行分派，直接标记为已回复 |

### 分类 prompt 模板

工具生成的 prompt 包含：
1. 2 个 few-shot 示例（如上表）
2. 用户消息内容（包裹在 `<message>...</message>` 标签中，防注入）
3. 业务上下文（视频评论 / 动态回复 / 动态）
4. JSON 输出格式要求

Agent 不应修改或添加额外指令，直接发送给 LLM 即可。

## 回复策略

at-orchestrator 根据子 skill 执行结果自动选择回复方式：

```
子 skill 执行完成
       │
       ▼
  结果长度 < 200 字 且有 subject_id？
       │
   ┌───┴───┐
   YES     NO
    │       │
    ▼       ▼
 评论回复  私信回复
  (comment) (PM)
             │
             ▼
         检查是否存在私信会话？
             │
        ┌────┴────┐
       YES        NO
        │          │
        ▼          ▼
     发送私信   回退到评论回复
```

| 路由方式 | 条件 | 说明 |
|---------|------|------|
| 评论 (comment) | stdout < 200 字且存在 subject_id | 在原始帖子下回复，自动截断至 1000 字 |
| 私信 (PM) | stdout >= 200 字或无 subject_id | 发送私信，自动截断至 600 字 |
| 私信 → 评论回退 | 私信会话不存在 | 检查 `session_detail` API，无会话时回退到评论 |

### 消息截断规则

- 评论回复：截断至 1000 字符，超出追加 `…`
- 私信回复：截断至 600 字符，超出追加 `…`

## 任务生命周期

```
pending → classifying → dispatching → replying → replied
              ↓               ↓
          failed(class)   failed(dispatch) → failed(reply)
```

| 状态 | 说明 |
|------|------|
| `pending` | 已入库，等待处理 |
| `classifying` | 正在构建分类 prompt |
| `dispatching` | 正在执行子 skill |
| `replying` | 正在发送回复 |
| `replied` | 处理完成 |
| `failed` | 处理失败，`reply_error` 记录失败原因 |

## 鉴权处理

凭证优先级：`--auth-file` 参数 > 环境变量 > `./.auth.json` (CWD) > `~/.bili-helper/auth.json` > 二维码登录。

```bash
# 推荐方式（与项目其他工具共享）
export BILI_SESSDATA="..."
export BILI_BILI_JCT="..."
export BILI_BUVID3="..."  # 可选
```

凭证获取：浏览器 DevTools (F12) → Application → Cookies → `.bilibili.com`

> at-orchestrator 需要登录态才能拉取 @ 消息和发送回复。未登录时 fetch 和 process 均不可用。

## 错误处理

| 退出码 | 含义 | 处理建议 |
|--------|------|---------|
| 0 | 执行成功 | — |
| 1 | 运行错误（数据库初始化失败、无 pending 任务、LLM 结果解析失败） | 检查数据库路径、LLM 返回格式 |
| 非 0 | 子 skill 分派失败（子进程返回非零退出码） | 查看 error 字段获取具体原因 |

### 任务失败原因

| `reply_error` | 含义 | 处理建议 |
|--------------|------|---------|
| `classification_failed` | LLM 结果解析失败（JSON 格式错误或校验不通过） | 检查 LLM 返回的 JSON 格式 |
| `classification_error: ...` | 分类阶段异常 | 查看异常详情 |
| `non-zero exit code N` | 子 skill 进程返回非零退出码 | 检查子 skill 执行结果 |
| `dispatch_error: ...` | 分派阶段异常 | 查看异常详情 |
| `reply_failed` | 回复发送失败（API 返回非 0 code） | 检查网络和凭证 |
| `reply_exception: ...` | 回复阶段异常 | 查看异常详情 |

### B站 API 错误码

| 错误码 | 含义 | 处理建议 |
|--------|------|---------|
| -101 | 登录已过期 | 重新获取 SESSDATA |
| -111 | CSRF 校验失败 | 更新 bili_jct |
| 12015 | 评论频率过高（二级限流） | 等待后重试 |
| 12035 | 评论内容被反垃圾拦截 | 修改回复内容 |
| 21047 | 私信已达 1 条上限（无会话时） | 检查会话状态，回退到评论 |
| 21015 | 对方未绑定手机号 | 无法发送私信，回退到评论 |
| HTTP 412/429 | 请求频率过高 | 等待后重试（内置指数退避，最多 3 次） |
| 网络错误 | 连接失败 | 自动重试 3 次，仍失败则报错 |

## 限制

- 单次最多处理 `--limit` 条消息（默认 1，防止意外批量操作）
- fetch 仅支持 `x/msgfeed/at` 和 `x/msgfeed/reply` 两个端点，不支持其他消息类型
- 评论回复自动截断至 1000 字符，私信回复自动截断至 600 字符
- 子 skill 执行超时 120 秒，超时后进程被 SIGTERM / SIGKILL
- at-orchestrator 本身不调用 LLM，LLM 分类由 Agent 在阶段 2 完成
- 需要登录态，不支持游客模式
- 评论回复需找到对应 `subject_id`（视频 / 动态 ID），缺失时回退到私信

## 依赖安装

```bash
cd at-orchestrator && uv sync
```

## 与项目其他工具共享凭证

at-orchestrator 与 watch-later-recommender、video-analyzer 共享 B站 凭证。使用统一的环境变量前缀 `BILI_*`。

```bash
# 通用方式
export BILI_SESSDATA="..."
export BILI_BILI_JCT="..."
export BILI_BUVID3="..."
```

也可通过 `--auth-file` 参数指定单独的凭证文件，或使用 `--env-prefix` 使用不同的环境变量前缀隔离凭证。
