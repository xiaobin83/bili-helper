# bili-helper

B站 up主助手 — 基于 OpenCode skills 的 B站 自动化工具集，帮助 up主 完成收藏管理、数据分析等日常工作。

## 工具

| 工具 | 说明 |
|------|------|
| [fav-organizer](./fav-organizer/) | 收藏夹自动整理，一键分类/去重/清理失效内容 |
| [dyn-publisher](./dyn-publisher/) | 动态发布，支持纯文本和图文，CLI + JSON 模板 |

> ⚠️ **免责声明**：fav-organizer 会根据 LLM 的分析结果直接修改你的收藏夹（包括移动、删除、取消收藏等操作）。该工具仅提供 API 调用能力，**所有修改决策均由 LLM 生成**，请在使用前仔细审查 `plan` 命令生成的操作计划。因 LLM 判断失误或用户审查不严导致的收藏内容丢失、错乱等问题，本项目不承担任何责任。建议首次使用前备份重要收藏。

## 快速开始

### fav-organizer

```bash
cd fav-organizer
uv sync

# 整理默认收藏夹前 10 个视频
uv run fav-organizer classify --folder "默认收藏夹" --count 10

# 填写分类 → 生成计划 → 执行
uv run fav-organizer plan
uv run fav-organizer execute
```

### dyn-publisher

```bash
cd dyn-publisher
uv sync

# 发布纯文本动态
BILI_SESSDATA="..." BILI_BILI_JCT="..." uv run dyn-publisher publish --text "你好，世界！"

# 发布图文动态
uv run dyn-publisher publish --text "看图片" --image ./photo.png
```

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

## 项目结构

```
bili-helper/
├── bili-core/             # 共享基础库 (auth, HTTP, 签名, 错误处理)
├── dyn-publisher/         # 动态发布工具
├── fav-organizer/        # 收藏夹整理工具
│   ├── src/              # 源代码
│   ├── tests/            # 测试 (310+ 用例)
│   └── SKILL.md          # 技能定义
├── AGENTS.md             # 项目知识库 (AI 上下文)
└── README.md
```

## 参考

- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) — 社区维护的 B站 API 文档

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

# 安装 fav-organizer 收藏夹整理工具
cd ../fav-organizer && uv sync

# 安装 dyn-publisher 动态发布工具
cd ../dyn-publisher && uv sync
```

### 验证安装

```bash
# 验证 bili-core
cd bili-core && uv run pytest tests/ -v

# 验证 fav-organizer
cd ../fav-organizer && uv run fav-organizer --help

# 验证 dyn-publisher
cd ../dyn-publisher && uv run dyn-publisher --help
```

### 注册 Skill（OpenCode）

注册以下 Skill 文件到 OpenCode：

| 文件 | 触发词 |
|------|--------|
| `fav-organizer/SKILL.md` | 整理收藏夹、B站收藏管理、收藏夹分类、清理失效收藏 |
| `dyn-publisher/SKILL.md` | 发布B站动态、发B站、B站动态发布、bilibili动态 |


