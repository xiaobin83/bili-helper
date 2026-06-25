# fav-organizer

B站收藏夹自动整理工具。一键扫描、去重、分类你的所有收藏内容。

**工作流：** 扫描 → 清理失效 → 去重 → 分类 → 预览 → 确认 → 执行

## 快速开始

```bash
# 安装依赖
uv sync

# 预览整理计划（不执行任何操作）
uv run fav-organizer --dry-run

# 或者直接运行模块
python src/main.py --dry-run

# 执行完整整理（预览 → 确认 → 执行）
uv run fav-organizer
```

## 鉴权

工具需要 B站 登录凭证才能访问你的收藏夹数据。

### 自动二维码登录（推荐）

首次运行时，工具会自动弹出二维码登录：

```
✨ 自动打开系统浏览器 → 显示 B站 扫码页面
📱 请使用 B站 APP 扫描二维码
✅ 扫描成功后自动保存凭证到 .auth.json
```

无图形界面时（如 SSH），终端会输出 ASCII 二维码作为回退。

### 环境变量（可选）

适合不想重复扫码的用户：

```bash
export FAV_SESSDATA="你的 SESSDATA 值"
export FAV_BILI_JCT="你的 bili_jct 值"
export FAV_BUVID3="你的 buvid3 值"   # 可选
```

获取方式：浏览器 DevTools (F12) → Application → Cookies → `.bilibili.com`

### 凭证优先级

1. `.auth.json`（二维码登录自动生成）
2. 环境变量 `FAV_SESSDATA` / `FAV_BILI_JCT` / `FAV_BUVID3`
3. 以上都不存在 → 自动触发二维码登录流程

## 功能

### 1. 清理失效内容

扫描所有收藏夹，识别已失效的视频（UP主删除 / 平台删除），确认后批量清除。

### 2. 去重

检测默认收藏夹与命名文件夹之间的重复内容。同一内容在多个命名文件夹中保留（合法多分类），仅在默认收藏夹中的重复项标记为待删除。

### 3. 智能分类

三种分类策略按优先级合并：

| 优先级 | 策略 | 方式 |
|--------|------|------|
| **高** | LLM 智能分类 | 基于标题+简介，AI 判断 2-6 字中文类别名 |
| 中 | 分区归类 | 调用视频 API 获取分区 (tid)，映射到 20 个主分区 |
| 低 | UP主归类 | 按 UP主 名称精确分组 |

冲突优先级：**LLM 分类 > 分区分类 > UP主分类**

### 4. 预览

生成 Markdown 格式的完整整理计划，包含：
- 统计摘要（创建/移动/删除数量）
- 失效内容表格
- 重复内容表格
- 按目标文件夹分组的分类移动清单
- 新创建文件夹列表
- 空文件夹建议

### 5. 安全执行

- 先创建文件夹 → 再移动 → 最后删除
- 每批 ≤30 个资源，分批执行
- 单批失败不影响后续（记录错误，继续执行）
- `--dry-run` 模式仅预览，不执行任何操作

## CLI 选项

| 选项 | 说明 |
|------|------|
| `--dry-run` | 仅生成预览，不执行任何写入操作 |
| `--help` | 显示帮助信息 |

## 运行要求

- **Python** 3.12+
- **B站凭证**：SESSDATA（二维码登录或环境变量）
- **网络**：需要访问 `api.bilibili.com`

```bash
# 安装（使用清华镜像）
uv sync --no-dev --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

## 项目结构

```
fav-organizer/
├── src/
│   ├── main.py              # CLI 入口、管线编排、预览生成
│   ├── auth.py              # 鉴权（文件/环境变量/二维码登录）
│   ├── scanner.py           # 失效内容扫描
│   ├── dedup.py             # 重复内容检测
│   ├── classifier_zone.py   # 分区归类（20 个主分区）
│   ├── classifier_upper.py  # UP主 名称归类
│   ├── classifier_llm.py    # LLM 智能分类
│   ├── planner.py           # 操作计划生成（合并分类结果）
│   ├── preview.py           # Markdown 预览格式化
│   ├── confirm.py           # 交互式确认提示
│   ├── executor.py          # 计划执行器（分批写入）
│   ├── fav_api.py           # 收藏夹 API 客户端
│   ├── video_api.py         # 视频信息 API 客户端
│   ├── http_client.py       # HTTP 客户端（鉴权、限流、重试）
│   ├── signing.py           # Wbi 签名算法
│   ├── types.py             # Pydantic 数据模型
│   └── errors.py            # 自定义异常类
├── tests/
│   ├── test_integration.py  # 端到端集成测试
│   └── test_*.py            # 单元测试（330+ 测试用例）
├── pyproject.toml           # 项目配置与依赖
├── SKILL.md                 # 技能定义文档
└── .auth.json               # 凭证存储（gitignored）
```

## 凭证存储

二维码登录成功后，凭证自动保存到 `.auth.json`：

```json
{
  "sessdata": "xxxx",
  "bili_jct": "yyyy",
  "buvid3": "zzzz",
  "mid": 123456
}
```

文件权限自动设为 `0o600`（仅所有者可读写），**已加入 `.gitignore`**。

## 错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| 鉴权过期 (code=-101) | 提示重新获取 SESSDATA，建议重新扫码登录 |
| CSRF 校验失败 (code=-111) | 提示 bili_jct 无效 |
| 限流 (HTTP 412/429) | 指数退避重试（等 60s → 最多 3 次） |
| 网络错误 | 重试 3 次，失败后报错退出 |
| API 错误 | 单批失败不影响后续，记录失败项 |

## 限制（v1）

- v1 不支持重命名/合并已有文件夹（仅创建+归类）
- v1 不提供回滚/撤销功能（预览即"回滚"）
- v1 不操作他人创建的收藏夹（collected 文件夹）
- v1 不处理 UP主 名称变体智能合并（精确匹配）
- 空文件夹仅标注"建议删除"，不自动删除

## 开发

```bash
# 安装开发依赖
uv sync

# 运行所有测试
uv run pytest tests/ -v

# 运行集成测试
uv run pytest tests/test_integration.py -v

# 运行单个测试文件
uv run pytest tests/test_main.py -v --tb=short
```
