---
name: dyn-publisher
description: B站（Bilibili）动态发布工具。支持纯文本动态和图文动态发布，CLI 命令 + JSON 模板。当用户提到发布B站动态、发B站动态、B站动态发布、bilibili动态发布、图文动态、B站发帖时触发。
---

# dyn-publisher — B站动态发布

你协助用户发布 B站（Bilibili）动态。支持**纯文本**和**图文**两种类型，通过 CLI 或 JSON 模板发布。

## 使用方式

### 环境准备

首次使用需获取 B站 Cookie：

```bash
export BILI_SESSDATA="..."
export BILI_BILI_JCT="..."
export BILI_BUVID3="..."  # 可选
```

凭证优先级：`.auth.json` > 环境变量 > 二维码登录。

凭证获取：浏览器 DevTools (F12) → Application → Cookies → `.bilibili.com`

### CLI 命令

#### publish — 发布动态

```bash
# 纯文本动态
uv run dyn-publisher publish --text "今天天气真好！"

# 图文动态
uv run dyn-publisher publish --text "分享图片" --image ./photo.png

# 指定图片分类
uv run dyn-publisher publish --text "新画作" --image ./artwork.png --category draw

# 使用 JSON 模板
uv run dyn-publisher publish --template ./mytemplate.json

# 预览模板（不实际发布）
uv run dyn-publisher publish --template ./mytemplate.json --dry-run
```

#### upload-image — 上传图片

```bash
# 上传图片到B站图床
uv run dyn-publisher upload-image --file ./image.png --category daily
```

图片分类：`daily`（日常）、`draw`（绘画）、`cos`（COSPLAY）

### 自动尾部标识

所有通过 dyn-publisher 发布的动态，末尾会自动追加换行标识：

```
from bili-helper: https://github.com/xiaobin83/bili-helper
```

该行为不可关闭，无需用户手动添加。

### JSON 模板格式

支持两种模板类型，文件必须为合法 JSON：

```json
{
  "type": "text",
  "text": "动态文本内容"
}

{
  "type": "image",
  "text": "图片说明文字",
  "images": [
    {"file": "path/to/image.png", "category": "daily"}
  ]
}
```

模板验证规则：

| 字段 | 要求 |
|------|------|
| `type` | 必须为 `"text"` 或 `"image"` |
| `text` | 必须存在且非空字符串 |
| `images` | `type=image` 时必须存在且非空数组 |
| `images[].file` | 每个图片项必须包含 `file` 字段 |

### 环境变量

推荐使用统一前缀 `BILI_*`（兼容旧的 `FAV_*` 前缀）：

| 变量 | 说明 |
|------|------|
| `BILI_SESSDATA` | B站登录凭证（必需） |
| `BILI_BILI_JCT` | CSRF Token（必需） |
| `BILI_BUVID3` | 浏览器标识（建议） |

## 工作流

### 场景 1：发布纯文本动态

```bash
uv run dyn-publisher publish --text "你好，世界！"
```

### 场景 2：发布图片动态

```bash
# 一步到位：使用 --image 参数
uv run dyn-publisher publish --text "新照片" --image ./photo.jpg

# 或分步：先上传再发布（需自行拼接）
IMAGE_URL=$(uv run dyn-publisher upload-image --file ./photo.jpg --category daily | python -c "import sys,json; print(json.load(sys.stdin)['data']['image_url'])")
echo $IMAGE_URL
```

### 场景 3：JSON 模板批量发布

```bash
cat > /tmp/dyn.json << 'EOF'
{"type": "text", "text": "今日心得分享"}
EOF
uv run dyn-publisher publish --template /tmp/dyn.json
```

## 错误处理

| 错误码 | 含义 | 处理建议 |
|--------|------|---------|
| -1 | 未添加图片 / 图片不存在 | 检查图片路径是否正确 |
| -2 | 参数错误 | 检查命令参数或模板格式 |
| -3 | 图片尺寸过小 | 非日常类型图片宽高需 > 420px |
| -4 | 账号未登录 | 设置 BILI_SESSDATA |
| -7 | 图片信息错误 | 检查图片文件是否损坏 |
| -101 | 登录已过期 | 重新获取 SESSDATA |
| -111 | CSRF 校验失败 | 更新 bili_jct |
| HTTP 412/429 | 请求频率过高 | 等待后重试（内置指数退避，最多 3 次） |
| 网络错误 | 连接失败 | 自动重试 3 次，仍失败则报错 |

## 限制

- 仅支持纯文本和图文动态（不支持投票、话题、@ 用户、定时发布）
- 单次最多 9 张图片（B站 API 限制）
- 图片格式仅支持 jpg/png/gif
- 非日常类型图片需 > 420px
- 文本长度建议不超过 2000 字符
- JSON 模板仅支持 JSON 格式（不支持 YAML）
- 不提供撤回/删除已发布动态的功能

## 依赖安装

```bash
cd dyn-publisher && uv sync
```

## 与 fav-organizer 共享凭证

dyn-publisher 与 fav-organizer 共享 B站 凭证。使用统一的环境变量前缀 `BILI_*`，也兼容旧的 `FAV_*` 前缀。

```bash
# 推荐方式（dyn-publisher 和 fav-organizer 通用）
export BILI_SESSDATA="..."
export BILI_BILI_JCT="..."

# 兼容方式（仅 fav-organizer 旧版）
export FAV_SESSDATA="..."
export FAV_BILI_JCT="..."
```

两种前缀可混用，读取优先级：`BILI_*` > `FAV_*`。
