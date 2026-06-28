# bili-helper

B站 up主助手 — 基于 OpenCode skills 的 B站 自动化工具集，帮助 up主 完成收藏管理、数据分析等日常工作。

## 安装

告诉你的 Agent：

> 安装 https://github.com/xiaobin83/bili-helper 中包含的所有技能

## 工具

| 工具 | 说明 | 执行流程 |
|------|------|---------|
| [watch-later-recommender](./watch-later-recommender/) | 视频智能推荐 — LLM 从热门/排行中精选，添加到稍后再看或收藏夹 | **generate prompt → LLM → apply-llm-result**（一次调用 LLM） |
| [fav-organizer](./fav-organizer/) | 收藏夹自动整理，一键分类/去重/清理失效内容 | **classify → Agent 逐条分类 → plan → execute**（逐条调用 LLM，可迭代调整分类） |
| [dyn-publisher](./dyn-publisher/) | 动态发布，支持纯文本和图文，CLI + JSON 模板 | 一次调用 |
| [video-analyzer](./video-analyzer/) | 视频六维分析，一键获取详情/热评/PBP/AI总结/播放地址/截图 | 一次调用 |

> ⚠️ **免责声明**：fav-organizer 会根据 LLM 的分析结果直接修改你的收藏夹（包括移动、删除、取消收藏等操作）。该工具仅提供 API 调用能力，**所有修改决策均由 LLM 生成**，请在使用前仔细审查 `plan` 命令生成的操作计划。因 LLM 判断失误或用户审查不严导致的收藏内容丢失、错乱等问题，本项目不承担任何责任。建议首次使用前备份重要收藏。

## 快速开始

### watch-later-recommender

两阶段工作流：工具只负责数据采集和 prompt 生成，LLM 决策由 Agent 完成。

```bash
cd watch-later-recommender
uv sync

# 阶段 1：生成 LLM prompt（指定目标、主题、数量）
uv run watch-later-recommender --target fav --topic "AI Agent" --count 5

# Agent 读取 prompt → 调用 LLM → 保存结果到文件

# 阶段 2：应用 LLM 结果（无需重复指定 --target，从 LLM 输出自动推断）
uv run watch-later-recommender --apply-llm-result llm-output.json

# 干跑预览
uv run watch-later-recommender --apply-llm-result llm-output.json --dry-run
```

### fav-organizer

三阶段管线，Agent 逐条调用 LLM 分类后可迭代调整：

```bash
cd fav-organizer
uv sync

# 阶段 1：扫描收藏夹，准备数据
uv run fav-organizer classify --folder "默认收藏夹" --count 10

# 阶段 2：Agent 逐条调用 LLM 分类，填写 classification_result.json

# 阶段 3：生成整理计划（可反复调整分类结果后重新生成）
uv run fav-organizer plan

# 阶段 4：执行整理（写操作，需用户确认）
uv run fav-organizer execute
```

### dyn-publisher

```bash
cd dyn-publisher
uv sync

# 发布纯文本动态
uv run dyn-publisher publish --text "你好，世界！"

# 发布图文动态
uv run dyn-publisher publish --text "看图片" --image ./photo.png
```

### video-analyzer

```bash
cd video-analyzer
uv sync

# 获取完整六维分析报告（视频详情、热评、高能进度条、AI总结、播放地址、截图）
uv run video-analyzer --bvid BV1GJ411x7

# 仅获取视频详情和热评，跳过其他维度
uv run video-analyzer --bvid BV1GJ411x7 --no-pbp --no-summary --no-playurl --no-screenshot

# 指定输出文件路径
uv run video-analyzer --bvid BV1GJ411x7 --output ./report.md
```

## LLM 介入时机

项目中有两个工具涉及 LLM 决策，但 LLM 介入的方式不同：

### watch-later-recommender：一次性 batch 决策

```
工具采集数据 → 工具生成 prompt → Agent 调用 LLM → 工具执行添加
     ── 阶段 1 ──→      ── （Agent 负责） ──→     ── --apply-llm-result ──→
```

- **LLM 调用次数**：1 次（所有候选视频合并到一个 prompt）
- **Agent 职责**：读取 stdout 的 prompt → 发送给 LLM → 保存返回结果 → 传入 `--apply-llm-result`
- **LLM 输出**：人性化推荐总结 + ` ```json ` 结构化数据（bvids、reasons、target_action）
- **可迭代性**：不可迭代。想换结果需重新生成 prompt 再调 LLM
- **工具角色**：数据采集容器 + prompt 生成器 + 执行器

### fav-organizer：逐条迭代决策

```
工具采集数据 → Agent 逐条调 LLM → 工具生成计划 → 工具执行整理
  classify ──→   分类结果.json   ──→   plan   ──→  execute
