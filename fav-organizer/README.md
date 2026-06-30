# fav-organizer

B站收藏夹自动整理工具。三阶段管线：**classify → plan → execute**。

## 快速开始

```bash
# 安装依赖
uv sync

# 阶段 1: 扫描收藏夹，准备数据
uv run fav-organizer classify --folder "默认收藏夹"

# 阶段 2: （Agent 或用户填写 ~/.bili-helper/fav-organizer/classification_result.json）

# 阶段 3: 生成整理计划（可反复调整分类后重新运行）
uv run fav-organizer plan

# 阶段 4: 执行整理
uv run fav-organizer execute
```

## 三阶段管线

```
classify → state.json + classification_result.json → plan → plan.json → execute
              ↑________ Agent 填写 LLM 分类 ___________↑

所有中间文件存储在 `~/.bili-helper/fav-organizer/` 目录。
```

### classify — 数据采集

```bash
uv run fav-organizer classify --folder "默认收藏夹"   # 指定文件夹
uv run fav-organizer classify --all                   # 所有文件夹
uv run fav-organizer classify --all --clear-cache     # 清除视频缓存
```

完成：鉴权 → 扫描失效 → 去重 → 获取视频元数据（磁盘缓存，30 天 TTL）→ 输出数据文件。

### plan — 生成计划

```bash
uv run fav-organizer plan                              # 使用默认分类文件
uv run fav-organizer plan --classification result.json # 指定分类文件
```

读取 `state.json` + `classification_result.json` → 生成 `plan.json` + Markdown 预览。可反复调整分类后重新运行。

### execute — 执行整理

```bash
uv run fav-organizer execute                    # 使用默认计划
uv run fav-organizer execute --plan plan.json   # 指定计划
```

执行顺序：创建文件夹 → 移动内容（每批 ≤30）→ 删除失效/重复。单批失败不影响后续。

## 鉴权

```bash
# 方法 1: 二维码登录（首次自动弹出）
uv run fav-organizer classify --folder "默认收藏夹"

# 方法 2: 环境变量
export FAV_SESSDATA="..."
export FAV_BILI_JCT="..."
```

凭证获取：浏览器 DevTools (F12) → Application → Cookies → `.bilibili.com`

## 中间文件格式

| 文件 | 说明 |
|------|------|
| `~/.bili-helper/fav-organizer/state.json` | 扫描状态（文件夹、失效项、内容列表） |
| `~/.bili-helper/fav-organizer/classification_result.json` | LLM 分类结果（Agent 填写） |
| `~/.bili-helper/fav-organizer/plan.json` | 可执行计划 |
| `~/.bili-helper/fav-organizer/video_cache.json` | 视频信息缓存（30 天 TTL） |

## 项目结构

```
fav-organizer/
├── src/
│   ├── main.py              # CLI 入口（3 个子命令）
│   ├── auth.py              # 鉴权（文件/环境变量/二维码登录）
│   ├── scanner.py           # 失效内容扫描
│   ├── dedup.py             # 重复内容检测

│   ├── planner.py           # 操作计划生成
│   ├── executor.py          # 计划执行器
│   ├── fav_api.py           # 收藏夹 API 客户端
│   ├── video_api.py         # 视频信息 API（磁盘缓存）
│   ├── http_client.py       # HTTP 客户端（鉴权、限流、重试）
│   ├── signing.py           # Wbi 签名算法
│   ├── state_manager.py     # 状态文件读写
│   ├── models.py            # Pydantic 数据模型
│   └── errors.py            # 自定义异常类
├── tests/                   # 测试（310+ 用例）
└── pyproject.toml
```

## 运行要求

- Python 3.12+
- B站凭证（SESSDATA）
- 网络访问 `api.bilibili.com`

## 开发

```bash
uv sync                           # 安装依赖
uv run pytest tests/ -v           # 运行测试
```

## 限制（v2）

- LLM 分类为唯一分类方式
- 不支持重命名/合并已有文件夹
- 不提供回滚/撤销
- 不操作他人创建的收藏夹
