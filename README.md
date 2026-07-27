# Grok 联网搜索 (astrbot_plugin_grok_web_search)

通过 Grok / 豆包 API 进行实时联网搜索，返回综合答案和来源链接。支持多模态图片搜索、网页内容抓取、网页图片自动发送。

## 环境要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Python | >= 3.10 | |
| AstrBot | >= v4.9.2 | 基础功能（指令 + LLM Tool） |
| AstrBot | >= v4.13.2 | 使用 Skill 功能 |

**平台支持**: 全平台（无限制）

## 功能

- `/grok` 指令 - 直接执行搜索，支持附带图片进行多模态搜索
- LLM Tool (`grok_web_search`) - 供 LLM 自动调用的实时搜索工具，支持搜索网页和 X (Twitter) 平台；v1.5.1 起支持 `prefer_quality` 参数切换速度/质量链路
- LLM Tool (`grok_web_fetch`) - 网页内容抓取工具，将 URL 转为结构化 Markdown，优先使用豆包网页解析应用（支持图片提取），回退到 Grok 联网能力
- LLM Tool (`grok_download_file`) - 文件下载并发送工具，支持图片和文档（PDF/Word/Excel 等），自动检测 Content-Type 修正文件类型
- Skill 脚本 - 可安装到 skills 目录供 LLM 脚本调用，支持 `--image-files` 传入图片
- 搜索结果图片卡片 - 基于 Pillow 纯本地渲染，面板式布局，支持日/夜自动主题
- **双链路提供商路由**（v1.5.0） - `speed_chain`（速度优先）与 `quality_chain`（质量优先）独立配置，解决即时搜索与后台研究场景对提供商速度/质量需求冲突
- **豆包搜索支持** - 在 providers 列表中添加火山方舟端点，自动识别并使用豆包 Responses API 进行联网搜索
- **豆包网页解析** - 通过 Bot API 调用带网页解析插件的零代码应用，抓取网页内容并自动提取图片发送

## 安装

### 俩种方式

1. 在 AstrBot 插件市场搜索 `Grok联网搜索` 点击安装
2. 在插件界面右下角点击加号选择从链接安装输入 ` https://github.com/Inoryu7z/astrbot_plugin_grok_web_search  `

## 配置

### 供应商设置

| 配置项 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `use_builtin_provider` | bool | 否 | 是否使用 AstrBot 自带供应商（默认: false） |
| `provider` | string | 条件 | 选择已配置的 LLM 供应商（启用自带供应商时必填） |
| `providers` | template_list | 条件 | 自定义提供商列表（使用自定义供应商时必填，按顺序故障转移） |

### 自定义提供商字段（`providers` 模板）

每个 provider 条目可配置以下字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 否 | 提供商唯一标识（v1.5.0 新增），用于在 `speed_chain` / `quality_chain` 中引用，如 `grok-fast`、`doubao-pro` |
| `base_url` | string | 是 | API 端点 URL。Grok: `https://api.x.ai`；豆包: `https://ark.cn-beijing.volces.com`（会自动补全 API 路径） |
| `api_key` | string | 是 | API 密钥 |
| `model` | string | 否 | 模型名称（留空使用全局默认：grok-4-fast / doubao-seed-2-0-pro-260215） |
| `fetch_bot_id` | string | 否 | 豆包网页解析应用ID（仅豆包），填写后网页抓取优先使用此应用 |
| `reasoning_effort` | string | 否 | 思考强度（仅豆包，v1.5.0 新增）：`minimal`/`low`/`medium`/`high`，默认 `medium` |

### 链路配置（v1.5.0）

为解决即时搜索与后台研究场景对提供商速度/质量需求冲突，新增两条独立链路：

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `speed_chain` | template_list | 速度优先链路。填写 providers 中的提供商 ID，用于 `/grok` 指令和 LLM Tool（即时搜索场景） |
| `quality_chain` | template_list | 质量优先链路。填写 providers 中的提供商 ID，用于后台研究场景（如日程插件的风格研究） |