```

- **LLM 调用次数**：每条收藏内容 1 次（每个视频单独分类）
- **Agent 职责**：读取 `state.json` 获取待分类条目 → 逐条构建 prompt → 调 LLM → 验证结果 → 写入 `classification_result.json`
- **LLM 输出**：2-6 个中文字的分类名称
- **可迭代性**：可迭代。Agent 可反复修改 `classification_result.json` 中的分类结果，每改一次都能重新 `plan` 预览效果
- **工具角色**：数据采集容器 + LLM 辅助函数（`build_classification_prompt`/`validate_category`）+ 计划生成器 + 执行器

### 鉴权

所有工具共享 B站 凭证，使用统一的环境变量前缀：

```bash
# 推荐方式（所有工具通用）
export BILI_SESSDATA="..."
export BILI_BILI_JCT="..."
export BILI_BUVID3="..."

# （兼容旧版 fav-organizer 的 FAV_ 前缀）
```

凭证获取：浏览器 DevTools (F12) → Application → Cookies → `.bilibili.com`

> **无凭证时自动回退二维码登录**：如果既没有 `.auth.json` 也没有设置环境变量，工具会自动在终端输出 ASCII 二维码。在 OpenCode 等可显示 console log 的工具中，可直接扫描终端中的二维码完成登录，登录后凭证自动保存到 `.auth.json`。

## 项目结构

```
bili-helper/
├── bili-core/                 # 共享基础库 (auth, HTTP, 签名, 错误处理)
├── watch-later-recommender/   # 视频智能推荐工具
│   ├── src/                   # 源代码
│   ├── tests/                 # 测试
│   └── SKILL.md               # 技能定义
├── fav-organizer/             # 收藏夹整理工具
│   ├── src/                   # 源代码
│   ├── tests/                 # 测试 (310+ 用例)
│   └── SKILL.md               # 技能定义
├── dyn-publisher/             # 动态发布工具
│   ├── src/                   # 源代码
│   └── SKILL.md               # 技能定义
├── video-analyzer/            # 视频六维分析工具
│   ├── src/                   # 源代码
│   ├── tests/                 # 测试 (25+ 用例)
│   └── SKILL.md               # 技能定义
├── AGENTS.md                  # 项目知识库 (AI 上下文)
└── README.md
```

---

## Agent 安装指南

> 以下内容供 AI Agent 阅读，用于自动化安装 bili-helper 下的所有工具。

### 前置条件

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装步骤

```bash
# 安装 bili-core 公共基础库
cd bili-core && uv sync

# 安装 watch-later-recommender 视频智能推荐工具
cd ../watch-later-recommender && uv sync

# 安装 fav-organizer 收藏夹整理工具
cd ../fav-organizer && uv sync

# 安装 dyn-publisher 动态发布工具
cd ../dyn-publisher && uv sync

# 安装 video-analyzer 视频分析工具
cd ../video-analyzer && uv sync
```

### 验证安装

```bash
# 验证 bili-core
cd bili-core && uv run pytest tests/ -v

# 验证 watch-later-recommender
cd ../watch-later-recommender && uv run python -m watch_later_recommender.main --help

# 验证 fav-organizer
cd ../fav-organizer && uv run fav-organizer --help

# 验证 dyn-publisher
cd ../dyn-publisher && uv run dyn-publisher --help

# 验证 video-analyzer
cd ../video-analyzer && uv run video-analyzer --help
```

### 注册 Skill（OpenCode）

注册以下 Skill 文件到 OpenCode：

| 文件 | 触发词 |
|------|--------|
| `watch-later-recommender/SKILL.md` | 稍后再看推荐、收藏夹推荐、视频推荐、智能推荐、B站视频推荐 |
| `fav-organizer/SKILL.md` | 整理收藏夹、B站收藏管理、收藏夹分类、清理失效收藏 |
| `dyn-publisher/SKILL.md` | 发布B站动态、发B站、B站动态发布、bilibili动态 |
| `video-analyzer/SKILL.md` | 分析B站视频、bilibili视频分析、获取视频数据、视频详情、bvid分析 |


