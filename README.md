# bili-helper

B站 up主助手 — 基于 OpenCode skills 的 B站 自动化工具集，帮助 up主 完成收藏管理、数据分析等日常工作。

## 工具

| 工具 | 说明 |
|------|------|
| [fav-organizer](./fav-organizer/) | 收藏夹自动整理，一键分类/去重/清理失效内容 |

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

### 鉴权

首次使用需获取 B站 Cookie：

```bash
export FAV_SESSDATA="..."
export FAV_BILI_JCT="..."
```

或程序将自动弹出二维码登录，凭证保存在 `fav-organizer/.auth.json`（已 gitignore）。

## 项目结构

```
bili-helper/
├── fav-organizer/        # 收藏夹整理工具
│   ├── src/              # 源代码
│   ├── tests/            # 测试 (310+ 用例)
│   └── SKILL.md          # 技能定义
├── AGENTS.md             # 项目知识库 (AI 上下文)
└── README.md
```

## 参考

- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) — 社区维护的 B站 API 文档
