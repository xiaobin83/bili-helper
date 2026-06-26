# PROJECT KNOWLEDGE BASE

**Generated:** 2026-06-25
**Branch:** main (new)

## OVERVIEW

B站 up主助手 —— 编写 OpenCode skills 辅助 B站 up主 完成各项日常工作。Skills 通过调用 B站 API（以 `../bili-apis` 为参考）实现数据获取、内容管理、数据分析等功能。

所有 skill 共享 `bili-core/` 基础库，提供统一鉴权、HTTP 客户端、签名算法和错误处理。开发新 skill 时**必须优先重用它**，不要自行实现重复功能。

## REFERENCE: bili-apis

路径: `../bili-apis` — 社区维护的第三方 B站 API 文档 (`bilibili-API-collect`)。

### 结构

```
bili-apis/
├── docs/                  # 按功能分类的 API 文档 (32 个目录)
│   ├── login/             # 登录 (短信/密码/二维码/Cookie刷新)
│   ├── video/             # 视频信息、播放流、点赞投币收藏
│   ├── article/           # 专栏文章管理
│   ├── dynamic/           # 动态发布与管理
│   ├── live/              # 直播间信息与管理
│   ├── comment/           # 评论区
│   ├── danmaku/           # 弹幕 (protobuf/xml)
│   ├── creativecenter/    # 创作中心 (数据统计/电磁力)
│   ├── search/            # 搜索
│   ├── user/              # 用户信息与关系
│   ├── fav/               # 收藏夹
│   ├── electric/          # 充电 (包月/自定义/B币)
│   └── ...                # 30+ 更多分类
├── grpc_api/              # gRPC proto 定义 (反编译自官方 APP)
│   ├── bilibili/          # 主 proto 定义
│   ├── pgc/               # PGC/番剧相关
│   └── datacenter/        # 数据中心
├── assets/                # 图片/图标资源
└── .vuepress/             # VuePress 文档站点配置
```

### 关键 API 速查

| 功能域 | 文档路径 | 要点 |
|--------|---------|------|
| 登录 | `docs/login/` | SMS/密码/二维码登录, Cookie 刷新, access_key 管理 |
| 鉴权签名 | `docs/misc/sign/` | APP API 签名 (appkey+sign), Wbi 签名 (wts+w_rid) |
| 视频 | `docs/video/` | 信息/状态数/播放流/分区代码/高能进度条/AI摘要 |
| 直播 | `docs/live/` | 直播间信息/直播流/禁言管理 |
| 动态 | `docs/dynamic/` | 动态列表/发送转载/操作 |
| 创作中心 | `docs/creativecenter/` | 数据统计/电磁力 |
| 弹幕 | `docs/danmaku/` | protobuf 实时弹幕/xml 弹幕/历史弹幕 |
| gRPC | `grpc_api/` | 播放链接/搜索/动态 (需要设备指纹 + Metadata 鉴权) |

### API 鉴权要点

**HTTP API**: 需要 `Cookies` (web端) 或 `access_key` (APP端) + 签名
- Web端: `SESSDATA` Cookie, Wbi 签名 (`w_rid` + `wts`)
- APP端: `access_key` + APP 签名 (`appkey` + `sign`)

**gRPC API**: Metadata 中需要 10+ 个 Header (设备信息、网络信息、locale 等)，使用 protobuf 序列化

## SHARED LIBRARY: bili-core

路径: `bili-core/` — 所有 skill 的共享基础库，**开发新 skill 时必须优先重用，不要自行重复实现**。

### 提供的能力