**回退规则**：
- `speed_chain` 未配置 → 回退到 `providers` 列表原始顺序
- `quality_chain` 未配置 → 回退到 `speed_chain`，再回退到 `providers` 列表原始顺序
- 完全向后兼容：两条链路都未配置时，行为与 v1.4.x 一致

**链路引用格式**：每个链路条目仅含 `provider_id` 字段，填写在 `providers` 中配置的唯一 ID。链路引用的 ID 不存在时会跳过并记录警告日志。

### 连接设置

| 配置项 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `timeout_seconds` | int | 否 | 超时时间（默认: 60 秒） |
| `reuse_session` | bool | 否 | 是否复用 HTTP 会话（高频调用场景可开启，默认: false） |

### 行为设置

| 配置项 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `enable_thinking` | bool | 否 | 是否开启思考模式（默认: true） |
| `thinking_budget` | int | 否 | 思考 token 预算（默认: 32000） |
| `max_retries` | int | 否 | 最大重试次数（默认: 3） |
| `retry_delay` | float | 否 | 重试间隔时间（默认: 1 秒），429 时优先使用 Retry-After 头 |
| `retryable_status_codes` | list | 否 | 可重试的 HTTP 状态码（默认: [429, 500, 502, 503, 504]） |

### 输出设置

| 配置项 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `show_sources` | bool | 否 | 是否显示来源 URL（默认: false） |
| `render_as_image` | bool | 否 | 是否将搜索结果渲染为图片卡片（默认: false） |
| `card_theme` | string | 否 | 卡片主题：auto（按时间自动）/ dark / light（默认: auto） |
| `max_sources` | int | 否 | 最大返回来源数量，0 表示不限制（默认: 5） |
| `custom_system_prompt` | text | 否 | 自定义系统提示词（留空使用默认提示词） |

### Skill 与 API 模式

| 配置项 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `enable_fetch` | bool | 否 | 启用网页抓取工具（默认: false），关闭时工具不会注册 |
| `enable_skill` | bool | 否 | 安装 Skill 到 skills 目录（启用后所有 LLM Tool 不会注册） |
| `use_responses_api` | bool | 否 | 使用 xAI Responses API（仅官方 API 支持，非官方端点兼容性不佳） |

> 工具开关在插件初始化时生效，修改配置后插件会自动重载卸载工具。

### 图片卡片渲染

启用 `render_as_image` 后，`/grok` 指令的搜索结果将渲染为精美的图片卡片发送：

- **面板式布局**：每个标题自动分割为独立面板，圆角矩形 + 科技青竖条装饰
- **日/夜自动主题**：`card_theme` 为 `auto` 时根据系统时间自动切换（7:00-18:00 浅色）
- **Markdown 支持**：标题、列表、代码块、引用、**粗体**、`行内代码`
- **来源链接**：以单独文本消息发送（可点击/复制）

#### 效果展示

