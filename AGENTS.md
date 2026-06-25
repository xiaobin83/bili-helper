# PROJECT KNOWLEDGE BASE

**Generated:** 2026-06-25
**Branch:** main (new)

## OVERVIEW

B站 up主助手 —— 编写 OpenCode skills 辅助 B站 up主 完成各项日常工作。Skills 通过调用 B站 API（以 `../bili-apis` 为参考）实现数据获取、内容管理、数据分析等功能。

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

## SKILL DEVELOPMENT CONVENTIONS

### 项目结构

```
workspace/
├── AGENTS.md              # 本文件
├── .omo/                  # OpenCode 工作产物 (plans/reviews)
└── [skill-name]/          # 按技能组织 (未来)
    └── SKILL.md           # 技能定义
```

### Skill 编写规范

1. **单一职责**: 每个 skill 聚焦一个 up主 工作场景 (如: 数据分析、视频管理、评论管理)
2. **API 优先**: 所有 B站 数据操作必须基于 `../bili-apis` 文档，不自行猜测接口
3. **鉴权处理**: 每个 skill 需考虑登录态管理 (Cookie/access_key)，参考 `bili-apis/docs/login/`
4. **错误处理**: 遵循 B站 公共错误码 (`bili-apis/docs/misc/errcode.md`)，处理限流/风控
5. **签名合规**: HTTP 接口需实现对应签名算法 (Wbi/APP)
6. **使用持久化背景任务**: 长时间操作(如爬取数据)使用 OpenCode 后台任务机制
7. **使用系统浏览器**: 任何需要浏览器窗口的操作通过 Playwright MCP 实现

### 开发工作流

1. 查阅 `../bili-apis/docs/` 定位所需 API
2. 理解鉴权方式与签名算法
3. 编写 SKILL.md 定义技能
4. 使用 OpenCode skill system 注册

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