| 模块 | 能力 | 说�� |
|------|------|------|
| `bili_core.auth` | 凭证加载 + QR 登录 | `get_credentials()`: `.auth.json` → 环境变量 → 二维码扫码登录，自动保存凭证。提供 `Credentials` dataclass、`login_flow()`、`check_expired()` |
| `bili_core.http_client` | HTTP 客户端 | `BiliHTTPClient`: 基于 curl_cffi 的 Chrome 131 指纹模拟，内置 2s 间隔限流、412/429 自动重试（3 次，120s 等待）、-101/-111 错误码转异常。提供 `DEFAULT_HEADERS`（完整反爬 header 集合）|
| `bili_core.signing` | Wbi 签名 | `sign_params()`: 自动获取 mixin key（24h 缓存），生成 `w_rid` + `wts` 签名参数。提供 `clear_cache()` |
| `bili_core.errors` | 异常类 | `AuthError`（登录过期）、`CSRFError`（CSRF 校验失败）、`RateLimitError`（限流）、`BiliAPIError`（通用 API 错误）、`PublishError`（发布错误）及错误码常量 |

### 使用方式

所有 skill 在 `pyproject.toml` 中通过 **editable path dependency** 引用：

```toml
[tool.uv.sources]
bili-core = { path = "../bili-core", editable = true }
```

```python
from bili_core.auth import get_credentials
from bili_core.http_client import BiliHTTPClient, DEFAULT_HEADERS
from bili_core.signing import sign_params
from bili_core.errors import AuthError, CSRFError, RateLimitError

# 示例：完整鉴权流转
creds = get_credentials()                     # 自动 QR 登录
client = BiliHTTPClient(sessdata=..., ...)     # 带防 WAF 的 HTTP 客户端
signed = sign_params({"aid": 123})             # Wbi 签名
```

## SKILL DEVELOPMENT CONVENTIONS

### 项目结构

```
workspace/
├── AGENTS.md              # 本文件
├── .omo/                  # OpenCode 工作产物 (plans/reviews)
├── bili-core/             # 共享基础库 (auth, HTTP, 签名, 错误处理)
└── [skill-name]/          # 按技能组织
    └── SKILL.md           # 技能定义
```

### Skill 编写规范

1. **单一职责**: 每个 skill 聚焦一个 up主 工作场景 (如: 数据分析、视频管理、评论管理)
2. **API 优先**: 所有 B站 数据操作必须基于 `../bili-apis` 文档，不自行猜测接口
3. **优先复用 bili-core**: 鉴权、HTTP 客户端、签名、错误类型等公共能力已由 `bili-core/` 提供，**不要自行重复实现**。引用方式见上方 bili-core 章节
4. **鉴权处理**: 使用 `bili_core.auth.get_credentials()` 获取凭证（自动走 `.auth.json` → 环境变量 → QR 登录），通过 `BiliHTTPClient` 发送请求（自动附带 Cookie 和反爬 header）
5. **错误处理**: 优先使用 `bili_core.errors` 定义的异常类型（`AuthError`、`CSRFError`、`RateLimitError`、`BiliAPIError`），参考 B站 公共错误码 (`bili-apis/docs/misc/errcode.md`)
6. **签名合规**: HTTP 接口签名使用 `bili_core.signing.sign_params()`（Wbi），不要自行实现签名算法
7. **使用持久化背景任务**: 长时间操作(如爬取数据)使用 OpenCode 后台任务机制
8. **使用系统浏览器**: 任何需要浏览器窗口的操作通过 Playwright MCP 实现

### 开发工作流

1. 查阅 `../bili-apis/docs/` 定位所需 API
2. 理解鉴权方式与签名算法
3. 确认 `bili-core/` 是否已提供所需能力（auth/HTTP/签名/错误处理），避免重复实现
4. 编写 SKILL.md 定义技能
5. 使用 OpenCode skill system 注册

## COMMANDS

```bash
# Skill 安装与测试 (通过 OpenCode skill 系统)
# 暂无项目级构建命令，skills 由 OpenCode 运行时加载
```

## NOTES

- B站 API 可能随时变更，`bili-apis` 为社区维护版本，实际使用时建议自行验证
- gRPC 接口需要设备指纹模拟 (FawkesReq, Device, Network bin headers)，参考 `grpc_api/readme.md`
- 风控策略敏感: 频繁请求可能触发验证码或封禁，skill 需内置请求频率控制
- 视频流/直播流 URL 具有时效性，需实时获取