| 深色主题 | 浅色主题 |
|:---:|:---:|
| ![深色主题](https://github.com/Inoryu7z/astrbot_plugin_grok_web_search/blob/master/image/dark.png) | ![浅色主题](https://github.com/Inoryu7z/astrbot_plugin_grok_web_search/blob/master/image/light.png) |

**字体说明**：首次启用时自动从清华镜像下载 Sarasa Term Slab SC 字体。也可在 `data/plugin_data/astrbot_plugin_grok_web_search/font/` 目录放入自定义 `.ttf` 字体文件。

### HTTP 扩展

| 配置项 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `extra_body` | JSON | 否 | 额外请求体参数 |
| `extra_headers` | JSON | 否 | 额外请求头 |

### 豆包搜索设置

当 providers 列表中的 `base_url` 包含 `ark.cn-beijing.volces.com` 时，插件会自动识别为豆包提供商并使用 Responses API + web_search 工具进行搜索。

| 配置项 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `doubao_sources` | list | 否 | 附加搜索来源，可选项: `douyin`(抖音百科)、`moji`(墨迹天气)、`toutiao`(头条图文) |
| `doubao_max_keyword` | int | 否 | 单轮搜索最大关键词数量(1-50)，默认: 5 |
| `doubao_limit` | int | 否 | 单轮搜索返回最大结果条数(1-50)，默认: 10 |
| `doubao_max_tool_calls` | int | 否 | 一次响应中工具调用最大轮次(1-10)，默认: 3 |
| `doubao_user_location` | JSON | 否 | 用户地理位置（优化地域相关搜索），格式 `{"country":"中国","region":"浙江","city":"杭州"}`，留空不启用 |
| `doubao_max_images` | int | 否 | 网页解析时最大发送图片数量（0-10，默认: 3，0 表示不发送图片） |

> 豆包提供商默认模型为 `doubao-seed-2-0-pro-260215`，可在 providers 列表中自定义。
> 豆包思考深度通过 providers 模板中的 `reasoning_effort` 字段控制（`minimal`/`low`/`medium`/`high`，默认 `medium`），v1.5.0 起替代原 `doubao_enable_thinking`。

### 豆包网页解析设置

`grok_web_fetch` 工具支持通过豆包 Bot API 调用带网页解析插件的零代码应用进行网页抓取，并可自动提取网页中的图片发送给用户。

**前置条件**：
1. 在[豆包控制台](https://console.volcengine.com/ark)创建一个零代码应用
2. 为该应用添加**网页解析插件**（注意：网页解析插件不能与联网插件同时开启）
3. 获取应用ID（格式如 `bot-20xxxxxxxx`）

**配置方式**：在 providers 列表中，为豆包提供商填写 `fetch_bot_id` 字段：

| 配置项 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `fetch_bot_id` | string | 否 | 带网页解析插件的零代码应用ID，填写后网页抓取优先使用此应用 |

> 未配置 `fetch_bot_id` 的豆包提供商在网页抓取时会被跳过，回退到 Grok 提供商。

**工作流程**：
1. 用户触发网页抓取（如"帮我看看这个页面"）
2. 插件优先查找配置了 `fetch_bot_id` 的豆包提供商
3. 通过 Bot API 调用零代码应用读取网页内容
4. 自动提取网页中的图片（受 `doubao_max_images` 限制），下载并发送给用户
5. 如果没有豆包提供商配置了 `fetch_bot_id`，回退到 Grok 提供商

## 使用

### 指令

```
/grok Python 3.12 有什么新特性
/grok 最新的 AI 新闻
/grok help              # 显示帮助和当前配置状态
```

发送图片时附带 `/grok` 指令，可进行多模态图片搜索：

```
[图片] /grok 这张图片里有什么？
```

> `/grok help` 会显示当前供应商来源、模型、系统提示词类型等配置信息。

### 重试机制

- `/grok` 指令启用自动重试，429 时优先使用服务端 `Retry-After` 头指定的等待时间，其他错误使用线性退避
- LLM Tool 不自动重试，失败立即返回，由 AI 自行决定是否重新调用
- 重试仅对自定义 HTTP 客户端通过 `retryable_status_codes` 匹配状态码
- 使用 AstrBot 自带供应商时，采用异常重试机制（不受 `retryable_status_codes` 限制）

### LLM Tool

当 LLM 需要搜索实时信息时，会自动调用 `grok_web_search` 工具。如果用户消息中包含图片，工具会自动提取图片进行多模态搜索。LLM 也可以通过 `image_urls` 参数主动传入图片链接。

每次搜索请求会自动注入当前时间上下文（日期、星期、时区），帮助 Grok 更好地处理时效性查询。

**`prefer_quality` 参数（v1.5.1）**：`grok_web_search` 工具新增 `prefer_quality` 布尔参数（默认 `false`）：
- `false`（默认）：走速度优先链路（`speed_chain`），覆盖绝大多数即时搜索场景
- `true`：走质量优先链路（`quality_chain`），仅当用户**明确要求**"用质量链/深度搜/高质量搜"时才设为 `true`，普通搜索请求保持 `false`
- 链路未配置时回退到 `providers` 列表原始顺序（完全向后兼容）

### Web Fetch

`grok_web_fetch` 工具可抓取指定 URL 的网页内容并转为结构化 Markdown。优先使用豆包网页解析应用（如已配置 `fetch_bot_id`），支持自动提取网页图片发送；未配置时回退到 Grok 联网能力。

```
# LLM 可自动调用，例如用户说：
"帮我看看 https://example.com 这个页面的内容"
"搜俩洛丽塔裙衣服来"  # AI 搜索后自动抓取网页并提取图片
```

### 文件下载（`grok_download_file`）

v1.4.0 新增的 LLM Tool，用于下载指定 URL 的文件并发送给用户。典型场景是用户说"搜个XX图片发给我"——LLM 先调用 `grok_web_search` 获取图片 URL，再调用本工具下载发送。

| 支持类型 | 行为 |
|----------|------|
| 图片（jpg/png/gif/webp/bmp/svg） | 直接发送图片消息（`file_image` → 失败时回退 `fromBytes`） |
| 文档（pdf/doc/xls/ppt/zip 等） | 直接发送文件消息 |
| 其他类型 | 通过 Content-Type 自动检测修正文件扩展名 |

- 单文件大小限制：20MB
- 下载到 `data/plugin_data/astrbot_plugin_grok_web_search_Inoryu7z/downloads/` 目录
- 返回本地路径供后续工具引用（如代码执行器读取）
- 支持 HTTP 代理（使用全局 `proxy` 配置）

### Skill

开启 `enable_skill` 后，会安装 Skill 到 `data/skills/grok-search/`，LLM 可读取 SKILL.md 后执行脚本。

Skill 脚本支持通过 `--image-files` 参数传入本地图片进行多模态搜索：

```bash
python scripts/grok_search.py --query "这张图片是什么？" --image-files "/path/to/image.jpg"
```

## 输出示例

```
Python 3.12 的主要新特性包括:

1. 更好的错误消息 - 改进了语法错误提示
2. 类型参数语法 - 支持泛型类型参数
3. 性能提升 - 解释器启动更快

来源:
  1. Python 3.12 Release Notes
     https://docs.python.org/3/whatsnew/3.12.html
  2. ...

(耗时: 2345ms)
```

## 项目结构

```
astrbot_plugin_grok_web_search/
├── main.py              # 插件主入口
├── api/                 # API 客户端
│   ├── grok_chat.py     # Chat Completions API 客户端
│   ├── grok_responses.py# Responses API 客户端（xAI 官方）
│   └── doubao_responses.py # 豆包 Responses API + Bot API 客户端
├── tool/                # 工具模块
│   ├── tool.py          # 共享工具（常量、工具函数、重试逻辑）
│   └── card_render.py   # 搜索结果图片卡片渲染器
├── image/               # 示例图片
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置项 Schema
├── README.md
└── skill/               # Skill 脚本（首次运行后迁移到 plugin_data）
    ├── SKILL.md         # Skill 说明文档
    └── scripts/
        └── grok_search.py  # 独立搜索脚本（仅标准库）
```

## 致谢

- [grok-skill](https://github.com/Frankieli123/grok-skill) — 原始 Skill 脚本项目，感谢 [@a3180623](https://linux.do/u/a3180623/summary) 的开源贡献。
- [GrokSearch](https://github.com/GuDaStudio/GrokSearch) — 网页内容抓取功能参考了该项目的实现，感谢 [GuDa Studio](https://github.com/GuDaStudio) 的开源贡献。
- [@Stonesan233](https://github.com/Stonesan233) — PR [#5](https://github.com/Inoryu7z/astrbot_plugin_grok_web_search/pull/5) 贡献了 Responses API 支持、x_search 工具和代理配置。

## 更新日志

查看 [CHANGELOG.md](https://github.com/Inoryu7z/astrbot_plugin_grok_web_search/blob/master/CHANGELOG.md) 了解版本更新历史。

## 支持

- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [Issues](https://github.com/Inoryu7z/astrbot_plugin_grok_web_search/issues)

## 🔗 相关链接
- [AstrBot](https://docs.astrbot.app/)
- [grok2api](https://github.com/chenyme/grok2api)

## 许可

AGPL-3.0 License

<div align="center">

**如果这个插件对你有帮助，请给个 ⭐ Star 支持一下！**

</div>
