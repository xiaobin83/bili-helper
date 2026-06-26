---
name: video-analyzer
description: B站（Bilibili）视频分析工具。一键获取视频详情、前10热评、高能进度条、AI总结、播放地址、视频截图。当用户提到分析B站视频、bilibili视频分析、获取视频数据、视频详情、b站视频信息、视频热评、bvid分析时触发。
---

# video-analyzer — B站视频分析工具

一键获取指定 B站 视频的六维数据分析报告，涵盖视频详情、热门评论、高能进度条、AI 总结、播放地址和视频截图。

## 使用方式

```bash
uv run video-analyzer --bvid <BV号> [options]
```

### 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--bvid` | `str` | 是 | 视频 BV 号，例如 `BV1GJ411x7` |
| `-o, --output` | `str` | 否 | 输出文件路径，默认输出到 `.video-analyzer/` 目录 |
| `--no-comments` | `flag` | 否 | 跳过热门评论获取 |
| `--no-pbp` | `flag` | 否 | 跳过（高能进度条）获取 |
| `--no-summary` | `flag` | 否 | 跳过 AI 总结获取 |
| `--no-playurl` | `flag` | 否 | 跳过播放地址获取 |
| `--no-screenshot` | `flag` | 否 | 跳过视频截图获取 |

### 示例

```bash
# 获取完整六维分析报告
uv run video-analyzer --bvid BV1GJ411x7

# 仅获取视频详情和热评，跳过其他维度
uv run video-analyzer --bvid BV1GJ411x7 --no-pbp --no-summary --no-playurl --no-screenshot

# 指定输出路径
uv run video-analyzer --bvid BV1GJ411x7 --output ./my_report.md
```

## 鉴权处理

凭证优先级：`.auth.json`（二维码登录）> 环境变量 > 降级模式。

```bash
# 推荐方式（与项目其他工具共享）
export BILI_SESSDATA="..."
export BILI_BILI_JCT="..."
export BILI_BUVID3="..."  # 可选
```

凭证获取：浏览器 DevTools (F12) → Application → Cookies → `.bilibili.com`

**降级策略**：无鉴权时，可获取的视频数据（如视频详情）正常返回；需登录态的数据（如热门评论、高能进度条）在报告中标注为"需登录"。

## 工作流

```
┌──────────┐   ┌──────────────────────────────┐   ┌──────────┐
│  视频详情  │   │  并行拉取 5 种数据（可选跳过） │   │  生成报告  │
│  - 基础信息 │ → │  - 热门评论 / PBP / AI总结   │ → │  Markdown │
│  - UP主信息 │   │  - PlayURL / 截图          │   │  输出文件  │
└──────────┘   └──────────────────────────────┘   └──────────┘
```

### 步骤 1 — 获取视频详情

通过 B站 API 获取视频基础信息，包含：
- 标题、描述、播放量、弹幕数、点赞/投币/收藏数
- UP 主信息（uid、昵称、粉丝数）
- 视频分区、标签、发布时间
- 时长、分辨率、互动数据

### 步骤 2 — 并行拉取增强数据

根据 CLI 参数跳过不需要的维度，并行拉取以下数据：

| 维度 | 来源 API | 数据说明 |
|------|----------|----------|
| 热门评论 | `v2/reply/wbi/main` | 前 10 热评（点赞数排序），含回复数、点赞数 |
| 高能进度条 (PBP) | `x/player/pagelist?bvid=` | 视频各时间段的弹幕密度分布 |
| AI 总结 | `x/web-interface/related/content?bvid=` | B站 服务端生成的 AI 视频摘要 |
| 播放地址 (PlayURL) | `x/player/playurl?bvid=` | 视频流 URL（清晰度、格式、过期时间） |
| 视频截图 | `x/web-interface/archive/stat` + 截图服务 | 视频封面及关键帧截图地址 |

### 步骤 3 — 生成 Markdown 报告

将以上所有数据整理为结构化 Markdown 报告，输出到指定文件：

```markdown
# BV1GJ411x7 视频分析报告

## 视频信息
- **标题**: xxx
- **UP主**: xxx
- **播放/弹幕**: 12.3万 / 456

## 热门评论
1. @user: 评论内容... (👍123)

## 高能进度条
- 00:15-00:30: 高能 (256条弹幕)
- 01:20-01:45: 峰值 (890条弹幕)

## AI 总结
B站 AI 生成的视频内容摘要...

## 播放地址
- 1080P: https://... (有效期至 ...)
- 720P: https://...

## 截图
![封面](...)
```

## 错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| 视频不存在（code=-400） | 提示"视频不存在或已删除" |
| 鉴权过期（code=-101） | 引导用户重新登录，降级返回公开数据 |
| CSRF 校验失败（code=-111） | 提示更新 bili_jct |
| 限流（HTTP 412/429） | 指数退避重试（最多 3 次） |
| 网络错误 | 重试 3 次，失败后报错 |
| 部分维度获取失败 | 报告中标注失败原因，不影响其他维度 |

## 限制

- 仅支持单 bvid 分析（不支持批量或 avid）
- 仅分析视频第一 P（不支持多 P 视频）
- 只读工具，不修改任何内容和数据
- 播放地址具有时效性，生成后需尽快使用

## 依赖安装

```bash
cd video-analyzer
uv sync
```
